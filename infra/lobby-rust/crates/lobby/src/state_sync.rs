//! Quick Play state-sync endpoint for 120Hz netplay (online-v0-1; the
//! STATE relay and finish ordering come from netsync-spike-1 Part 3).
//!
//! One UDP socket serves every room:
//! - A client waiting at the race card sends `QP_JOIN` (resent until it is
//!   paired). Two compatible waiters become a room; both get `QP_START`
//!   with their slot and the lead to one shared GO instant (the START
//!   barrier). Resent JOINs are answered with the same room and the time
//!   left, so a lost datagram never splits the start.
//! - In the race each client streams `STATE`; the room forwards it to the
//!   other seat (last-per-slot, stale ticks dropped), emits `DROP_TO_AI`
//!   on `QUIT` or `STATE_TIMEOUT`, and once every seat finished or dropped
//!   sends `FINALIZE` + `RESULT` with canonical times (owner reports,
//!   ordered by time).
//! - `PING` is answered with `PONG` (the client's RTT estimate for GO).
//!
//! `Hub` is pure (no sockets, caller passes `now`) and unit-tested below;
//! `run` is a thin driver. Logs carry room ids and slots, never addresses.
//! The TCP lobby and the O5 relay (`relay.rs`) are untouched.

use ssx3_proto::netplay::*;
use std::collections::HashMap;
use std::net::{IpAddr, SocketAddr};
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::net::UdpSocket;
use tracing::info;

const DATAGRAM_MAX: usize = 2048;
const ZERO16: [u8; 16] = [0u8; 16];
/// Seats that went quiet before their first STATE are dropped after this.
const PRE_RACE_SILENCE: Duration = Duration::from_secs(10);
/// RESULT is resent this many times, one per second (UDP).
const RESULT_SENDS: u8 = 4;
/// Per-IP bucket map cap (oldest entry evicted beyond it).
const MAX_BUCKETS: usize = 4096;

/// Endpoint configuration (`[state_sync]` in lobby.toml).
#[derive(Debug, Clone)]
pub struct Sscfg {
    pub bind: String,
    pub port: u16,
    /// Test-only injected one-way delay + uniform jitter, milliseconds.
    pub delay_ms: f64,
    pub jitter_ms: f64,
    /// No-STATE timeout per racing seat, milliseconds.
    pub timeout_ms: u64,
    /// Lead from pairing to GO, milliseconds.
    pub start_delay_ms: u64,
    /// A waiter is forgotten when its JOINs stop for this long.
    pub waiter_ttl_ms: u64,
    /// A room is removed when every seat was silent this long.
    pub room_idle_ms: u64,
    /// Hard room lifetime, seconds.
    pub max_room_secs: u64,
    pub max_rooms: usize,
    pub max_waiters: usize,
    pub max_waiters_per_ip: usize,
    /// Per source IP packet bucket.
    pub packets_per_sec: f64,
    pub burst_packets: f64,
    /// Global egress bucket (payload + 28 B IP/UDP per datagram).
    pub global_bytes_per_sec: f64,
    pub global_burst_bytes: f64,
}

impl Default for Sscfg {
    fn default() -> Self {
        Self {
            bind: "127.0.0.1".into(),
            port: 3757,
            delay_ms: 0.0,
            jitter_ms: 0.0,
            timeout_ms: 2000,
            start_delay_ms: 2000,
            waiter_ttl_ms: 3000,
            room_idle_ms: 30_000,
            max_room_secs: 1200,
            max_rooms: 256,
            max_waiters: 128,
            max_waiters_per_ip: 4,
            packets_per_sec: 100.0,
            burst_packets: 200.0,
            global_bytes_per_sec: 812_000.0,
            global_burst_bytes: 1_048_576.0,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum Action {
    Forward(Vec<u8>),
}

/// Per-room STATE tracker (netsync-spike-1 Part 3): last-per-slot, stale
/// drop, timeout sweep, canonical finish ordering.
pub struct Tracker {
    timeout: Duration,
    last: HashMap<u16, StateUpdate>,
    last_recv: HashMap<u16, Instant>,
    finished: HashMap<u16, (u64, u32)>,
    dropped: Vec<u16>,
    finalized: bool,
    stale_drops: u64,
    forwarded: u64,
}

impl Tracker {
    pub fn new(timeout_ms: u64) -> Self {
        Self {
            timeout: Duration::from_millis(timeout_ms),
            last: HashMap::new(),
            last_recv: HashMap::new(),
            finished: HashMap::new(),
            dropped: Vec::new(),
            finalized: false,
            stale_drops: 0,
            forwarded: 0,
        }
    }

    /// One STATE sample. Returns actions for the driver.
    pub fn on_state(&mut self, slot: u16, st: StateUpdate, now: Instant, raw: Vec<u8>) -> Vec<Action> {
        let mut out = Vec::new();
        if let Some(prev) = self.last.get(&slot) {
            if st.tick <= prev.tick {
                self.stale_drops += 1;
                return out;
            }
        }
        self.last.insert(slot, st);
        self.last_recv.insert(slot, now);
        self.forwarded += 1;
        out.push(Action::Forward(raw));
        if st.fin != 0 && !self.finished.contains_key(&slot) {
            let ms = (st.ftime * 1000.0) as u32;
            self.finished.insert(slot, (st.tick, ms));
        }
        out
    }

    /// Marks `slot` dropped; false when it already was.
    fn drop_slot(&mut self, slot: u16) -> bool {
        if self.dropped.contains(&slot) {
            return false;
        }
        self.dropped.push(slot);
        true
    }

    /// Timeout sweep: the slots that newly went silent past the timeout
    /// (finished slots may stop sending). The caller drops and notifies.
    pub fn sweep(&self, live: &[u16], now: Instant) -> Vec<u16> {
        live.iter()
            .copied()
            .filter(|s| !self.dropped.contains(s) && !self.finished.contains_key(s))
            .filter(|s| self.last_recv.get(s).is_some_and(|t| now.duration_since(*t) > self.timeout))
            .collect()
    }

    /// Canonical results once every racing slot finished or dropped.
    /// Canonical time = the owner's report; rows ordered by time.
    pub fn finalize(&mut self, slots: &[u16]) -> Option<(Finalize, NetResult)> {
        if self.finalized || slots.is_empty() {
            return None;
        }
        for &s in slots {
            if !self.finished.contains_key(&s) && !self.dropped.contains(&s) {
                return None;
            }
        }
        self.finalized = true;
        let mut rows: Vec<(u16, u64, u32, u8)> = Vec::new();
        let mut boundary = 0u64;
        for &s in slots {
            if let Some(&(tick, ms)) = self.finished.get(&s) {
                boundary = boundary.max(tick);
                rows.push((s, tick, ms, DISP_FINISHED));
            } else {
                rows.push((s, 0, 0xffffffff, DISP_DNF));
            }
        }
        rows.sort_by_key(|&(_, _, ms, _)| ms);
        let mut result_rows = Vec::new();
        for (i, (slot, tick, ms, disp)) in rows.iter().enumerate() {
            result_rows.push(ResultRow {
                slot: *slot,
                guest_actor: *slot as u8,
                disposition: *disp,
                rank: if *disp == DISP_FINISHED { (i + 1) as u8 } else { 0 },
                finish_tick: *tick,
                race_time_ms: *ms,
                score: i64::MIN,
            });
        }
        let fin = Finalize { boundary, last_commit_seq: 0, chain: [0u8; 32], roster_rev: 1 };
        let mut digest_in = Vec::new();
        for r in &result_rows {
            digest_in.extend_from_slice(&r.encode());
        }
        let mut db = [0u8; 32];
        db[0..8].copy_from_slice(&fnv1a(&digest_in).to_le_bytes());
        let res = NetResult {
            result_id: rand::random(),
            final_boundary: boundary,
            final_commit_seq: 0,
            chain: [0u8; 32],
            state: StateDigest { alg: STATE_ALG_FNV64_DIAG, bytes: db },
            transcript: [0u8; 32],
            results: result_rows,
        };
        Some((fin, res))
    }
}

fn fnv1a(b: &[u8]) -> u64 {
    let mut h: u64 = 0xcbf29ce484222325;
    for &x in b {
        h ^= x as u64;
        h = h.wrapping_mul(0x100000001b3);
    }
    h
}

/// Token bucket with an explicit clock (the hub is pure).
#[derive(Debug, Clone)]
struct Bucket {
    rate: f64,
    cap: f64,
    tokens: f64,
    last: Instant,
}

impl Bucket {
    fn new(rate: f64, cap: f64, now: Instant) -> Self {
        Self { rate: rate.max(0.0), cap: cap.max(0.0), tokens: cap.max(0.0), last: now }
    }

    fn take(&mut self, n: f64, now: Instant) -> bool {
        let dt = now.saturating_duration_since(self.last).as_secs_f64();
        self.last = now.max(self.last);
        self.tokens = (self.tokens + dt * self.rate).min(self.cap);
        if self.tokens >= n {
            self.tokens -= n;
            true
        } else {
            false
        }
    }
}

struct Waiter {
    addr: SocketAddr,
    client: [u8; 16],
    course: u8,
    seen: Instant,
}

struct Seat {
    addr: SocketAddr,
    seen: Instant,
    quit: bool,
    start_sends: Vec<Instant>, // send time of each QP_START (index = seq)
    go: Option<QpGo>,
    result_sends: u8,
    result_last: Option<Instant>,
}

impl Seat {
    fn new(addr: SocketAddr, now: Instant) -> Self {
        Self { addr, seen: now, quit: false, start_sends: Vec::new(), go: None, result_sends: 0, result_last: None }
    }
}

struct Room {
    id: [u8; 16],
    seats: Vec<Seat>,
    course: u8,
    start_id: u32,
    go_at: Instant,
    created: Instant,
    tracker: Tracker,
    result: Option<(Vec<u8>, Vec<u8>)>,
    skew_logged: bool,
}

impl Room {
    fn other_addrs(&self, slot: u16) -> Vec<SocketAddr> {
        self.seats
            .iter()
            .enumerate()
            .filter(|(i, s)| *i as u16 != slot && !s.quit)
            .map(|(_, s)| s.addr)
            .collect()
    }
}

/// One outgoing datagram.
pub(crate) type Out = (SocketAddr, Vec<u8>);

/// Counters for the periodic stats line.
#[derive(Debug, Default, Clone, Copy)]
pub struct Stats {
    pub rooms_made: u64,
    pub forwarded: u64,
    pub dropped: u64,
    pub limited: u64,
}

/// The pure matchmaking + relay state machine.
pub struct Hub {
    cfg: Sscfg,
    waiters: Vec<Waiter>,
    rooms: HashMap<[u8; 16], Room>,
    seat_of: HashMap<SocketAddr, ([u8; 16], u16)>,
    buckets: HashMap<IpAddr, Bucket>,
    global: Bucket,
    seq: u64,
    pub stats: Stats,
}

fn short(id: &[u8; 16]) -> String {
    hex::encode(&id[..4])
}

impl Hub {
    pub fn new(cfg: Sscfg, now: Instant) -> Self {
        let global = Bucket::new(cfg.global_bytes_per_sec, cfg.global_burst_bytes, now);
        Self {
            cfg,
            waiters: Vec::new(),
            rooms: HashMap::new(),
            seat_of: HashMap::new(),
            buckets: HashMap::new(),
            global,
            seq: 0,
            stats: Stats::default(),
        }
    }

    pub fn waiting(&self) -> usize {
        self.waiters.len()
    }

    pub fn rooms(&self) -> usize {
        self.rooms.len()
    }

    fn frame(&mut self, session: &[u8; 16], kind: u8, payload: &[u8]) -> Vec<u8> {
        self.seq += 1;
        encode_frame(session, 0, kind, SENDER_RELAY, self.seq, payload)
    }

    fn push(&mut self, out: &mut Vec<Out>, dst: SocketAddr, bytes: Vec<u8>, now: Instant) {
        if self.global.take(bytes.len() as f64 + 28.0, now) {
            out.push((dst, bytes));
        } else {
            self.stats.limited += 1;
        }
    }

    fn admit(&mut self, ip: IpAddr, now: Instant) -> bool {
        if !self.buckets.contains_key(&ip) && self.buckets.len() >= MAX_BUCKETS {
            if let Some(old) = self.buckets.iter().min_by_key(|(_, b)| b.last).map(|(k, _)| *k) {
                self.buckets.remove(&old);
            }
        }
        let (rate, burst) = (self.cfg.packets_per_sec, self.cfg.burst_packets);
        self.buckets.entry(ip).or_insert_with(|| Bucket::new(rate, burst, now)).take(1.0, now)
    }

    fn wait_frame(&mut self, status: u8) -> Vec<u8> {
        let w = QpWait { status, waiting: self.waiters.len().min(u16::MAX as usize) as u16 };
        self.frame(&ZERO16, KIND_QP_WAIT, &w.encode())
    }

    fn start_frame(&mut self, id: [u8; 16], slot: u16, now: Instant) -> Option<Vec<u8>> {
        let room = self.rooms.get_mut(&id)?;
        let left = room.go_at.saturating_duration_since(now).as_millis().min(u16::MAX as u128) as u16;
        let (start_id, players, course) = (room.start_id, room.seats.len() as u8, room.course);
        let seat = room.seats.get_mut(slot as usize)?;
        let seq = seat.start_sends.len().min(255) as u8;
        seat.start_sends.push(now);
        let st = QpStart { start_id, slot: slot as u8, players, course, seq, go_in_ms: left };
        Some(self.frame(&id, KIND_QP_START, &st.encode()))
    }

    /// Drops `slot` (once) and tells the other seats.
    fn drop_seat(&mut self, id: [u8; 16], slot: u16, reason: u16, out: &mut Vec<Out>, now: Instant) {
        let Some(room) = self.rooms.get_mut(&id) else { return };
        if !room.tracker.drop_slot(slot) {
            return;
        }
        let others = room.other_addrs(slot);
        info!(room = %short(&id), slot, reason, "quickplay drop-ai");
        let ev = Event {
            event_seq: 1,
            event_type: EVENT_DROP_TO_AI,
            drop: DropToAi { slot, reason, evidence_boundary: 0 },
        };
        let msg = self.frame(&id, KIND_EVENT, &ev.encode());
        for dst in others {
            self.push(out, dst, msg.clone(), now);
        }
    }

    /// One datagram from `src`. Returns datagrams to send.
    pub fn on_datagram(&mut self, src: SocketAddr, data: &[u8], now: Instant) -> Vec<Out> {
        let mut out = Vec::new();
        if !self.admit(src.ip(), now) {
            self.stats.limited += 1;
            return out;
        }
        let frame = match decode_frame(data) {
            Ok(f) => f,
            Err(_) => {
                self.stats.dropped += 1;
                return out;
            }
        };
        let session = *frame.session;
        let (kind, sender, payload) = (frame.kind, frame.sender, frame.payload.to_vec());
        match kind {
            KIND_QP_JOIN => self.on_join(src, &payload, now, &mut out),
            KIND_PING => {
                if Ping::decode(&payload).is_ok() {
                    let pong = self.frame(&ZERO16, KIND_PONG, &payload);
                    self.push(&mut out, src, pong, now);
                } else {
                    self.stats.dropped += 1;
                }
            }
            KIND_QUIT => {
                if let Some(&(id, slot)) = self.seat_of.get(&src) {
                    if let Some(room) = self.rooms.get_mut(&id) {
                        room.seats[slot as usize].quit = true;
                        room.seats[slot as usize].seen = now;
                    }
                    self.drop_seat(id, slot, REASON_QUIT, &mut out, now);
                    self.seat_of.remove(&src);
                    self.try_finalize(id, now, &mut out);
                } else {
                    self.waiters.retain(|w| w.addr != src);
                }
            }
            KIND_QP_GO => {
                let seated = self.seat_of.get(&src).copied();
                match (seated, QpGo::decode(&payload)) {
                    (Some((id, slot)), Ok(go)) if session == id && sender == slot => {
                        if let Some(room) = self.rooms.get_mut(&id) {
                            room.seats[slot as usize].go = Some(go);
                            room.seats[slot as usize].seen = now;
                            Self::log_skew(room);
                        }
                    }
                    _ => self.stats.dropped += 1,
                }
            }
            KIND_STATE => self.on_state(src, session, sender, &payload, data, now, &mut out),
            _ => self.stats.dropped += 1,
        }
        out
    }

    fn on_join(&mut self, src: SocketAddr, payload: &[u8], now: Instant, out: &mut Vec<Out>) {
        let Ok(j) = QpJoin::decode(payload) else {
            self.stats.dropped += 1;
            return;
        };
        if j.proto != QP_PROTO || j.rate != QP_RATE_120 {
            let f = self.wait_frame(QP_WAIT_INCOMPATIBLE);
            self.push(out, src, f, now);
            return;
        }
        // Already seated: the START was lost (or is being confirmed).
        if let Some(&(id, slot)) = self.seat_of.get(&src) {
            if let Some(room) = self.rooms.get_mut(&id) {
                room.seats[slot as usize].seen = now;
            }
            if let Some(f) = self.start_frame(id, slot, now) {
                self.push(out, src, f, now);
            }
            return;
        }
        if let Some(w) = self.waiters.iter_mut().find(|w| w.addr == src) {
            w.seen = now;
        } else {
            let per_ip = self.waiters.iter().filter(|w| w.addr.ip() == src.ip()).count();
            if self.waiters.len() >= self.cfg.max_waiters
                || per_ip >= self.cfg.max_waiters_per_ip
                || self.rooms.len() >= self.cfg.max_rooms
            {
                let f = self.wait_frame(QP_WAIT_FULL);
                self.push(out, src, f, now);
                return;
            }
            self.waiters.push(Waiter { addr: src, client: j.client, course: j.course, seen: now });
        }
        // Pair this waiter with the oldest compatible one.
        let me = self.waiters.iter().position(|w| w.addr == src).expect("just queued");
        let (my_client, my_course) = (self.waiters[me].client, self.waiters[me].course);
        let Some(mate) = self
            .waiters
            .iter()
            .position(|w| w.addr != src && w.client != my_client && w.course == my_course)
        else {
            let f = self.wait_frame(QP_WAIT_QUEUED);
            self.push(out, src, f, now);
            return;
        };
        let (first, second) = if mate < me { (mate, me) } else { (me, mate) };
        let b = self.waiters.remove(second);
        let a = self.waiters.remove(first);
        let id: [u8; 16] = rand::random();
        let room = Room {
            id,
            seats: vec![Seat::new(a.addr, now), Seat::new(b.addr, now)],
            course: a.course,
            start_id: rand::random(),
            go_at: now + Duration::from_millis(self.cfg.start_delay_ms),
            created: now,
            tracker: Tracker::new(self.cfg.timeout_ms),
            result: None,
            skew_logged: false,
        };
        self.rooms.insert(id, room);
        self.seat_of.insert(a.addr, (id, 0));
        self.seat_of.insert(b.addr, (id, 1));
        self.stats.rooms_made += 1;
        info!(room = %short(&id), course = a.course, lead_ms = self.cfg.start_delay_ms, "quickplay paired");
        for (slot, addr) in [(0u16, a.addr), (1u16, b.addr)] {
            if let Some(f) = self.start_frame(id, slot, now) {
                self.push(out, addr, f, now);
            }
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn on_state(
        &mut self,
        src: SocketAddr,
        session: [u8; 16],
        sender: u16,
        payload: &[u8],
        raw: &[u8],
        now: Instant,
        out: &mut Vec<Out>,
    ) {
        let Some(&(id, slot)) = self.seat_of.get(&src) else {
            self.stats.dropped += 1;
            return;
        };
        let Ok(st) = StateUpdate::decode(payload) else {
            self.stats.dropped += 1;
            return;
        };
        if session != id || sender != slot || st.slot != slot {
            self.stats.dropped += 1;
            return;
        }
        let Some(room) = self.rooms.get_mut(&id) else { return };
        room.seats[slot as usize].seen = now;
        let acts = room.tracker.on_state(slot, st, now, raw.to_vec());
        let dsts = room.other_addrs(slot);
        for a in acts {
            let Action::Forward(bytes) = a;
            for &d in &dsts {
                self.stats.forwarded += 1;
                self.push(out, d, bytes.clone(), now);
            }
        }
        self.try_finalize(id, now, out);
    }

    /// Builds FINALIZE + RESULT once every seat finished or dropped, then
    /// (re)sends them to each live seat at most once per second.
    fn try_finalize(&mut self, id: [u8; 16], now: Instant, out: &mut Vec<Out>) {
        let Some(room) = self.rooms.get_mut(&id) else { return };
        if room.result.is_none() {
            let slots: Vec<u16> = (0..room.seats.len() as u16).collect();
            if let Some((fin, res)) = room.tracker.finalize(&slots) {
                info!(room = %short(&id), boundary = fin.boundary,
                      ranks = ?res.results.iter().map(|r| (r.slot, r.rank, r.race_time_ms)).collect::<Vec<_>>(),
                      "quickplay result");
                let (fe, re) = (fin.encode(), res.encode());
                let f = self.frame(&id, KIND_FINALIZE, &fe);
                let r = self.frame(&id, KIND_RESULT, &re);
                if let Some(room) = self.rooms.get_mut(&id) {
                    room.result = Some((f, r));
                }
            }
        }
        let Some(room) = self.rooms.get_mut(&id) else { return };
        let Some((f, r)) = room.result.clone() else { return };
        let mut sends = Vec::new();
        for seat in room.seats.iter_mut() {
            if seat.quit || seat.result_sends >= RESULT_SENDS {
                continue;
            }
            if seat.result_last.is_none_or(|t| now.duration_since(t) >= Duration::from_secs(1)) {
                seat.result_sends += 1;
                seat.result_last = Some(now);
                sends.push(seat.addr);
            }
        }
        for dst in sends {
            self.push(out, dst, f.clone(), now);
            self.push(out, dst, r.clone(), now);
        }
    }

    /// Start-skew evidence: estimated GO on the server clock = the START
    /// send the client used + its RTT/2 + its START-arrival-to-GO time.
    fn log_skew(room: &mut Room) {
        if room.skew_logged || room.seats.iter().any(|s| s.go.is_none() && !s.quit) {
            return;
        }
        let mut est: Vec<i128> = Vec::new();
        for s in &room.seats {
            let Some(go) = s.go else { continue };
            let Some(sent) = s.start_sends.get(go.seq as usize) else { continue };
            if go.start_id != room.start_id {
                continue;
            }
            let base = sent.saturating_duration_since(room.created).as_micros() as i128;
            est.push(base + go.rtt_us as i128 / 2 + go.recv_to_go_us as i128);
        }
        if est.len() == 2 {
            room.skew_logged = true;
            let skew_us = (est[1] - est[0]) as i64;
            info!(room = %short(&room.id), skew_us,
                  skew_ticks_120 = %format!("{:.2}", skew_us as f64 * 120.0 / 1e6),
                  rtt0_us = room.seats[0].go.map_or(0, |g| g.rtt_us),
                  rtt1_us = room.seats[1].go.map_or(0, |g| g.rtt_us),
                  "quickplay start skew");
        }
    }

    /// Periodic sweep (the driver calls it every ~100 ms): waiter expiry,
    /// race timeouts, pre-race silence, RESULT resends, room expiry.
    pub fn on_tick(&mut self, now: Instant) -> Vec<Out> {
        let mut out = Vec::new();
        let ttl = Duration::from_millis(self.cfg.waiter_ttl_ms);
        self.waiters.retain(|w| now.duration_since(w.seen) <= ttl);
        let ids: Vec<[u8; 16]> = self.rooms.keys().copied().collect();
        for id in ids {
            let Some(room) = self.rooms.get(&id) else { continue };
            let live: Vec<u16> = (0..room.seats.len() as u16).filter(|&s| !room.seats[s as usize].quit).collect();
            let mut drops: Vec<u16> = room.tracker.sweep(&live, now);
            // Seats that never streamed and went silent (gone at the card).
            for &s in &live {
                let seat = &room.seats[s as usize];
                if !room.tracker.last.contains_key(&s)
                    && !room.tracker.dropped.contains(&s)
                    && now.saturating_duration_since(seat.seen.max(room.go_at)) > PRE_RACE_SILENCE
                {
                    drops.push(s);
                }
            }
            for s in drops {
                self.drop_seat(id, s, REASON_STATE_TIMEOUT, &mut out, now);
            }
            self.try_finalize(id, now, &mut out);
            let room = &self.rooms[&id];
            let idle = Duration::from_millis(self.cfg.room_idle_ms);
            let all_gone = room.seats.iter().all(|s| s.quit || now.duration_since(s.seen) > idle);
            let too_old = now.duration_since(room.created) > Duration::from_secs(self.cfg.max_room_secs);
            if all_gone || too_old {
                info!(room = %short(&id), too_old, "quickplay room closed");
                let addrs: Vec<SocketAddr> = room.seats.iter().map(|s| s.addr).collect();
                for a in addrs {
                    if self.seat_of.get(&a).map(|x| x.0) == Some(id) {
                        self.seat_of.remove(&a);
                    }
                }
                self.rooms.remove(&id);
            }
        }
        out
    }
}

/// Socket driver.
pub async fn run(cfg: Sscfg, mut stop: tokio::sync::watch::Receiver<bool>) {
    let sock = match UdpSocket::bind((cfg.bind.as_str(), cfg.port)).await {
        Ok(s) => Arc::new(s),
        Err(e) => {
            info!(port = cfg.port, err = %e, "quickplay bind failed");
            return;
        }
    };
    info!(port = cfg.port, lead_ms = cfg.start_delay_ms, delay_ms = cfg.delay_ms,
          jitter_ms = cfg.jitter_ms, "quickplay up");
    let (delay, jitter) = (cfg.delay_ms, cfg.jitter_ms);
    let mut hub = Hub::new(cfg, Instant::now());
    let mut buf = [0u8; DATAGRAM_MAX];
    let mut tick = tokio::time::interval(Duration::from_millis(100));
    let mut stat_t = Instant::now();
    let send = |sock: &Arc<UdpSocket>, outs: Vec<Out>| {
        for (dst, bytes) in outs {
            let d = delay / 1000.0 + rand::random::<f64>() * jitter / 1000.0;
            if d > 0.0 {
                let s2 = sock.clone();
                tokio::spawn(async move {
                    tokio::time::sleep(Duration::from_secs_f64(d)).await;
                    let _ = s2.send_to(&bytes, dst).await;
                });
            } else {
                let _ = sock.try_send_to(&bytes, dst);
            }
        }
    };
    loop {
        tokio::select! {
            _ = stop.changed() => break,
            _ = tick.tick() => {
                let outs = hub.on_tick(Instant::now());
                send(&sock, outs);
                if stat_t.elapsed() >= Duration::from_secs(60) {
                    stat_t = Instant::now();
                    info!(waiting = hub.waiting(), rooms = hub.rooms(), made = hub.stats.rooms_made,
                          fwd = hub.stats.forwarded, drop = hub.stats.dropped, limited = hub.stats.limited,
                          "quickplay stats");
                }
            }
            r = sock.recv_from(&mut buf) => {
                let Ok((n, src)) = r else { continue };
                let outs = hub.on_datagram(src, &buf[..n], Instant::now());
                send(&sock, outs);
            }
        }
    }
    info!("quickplay down");
}

#[cfg(test)]
mod tests {
    use super::*;

    fn st(slot: u16, tick: u64, fin: u8, ftime: f32) -> StateUpdate {
        StateUpdate {
            slot, tick, pos: [1.0, 2.0, 3.0], quat: [0.0, 0.0, 0.0, 1.0],
            vel: [4.0, 5.0, 6.0], boost: 0.0, uber: 0, wipe: 0, ctl: 0,
            lap: 1, chk: 0, dist: 0.0, fin, ftime, peer_tick: 0,
            peer_time_ms: 0, in_tick: tick, in_de4: 5,
            in_w0: 0x11223344, in_w1: 0x55667788, digest: 0,
        }
    }

    fn addr(p: u16) -> SocketAddr {
        SocketAddr::from(([10, 0, 0, 1], p))
    }

    fn join(client: u8, rate: u8) -> Vec<u8> {
        let j = QpJoin { proto: QP_PROTO, client: [client; 16], rate, course: 0 };
        encode_frame(&ZERO16, 0, KIND_QP_JOIN, 0, 1, &j.encode())
    }

    fn kinds(outs: &[Out]) -> Vec<(u16, u8)> {
        outs.iter().map(|(a, b)| (a.port(), decode_frame(b).unwrap().kind)).collect()
    }

    fn start_of(outs: &[Out], port: u16) -> ([u8; 16], QpStart) {
        let (_, b) = outs
            .iter()
            .find(|(a, b)| a.port() == port && decode_frame(b).unwrap().kind == KIND_QP_START)
            .unwrap();
        let f = decode_frame(b).unwrap();
        (*f.session, QpStart::decode(f.payload).unwrap())
    }

    fn state_frame(room: &[u8; 16], slot: u16, tick: u64, fin: u8, ftime: f32) -> Vec<u8> {
        encode_frame(room, 0, KIND_STATE, slot, tick + 1, &st(slot, tick, fin, ftime).encode())
    }

    fn paired(hub: &mut Hub, t0: Instant) -> [u8; 16] {
        let a = hub.on_datagram(addr(1), &join(1, 120), t0);
        assert_eq!(kinds(&a), vec![(1, KIND_QP_WAIT)]);
        let b = hub.on_datagram(addr(2), &join(2, 120), t0);
        let (ra, sa) = start_of(&b, 1);
        let (rb, sb) = start_of(&b, 2);
        assert_eq!(ra, rb);
        assert_eq!((sa.slot, sb.slot), (0, 1));
        assert_eq!(sa.start_id, sb.start_id);
        assert_eq!((sa.go_in_ms, sb.go_in_ms), (2000, 2000));
        ra
    }

    #[test]
    fn last_per_slot_wins() {
        let mut tr = Tracker::new(2000);
        let now = Instant::now();
        assert_eq!(tr.on_state(0, st(0, 100, 0, 0.0), now, vec![1]).len(), 1);
        assert_eq!(tr.on_state(0, st(0, 200, 0, 0.0), now, vec![2]).len(), 1);
        assert_eq!(tr.last[&0].tick, 200);
        assert_eq!(tr.forwarded, 2);
    }

    #[test]
    fn stale_ticks_drop() {
        let mut tr = Tracker::new(2000);
        let now = Instant::now();
        tr.on_state(1, st(1, 300, 0, 0.0), now, vec![1]);
        assert!(tr.on_state(1, st(1, 300, 0, 0.0), now, vec![2]).is_empty());
        assert!(tr.on_state(1, st(1, 299, 0, 0.0), now, vec![3]).is_empty());
        assert_eq!(tr.stale_drops, 2);
    }

    #[test]
    fn finish_order_canonical() {
        let mut tr = Tracker::new(2000);
        let now = Instant::now();
        tr.on_state(1, st(1, 30496, 1, 239.5), now, vec![1]);
        assert!(tr.finalize(&[0, 1]).is_none());
        tr.on_state(0, st(0, 33053, 1, 260.2), now, vec![1]);
        let (fin, res) = tr.finalize(&[0, 1]).unwrap();
        assert_eq!(fin.boundary, 33053);
        assert_eq!((res.results[0].slot, res.results[0].rank), (1, 1));
        assert_eq!((res.results[1].slot, res.results[1].rank), (0, 2));
        assert!(tr.finalize(&[0, 1]).is_none());
    }

    #[test]
    fn quitter_is_dnf() {
        let mut tr = Tracker::new(2000);
        let now = Instant::now();
        tr.on_state(0, st(0, 30496, 1, 239.5), now, vec![1]);
        tr.drop_slot(1);
        let (_, res) = tr.finalize(&[0, 1]).unwrap();
        assert_eq!(res.results[0].slot, 0);
        assert_eq!((res.results[1].disposition, res.results[1].rank), (DISP_DNF, 0));
    }

    #[test]
    fn pairs_two_waiters_with_one_go_instant() {
        let t0 = Instant::now();
        let mut hub = Hub::new(Sscfg::default(), t0);
        let room = paired(&mut hub, t0);
        // A resent JOIN 500 ms later: same room, 500 ms less lead.
        let r = hub.on_datagram(addr(1), &join(1, 120), t0 + Duration::from_millis(500));
        let (rr, s) = start_of(&r, 1);
        assert_eq!(rr, room);
        assert_eq!((s.slot, s.seq, s.go_in_ms), (0, 1, 1500));
        assert_eq!((hub.waiting(), hub.rooms()), (0, 1));
        // After GO the lead saturates at 0 (a late START starts at once).
        let r = hub.on_datagram(addr(2), &join(2, 120), t0 + Duration::from_secs(5));
        assert_eq!(start_of(&r, 2).1.go_in_ms, 0);
    }

    #[test]
    fn refuses_incompatible_and_full() {
        let t0 = Instant::now();
        let mut cfg = Sscfg::default();
        cfg.max_waiters_per_ip = 1;
        let mut hub = Hub::new(cfg, t0);
        let r = hub.on_datagram(addr(1), &join(1, 60), t0);
        let f = decode_frame(&r[0].1).unwrap();
        assert_eq!(QpWait::decode(f.payload).unwrap().status, QP_WAIT_INCOMPATIBLE);
        assert_eq!(hub.waiting(), 0);
        hub.on_datagram(addr(1), &join(1, 120), t0);
        let r = hub.on_datagram(addr(2), &join(2, 120), t0);
        let f = decode_frame(&r[0].1).unwrap();
        assert_eq!(QpWait::decode(f.payload).unwrap().status, QP_WAIT_FULL);
    }

    #[test]
    fn same_client_never_pairs_with_itself_and_waiters_expire() {
        let t0 = Instant::now();
        let mut hub = Hub::new(Sscfg::default(), t0);
        hub.on_datagram(addr(1), &join(1, 120), t0);
        let r = hub.on_datagram(addr(2), &join(1, 120), t0);
        assert_eq!(kinds(&r), vec![(2, KIND_QP_WAIT)]);
        assert_eq!(hub.waiting(), 2);
        hub.on_tick(t0 + Duration::from_millis(3500));
        assert_eq!(hub.waiting(), 0);
    }

    #[test]
    fn relays_state_and_finalizes_both() {
        let t0 = Instant::now();
        let mut hub = Hub::new(Sscfg::default(), t0);
        let room = paired(&mut hub, t0);
        let t1 = t0 + Duration::from_secs(3);
        let r = hub.on_datagram(addr(1), &state_frame(&room, 0, 6, 0, 0.0), t1);
        assert_eq!(kinds(&r), vec![(2, KIND_STATE)]);
        // A forged slot (seat 0 claiming 1) is dropped.
        assert!(hub.on_datagram(addr(1), &state_frame(&room, 1, 7, 0, 0.0), t1).is_empty());
        hub.on_datagram(addr(2), &state_frame(&room, 1, 6, 1, 200.5), t1);
        let r = hub.on_datagram(addr(1), &state_frame(&room, 0, 12, 1, 210.25), t1);
        let k = kinds(&r);
        assert!(k.contains(&(1, KIND_RESULT)) && k.contains(&(2, KIND_RESULT)));
        let (_, b) = r
            .iter()
            .find(|(a, b)| a.port() == 1 && decode_frame(b).unwrap().kind == KIND_RESULT)
            .unwrap();
        let res = NetResult::decode(decode_frame(b).unwrap().payload).unwrap();
        assert_eq!((res.results[0].slot, res.results[0].race_time_ms), (1, 200500));
        assert_eq!((res.results[1].slot, res.results[1].rank), (0, 2));
        // Resent at most once per second.
        assert!(!kinds(&hub.on_tick(t1 + Duration::from_millis(500))).contains(&(1, KIND_RESULT)));
        assert!(kinds(&hub.on_tick(t1 + Duration::from_millis(1100))).contains(&(1, KIND_RESULT)));
    }

    #[test]
    fn quit_and_timeout_drop_to_ai() {
        let t0 = Instant::now();
        let mut hub = Hub::new(Sscfg::default(), t0);
        let room = paired(&mut hub, t0);
        let t1 = t0 + Duration::from_secs(3);
        hub.on_datagram(addr(1), &state_frame(&room, 0, 6, 0, 0.0), t1);
        hub.on_datagram(addr(2), &state_frame(&room, 1, 6, 0, 0.0), t1);
        // Seat 1 goes silent: seat 0 hears DROP_TO_AI(slot 1, STATE_TIMEOUT), once.
        hub.on_datagram(addr(1), &state_frame(&room, 0, 300, 0, 0.0), t1 + Duration::from_millis(2500));
        let r = hub.on_tick(t1 + Duration::from_millis(2600));
        let (_, b) = r.iter().find(|(a, _)| a.port() == 1).unwrap();
        let f = decode_frame(b).unwrap();
        assert_eq!(f.kind, KIND_EVENT);
        let (ev, _) = Event::decode_one(f.payload).unwrap();
        assert_eq!((ev.drop.slot, ev.drop.reason), (1, REASON_STATE_TIMEOUT));
        assert!(!kinds(&hub.on_tick(t1 + Duration::from_millis(2700))).contains(&(1, KIND_EVENT)));
        // Then seat 0 quits: the room closes once everyone is gone.
        let quit = encode_frame(&room, 0, KIND_QUIT, 0, 99, &Quit { reason: REASON_QUIT }.encode());
        hub.on_datagram(addr(1), &quit, t1 + Duration::from_secs(3));
        hub.on_tick(t1 + Duration::from_secs(40));
        assert_eq!(hub.rooms(), 0);
    }

    #[test]
    fn pre_race_quit_tells_the_other_seat() {
        let t0 = Instant::now();
        let mut hub = Hub::new(Sscfg::default(), t0);
        let room = paired(&mut hub, t0);
        let quit = encode_frame(&room, 0, KIND_QUIT, 1, 5, &Quit { reason: REASON_QUIT }.encode());
        let r = hub.on_datagram(addr(2), &quit, t0 + Duration::from_millis(300));
        assert_eq!(kinds(&r), vec![(1, KIND_EVENT)]);
    }

    #[test]
    fn silent_seat_at_the_card_is_dropped() {
        let t0 = Instant::now();
        let mut hub = Hub::new(Sscfg::default(), t0);
        let room = paired(&mut hub, t0);
        let t1 = t0 + Duration::from_secs(3);
        hub.on_datagram(addr(1), &state_frame(&room, 0, 6, 0, 0.0), t1);
        // Seat 1 never streams: dropped PRE_RACE_SILENCE after GO.
        let r = hub.on_tick(t0 + Duration::from_secs(13));
        assert!(kinds(&r).contains(&(1, KIND_EVENT)));
    }

    #[test]
    fn rate_limit_per_ip() {
        let t0 = Instant::now();
        let mut cfg = Sscfg::default();
        cfg.burst_packets = 3.0;
        cfg.packets_per_sec = 1.0;
        let mut hub = Hub::new(cfg, t0);
        let ping = encode_frame(&ZERO16, 0, KIND_PING, 0, 1, &Ping { nonce: 1, origin_monotonic_us: 2 }.encode());
        let n = (0..10).filter(|_| !hub.on_datagram(addr(1), &ping, t0).is_empty()).count();
        assert_eq!(n, 3);
        assert_eq!(hub.stats.limited, 7);
    }

    #[test]
    fn start_skew_is_logged_from_go_reports() {
        let t0 = Instant::now();
        let mut hub = Hub::new(Sscfg::default(), t0);
        let room = paired(&mut hub, t0);
        let sid = hub.rooms[&room].start_id;
        for (port, slot, rtt) in [(1u16, 0u16, 20_000u32), (2, 1, 40_000)] {
            let go = QpGo { start_id: sid, seq: 0, rtt_us: rtt, recv_to_go_us: 1_990_000 - rtt / 2, go_tick: 5000 };
            hub.on_datagram(addr(port), &encode_frame(&room, 0, KIND_QP_GO, slot, 3, &go.encode()), t0);
        }
        assert!(hub.rooms[&room].skew_logged);
    }
}
