//! State-sync relay endpoint for 120Hz ghost-rider netcode
//! (netsync-spike-1 Part 3). Loopback test-only transport: two enrolled
//! peers exchange SNP2 `STATE` frames; this task keeps last-per-slot,
//! drops stale ticks, injects configured delay/jitter, emits `DROP_TO_AI`
//! on `STATE_TIMEOUT`, and finalizes both finishes with canonical times.
//! The O5 lockstep relay (`relay.rs`) and TCP lobby are untouched.

use ssx3_proto::netplay::*;
use std::collections::HashMap;
use std::net::SocketAddr;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tokio::net::UdpSocket;
use tracing::info;

const HELLO_MAX: usize = 64;
const DATAGRAM_MAX: usize = 2048;
const ZERO16: [u8; 16] = [0u8; 16];

fn ct_eq(a: &[u8], b: &[u8]) -> bool {
    if a.len() != b.len() {
        return false;
    }
    let mut diff = 0u8;
    for (x, y) in a.iter().zip(b) {
        diff |= x ^ y;
    }
    diff == 0
}

/// Test-only endpoint configuration (loopback; fixed tokens).
#[derive(Debug, Clone)]
pub struct Sscfg {
    pub port: u16,
    pub token_a: Vec<u8>,
    pub token_b: Vec<u8>,
    pub delay_ms: f64,
    pub jitter_ms: f64,
    pub timeout_ms: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum Action {
    Forward(Vec<u8>),
    DropAi { slot: u16 },
}

/// Pure per-room tracker: no sockets, no clock (caller passes `now`).
/// Unit-tested below; the socket task is a thin driver around it.
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

    /// Timeout sweep. Emits one DROP_TO_AI per newly dead slot.
    pub fn sweep(&mut self, live: &[u16], now: Instant) -> Vec<Action> {
        let mut out = Vec::new();
        for &slot in live {
            if self.dropped.contains(&slot) {
                continue;
            }
            let dead = match self.last_recv.get(&slot) {
                None => true,
                Some(t) => now.duration_since(*t) > self.timeout,
            };
            if dead && self.last.contains_key(&slot) {
                self.dropped.push(slot);
                out.push(Action::DropAi { slot });
            }
        }
        out
    }

    /// Canonical results once every known racing slot finished or dropped.
    /// Canonical time = owner report; peer-view sanity (<=1000 ticks) is
    /// checked by the caller via `peer_ok` (the relayed copy's tick delta).
    pub fn finalize(
        &mut self,
        slots: &[u16],
        peer_ok: &HashMap<u16, bool>,
    ) -> Option<(Finalize, NetResult)> {
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
                let ok = peer_ok.get(&s).copied().unwrap_or(true);
                let _ = ok;
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
        let h = fnv1a(&digest_in);
        db[0..8].copy_from_slice(&h.to_le_bytes());
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

struct Peer {
    addr: SocketAddr,
    slot: u16,
}

/// Socket driver: enroll by token, route STATE, serve PING/QUIT/CLOSE.
pub async fn run(cfg: Sscfg, mut stop: tokio::sync::watch::Receiver<bool>) {
    let sock = match UdpSocket::bind(("127.0.0.1", cfg.port)).await {
        Ok(s) => Arc::new(s),
        Err(e) => {
            info!(port = cfg.port, err = %e, "state_sync bind failed");
            return;
        }
    };
    info!(port = cfg.port, delay_ms = cfg.delay_ms, jitter_ms = cfg.jitter_ms, "state_sync up");
    let mut peers: HashMap<SocketAddr, Peer> = HashMap::new();
    let mut by_slot: HashMap<u16, SocketAddr> = HashMap::new();
    let tracker = Arc::new(Mutex::new(Tracker::new(cfg.timeout_ms)));
    let mut seq: u64 = 0;
    let mut buf = [0u8; DATAGRAM_MAX];
    let mut stat_t = tokio::time::Instant::now();
    let mut fwd = 0u64;
    let mut drop = 0u64;
    loop {
        tokio::select! {
            _ = stop.changed() => break,
            r = sock.recv_from(&mut buf) => {
                let (n, src) = match r { Ok(x) => x, Err(_) => continue };
                let data = &buf[..n];
                if n <= HELLO_MAX && (ct_eq(data, &cfg.token_a) || ct_eq(data, &cfg.token_b)) {
                    let slot = if ct_eq(data, &cfg.token_a) { 0 } else { 1 };
                    peers.insert(src, Peer { addr: src, slot });
                    by_slot.insert(slot, src);
                    info!(port = cfg.port, slot, "state_sync enrolled");
                    continue;
                }
                let peer = match peers.get(&src) { Some(p) => p, None => { drop += 1; continue } };
                let frame = match decode_frame(data) { Ok(f) => f, Err(_) => { drop += 1; continue } };
                match frame.kind {
                    KIND_STATE => {
                        let st = match StateUpdate::decode(frame.payload) { Ok(s) => s, Err(_) => { drop += 1; continue } };
                        if st.slot != peer.slot { drop += 1; continue; }
                        let now = Instant::now();
                        let acts = tracker.lock().unwrap().on_state(st.slot, st, now, data.to_vec());
                        for a in acts {
                            match a {
                                Action::Forward(raw) => {
                                    if let Some(&dst) = by_slot.get(&(1 - peer.slot)) {
                                        if peers.contains_key(&dst) {
                                            let d = cfg.delay_ms / 1000.0
                                                + rand::random::<f64>() * cfg.jitter_ms / 1000.0;
                                            let s2 = sock.clone();
                                            tokio::spawn(async move {
                                                if d > 0.0 { tokio::time::sleep(Duration::from_secs_f64(d)).await; }
                                                let _ = s2.send_to(&raw, dst).await;
                                            });
                                            fwd += 1;
                                        }
                                    }
                                }
                                _ => {}
                            }
                        }
                    }
                    KIND_PING => {
                        seq += 1;
                        let pong = encode_frame(&ZERO16, 0, KIND_PONG, SENDER_RELAY, seq, frame.payload);
                        let _ = sock.send_to(&pong, src).await;
                    }
                    KIND_QUIT => {
                        let mut tr = tracker.lock().unwrap();
                        if !tr.dropped.contains(&peer.slot) {
                            tr.dropped.push(peer.slot);
                            let ev = Event { event_seq: 1, event_type: EVENT_DROP_TO_AI,
                                drop: DropToAi { slot: peer.slot, reason: REASON_QUIT, evidence_boundary: 0 } };
                            seq += 1;
                            let msg = encode_frame(&ZERO16, 0, KIND_EVENT, SENDER_RELAY, seq, &ev.encode());
                            if let Some(&dst) = by_slot.get(&(1 - peer.slot)) {
                                let _ = sock.send_to(&msg, dst).await;
                            }
                        }
                        peers.remove(&src);
                    }
                    KIND_CLOSE => break,
                    _ => { drop += 1; }
                }
                // timeout sweep + finalize attempt on every packet (cheap at 40/s)
                let now = Instant::now();
                let slots: Vec<u16> = peers.values().map(|p| p.slot).collect();
                let dead: Vec<u16> = {
                    let mut tr = tracker.lock().unwrap();
                    tr.sweep(&slots, now).iter().filter_map(|a| match a {
                        Action::DropAi { slot } => Some(*slot),
                        _ => None,
                    }).collect()
                };
                for slot in dead {
                    let ev = Event { event_seq: 1, event_type: EVENT_DROP_TO_AI,
                        drop: DropToAi { slot, reason: REASON_STATE_TIMEOUT, evidence_boundary: 0 } };
                    seq += 1;
                    let msg = encode_frame(&ZERO16, 0, KIND_EVENT, SENDER_RELAY, seq, &ev.encode());
                    if let Some(&dst) = by_slot.get(&(1 - slot)) {
                        let _ = sock.send_to(&msg, dst).await;
                    }
                    info!(port = cfg.port, slot, "STATE_TIMEOUT drop-ai");
                }
                // finish finalize
                let fin = {
                    let mut tr = tracker.lock().unwrap();
                    let all: Vec<u16> = vec![0, 1];
                    tr.finalize(&all, &HashMap::new())
                };
                if let Some((fin, res)) = fin {
                    seq += 1;
                    let fm = encode_frame(&ZERO16, 0, KIND_FINALIZE, SENDER_RELAY, seq, &fin.encode());
                    seq += 1;
                    let rm = encode_frame(&ZERO16, 0, KIND_RESULT, SENDER_RELAY, seq, &res.encode());
                    for p in peers.values() {
                        let _ = sock.send_to(&fm, p.addr).await;
                        let _ = sock.send_to(&rm, p.addr).await;
                    }
                    info!(port = cfg.port, boundary = fin.boundary, "FINALIZE+RESULT sent");
                }
                if tokio::time::Instant::now().duration_since(stat_t).as_secs() >= 30 {
                    stat_t = tokio::time::Instant::now();
                    info!(port = cfg.port, fwd, drop, "state_sync stats");
                }
            }
        }
    }
    info!(port = cfg.port, fwd, drop, "state_sync down");
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
    #[test]
    fn last_per_slot_wins() {
        let mut tr = Tracker::new(2000);
        let now = Instant::now();
        let a = tr.on_state(0, st(0, 100, 0, 0.0), now, vec![1]);
        assert_eq!(a.len(), 1);
        let b = tr.on_state(0, st(0, 200, 0, 0.0), now, vec![2]);
        assert_eq!(b.len(), 1);
        assert_eq!(tr.last[&0].tick, 200);
        assert_eq!(tr.forwarded, 2);
    }
    #[test]
    fn stale_ticks_drop() {
        let mut tr = Tracker::new(2000);
        let now = Instant::now();
        tr.on_state(1, st(1, 300, 0, 0.0), now, vec![1]);
        let same = tr.on_state(1, st(1, 300, 0, 0.0), now, vec![2]);
        let old = tr.on_state(1, st(1, 299, 0, 0.0), now, vec![3]);
        assert!(same.is_empty() && old.is_empty());
        assert_eq!(tr.stale_drops, 2);
        assert_eq!(tr.last[&1].tick, 300);
    }
    #[test]
    fn timeout_drops_exactly_once() {
        let mut tr = Tracker::new(100);
        let t0 = Instant::now();
        tr.on_state(0, st(0, 10, 0, 0.0), t0, vec![1]);
        tr.on_state(1, st(1, 10, 0, 0.0), t0, vec![1]);
        let late = t0 + Duration::from_millis(500);
        let a = tr.sweep(&[0, 1], late);
        assert_eq!(a.len(), 2);
        let b = tr.sweep(&[0, 1], late);
        assert!(b.is_empty());
    }
    #[test]
    fn finish_order_canonical() {
        let mut tr = Tracker::new(2000);
        let now = Instant::now();
        // slot 1 finishes first (faster), slot 0 second.
        tr.on_state(1, st(1, 30496, 1, 239.5), now, vec![1]);
        assert!(tr.finalize(&[0, 1], &HashMap::new()).is_none());
        tr.on_state(0, st(0, 33053, 1, 260.2), now, vec![1]);
        let (fin, res) = tr.finalize(&[0, 1], &HashMap::new()).unwrap();
        assert_eq!(fin.boundary, 33053);
        assert_eq!(res.results.len(), 2);
        assert_eq!(res.results[0].slot, 1);
        assert_eq!(res.results[0].rank, 1);
        assert_eq!(res.results[1].slot, 0);
        assert_eq!(res.results[1].rank, 2);
        // idempotent: second call yields nothing.
        assert!(tr.finalize(&[0, 1], &HashMap::new()).is_none());
    }
    #[test]
    fn quitter_is_dnf() {
        let mut tr = Tracker::new(2000);
        let now = Instant::now();
        tr.on_state(0, st(0, 30496, 1, 239.5), now, vec![1]);
        tr.dropped.push(1);
        let (_, res) = tr.finalize(&[0, 1], &HashMap::new()).unwrap();
        assert_eq!(res.results[0].slot, 0);
        assert_eq!(res.results[1].disposition, DISP_DNF);
        assert_eq!(res.results[1].rank, 0);
    }
}
