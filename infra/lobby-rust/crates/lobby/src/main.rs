// SPDX-License-Identifier: GPL-3.0-or-later OR MIT
//! ssx3-lobby: quick-play matchmaker plus optional UDP relay.
//!
//! Clean-room phase-1 server from `docs/research/ssx3-online-protocol.md`
//! (spec) and PRO1 fixtures. Scope: login, buddy subset, quick-play
//! pairing; hands each player the other's address (direct P2P), or the
//! server relay address when `relay.mode = "always"`. The client HLE
//! reads the assigned relay port and role token from `+ses`.
//!
//! Never point this at EA's, ssxor's or any public server, and never
//! send it real credentials: `auth` accepts anonymous names and ignores
//! the stock PASS field. A local per-install DID binds the buddy login.

mod health;
mod relay;
mod state_sync;
mod time_util;

use rand::RngCore;
use serde::Deserialize;
use ssx3_proto::{types, Message};
use std::collections::{HashMap, VecDeque};
use std::net::{IpAddr, SocketAddr};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::tcp::OwnedWriteHalf;
use tokio::net::{TcpListener, TcpStream};
use tracing::{info, warn};

/// Shared game-connection writer: the pairing task pushes +ses through
/// this while the connection task owns the read half.
type GameWriter = Arc<tokio::sync::Mutex<OwnedWriteHalf>>;

async fn gsend(
    wr: &GameWriter,
    typ: [u8; 4],
    sub: [u8; 4],
    pairs: &[(&str, &str)],
    strays: &[&str],
) -> std::io::Result<()> {
    let wire = ssx3_proto::encode(typ, sub, pairs, strays);
    gsend_wire(wr, &wire).await
}

async fn gsend_wire(wr: &GameWriter, wire: &[u8]) -> std::io::Result<()> {
    tokio::time::timeout(Duration::from_secs(5), async {
        wr.lock().await.write_all(wire).await
    })
    .await
    .map_err(|_| std::io::Error::new(std::io::ErrorKind::TimedOut, "write deadline"))?
}

// ---------------------------------------------------------------- config

#[derive(Debug, Deserialize, Clone)]
struct Config {
    #[serde(default)]
    lobby: LobbyCfg,
    #[serde(default)]
    ports: PortsCfg,
    #[serde(default = "default_rooms")]
    rooms: Vec<RoomCfg>,
    #[serde(default)]
    limits: LimitsCfg,
    #[serde(default)]
    auth: AuthCfg,
    #[serde(default)]
    relay: RelayCfg,
    #[serde(default)]
    session: SessionCfg,
    #[serde(default)]
    health: HealthCfg,
    #[serde(default)]
    state_sync: StateSyncCfg,
}

#[derive(Debug, Deserialize, Clone)]
struct LobbyCfg {
    #[serde(default = "default_bind")]
    bind: String,
    #[serde(default)]
    advertise_ip: String,
    #[serde(default = "default_motd")]
    motd: String,
}

#[derive(Debug, Deserialize, Clone)]
struct PortsCfg {
    #[serde(default = "port_lobby")]
    lobby: u16,
    #[serde(default = "port_game")]
    game: u16,
    #[serde(default = "port_buddy")]
    buddy: u16,
}

/// External HTTP health endpoint (UptimeRobot HEAD checks).
#[derive(Debug, Deserialize, Clone)]
struct HealthCfg {
    /// TCP port for HEAD/GET /healthz (0 = disabled).
    #[serde(default = "d_health_port")]
    port: u16,
}

const fn d_health_port() -> u16 {
    8080
}

impl Default for HealthCfg {
    fn default() -> Self {
        Self {
            port: d_health_port(),
        }
    }
}

#[derive(Debug, Deserialize, Clone)]
struct RoomCfg {
    name: String,
    peak: u32,
}

fn default_rooms() -> Vec<RoomCfg> {
    vec![
        RoomCfg {
            name: "Beginner".into(),
            peak: 1,
        },
        RoomCfg {
            name: "Advanced".into(),
            peak: 2,
        },
        RoomCfg {
            name: "Elite".into(),
            peak: 3,
        },
        RoomCfg {
            name: "Intermediate".into(),
            peak: 4,
        },
    ]
}

#[derive(Debug, Deserialize, Clone)]
struct LimitsCfg {
    #[serde(default = "d_max_conn")]
    max_connections: usize,
    #[serde(default = "d_max_per_ip")]
    max_per_ip: usize,
    #[serde(default = "d_msg_per_sec")]
    msg_per_sec_per_ip: u32,
    #[serde(default = "d_max_sessions")]
    max_sessions: usize,
    #[serde(default = "d_max_queue")]
    max_queue: usize,
    /// Active-pair admission ceiling: N-1 where N is the measured
    /// capacity (100 relay ports; CPU headroom far above that on the A1
    /// per the load curves). One port stays free as breathing room.
    #[serde(default = "d_max_pairs")]
    max_pairs: usize,
    /// Pairs touching one client IP (same-NAT households share an IP;
    /// default 4 leaves room for two local pairs plus churn).
    #[serde(default = "d_max_pairs_per_ip")]
    max_pairs_per_ip: usize,
    /// Queue waiters per client IP (storm fairness: one IP cannot fill
    /// the whole queue).
    #[serde(default = "d_max_queue_per_ip")]
    max_queue_per_ip: usize,
    /// Reserved sessions (names) per client IP (M2): a few dozen IPs
    /// cannot hold all 256 names. Counts reservations, pre-pers churn
    /// included.
    #[serde(default = "d_max_sessions_per_ip")]
    max_sessions_per_ip: usize,
    #[serde(default = "d_waiter_timeout")]
    waiter_timeout_secs: u64,
    #[serde(default = "d_idle_probe")]
    idle_probe_secs: u64,
    /// Idle keepalive for an unpaired queued searcher (online-lone-1):
    /// the stock client drops a search that sees no inbound game-socket
    /// bytes for ~3600 ticks (~60 s at 60 Hz), while the generic probe
    /// fires at 90 s wall. Probing waiters faster keeps a lone search
    /// alive to the stock give-up path. Paired sessions (off-queue) and
    /// pre-search browsers keep the generic probe.
    #[serde(default = "d_waiter_probe")]
    waiter_probe_secs: u64,
    #[serde(default = "d_idle_close")]
    idle_close_secs: u64,
    /// Buddy presence TCP (13505) idle lifetime for an authenticated
    /// session with no inbound bytes (lobby-buddy-1: was hardcoded
    /// 300 s in handle_buddy). Paired cover (below) still applies.
    #[serde(default = "d_buddy_close")]
    buddy_close_secs: u64,
    /// Grace after a pair's relay task ends during which the ex-pair's
    /// buddy and game links keep their paired cover (results screen
    /// and rematch browsing send nothing on either TCP).
    #[serde(default = "d_paired_grace")]
    paired_grace_secs: u64,
}

#[derive(Debug, Deserialize, Clone)]
struct AuthCfg {
    /// Anonymous, throwaway names only. PASS is accepted for stock wire
    /// compatibility and never stored or checked.
    #[serde(default = "d_auth_mode")]
    mode: String,
}

/// Quick Play state-sync endpoint (online-v0-1): 120<->120 matchmaking,
/// START barrier, STATE relay and canonical results on one UDP port.
/// Default off: the TCP lobby + O5 relay behave exactly as before.
#[derive(Debug, Deserialize, Clone)]
struct StateSyncCfg {
    /// Master switch for the endpoint.
    #[serde(default)]
    pub enable: bool,
    /// Bind address (loopback default; 0.0.0.0 in production).
    #[serde(default = "d_ss_bind")]
    pub bind: String,
    /// UDP port (production: 3657, a fixed dedicated port just below the
    /// relay UDP range so widening the range never moves it; the O5 relay
    /// range starts at 3658, the stock P2P convention).
    #[serde(default = "d_ss_port")]
    pub port: u16,
    /// Extra legacy STATE ports to keep listening on for the transition
    /// (old builds bake in 3757 via `ps2_netsync.h kDefaultPort`). Fed
    /// into the same hub, and excluded from the relay port pool.
    #[serde(default)]
    pub legacy_ports: Vec<u16>,
    /// Test-only injected one-way delay + uniform jitter, milliseconds.
    #[serde(default)]
    pub delay_ms: f64,
    #[serde(default)]
    pub jitter_ms: f64,
    /// No-STATE timeout per racing seat, milliseconds.
    #[serde(default = "d_ss_timeout")]
    pub timeout_ms: u64,
    /// Lead from pairing to the shared GO instant, milliseconds.
    #[serde(default = "d_ss_start_delay")]
    pub start_delay_ms: u64,
    #[serde(default = "d_ss_max_rooms")]
    pub max_rooms: usize,
    #[serde(default = "d_ss_max_waiters")]
    pub max_waiters: usize,
    #[serde(default = "d_ss_max_waiters_per_ip")]
    pub max_waiters_per_ip: usize,
    /// Active-room seats per source IP (netsec-fix-1 F4).
    #[serde(default = "d_ss_max_seats_per_ip")]
    pub max_seats_per_ip: usize,
    /// Per source IP packet bucket (a racing client sends ~22/s).
    #[serde(default = "d_ss_pps")]
    pub packets_per_sec: f64,
    #[serde(default = "d_ss_burst")]
    pub burst_packets: f64,
}

fn d_ss_bind() -> String {
    "127.0.0.1".to_string()
}

const fn d_ss_port() -> u16 {
    3657
}

const fn d_ss_timeout() -> u64 {
    2000
}

const fn d_ss_start_delay() -> u64 {
    2000
}

const fn d_ss_max_rooms() -> usize {
    256
}

const fn d_ss_max_waiters() -> usize {
    128
}

const fn d_ss_max_waiters_per_ip() -> usize {
    4
}

const fn d_ss_max_seats_per_ip() -> usize {
    8
}

const fn d_ss_pps() -> f64 {
    100.0
}

const fn d_ss_burst() -> f64 {
    200.0
}

impl Default for StateSyncCfg {
    fn default() -> Self {
        Self {
            enable: false,
            bind: d_ss_bind(),
            port: d_ss_port(),
            legacy_ports: Vec::new(),
            delay_ms: 0.0,
            jitter_ms: 0.0,
            timeout_ms: d_ss_timeout(),
            start_delay_ms: d_ss_start_delay(),
            max_rooms: d_ss_max_rooms(),
            max_waiters: d_ss_max_waiters(),
            max_waiters_per_ip: d_ss_max_waiters_per_ip(),
            max_seats_per_ip: d_ss_max_seats_per_ip(),
            packets_per_sec: d_ss_pps(),
            burst_packets: d_ss_burst(),
        }
    }
}

#[derive(Debug, Deserialize, Clone)]
struct RelayCfg {
    /// "always" (default) | "off" (opt-in direct P2P) | "fallback"
    /// (direct +ses plus a "would relay" log per pair;
    /// NAT-failure detection is a stub).
    #[serde(default = "d_relay_mode")]
    mode: String,
    #[serde(default = "d_relay_start")]
    port_start: u16,
    #[serde(default = "d_relay_end")]
    port_end: u16,
    #[serde(default = "d_relay_idle")]
    idle_timeout_secs: u64,
    /// Per-pair packet token bucket (sustained rate + burst). A stock-60
    /// race needs ~40 pps per pair; the default allows ~7x headroom and
    /// the burst absorbs the opening handshake.
    #[serde(default = "d_relay_pps")]
    packets_per_sec: f64,
    #[serde(default = "d_relay_burst")]
    burst_packets: f64,
    /// Per-role (per direction) packet bucket inside a pair.
    #[serde(default = "d_relay_role_pps")]
    per_role_per_sec: f64,
    #[serde(default = "d_relay_role_burst")]
    per_role_burst: f64,
    /// Per-role handshake bucket (H1): hello/proof attempts pay here so
    /// garbage floods cannot starve enrollment. One enrollment costs 2.
    #[serde(default = "d_handshake_pps")]
    handshake_per_sec: f64,
    #[serde(default = "d_handshake_burst")]
    handshake_burst: f64,
    /// Per-pair byte bucket (UDP payload + 28 B IP/UDP overhead per
    /// forwarded datagram). 8192 B/s is ~5x a racing pair's ~1.5 KB/s.
    #[serde(default = "d_relay_bps")]
    bytes_per_sec_per_pair: f64,
    #[serde(default = "d_relay_burst_bytes")]
    burst_bytes_per_pair: f64,
    /// Global egress ceiling (all pairs): sized at or above a full
    /// house at per-pair maxima (M1), so abuse is bounded per pair and
    /// the monthly cap is the real backstop. Still far under the
    /// Always Free 10 TB/mo allowance.
    #[serde(default = "d_global_bps")]
    global_bytes_per_sec: f64,
    #[serde(default = "d_global_burst")]
    global_burst_bytes: f64,
    /// Cumulative forwarded bytes (since boot) past which new pairs stop
    /// admitting (waiters stay queued; a warn event is logged). 0 =
    /// accounting only, no admission gate. Reset is a unit restart; the
    /// alerts lane owns the monthly cadence.
    #[serde(default)]
    monthly_egress_cap_bytes: u64,
}

#[derive(Debug, Deserialize, Clone)]
struct SessionCfg {
    /// Append SESSTOK=<hex> to +ses (prepared, optional, default off).
    /// Our HLE ignores unknown +ses keys, so stock-60 compat holds.
    #[serde(default)]
    token: bool,
    /// +ses P1 track id / P2 mode (HOST picks vary on the wire; the
    /// P1 course map is spec §8 Q2 — configurable, default 2).
    #[serde(default = "d_track")]
    quick_track: u32,
    /// online-stock-2 (Brad 10-17): each pairing races a random course
    /// from this list (both players get the same +ses P1). P1 is the SSX 3
    /// event id (the guest stores it at 0x535c08; 14-16 are backcountry).
    /// Default: every SSX 3 race course; `[]` falls back to quick_track.
    #[serde(default = "d_tracks")]
    quick_tracks: Vec<u32>,
    #[serde(default)]
    quick_mode: u32,
    /// Fixed server SKEY hex (default: minted once at startup).
    #[serde(default)]
    server_key: String,
}

fn default_bind() -> String {
    "0.0.0.0".into()
}
fn default_motd() -> String {
    "WIP Server".into()
}
const fn port_lobby() -> u16 {
    11000
}
const fn port_game() -> u16 {
    10901
}
const fn port_buddy() -> u16 {
    13505
}
const fn d_max_conn() -> usize {
    512
}
const fn d_max_per_ip() -> usize {
    16
}
const fn d_msg_per_sec() -> u32 {
    60
}
const fn d_max_sessions() -> usize {
    256
}
const fn d_max_queue() -> usize {
    128
}
const fn d_max_pairs() -> usize {
    99
}
const fn d_max_pairs_per_ip() -> usize {
    4
}
const fn d_max_queue_per_ip() -> usize {
    8
}
const fn d_max_sessions_per_ip() -> usize {
    6
}
const fn d_waiter_timeout() -> u64 {
    600
}
const fn d_idle_probe() -> u64 {
    90
}
const fn d_waiter_probe() -> u64 {
    30
}
const fn d_idle_close() -> u64 {
    240
}
const fn d_buddy_close() -> u64 {
    300
}
const fn d_paired_grace() -> u64 {
    300
}
fn d_auth_mode() -> String {
    "open".into()
}
fn d_relay_mode() -> String {
    // Brad 10-13: relay is the DEFAULT (odd configs, same-LAN play,
    // dorm isolation, CGNAT/firewalls, hides player IPs). "off"
    // (direct P2P) stays as an opt-in.
    "always".into()
}
const fn d_relay_start() -> u16 {
    3658
}
const fn d_relay_end() -> u16 {
    3757
}
const fn d_relay_idle() -> u64 {
    120
}
const fn d_relay_pps() -> f64 {
    300.0
}
const fn d_relay_burst() -> f64 {
    600.0
}
const fn d_relay_role_pps() -> f64 {
    150.0
}
const fn d_relay_role_burst() -> f64 {
    300.0
}
const fn d_handshake_pps() -> f64 {
    5.0
}
const fn d_handshake_burst() -> f64 {
    10.0
}
const fn d_relay_bps() -> f64 {
    8192.0
}
const fn d_relay_burst_bytes() -> f64 {
    32768.0
}
/// Full house at per-pair maxima: 99 x 8192 B/s = 811008 (M1), rounded
/// up. About 2.1 TB per month worst case, inside the 8 TiB monthly cap.
const fn d_global_bps() -> f64 {
    812000.0
}
const fn d_global_burst() -> f64 {
    1048576.0
}
/// 8 TiB: headroom under the Always Free 10 TB/mo outbound allowance
/// (list-price egress applies only above that, so egress is not the
/// binding cost — the free A1 shape is).
const fn d_monthly_cap() -> u64 {
    8796093022208
}
const fn d_track() -> u32 {
    2
}

/// SSX 3 race events: Snow Jam, Metro-City, Ruthless Ridge, Intimidator,
/// Gravitude, Happiness, Ruthless, The Throne.
fn d_tracks() -> Vec<u32> {
    vec![0, 1, 2, 3, 4, 14, 15, 16]
}

/// One pairing's +ses P1: a uniform pick from `tracks`, else `fixed`.
fn pick_track(tracks: &[u32], fixed: u32, roll: u32) -> u32 {
    if tracks.is_empty() {
        fixed
    } else {
        tracks[roll as usize % tracks.len()]
    }
}

impl Default for LobbyCfg {
    fn default() -> Self {
        Self {
            bind: default_bind(),
            advertise_ip: String::new(),
            motd: default_motd(),
        }
    }
}
impl Default for PortsCfg {
    fn default() -> Self {
        Self {
            lobby: port_lobby(),
            game: port_game(),
            buddy: port_buddy(),
        }
    }
}
impl Default for LimitsCfg {
    fn default() -> Self {
        Self {
            max_connections: d_max_conn(),
            max_per_ip: d_max_per_ip(),
            msg_per_sec_per_ip: d_msg_per_sec(),
            max_sessions: d_max_sessions(),
            max_queue: d_max_queue(),
            max_pairs: d_max_pairs(),
            max_pairs_per_ip: d_max_pairs_per_ip(),
            max_queue_per_ip: d_max_queue_per_ip(),
            max_sessions_per_ip: d_max_sessions_per_ip(),
            waiter_timeout_secs: d_waiter_timeout(),
            idle_probe_secs: d_idle_probe(),
            waiter_probe_secs: d_waiter_probe(),
            idle_close_secs: d_idle_close(),
            buddy_close_secs: d_buddy_close(),
            paired_grace_secs: d_paired_grace(),
        }
    }
}
impl Default for AuthCfg {
    fn default() -> Self {
        Self {
            mode: d_auth_mode(),
        }
    }
}
impl Default for RelayCfg {
    fn default() -> Self {
        Self {
            mode: d_relay_mode(),
            port_start: d_relay_start(),
            port_end: d_relay_end(),
            idle_timeout_secs: d_relay_idle(),
            packets_per_sec: d_relay_pps(),
            burst_packets: d_relay_burst(),
            per_role_per_sec: d_relay_role_pps(),
            per_role_burst: d_relay_role_burst(),
            handshake_per_sec: d_handshake_pps(),
            handshake_burst: d_handshake_burst(),
            bytes_per_sec_per_pair: d_relay_bps(),
            burst_bytes_per_pair: d_relay_burst_bytes(),
            global_bytes_per_sec: d_global_bps(),
            global_burst_bytes: d_global_burst(),
            monthly_egress_cap_bytes: d_monthly_cap(),
        }
    }
}
impl Default for SessionCfg {
    fn default() -> Self {
        Self {
            token: false,
            quick_track: d_track(),
            quick_tracks: d_tracks(),
            quick_mode: 0,
            server_key: String::new(),
        }
    }
}

impl Default for Config {
    fn default() -> Self {
        Self {
            lobby: LobbyCfg::default(),
            ports: PortsCfg::default(),
            rooms: default_rooms(),
            limits: LimitsCfg::default(),
            auth: AuthCfg::default(),
            relay: RelayCfg::default(),
            session: SessionCfg::default(),
            health: HealthCfg::default(),
            state_sync: StateSyncCfg::default(),
        }
    }
}

// ---------------------------------------------------------------- state

#[allow(dead_code)] // session record: NAT input + login stamp kept for ops
struct GameSession {
    requested: String,
    persona: String,
    device_id: String,
    peer: SocketAddr,
    reported_addr: String,
    login_stamp: String,
    waiter_since: Option<Instant>,
    session: Option<String>,
    last_active: Instant,
    writer: GameWriter,
    owner: u64,
}

#[allow(dead_code)] // presence record kept for ops
struct BuddySession {
    user: String,
    show: String,
    last_active: Instant,
    owner: u64,
}

#[derive(Clone)]
#[allow(dead_code)] // pair record: relay port kept for ops/receipts
struct Pair {
    name: String,
    a: String, // persona
    b: String,
    host: String,
    seed: u32,
    when: String,
    token: Option<String>,
    relay_tokens: Option<[String; 2]>,
    relay_port: Option<u16>,
    /// Member IPs at formation (H2): per-IP admission counts pairs from
    /// these, not from live sessions, so drop-and-repair cycling cannot
    /// evade the per-IP ceiling.
    a_ip: IpAddr,
    b_ip: IpAddr,
}

struct State {
    cfg: Config,
    server_key: String,
    advertise_ip: String,
    names: Mutex<HashMap<String, (String, u64, IpAddr)>>, // assigned name -> (device ID, owner, IP)
    games: Mutex<HashMap<String, GameSession>>,           // persona -> session
    buddies: Mutex<HashMap<String, BuddySession>>,        // user -> session
    queue: Mutex<VecDeque<String>>,                       // waiter personas, oldest first
    pairs: Mutex<HashMap<String, Pair>>,                  // session name -> pair
    /// Persona -> wall time its pair's relay task ended (lobby-buddy-1).
    /// Grants post-pair grace cover; pruned on every access.
    pair_ended: Mutex<HashMap<String, Instant>>,
    conns_per_ip: Mutex<HashMap<IpAddr, usize>>,
    pending_addrs: Mutex<HashMap<SocketAddr, (String, String)>>,
    /// Actual bound game/buddy ports (may differ from config when ephemeral).
    game_port: Mutex<u16>,
    buddy_port: Mutex<u16>,
    total_conns: Mutex<usize>,
    msg_times: Mutex<HashMap<IpAddr, VecDeque<Instant>>>,
    accept_times: Mutex<HashMap<IpAddr, VecDeque<Instant>>>,
    relay_ports: Mutex<Vec<u16>>,        // free relay ports (stack)
    wire_budget: tokio::sync::Semaphore, // outstanding decode bytes
    /// Cumulative forwarded egress bytes since boot (payload + 28 B
    /// IP/UDP overhead per datagram). Feeds the monthly cap gate.
    egress_bytes_total: AtomicU64,
    /// Global egress token bucket shared by all relay tasks.
    egress_bucket: Mutex<relay::TokenBucket>,
}

fn new_guid() -> String {
    let b: [u8; 16] = rand::random();
    format!(
        "{:02x}{:02x}{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}",
        b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7],
        b[8], b[9], b[10], b[11], b[12], b[13], b[14], b[15]
    )
}

fn new_hex(n: usize) -> String {
    let mut bytes = vec![0u8; n.div_ceil(2)];
    rand::thread_rng().fill_bytes(&mut bytes);
    let mut result = hex::encode(bytes);
    result.truncate(n);
    result
}

impl State {
    /// Live TCP connections across the lobby/game/buddy listeners
    /// (Part 7 `/statusz` input).
    fn tcp_sessions(&self) -> usize {
        *self.total_conns.lock().unwrap()
    }

    fn reserve_name(
        &self,
        requested: &str,
        device_id: &str,
        owner: u64,
        ip: IpAddr,
    ) -> Option<String> {
        let mut names = self.names.lock().unwrap();
        if names.len() >= self.cfg.limits.max_sessions {
            return None;
        }
        // A device owns at most one live login, including the interval
        // between auth and pers. This makes requested-name lookup for
        // buddy AUTH unambiguous under a same-device reconnect race.
        if names.values().any(|(id, _, _)| id == device_id) {
            return None;
        }
        // Sessions per IP (M2): a few dozen IPs must not hold all 256
        // names. Counts reservations, so pre-pers churn counts too.
        if names.values().filter(|(_, _, addr)| *addr == ip).count()
            >= self.cfg.limits.max_sessions_per_ip
        {
            return None;
        }
        for suffix in 1..=self.cfg.limits.max_sessions + 1 {
            let tail = if suffix == 1 {
                String::new()
            } else {
                suffix.to_string()
            };
            let stem_len = requested.len().min(64 - tail.len());
            let candidate = format!("{}{}", &requested[..stem_len], tail);
            if !names.contains_key(&candidate) {
                names.insert(candidate.clone(), (device_id.to_string(), owner, ip));
                return Some(candidate);
            }
        }
        None
    }

    fn check_rate(&self, ip: IpAddr) -> bool {
        let window = Duration::from_secs(1);
        let mut map = self.msg_times.lock().unwrap();
        let now = Instant::now();
        if !map.contains_key(&ip) && map.len() >= 2048 {
            map.retain(|_, q| {
                q.back()
                    .map(|t| now.duration_since(*t) <= window)
                    .unwrap_or(false)
            });
            if map.len() >= 2048 {
                evict_oldest(&mut map);
            }
        }
        let q = map.entry(ip).or_default();
        while q
            .front()
            .map(|t| now.duration_since(*t) > window)
            .unwrap_or(false)
        {
            q.pop_front();
        }
        if q.len() as u32 >= self.cfg.limits.msg_per_sec_per_ip {
            return false;
        }
        q.push_back(now);
        true
    }

    fn conn_open(&self, ip: IpAddr) -> bool {
        // Churn limit applies equally to lobby, game and buddy accepts.
        let mut accepts = self.accept_times.lock().unwrap();
        let now = Instant::now();
        let window = Duration::from_secs(60);
        if !accepts.contains_key(&ip) && accepts.len() >= 2048 {
            accepts.retain(|_, q| {
                q.back()
                    .map(|t| now.duration_since(*t) <= window)
                    .unwrap_or(false)
            });
            if accepts.len() >= 2048 {
                evict_oldest(&mut accepts);
            }
        }
        let q = accepts.entry(ip).or_default();
        while q
            .front()
            .map(|t| now.duration_since(*t) > window)
            .unwrap_or(false)
        {
            q.pop_front();
        }
        if q.len() >= 120 {
            return false;
        }
        q.push_back(now);
        drop(accepts);
        let mut total = self.total_conns.lock().unwrap();
        let mut per_ip = self.conns_per_ip.lock().unwrap();
        let n = per_ip.get(&ip).copied().unwrap_or(0);
        if *total >= self.cfg.limits.max_connections || n >= self.cfg.limits.max_per_ip {
            return false;
        }
        *total += 1;
        per_ip.insert(ip, n + 1);
        true
    }

    fn conn_close(&self, ip: IpAddr) {
        let mut total = self.total_conns.lock().unwrap();
        let mut per_ip = self.conns_per_ip.lock().unwrap();
        *total = total.saturating_sub(1);
        if let Some(n) = per_ip.get(&ip).copied() {
            if n <= 1 {
                per_ip.remove(&ip);
            } else {
                per_ip.insert(ip, n - 1);
            }
        }
    }
}

struct GameCleanup {
    state: Arc<State>,
    peer: SocketAddr,
    persona: Option<String>,
    reserved: Option<String>,
    owner: u64,
}

impl Drop for GameCleanup {
    fn drop(&mut self) {
        self.state.pending_addrs.lock().unwrap().remove(&self.peer);
        if let Some(name) = &self.reserved {
            let mut names = self.state.names.lock().unwrap();
            if names.get(name).map(|x| x.1) == Some(self.owner) {
                names.remove(name);
            }
        }
        if let Some(p) = &self.persona {
            let mut queue = self.state.queue.lock().unwrap();
            let mut games = self.state.games.lock().unwrap();
            if games.get(p).map(|g| g.owner) == Some(self.owner) {
                queue.retain(|n| n != p);
                games.remove(p);
            }
        }
    }
}

struct BuddyCleanup {
    state: Arc<State>,
    user: Option<String>,
    owner: u64,
}

impl Drop for BuddyCleanup {
    fn drop(&mut self) {
        if let Some(u) = &self.user {
            let mut buddies = self.state.buddies.lock().unwrap();
            if buddies.get(u).map(|b| b.owner) == Some(self.owner) {
                buddies.remove(u);
            }
        }
    }
}

struct ConnectionPermit {
    state: Arc<State>,
    ip: IpAddr,
}

impl Drop for ConnectionPermit {
    fn drop(&mut self) {
        self.state.conn_close(self.ip);
    }
}

// ---------------------------------------------------------------- wire io

/// Read exactly one framed message (12-byte header + claimed body).
async fn read_msg<R: AsyncReadExt + Unpin>(
    stream: &mut R,
    buf: &mut Vec<u8>,
    state: &State,
    ip: IpAddr,
) -> Result<Message, ReadErr> {
    let mut rate_checked = false;
    let mut body_deadline = None;
    loop {
        if buf.len() >= 12 {
            let size = u32::from_be_bytes([buf[8], buf[9], buf[10], buf[11]]) as usize;
            if size < 12
                || size > ssx3_proto::MAX_MESSAGE_SIZE
                || size > ssx3_proto::type_limit(buf[..4].try_into().unwrap())
            {
                return Err(ReadErr::Framing);
            }
            if !rate_checked {
                if !state.check_rate(ip) {
                    return Err(ReadErr::Rate);
                }
                rate_checked = true;
                body_deadline = Some(Instant::now() + Duration::from_secs(10));
            }
            if buf.len() >= size {
                let _permit = state
                    .wire_budget
                    .acquire_many(size as u32)
                    .await
                    .map_err(|_| ReadErr::Io)?;
                let parsed = ssx3_proto::decode_one(&buf[..size])
                    .map_err(|_| ReadErr::Framing)?
                    .0;
                buf.drain(..size);
                return Ok(parsed);
            }
        }
        let mut tmp = [0u8; 4096];
        let read = stream.read(&mut tmp);
        let n = if let Some(deadline) = body_deadline {
            tokio::time::timeout_at(tokio::time::Instant::from_std(deadline), read)
                .await
                .map_err(|_| ReadErr::Io)?
                .map_err(|_| ReadErr::Io)?
        } else {
            read.await.map_err(|_| ReadErr::Io)?
        };
        if n == 0 {
            return Err(ReadErr::Closed);
        }
        if buf.len() + n > ssx3_proto::MAX_MESSAGE_SIZE {
            return Err(ReadErr::Framing);
        }
        buf.extend_from_slice(&tmp[..n]);
    }
}

#[derive(Debug)]
enum ReadErr {
    Closed,
    Io,
    Framing,
    Rate,
}

async fn send_msg<W: AsyncWriteExt + Unpin>(
    stream: &mut W,
    typ: [u8; 4],
    sub: [u8; 4],
    pairs: &[(&str, &str)],
    strays: &[&str],
) -> std::io::Result<()> {
    let wire = ssx3_proto::encode(typ, sub, pairs, strays);
    tokio::time::timeout(Duration::from_secs(5), stream.write_all(&wire))
        .await
        .map_err(|_| std::io::Error::new(std::io::ErrorKind::TimedOut, "write deadline"))?
}

fn typ_str(m: &Message) -> String {
    m.type_str().unwrap_or("?").to_string()
}

// ---------------------------------------------------------------- handlers

async fn handle_lobby(state: Arc<State>, mut stream: TcpStream, peer: SocketAddr) {
    let mut buf = Vec::new();
    let msg = match tokio::time::timeout(
        Duration::from_secs(10),
        read_msg(&mut stream, &mut buf, &state, peer.ip()),
    )
    .await
    {
        Ok(Ok(m)) => m,
        _ => return,
    };
    if msg.type_str() != Some("@dir") {
        return;
    }
    info!("lobby directory request");
    let sess: u64 = rand::random();
    let mask = new_hex(32);
    let port = state.game_port.lock().unwrap().to_string();
    let sess_s = sess.to_string();
    let _ = send_msg(
        &mut stream,
        types::DIR,
        types::SUB_ZERO,
        &[
            ("ADDR", state.advertise_ip.as_str()),
            ("PORT", port.as_str()),
            ("SESS", sess_s.as_str()),
            ("MASK", mask.as_str()),
        ],
        &[],
    )
    .await;
    // Reply hands the client ADDR/PORT/SESS/MASK, then this connection
    // is closed (the client opens 10901 and never uses 11000 again).
}

async fn handle_game(state: Arc<State>, stream: TcpStream, peer: SocketAddr) {
    // Split so the pairing task can push +ses the moment a pair forms
    // (the wire shows +ses with no further request, §3.11).
    let (mut rd, wr) = stream.into_split();
    let wr: GameWriter = Arc::new(tokio::sync::Mutex::new(wr));
    let mut buf = Vec::new();
    let mut persona: Option<String> = None;
    let mut authenticated: Option<String> = None;
    let mut requested_name: Option<String> = None;
    let mut device_id: Option<String> = None;
    let owner: u64 = rand::random();
    let mut cleanup = GameCleanup {
        state: state.clone(),
        peer,
        persona: None,
        reserved: None,
        owner,
    };
    let mut last_active = Instant::now();
    let idle_probe = Duration::from_secs(state.cfg.limits.idle_probe_secs);
    let waiter_probe = Duration::from_secs(state.cfg.limits.waiter_probe_secs);
    let idle_close = Duration::from_secs(state.cfg.limits.idle_close_secs);
    loop {
        // Unauthenticated connections get 10 s, not 240 s (M2): login is
        // a handful of round trips; anything slower is a slot holder.
        let wait_secs = if authenticated.is_some() { 30 } else { 10 };
        let msg = match tokio::time::timeout(
            Duration::from_secs(wait_secs),
            read_msg(&mut rd, &mut buf, &state, peer.ip()),
        )
        .await
        {
            Ok(Ok(m)) => m,
            Ok(Err(_)) => break,
            Err(_) => {
                if authenticated.is_none() {
                    break;
                }
                // A partial frame has an absolute body deadline. An
                // actually idle connection survives until configured close.
                if !buf.is_empty() || last_active.elapsed() >= idle_close {
                    // Paired cover (lobby-buddy-1): racing clients send
                    // nothing here (race traffic is UDP), and stock
                    // clients only answer PNG from the lobby pump, which
                    // does not run mid-race — so idle_close would reap
                    // live races. Skip the reap while covered.
                    if persona
                        .as_deref()
                        .map(|p| state.pair_cover(p))
                        .unwrap_or(false)
                    {
                        continue;
                    }
                    break;
                }
                // online-lone-1: an unpaired queued searcher starves after
                // ~3600 silent game ticks, far inside the generic 90 s
                // probe. Probe waiters on the faster cadence so a lone
                // search reaches the stock give-up instead of the
                // disconnect dialog. Paired sessions leave the queue at
                // pair time, so racing pairs keep the generic cadence.
                // Single lock only (queue), no ordering issue.
                let probe = if persona
                    .as_deref()
                    .map(|p| state.queue.lock().unwrap().iter().any(|n| n.as_str() == p))
                    .unwrap_or(false)
                {
                    waiter_probe
                } else {
                    idle_probe
                };
                if last_active.elapsed() >= probe
                    && gsend(&wr, types::PNG, types::SUB_ZERO, &[], &[])
                        .await
                        .is_err()
                {
                    break;
                }
                continue;
            }
        };
        last_active = Instant::now();
        let t = typ_str(&msg);
        // Per-message at debug: a TCP message flood (60/s/IP) must not
        // turn into journal amplification. Cap hits still warn.
        tracing::debug!("game message received");
        match t.as_str() {
            "addr" => {
                let a = msg.get("ADDR").unwrap_or("").to_string();
                let p = msg.get("PORT").unwrap_or("").to_string();
                let _ = gsend(
                    &wr,
                    types::ADDR,
                    types::SUB_ZERO,
                    &[("ADDR", &a), ("PORT", &p)],
                    &[],
                )
                .await;
                // Stash the reported LAN endpoint (NAT/LAN input) for
                // the later pers/pairing record.
                state.pending_addrs.lock().unwrap().insert(peer, (a, p));
            }
            "skey" => {
                let key = format!("${}", state.server_key);
                let _ = gsend(&wr, types::SKEY, types::SUB_ZERO, &[("SKEY", &key)], &[]).await;
            }
            "sele" => {
                let _ = gsend(
                    &wr,
                    types::SELE,
                    types::SUB_ZERO,
                    &[("MORE", "0"), ("SLOTS", "0")],
                    &[],
                )
                .await;
            }
            "auth" => {
                if authenticated.is_some() {
                    break;
                }
                let requested = msg.get("NAME").unwrap_or("").to_string();
                let did = msg.get("DID").unwrap_or("");
                if requested.is_empty()
                    || requested.len() > 64
                    || !requested
                        .bytes()
                        .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
                    || did.len() != 32
                    || !did.bytes().all(|b| b.is_ascii_hexdigit())
                {
                    break;
                }
                // PASS is a stock protocol field; anonymous service does
                // not verify, retain or log it. Reserve the display name
                // before any await so concurrent logins receive distinct
                // names. The device ID binds the later buddy connection.
                let Some(name) =
                    state.reserve_name(&requested, &did.to_ascii_lowercase(), owner, peer.ip())
                else {
                    break;
                };
                cleanup.reserved = Some(name.clone());
                authenticated = Some(name.clone());
                requested_name = Some(requested);
                device_id = Some(did.to_ascii_lowercase());
                let stamp = time_util::stamp();
                if gsend(
                    &wr,
                    types::AUTH,
                    types::SUB_ZERO,
                    &[
                        ("TOS", "1"),
                        ("MAIL", ""),
                        ("PERSONAS", &name),
                        ("BORN", "1990.01.01"),
                        ("GEND", "m"),
                        ("FROM", "US"),
                        ("LANG", "en"),
                        ("SPAM", "0"),
                        ("SINCE", &stamp),
                        ("CPAT", "1"),
                    ],
                    &[],
                )
                .await
                .is_err()
                {
                    break;
                }
                let mut rooms_ok = true;
                for (i, room) in state.cfg.rooms.iter().enumerate() {
                    let title = format!("{}.Peak {}", room.name, room.peak);
                    let idx = (i + 1).to_string();
                    if gsend(
                        &wr,
                        types::ROM,
                        types::SUB_ZERO,
                        &[
                            ("A", state.advertise_ip.as_str()),
                            ("I", &idx),
                            ("N", &title),
                            ("H", "Mcomm"),
                            ("F", "CK"),
                            ("T", "01"),
                            ("L", "32"),
                            ("P", "10"),
                        ],
                        &[],
                    )
                    .await
                    .is_err()
                    {
                        rooms_ok = false;
                        break;
                    }
                }
                if !rooms_ok {
                    break;
                }
            }
            "pers" => {
                let selected = msg.get("PERS").unwrap_or("");
                let Some(assigned) = authenticated.as_deref() else {
                    break;
                };
                if persona.is_some()
                    || (selected != assigned && requested_name.as_deref() != Some(selected))
                {
                    break;
                }
                let p = assigned.to_string();
                let stamp = time_util::stamp();
                let lkey = new_hex(32);
                let reported = state
                    .pending_addrs
                    .lock()
                    .unwrap()
                    .remove(&peer)
                    .unwrap_or_default();
                let reported_addr = if reported.0.is_empty() {
                    peer.ip().to_string()
                } else {
                    reported.0
                };
                {
                    let mut games = state.games.lock().unwrap();
                    if games.contains_key(&p) || games.len() >= state.cfg.limits.max_sessions {
                        break;
                    }
                    games.insert(
                        p.clone(),
                        GameSession {
                            requested: requested_name.clone().unwrap(),
                            persona: p.clone(),
                            device_id: device_id.clone().unwrap(),
                            peer,
                            reported_addr,
                            login_stamp: stamp.clone(),
                            waiter_since: None,
                            session: None,
                            last_active: Instant::now(),
                            writer: wr.clone(),
                            owner,
                        },
                    );
                }
                persona = Some(p.clone());
                cleanup.persona = Some(p.clone());
                if gsend(
                    &wr,
                    types::PERS,
                    types::SUB_ZERO,
                    &[
                        ("A", state.advertise_ip.as_str()),
                        ("LA", state.advertise_ip.as_str()),
                        ("LOC", "enUS"),
                        ("MA", state.advertise_ip.as_str()),
                        ("NAME", &p),
                        ("PERS", &p),
                        ("LAST", &stamp),
                        ("PLAST", &stamp),
                        ("SINCE", &stamp),
                        ("LKEY", &lkey),
                    ],
                    &[],
                )
                .await
                .is_err()
                {
                    break;
                }
            }
            "onln" => {
                let selected = msg.get("PERS").unwrap_or("");
                let Some(p) = persona.as_deref() else {
                    break;
                };
                if selected != p && requested_name.as_deref() != Some(selected) {
                    break;
                }
                if gsend(&wr, types::ONLN, types::SUB_ZERO, &[("PERS", p)], &[])
                    .await
                    .is_err()
                {
                    break;
                }
            }
            "news" => {
                let buddy_port = state.buddy_port.lock().unwrap().to_string();
                let _ = gsend(
                    &wr,
                    types::NEWS,
                    types::SUB_NEW0,
                    &[
                        ("BUDDYSERVERNAME", state.advertise_ip.as_str()),
                        ("BUDDYPORT", &buddy_port),
                        ("BUDDYRESOURCE", "SSX-PS2-2004"),
                        ("BUDDYUSERNAME", persona.as_deref().unwrap_or("")),
                        ("BUDDYMSGTIMEOUT", "864000"),
                    ],
                    &[state.cfg.lobby.motd.as_str()],
                )
                .await;
            }
            "quik" => {
                let kind = msg.get("KIND").unwrap_or("").to_string();
                if persona.is_none() {
                    break;
                }
                let me = match &persona {
                    Some(p) => p.clone(),
                    None => {
                        warn!("quik before pers; ignoring queue");
                        continue;
                    }
                };
                // A paired client leaves QM on +ses; a stray late quik
                // gets echo-only (never re-queued).
                let paired = state
                    .games
                    .lock()
                    .unwrap()
                    .get(&me)
                    .map(|g| g.session.is_some())
                    .unwrap_or(true);
                // Whether this quik cannot lead to a prompt pair. The
                // echo below carries FULL=1 then (online-default-1 shows
                // the server-full message and backs off polling). The
                // echo still precedes any +ses try_pair may push.
                let mut full = false;
                let mut pair_after_echo = false;
                if !paired {
                    // Expire stale waiters (f2 back-out leaves no cancel).
                    {
                        let mut q = state.queue.lock().unwrap();
                        let mut games = state.games.lock().unwrap();
                        let timeout = Duration::from_secs(state.cfg.limits.waiter_timeout_secs);
                        q.retain(|name| {
                            games
                                .get(name)
                                .map(|g| {
                                    g.waiter_since
                                        .map(|t| t.elapsed() < timeout)
                                        .unwrap_or(true)
                                        && g.session.is_none()
                                })
                                .unwrap_or(false)
                        });
                        // Storm fairness: one IP cannot fill the queue.
                        let my_waiters = q
                            .iter()
                            .filter(|n| {
                                games
                                    .get(*n)
                                    .map(|g| g.peer.ip() == peer.ip())
                                    .unwrap_or(false)
                            })
                            .count();
                        let ip_capped = my_waiters >= state.cfg.limits.max_queue_per_ip;
                        if kind == "DeathRace" && !q.contains(&me) {
                            if q.len() >= state.cfg.limits.max_queue {
                                warn!("quik queue full; echo-only");
                            } else if ip_capped {
                                warn!("quik queue per-IP full; echo-only");
                            } else {
                                q.push_back(me.clone());
                                if let Some(g) = games.get_mut(&me) {
                                    g.waiter_since = Some(Instant::now());
                                }
                            }
                        } else if kind == "*" {
                            // online-stock-2: KIND=* is the client giving up
                            // ("Sorry, no users were found for a quickmatch",
                            // ~1200 ticks after its search; MComm de-queues
                            // it), not a poll. Pairing a client that gave up
                            // pushed a +ses it rejects (chal PERS=*) while
                            // the peer waited on a link that never came
                            // (q3, q4, Odin o4/o5).
                            q.retain(|n| n != &me);
                            if let Some(g) = games.get_mut(&me) {
                                g.waiter_since = None;
                            }
                        }
                        if kind == "*" {
                            // gave up: echo only, never FULL, never paired
                        } else if !q.contains(&me) {
                            full = true; // queue cap refused this quik
                        } else {
                            let pairs = state.pairs.lock().unwrap();
                            if pairs.len() >= state.cfg.limits.max_pairs
                                || state.egress_capped()
                                || count_pairs_on_ip(&pairs, peer.ip())
                                    >= state.cfg.limits.max_pairs_per_ip
                            {
                                full = true;
                            }
                        }
                    }
                    pair_after_echo = kind != "*";
                }
                let mut echo: Vec<(String, String)> = msg
                    .pairs
                    .iter()
                    .map(|(k, v)| (k.clone(), v.clone()))
                    .collect();
                if full {
                    echo.push(("FULL".to_string(), "1".to_string()));
                }
                let refs: Vec<(&str, &str)> =
                    echo.iter().map(|(k, v)| (k.as_str(), v.as_str())).collect();
                let wire = ssx3_proto::encode(types::QUIK, types::SUB_ZERO, &refs, &[]);
                if gsend_wire(&wr, &wire).await.is_err() {
                    break;
                }
                if pair_after_echo {
                    try_pair(&state).await;
                }
            }
            "rank" => {
                let _ = gsend(&wr, types::RANK, types::SUB_ZERO, &[], &[]).await;
            }
            "~png" => {
                let _ = gsend(&wr, types::PNG, types::SUB_ZERO, &[], &[]).await;
            }
            // online-stock-2: `chal PERS=*` (clear my challenge) is echoed
            // back, as MComm's ChalMessageIn does (PERS=* -> Broadcast).
            "chal" => {
                let pairs: Vec<(&str, &str)> =
                    msg.pairs.iter().map(|(k, v)| (k.as_str(), v.as_str())).collect();
                let _ = gsend(&wr, types::CHAL, types::SUB_ZERO, &pairs, &[]).await;
            }
            _ => {
                warn!("game unknown type; ignoring");
            }
        }
        if let Some(p) = &persona {
            if let Some(g) = state.games.lock().unwrap().get_mut(p) {
                if g.owner == owner {
                    g.last_active = Instant::now();
                }
            }
        }
    }
    // Disconnect cleanup: drop waiter entry and persona session.
    if let Some(p) = persona {
        let mut queue = state.queue.lock().unwrap();
        let mut games = state.games.lock().unwrap();
        if games.get(&p).map(|g| g.owner) == Some(owner) {
            queue.retain(|n| n != &p);
            games.remove(&p);
            info!("game disconnect cleanup");
        }
    }
    state.pending_addrs.lock().unwrap().remove(&peer);
}

async fn handle_buddy(state: Arc<State>, mut stream: TcpStream, peer: SocketAddr) {
    let mut buf = Vec::new();
    let mut user: Option<String> = None;
    let owner: u64 = rand::random();
    let mut cleanup = BuddyCleanup {
        state: state.clone(),
        user: None,
        owner,
    };
    loop {
        let deadline = if user.is_none() {
            10
        } else {
            state.cfg.limits.buddy_close_secs
        };
        let msg = match tokio::time::timeout(
            Duration::from_secs(deadline),
            read_msg(&mut stream, &mut buf, &state, peer.ip()),
        )
        .await
        {
            Ok(Ok(m)) => m,
            Ok(Err(_)) => break,
            Err(_) => {
                // Read timeout (not a protocol error). Paired cover
                // (lobby-buddy-1): racing clients send nothing on the
                // buddy link, so the close would fire mid-race and the
                // guest raises opponent-lost at results time.
                if user
                    .as_deref()
                    .map(|u| state.pair_cover(u))
                    .unwrap_or(false)
                {
                    continue;
                }
                break;
            }
        };
        let t = typ_str(&msg);
        // Per-message at debug (see game path: no journal amplification
        // under message floods).
        tracing::debug!("buddy message received");
        match t.as_str() {
            "DISC" => {
                // Empty; client drops any stale buddy session first.
                // Echo (consistent with every other keepalive).
                let _ = send_msg(&mut stream, types::DISC, types::SUB_ZERO, &[], &[]).await;
            }
            "AUTH" => {
                let u = msg.get("USER").unwrap_or("").to_string();
                let persona = u.split('/').next().unwrap_or("").to_string();
                if persona.is_empty() || user.is_some() {
                    break;
                }
                let did = msg.get("DID").unwrap_or("").to_ascii_lowercase();
                if did.len() != 32 || !did.bytes().all(|b| b.is_ascii_hexdigit()) {
                    break;
                }
                let canonical = {
                    let games = state.games.lock().unwrap();
                    games
                        .values()
                        .find(|g| {
                            g.device_id == did && (g.persona == persona || g.requested == persona)
                        })
                        .map(|g| g.persona.clone())
                };
                let Some(persona) = canonical else {
                    break;
                };
                // PASS is a stock field only. Never echo the host-only ID.
                let pairs: Vec<(&str, &str)> = msg
                    .pairs
                    .iter()
                    .filter(|(k, _)| k != "DID")
                    .map(|(k, v)| (k.as_str(), v.as_str()))
                    .collect();
                let wire = ssx3_proto::encode(types::BAUTH, types::SUB_ZERO, &pairs, &[]);
                if tokio::time::timeout(Duration::from_secs(5), stream.write_all(&wire))
                    .await
                    .is_err()
                {
                    break;
                }
                cleanup.user = Some(persona.clone());
                user = Some(persona);
            }
            "PSET" => {
                if user.is_none() {
                    break;
                }
                let show = msg.get("SHOW").unwrap_or("").to_string();
                if show == "DISC" {
                    // Presence announces the departure (f2 back-out).
                    if let Some(u) = &user {
                        let mut buddies = state.buddies.lock().unwrap();
                        if buddies.get(u).map(|b| b.owner) == Some(owner) {
                            buddies.remove(u);
                        }
                        info!("buddy presence departure");
                    }
                    let _ = send_msg(&mut stream, types::DISC, types::SUB_ZERO, &[], &[]).await;
                    continue;
                }
                // Echo with RSRC renamed PSRC (observed shape).
                let mut pairs: Vec<(&str, &str)> = Vec::new();
                for (k, v) in &msg.pairs {
                    let name = if k == "RSRC" { "PSRC" } else { k.as_str() };
                    pairs.push((name, v.as_str()));
                }
                let wire = ssx3_proto::encode(types::PSET, types::SUB_ZERO, &pairs, &[]);
                if tokio::time::timeout(Duration::from_secs(5), stream.write_all(&wire))
                    .await
                    .is_err()
                {
                    break;
                }
                if let Some(u) = &user {
                    let mut buddies = state.buddies.lock().unwrap();
                    if buddies.get(u).map(|b| b.owner != owner).unwrap_or(false) {
                        break;
                    }
                    buddies.insert(
                        u.clone(),
                        BuddySession {
                            user: u.clone(),
                            show,
                            last_active: Instant::now(),
                            owner,
                        },
                    );
                }
            }
            "RGET" => {
                let id = msg.get("ID").unwrap_or("").to_string();
                // Phase 1: empty rosters only (SIZE=0; nonzero reply
                // shape is spec §8 Q6, needs its own capture lane).
                let _ = send_msg(
                    &mut stream,
                    types::RGET,
                    types::SUB_ZERO,
                    &[("ID", &id), ("SIZE", "0")],
                    &[],
                )
                .await;
            }
            "PING" => {
                let _ = send_msg(&mut stream, types::PING, types::SUB_ZERO, &[], &[]).await;
            }
            _ => {
                warn!("buddy unknown type; ignoring");
            }
        }
        if let Some(u) = &user {
            if let Some(b) = state.buddies.lock().unwrap().get_mut(u) {
                if b.owner == owner {
                    b.last_active = Instant::now();
                }
            }
        }
    }
    if let Some(u) = user {
        let mut buddies = state.buddies.lock().unwrap();
        if buddies.get(&u).map(|b| b.owner) == Some(owner) {
            buddies.remove(&u);
        }
        info!("buddy disconnect cleanup");
    }
}

/// Pair the two oldest waiters, if any. HOST = first waiter
/// (deterministic; the wire leaves HOST selection open, spec §8 Q1).
/// Called after every quik; holds no lock across .await except via
/// the relay spawn (which takes its own state clone).
async fn try_pair(state: &Arc<State>) {
    // One lock order for queue and sessions everywhere. Reserve both
    // sessions and a *bound* UDP socket before exposing a pair to peers.
    let (pair, relay_socket, peers) = {
        let mut q = state.queue.lock().unwrap();
        let mut games = state.games.lock().unwrap();
        if q.len() < 2 {
            return;
        }
        // netsec-fix-1 F5: pick the first eligible pair behind a blocked head
        // instead of stalling the queue. Blocked waiters keep their places
        // and retry on the next quik; dead sessions are dropped.
        let views: Vec<(String, Option<IpAddr>)> = q
            .iter()
            .map(|name| {
                let ip = games
                    .get(name)
                    .filter(|g| g.session.is_none())
                    .map(|g| g.peer.ip());
                (name.clone(), ip)
            })
            .collect();
        let pair_ips: Vec<(IpAddr, IpAddr)> = state
            .pairs
            .lock()
            .unwrap()
            .values()
            .map(|p| (p.a_ip, p.b_ip))
            .collect();
        let (pick, dead) = pick_pair(
            &views,
            &pair_ips,
            state.cfg.limits.max_pairs,
            state.egress_capped(),
            state.cfg.limits.max_pairs_per_ip,
        );
        for name in &dead {
            if let Some(i) = q.iter().position(|n| n == name) {
                q.remove(i);
            }
        }
        let Some((a_name, b_name)) = pick else {
            warn!("no eligible pair; waiters remain queued");
            return;
        };
        // Remove the pick (one entry each), preserving everyone else's order.
        for name in [&a_name, &b_name] {
            if let Some(i) = q.iter().position(|n| n == name) {
                q.remove(i);
            }
        }
        let a_peer = games.get(&a_name).unwrap().peer;
        let b_peer = games.get(&b_name).unwrap().peer;
        let name = new_guid();
        let relay_mode = state.cfg.relay.mode.as_str();
        let (relay_port, relay_socket, relay_tokens) = if relay_mode == "always" {
            let Some(port) = state.relay_ports.lock().unwrap().pop() else {
                q.push_front(b_name);
                q.push_front(a_name);
                warn!("relay pool exhausted; pair remains queued");
                return;
            };
            let bind = format!("{}:{}", state.cfg.lobby.bind, port);
            let sock = match std::net::UdpSocket::bind(&bind) {
                Ok(sock) => sock,
                Err(_err) => {
                    state.relay_ports.lock().unwrap().push(port);
                    q.push_front(b_name);
                    q.push_front(a_name);
                    warn!(port, "relay bind failed; pair remains queued");
                    return;
                }
            };
            if sock.set_nonblocking(true).is_err() {
                state.relay_ports.lock().unwrap().push(port);
                q.push_front(b_name);
                q.push_front(a_name);
                return;
            }
            let sock = match tokio::net::UdpSocket::from_std(sock) {
                Ok(sock) => sock,
                Err(_err) => {
                    state.relay_ports.lock().unwrap().push(port);
                    q.push_front(b_name);
                    q.push_front(a_name);
                    warn!(port, "relay socket conversion failed; pair remains queued");
                    return;
                }
            };
            (Some(port), Some(sock), Some([new_hex(32), new_hex(32)]))
        } else {
            if relay_mode == "fallback" {
                info!("relay fallback uses direct route");
            }
            (None, None, None)
        };
        let pair = Pair {
            name: name.clone(),
            a: a_name.clone(),
            b: b_name.clone(),
            host: a_name.clone(),
            seed: rand::random(),
            when: time_util::stamp(),
            token: state.cfg.session.token.then(|| new_hex(32)),
            relay_tokens,
            relay_port,
            a_ip: a_peer.ip(),
            b_ip: b_peer.ip(),
        };
        for n in [&a_name, &b_name] {
            let g = games.get_mut(n).unwrap();
            g.session = Some(name.clone());
            g.waiter_since = None;
        }
        (pair, relay_socket, [a_peer, b_peer])
    };
    let name = pair.name.clone();
    let a_name = pair.a.clone();
    let b_name = pair.b.clone();
    let (a_addr, a_from, b_addr, b_from) = if pair.relay_port.is_some() {
        let s = state.advertise_ip.clone();
        (s.clone(), s.clone(), s.clone(), s)
    } else {
        (
            peers[1].ip().to_string(),
            peers[1].ip().to_string(),
            peers[0].ip().to_string(),
            peers[0].ip().to_string(),
        )
    };
    info!(relay = pair.relay_port.is_some(), "paired");
    state
        .pairs
        .lock()
        .unwrap()
        .insert(name.clone(), pair.clone());
    if let (Some(port), Some(sock), Some(tokens)) =
        (pair.relay_port, relay_socket, pair.relay_tokens.clone())
    {
        relay::spawn(
            state.clone(),
            name.clone(),
            port,
            sock,
            [
                relay::Role {
                    token: tokens[0].clone(),
                    expected_ip: peers[0].ip(),
                },
                relay::Role {
                    token: tokens[1].clone(),
                    expected_ip: peers[1].ip(),
                },
            ],
            Some([a_name.clone(), b_name.clone()]),
        );
    }
    let track = pick_track(
        &state.cfg.session.quick_tracks,
        state.cfg.session.quick_track,
        rand::random(),
    );
    info!(session = %name, track, "pairing course");
    let track = track.to_string();
    let mode = state.cfg.session.quick_mode.to_string();
    for (who, addr, from) in [(a_name, a_addr, a_from), (b_name, b_addr, b_from)] {
        let body = ses_pairs_for(&pair, &who, &addr, &from, &track, &mode);
        let refs: Vec<(&str, &str)> =
            body.iter().map(|(k, v)| (k.as_str(), v.as_str())).collect();
        let wire = ssx3_proto::encode(types::SES, types::SUB_ZERO, &refs, &[]);
        let writer = state
            .games
            .lock()
            .unwrap()
            .get(&who)
            .map(|g| g.writer.clone());
        if let Some(wr) = writer {
            if gsend_wire(&wr, &wire).await.is_err() {
                warn!("session push failed");
            }
        }
    }
    if pair.relay_port.is_none() {
        state.pairs.lock().unwrap().remove(&name);
    }
}

/// Build one side's +ses body. SELF/OPPO mirrored per recipient;
/// ADDR/FROM are this side's *destination* (peer's IP, or the relay).
fn ses_pairs_for(
    pair: &Pair,
    me: &str,
    addr: &str,
    from: &str,
    track: &str,
    mode: &str,
) -> Vec<(String, String)> {
    let oppo = if me == pair.a {
        pair.b.clone()
    } else {
        pair.a.clone()
    };
    let mut v = vec![
        ("NAME".to_string(), pair.name.clone()),
        ("SELF".to_string(), me.to_string()),
        ("HOST".to_string(), pair.host.clone()),
        ("OPPO".to_string(), oppo),
        ("P1".to_string(), track.to_string()),
        ("P2".to_string(), mode.to_string()),
        ("P3".to_string(), "0".to_string()),
        ("P4".to_string(), "0".to_string()),
        ("AUTH".to_string(), "1".to_string()),
        ("ADDR".to_string(), addr.to_string()),
        ("FROM".to_string(), from.to_string()),
        ("SEED".to_string(), pair.seed.to_string()),
        ("WHEN".to_string(), pair.when.clone()),
    ];
    if let Some(tok) = &pair.token {
        v.push(("SESSTOK".to_string(), tok.clone()));
    }
    if let (Some(port), Some(tokens)) = (pair.relay_port, &pair.relay_tokens) {
        let role = if me == pair.a { 0 } else { 1 };
        v.push(("RLYPORT".to_string(), port.to_string()));
        v.push(("RLYROLE".to_string(), role.to_string()));
        v.push(("RLYTOK".to_string(), tokens[role].clone()));
    }
    v
}

// ---------------------------------------------------------------- main

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info")),
        )
        .init();
    let cfg_path = std::env::args().nth(1).unwrap_or("lobby.toml".into());
    let mut cfg = Config::default();
    if let Ok(text) = std::fs::read_to_string(&cfg_path) {
        cfg = toml::from_str(&text).expect("invalid config TOML");
    } else {
        info!("no config file; defaults");
    }
    // Compose files inject the host IP via env (infra pattern).
    if let Ok(ip) = std::env::var("LOBBY_ADVERTISE_IP") {
        if !ip.trim().is_empty() {
            cfg.lobby.advertise_ip = ip;
        }
    }
    if cfg.auth.mode != "open" {
        panic!("only anonymous auth.mode=open is supported");
    }
    if !["off", "always", "fallback"].contains(&cfg.relay.mode.as_str()) {
        panic!("relay.mode must be off|always|fallback");
    }
    if cfg.relay.port_start > cfg.relay.port_end {
        panic!("relay port_start > port_end");
    }
    for (name, v) in [
        ("relay.packets_per_sec", cfg.relay.packets_per_sec),
        ("relay.burst_packets", cfg.relay.burst_packets),
        ("relay.per_role_per_sec", cfg.relay.per_role_per_sec),
        ("relay.per_role_burst", cfg.relay.per_role_burst),
        ("relay.handshake_per_sec", cfg.relay.handshake_per_sec),
        ("relay.handshake_burst", cfg.relay.handshake_burst),
        (
            "relay.bytes_per_sec_per_pair",
            cfg.relay.bytes_per_sec_per_pair,
        ),
        ("relay.burst_bytes_per_pair", cfg.relay.burst_bytes_per_pair),
        ("relay.global_bytes_per_sec", cfg.relay.global_bytes_per_sec),
        ("relay.global_burst_bytes", cfg.relay.global_burst_bytes),
    ] {
        if !(v > 0.0) {
            panic!("{name} must be > 0");
        }
    }
    if cfg.lobby.advertise_ip.is_empty() {
        // Default to the bind address only when it is a concrete IP;
        // 0.0.0.0 is never a usable +ses/news address.
        if cfg.lobby.bind != "0.0.0.0" {
            cfg.lobby.advertise_ip = cfg.lobby.bind.clone();
        } else {
            panic!("lobby.advertise_ip must be set (the IP clients dial)");
        }
    }
    let ports = relay_pool(&cfg);
    let server_key = if cfg.session.server_key.is_empty() {
        new_hex(32)
    } else {
        cfg.session.server_key.clone()
    };
    let state = build_state(cfg, ports, server_key);
    let bind = state.cfg.lobby.bind.clone();
    let (lp, gp, bp) = (
        state.cfg.ports.lobby,
        state.cfg.ports.game,
        state.cfg.ports.buddy,
    );
    let lobby_l = TcpListener::bind(format!("{bind}:{lp}"))
        .await
        .expect("bind lobby");
    let game_l = TcpListener::bind(format!("{bind}:{gp}"))
        .await
        .expect("bind game");
    let buddy_l = TcpListener::bind(format!("{bind}:{bp}"))
        .await
        .expect("bind buddy");
    let qp_status = Arc::new(Mutex::new(state_sync::QpStatus::default()));
    let hp = state.cfg.health.port;
    if hp != 0 {
        let health_l = TcpListener::bind(format!("{bind}:{hp}"))
            .await
            .expect("bind health");
        tokio::spawn(health::serve(health_l, lp, gp, state.clone(), qp_status.clone()));
    }
    *state.game_port.lock().unwrap() = game_l.local_addr().expect("game addr").port();
    *state.buddy_port.lock().unwrap() = buddy_l.local_addr().expect("buddy addr").port();
    info!(
        lobby_port = lp,
        game_port = gp,
        buddy_port = bp,
        "ssx3-lobby up"
    );
    if state.cfg.state_sync.enable {
        let c = &state.cfg.state_sync;
        let ssc = state_sync::Sscfg {
            bind: c.bind.clone(),
            port: c.port,
            legacy_ports: c.legacy_ports.clone(),
            delay_ms: c.delay_ms,
            jitter_ms: c.jitter_ms,
            timeout_ms: c.timeout_ms,
            start_delay_ms: c.start_delay_ms,
            max_rooms: c.max_rooms,
            max_waiters: c.max_waiters,
            max_waiters_per_ip: c.max_waiters_per_ip,
            max_seats_per_ip: c.max_seats_per_ip,
            packets_per_sec: c.packets_per_sec,
            burst_packets: c.burst_packets,
            global_bytes_per_sec: state.cfg.relay.global_bytes_per_sec,
            global_burst_bytes: state.cfg.relay.global_burst_bytes,
            ..state_sync::Sscfg::default()
        };
        let (_stop_tx, stop_rx) = tokio::sync::watch::channel(false);
        let _ = tokio::join!(
            serve(lobby_l, state.clone(), "lobby"),
            serve(game_l, state.clone(), "game"),
            serve(buddy_l, state.clone(), "buddy"),
            state_sync::run(ssc, stop_rx, qp_status),
        );
    } else {
        let _ = tokio::join!(
            serve(lobby_l, state.clone(), "lobby"),
            serve(game_l, state.clone(), "game"),
            serve(buddy_l, state.clone(), "buddy"),
        );
    }
}

/// Pairs touching `ip`, from formation addresses (H2): drop-and-repair
/// cycling cannot evade the per-IP ceiling by closing TCP first.
/// Callers hold the queue and games locks and take the pairs lock last
/// (lock order: queue -> games -> pairs).
/// Drop the least-recently-active identity-cache entry (M3): a full
/// cache evicts instead of refusing newcomers, so 2048 strangers
/// holding one slot each cannot lock out a new IP. Only runs on a full
/// cache, so the O(n) scan is rare. Empty queues sort first (dead
/// entries go before live ones).
fn evict_oldest(map: &mut HashMap<IpAddr, VecDeque<Instant>>) {
    let oldest = map
        .iter()
        .max_by_key(|(_, q)| std::cmp::Reverse(q.back().copied()))
        .map(|(ip, _)| *ip);
    if let Some(ip) = oldest {
        map.remove(&ip);
    }
}

fn count_pairs_on_ip(pairs: &HashMap<String, Pair>, ip: IpAddr) -> usize {
    pairs
        .values()
        .filter(|p| p.a_ip == ip || p.b_ip == ip)
        .count()
}

/// netsec-fix-1 F5: first eligible pair behind a blocked head (pure,
/// unit-tested). `waiters` is the queue in order with each waiter's member
/// IP (`None` = dead session); `pair_ips` lists existing pairs' member IPs.
/// Returns the two names in queue order plus dead names to drop. Blocked
/// waiters are skipped in place (they retry on the next quik); when no pair
/// is eligible the queue is left untouched apart from dead pruning.
fn pick_pair(
    waiters: &[(String, Option<IpAddr>)],
    pair_ips: &[(IpAddr, IpAddr)],
    max_pairs: usize,
    egress_capped: bool,
    max_pairs_per_ip: usize,
) -> (Option<(String, String)>, Vec<String>) {
    let dead: Vec<String> = waiters
        .iter()
        .filter(|(_, ip)| ip.is_none())
        .map(|(n, _)| n.clone())
        .collect();
    if pair_ips.len() >= max_pairs || egress_capped {
        return (None, dead);
    }
    let on_ip = |ip: IpAddr| {
        pair_ips
            .iter()
            .filter(|(a, b)| *a == ip || *b == ip)
            .count()
    };
    for i in 0..waiters.len() {
        let Some(a) = waiters[i].1 else {
            continue;
        };
        if on_ip(a) >= max_pairs_per_ip {
            continue;
        }
        for j in (i + 1)..waiters.len() {
            let Some(b) = waiters[j].1 else {
                continue;
            };
            if on_ip(b) >= max_pairs_per_ip {
                continue;
            }
            return (Some((waiters[i].0.clone(), waiters[j].0.clone())), dead);
        }
    }
    (None, dead)
}

impl State {
    /// Monthly egress gate: cumulative forwarded bytes past the
    /// configured cap. 0 disables the gate (accounting only).
    fn egress_capped(&self) -> bool {
        let cap = self.cfg.relay.monthly_egress_cap_bytes;
        cap > 0 && self.egress_bytes_total.load(Ordering::Relaxed) >= cap
    }

    /// Paired cover (lobby-buddy-1): a persona whose pair relay is live
    /// — the pairs record exists exactly while the relay task runs, and
    /// the task reaps itself `idle_timeout_secs` after its last
    /// forwarded datagram — or whose pair ended within
    /// `paired_grace_secs`. Racing clients send nothing on either TCP,
    /// so without cover the 300 s buddy / 240 s game reaps fire
    /// mid-race and the guest raises opponent-lost at results time.
    /// Lock order: pairs, then pair_ended; never nested with others.
    fn pair_cover(&self, persona: &str) -> bool {
        if self
            .pairs
            .lock()
            .unwrap()
            .values()
            .any(|p| p.a == persona || p.b == persona)
        {
            return true;
        }
        let grace = Duration::from_secs(self.cfg.limits.paired_grace_secs);
        let now = Instant::now();
        let mut ended = self.pair_ended.lock().unwrap();
        ended.retain(|_, t| now.duration_since(*t) <= grace);
        ended.contains_key(persona)
    }

    /// Record a pair's end for post-pair grace cover (relay task exit).
    fn note_pair_ended(&self, members: &[String; 2]) {
        let now = Instant::now();
        let grace = Duration::from_secs(self.cfg.limits.paired_grace_secs);
        let mut ended = self.pair_ended.lock().unwrap();
        for m in members {
            ended.insert(m.clone(), now);
        }
        ended.retain(|_, t| now.duration_since(*t) <= grace);
    }
}

/// The relay port pool: every port in the configured range except the
/// ports other listeners own (lobby/game/buddy/health) and the fixed or
/// legacy STATE ports. Reserved ports are dropped so widening the relay
/// range over an existing service port can never collide. Pop order is
/// ascending from `port_start` (the stock P2P port 3658 first).
fn relay_pool(cfg: &Config) -> Vec<u16> {
    let mut reserved: Vec<u16> = vec![
        cfg.ports.lobby,
        cfg.ports.game,
        cfg.ports.buddy,
        cfg.health.port,
        cfg.state_sync.port,
    ];
    reserved.extend(cfg.state_sync.legacy_ports.iter().copied());
    let mut ports: Vec<u16> = (cfg.relay.port_start..=cfg.relay.port_end)
        .filter(|p| !reserved.contains(p))
        .collect();
    ports.reverse(); // pop() allocates from port_start up
    ports
}

fn build_state(cfg: Config, ports: Vec<u16>, server_key: String) -> Arc<State> {
    let game_port = cfg.ports.game;
    let buddy_port = cfg.ports.buddy;
    let egress_bucket =
        relay::TokenBucket::new(cfg.relay.global_bytes_per_sec, cfg.relay.global_burst_bytes);
    Arc::new(State {
        advertise_ip: cfg.lobby.advertise_ip.clone(),
        server_key,
        cfg,
        names: Mutex::new(HashMap::new()),
        games: Mutex::new(HashMap::new()),
        buddies: Mutex::new(HashMap::new()),
        queue: Mutex::new(VecDeque::new()),
        pairs: Mutex::new(HashMap::new()),
        pair_ended: Mutex::new(HashMap::new()),
        conns_per_ip: Mutex::new(HashMap::new()),
        pending_addrs: Mutex::new(HashMap::new()),
        game_port: Mutex::new(game_port),
        buddy_port: Mutex::new(buddy_port),
        total_conns: Mutex::new(0),
        msg_times: Mutex::new(HashMap::new()),
        accept_times: Mutex::new(HashMap::new()),
        relay_ports: Mutex::new(ports),
        wire_budget: tokio::sync::Semaphore::new(1024 * 1024),
        egress_bytes_total: AtomicU64::new(0),
        egress_bucket: Mutex::new(egress_bucket),
    })
}

#[cfg(test)]
mod tests {
    #[test]
    fn pairing_course_is_a_race_event_from_the_list() {
        let all = super::d_tracks();
        assert_eq!(all, vec![0, 1, 2, 3, 4, 14, 15, 16]);
        let mut seen = std::collections::BTreeSet::new();
        for roll in 0..64u32 {
            let t = super::pick_track(&all, 2, roll);
            assert!(all.contains(&t));
            seen.insert(t);
        }
        assert_eq!(seen.len(), all.len(), "every course reachable");
        assert_eq!(super::pick_track(&[], 2, 7), 2, "empty list keeps quick_track");
        assert_eq!(super::pick_track(&[14], 2, 99), 14);
    }

    use super::*;
    use tokio::net::TcpStream;

    fn test_cfg(relay_mode: &str) -> (Config, Vec<u16>) {
        let mut cfg = Config::default();
        cfg.lobby.bind = "127.0.0.1".into();
        cfg.lobby.advertise_ip = "127.0.0.1".into();
        cfg.relay.mode = relay_mode.into();
        cfg.relay.port_start = 0;
        cfg.relay.port_end = 0;
        (cfg, vec![])
    }

    struct Script {
        stream: TcpStream,
        buf: Vec<u8>,
    }

    impl Script {
        async fn connect(port: u16) -> Self {
            let stream = TcpStream::connect(format!("127.0.0.1:{port}"))
                .await
                .unwrap();
            Self {
                stream,
                buf: Vec::new(),
            }
        }

        async fn send(&mut self, typ: [u8; 4], pairs: &[(&str, &str)]) {
            let wire = ssx3_proto::encode(typ, types::SUB_ZERO, pairs, &[]);
            self.stream.write_all(&wire).await.unwrap();
        }

        async fn recv(&mut self) -> Message {
            loop {
                if self.buf.len() >= 12 {
                    let size =
                        u32::from_be_bytes([self.buf[8], self.buf[9], self.buf[10], self.buf[11]])
                            as usize;
                    if self.buf.len() >= size {
                        let wire = self.buf[..size].to_vec();
                        self.buf.drain(..size);
                        let (m, _) = ssx3_proto::decode_one(&wire).unwrap();
                        return m;
                    }
                }
                let mut tmp = [0u8; 8192];
                let n = tokio::time::timeout(Duration::from_secs(5), self.stream.read(&mut tmp))
                    .await
                    .expect("recv timeout")
                    .expect("read failed");
                assert!(n > 0, "server closed connection mid-script");
                self.buf.extend_from_slice(&tmp[..n]);
            }
        }

        fn expect(&self, m: &Message, typ: &str) {
            assert_eq!(m.type_str(), Some(typ), "expected {typ}");
        }
    }

    async fn boot(relay_mode: &str) -> (Arc<State>, u16, u16, u16) {
        let (cfg, ports) = test_cfg(relay_mode);
        boot_cfg(cfg, ports).await
    }

    async fn boot_cfg(cfg: Config, ports: Vec<u16>) -> (Arc<State>, u16, u16, u16) {
        let state = build_state(cfg, ports, "testkey".into());
        let ll = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let gl = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let bl = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let (lp, gp, bp) = (
            ll.local_addr().unwrap().port(),
            gl.local_addr().unwrap().port(),
            bl.local_addr().unwrap().port(),
        );
        *state.game_port.lock().unwrap() = gp;
        *state.buddy_port.lock().unwrap() = bp;
        serve(ll, state.clone(), "lobby");
        serve(gl, state.clone(), "game");
        serve(bl, state.clone(), "buddy");
        (state, lp, gp, bp)
    }

    /// Full login through matchmaking for one persona; returns the game
    /// script positioned after the buddy handshake (ready for quik).
    async fn login(state: &Arc<State>, lp: u16, gp: u16, bp: u16, name: &str) -> (Script, Script) {
        let _ = state;
        let mut l = Script::connect(lp).await;
        l.send(types::DIR, &[("PROD", "SSX-PS2-2004"), ("LANG", "en")])
            .await;
        let r = l.recv().await;
        l.expect(&r, "@dir");
        assert_eq!(r.get("PORT"), Some(gp.to_string()).as_deref());

        let mut g = Script::connect(gp).await;
        g.send(types::ADDR, &[("ADDR", "127.0.0.1"), ("PORT", "52061")])
            .await;
        let r = g.recv().await;
        g.expect(&r, "addr");
        assert_eq!(r.get("ADDR"), Some("127.0.0.1"));
        g.send(types::SKEY, &[("SKEY", "$5075626c6963204b6579")])
            .await;
        let r = g.recv().await;
        g.expect(&r, "skey");
        assert_eq!(r.get("SKEY"), Some("$testkey"));
        g.send(
            types::SELE,
            &[("ROOMS", "1 USERS=1 RANKS=0 MESGS=1 GAMES=1")],
        )
        .await;
        let r = g.recv().await;
        g.expect(&r, "sele");
        assert_eq!((r.get("MORE"), r.get("SLOTS")), (Some("0"), Some("0")));
        let did = new_hex(32);
        g.send(
            types::AUTH,
            &[
                ("NAME", name),
                ("PASS", "anything-goes"),
                ("DID", &did),
                ("TOS", "1"),
            ],
        )
        .await;
        let r = g.recv().await;
        g.expect(&r, "auth");
        assert_eq!(r.get("PERSONAS"), Some(name));
        for _ in 0..4 {
            let r = g.recv().await;
            g.expect(&r, "+rom");
        }
        g.send(types::PERS, &[("PERS", name), ("PID", "SSX-PS2-2004")])
            .await;
        let r = g.recv().await;
        g.expect(&r, "pers");
        assert_eq!(r.get("PERS"), Some(name));
        assert!(r.get("LKEY").map(|s| s.len() == 32).unwrap_or(false));
        g.send(types::ONLN, &[("PERS", name)]).await;
        let r = g.recv().await;
        g.expect(&r, "onln");
        assert_eq!(r.get("PERS"), Some(name));
        g.send(types::NEWS, &[("NAME", "0")]).await;
        let r = g.recv().await;
        g.expect(&r, "news");
        assert_eq!(r.sub, types::SUB_NEW0);
        assert_eq!(r.get("BUDDYPORT"), Some(bp.to_string()).as_deref());
        assert_eq!(r.strays, vec!["WIP Server".to_string()]);

        let mut b = Script::connect(bp).await;
        b.send(types::DISC, &[]).await;
        let r = b.recv().await;
        b.expect(&r, "DISC");
        b.send(
            types::BAUTH,
            &[
                ("PROD", "SSX-PS2-2004"),
                ("USER", &format!("{name}/cso/SSX-PS2-2004")),
                ("PASS", "aaaa"),
                ("DID", &did),
            ],
        )
        .await;
        let r = b.recv().await;
        b.expect(&r, "AUTH");
        b.send(types::PSET, &[("RSRC", "SSX-PS2-2004"), ("SHOW", "CHAT")])
            .await;
        let r = b.recv().await;
        b.expect(&r, "PSET");
        assert!(r.get("PSRC").is_some() && r.get("RSRC").is_none());
        b.send(types::RGET, &[("LRSC", "cso"), ("LIST", "B"), ("ID", "1")])
            .await;
        let r = b.recv().await;
        b.expect(&r, "RGET");
        assert_eq!((r.get("ID"), r.get("SIZE")), (Some("1"), Some("0")));
        b.send(types::PING, &[]).await;
        let r = b.recv().await;
        b.expect(&r, "PING");
        (g, b)
    }

    #[tokio::test]
    async fn pair_two_clients_matchmaker() {
        let (state, lp, gp, bp) = boot("off").await;
        let (mut ga, _ba) = login(&state, lp, gp, bp, "lob1a").await;
        let (mut gb, _bb) = login(&state, lp, gp, bp, "lob1b").await;

        // A searches, then gives up (KIND=* leaves the queue); B's search
        // does not pair with A; A searches again while B still searches ->
        // the pair forms on A's search.
        ga.send(types::QUIK, &[("KIND", "DeathRace"), ("QMFP", "1,1,1,1,1")])
            .await;
        let r = ga.recv().await;
        ga.expect(&r, "quik");
        assert_eq!(r.get("FULL"), None);
        ga.send(types::QUIK, &[("KIND", "*")]).await;
        let r = ga.recv().await;
        ga.expect(&r, "quik");
        gb.send(types::QUIK, &[("KIND", "DeathRace"), ("QMFP", "1,1,1,1,1")])
            .await;
        let r = gb.recv().await;
        gb.expect(&r, "quik");
        assert!(
            tokio::time::timeout(Duration::from_millis(300), ga.recv()).await.is_err(),
            "a client that gave up is never paired"
        );
        ga.send(types::QUIK, &[("KIND", "DeathRace"), ("QMFP", "1,1,1,1,1")])
            .await;
        let r = ga.recv().await;
        ga.expect(&r, "quik");
        let sa = ga.recv().await;
        ga.expect(&sa, "+ses");
        let sb = gb.recv().await;
        gb.expect(&sb, "+ses");
        assert_eq!(sa.get("NAME"), sb.get("NAME"));
        assert_eq!(sa.get("SEED"), sb.get("SEED"));
        assert_eq!(sa.get("SELF"), Some("lob1a"));
        assert_eq!(sb.get("SELF"), Some("lob1b"));
        assert_eq!(sa.get("OPPO"), Some("lob1b"));
        assert_eq!(sb.get("OPPO"), Some("lob1a"));
        assert_eq!(sa.get("HOST"), sb.get("HOST"));
        // Matchmaker mode: each side dials the peer's observed IP.
        assert_eq!(sa.get("ADDR"), Some("127.0.0.1"));
        assert_eq!(sb.get("ADDR"), Some("127.0.0.1"));

        // The side that waited (last quik a poll) answers +ses with
        // `chal PERS=*` and links only after the echo.
        ga.send(types::CHAL, &[("PERS", "*")]).await;
        let r = ga.recv().await;
        ga.expect(&r, "chal");
        assert_eq!(r.get("PERS"), Some("*"));

        // Post-race upload gets the empty ack.
        ga.send(
            types::RANK,
            &[("WHEN", "2026.10.13 02:13:54"), ("REPT", "lob1a")],
        )
        .await;
        let r = ga.recv().await;
        ga.expect(&r, "rank");
        assert!(r.pairs.is_empty());
    }

    async fn enroll(sock: &tokio::net::UdpSocket, relay: &str, role: u8, token: &str) -> [u8; 8] {
        let mut hello = relay::REG_MAGIC.to_vec();
        hello.push(role);
        hello.extend_from_slice(token.as_bytes());
        sock.send_to(&hello, relay).await.unwrap();
        let mut buf = [0u8; 64];
        let (n, _) = tokio::time::timeout(Duration::from_secs(2), sock.recv_from(&mut buf))
            .await
            .unwrap()
            .unwrap();
        assert_eq!(n, 17);
        assert_eq!(&buf[..8], relay::CHALLENGE_MAGIC);
        assert_eq!(buf[8], role);
        let mut challenge = [0u8; 8];
        challenge.copy_from_slice(&buf[9..17]);
        let mut proof = relay::PROOF_MAGIC.to_vec();
        proof.push(role);
        proof.extend_from_slice(&challenge);
        proof.extend_from_slice(token.as_bytes());
        sock.send_to(&proof, relay).await.unwrap();
        challenge
    }

    #[tokio::test]
    async fn pair_two_clients_relay_always() {
        let port_socket = tokio::net::UdpSocket::bind("127.0.0.1:0").await.unwrap();
        let port = port_socket.local_addr().unwrap().port();
        drop(port_socket);
        let (mut cfg, _) = test_cfg("always");
        cfg.relay.port_start = port;
        cfg.relay.port_end = port;
        let state = build_state(cfg, vec![port], "testkey".into());
        let ll = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let gl = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let bl = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let (lp, gp, bp) = (
            ll.local_addr().unwrap().port(),
            gl.local_addr().unwrap().port(),
            bl.local_addr().unwrap().port(),
        );
        *state.game_port.lock().unwrap() = gp;
        *state.buddy_port.lock().unwrap() = bp;
        serve(ll, state.clone(), "lobby");
        serve(gl, state.clone(), "game");
        serve(bl, state.clone(), "buddy");
        let (mut ga, _ba) = login(&state, lp, gp, bp, "lob1ra").await;
        let (mut gb, _bb) = login(&state, lp, gp, bp, "lob1rb").await;
        ga.send(types::QUIK, &[("KIND", "DeathRace")]).await;
        let qa = ga.recv().await;
        ga.expect(&qa, "quik");
        gb.send(types::QUIK, &[("KIND", "DeathRace")]).await;
        let qb = gb.recv().await;
        gb.expect(&qb, "quik");
        let sa = ga.recv().await;
        let sb = gb.recv().await;
        ga.expect(&sa, "+ses");
        gb.expect(&sb, "+ses");
        assert_eq!(sa.get("RLYPORT"), Some(port.to_string()).as_deref());
        assert_eq!(sb.get("RLYPORT"), Some(port.to_string()).as_deref());
        assert_eq!(sa.get("RLYROLE"), Some("0"));
        assert_eq!(sb.get("RLYROLE"), Some("1"));
        assert_ne!(sa.get("RLYTOK"), sb.get("RLYTOK"));
        assert_eq!(sa.get("ADDR"), Some("127.0.0.1"));
        assert_eq!(sb.get("ADDR"), Some("127.0.0.1"));
        let ua = tokio::net::UdpSocket::bind("127.0.0.1:0").await.unwrap();
        let ub = tokio::net::UdpSocket::bind("127.0.0.1:0").await.unwrap();
        let rogue = tokio::net::UdpSocket::bind("127.0.0.1:0").await.unwrap();
        let relay = format!("127.0.0.1:{port}");
        // An arbitrary first game datagram cannot enroll either role.
        rogue.send_to(b"O5HI", &relay).await.unwrap();
        ua.send_to(b"O5HI", &relay).await.unwrap();
        // A matched player knows its own token, but cannot claim role 1.
        let mut wrong_role = relay::REG_MAGIC.to_vec();
        wrong_role.push(b'1');
        wrong_role.extend_from_slice(sa.get("RLYTOK").unwrap().as_bytes());
        ua.send_to(&wrong_role, &relay).await.unwrap();
        let mut tmp = [0u8; 128];
        assert!(
            tokio::time::timeout(Duration::from_millis(150), ua.recv_from(&mut tmp))
                .await
                .is_err()
        );
        let challenge = enroll(&ua, &relay, b'0', sa.get("RLYTOK").unwrap()).await;
        enroll(&ub, &relay, b'1', sb.get("RLYTOK").unwrap()).await;
        tokio::time::sleep(Duration::from_millis(20)).await;
        ub.send_to(b"O5HI", &relay).await.unwrap();
        let (n, _) = tokio::time::timeout(Duration::from_secs(2), ua.recv_from(&mut tmp))
            .await
            .unwrap()
            .unwrap();
        assert_eq!(&tmp[..n], b"O5HI");
        ua.send_to(b"O5OK", &relay).await.unwrap();
        let (n, _) = tokio::time::timeout(Duration::from_secs(2), ub.recv_from(&mut tmp))
            .await
            .unwrap()
            .unwrap();
        assert_eq!(&tmp[..n], b"O5OK");
        // A copied proof from another source cannot become a relay endpoint.
        let mut replay = relay::PROOF_MAGIC.to_vec();
        replay.push(b'0');
        replay.extend_from_slice(&challenge);
        replay.extend_from_slice(sa.get("RLYTOK").unwrap().as_bytes());
        rogue.send_to(&replay, &relay).await.unwrap();
        rogue.send_to(b"O5PKevil", &relay).await.unwrap();
        assert!(
            tokio::time::timeout(Duration::from_millis(150), ub.recv_from(&mut tmp))
                .await
                .is_err()
        );
    }

    #[tokio::test]
    async fn invalid_stream_does_not_prevent_relay_reap() {
        let (mut cfg, _) = test_cfg("always");
        cfg.relay.idle_timeout_secs = 1;
        let state = build_state(cfg, vec![], "testkey".into());
        let sock = tokio::net::UdpSocket::bind("127.0.0.1:0").await.unwrap();
        let port = sock.local_addr().unwrap().port();
        let attacker = tokio::net::UdpSocket::bind("127.0.0.1:0").await.unwrap();
        relay::spawn(
            state.clone(),
            "invalid-stream".into(),
            port,
            sock,
            [
                relay::Role {
                    token: "a".repeat(32),
                    expected_ip: "127.0.0.1".parse().unwrap(),
                },
                relay::Role {
                    token: "b".repeat(32),
                    expected_ip: "127.0.0.1".parse().unwrap(),
                },
            ],
            None,
        );
        for _ in 0..14 {
            attacker
                .send_to(b"O5HI", format!("127.0.0.1:{port}"))
                .await
                .unwrap();
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
        assert!(state.relay_ports.lock().unwrap().contains(&port));
    }

    #[tokio::test]
    async fn relay_pool_and_bind_fail_closed() {
        for occupied in [false, true] {
            let (state, lp, gp, bp) = boot("always").await;
            let _blocker = if occupied {
                let sock = tokio::net::UdpSocket::bind("127.0.0.1:0").await.unwrap();
                state
                    .relay_ports
                    .lock()
                    .unwrap()
                    .push(sock.local_addr().unwrap().port());
                Some(sock)
            } else {
                None
            };
            let (mut ga, _ba) = login(&state, lp, gp, bp, "pool-a").await;
            let (mut gb, _bb) = login(&state, lp, gp, bp, "pool-b").await;
            ga.send(types::QUIK, &[("KIND", "DeathRace")]).await;
            let qa = ga.recv().await;
            ga.expect(&qa, "quik");
            gb.send(types::QUIK, &[("KIND", "DeathRace")]).await;
            let qb = gb.recv().await;
            gb.expect(&qb, "quik");
            assert!(tokio::time::timeout(Duration::from_millis(150), ga.recv())
                .await
                .is_err());
            assert!(tokio::time::timeout(Duration::from_millis(150), gb.recv())
                .await
                .is_err());
            assert_eq!(state.queue.lock().unwrap().len(), 2);
            assert!(state.pairs.lock().unwrap().is_empty());
        }
    }

    #[tokio::test]
    async fn stale_pair_selection_returns_and_preserves_live_waiter() {
        let (state, lp, gp, bp) = boot("off").await;
        let (_game, _buddy) = login(&state, lp, gp, bp, "alive").await;
        {
            let mut q = state.queue.lock().unwrap();
            q.push_back("gone".into());
            q.push_back("alive".into());
        }
        tokio::time::timeout(Duration::from_secs(1), try_pair(&state))
            .await
            .unwrap();
        assert_eq!(
            state.queue.lock().unwrap().front().map(String::as_str),
            Some("alive")
        );
    }

    #[tokio::test]
    async fn unauthenticated_and_repeated_personas_leave_no_session() {
        let (state, lp, gp, bp) = boot("off").await;
        let mut unauth = Script::connect(gp).await;
        unauth.send(types::PERS, &[("PERS", "rogue")]).await;
        let mut tmp = [0u8; 64];
        let n = tokio::time::timeout(Duration::from_secs(2), unauth.stream.read(&mut tmp))
            .await
            .unwrap()
            .unwrap();
        assert_eq!(n, 0);
        assert!(!state.games.lock().unwrap().contains_key("rogue"));
        let (mut game, _buddy) = login(&state, lp, gp, bp, "single").await;
        game.send(types::PERS, &[("PERS", "second")]).await;
        let n = tokio::time::timeout(Duration::from_secs(2), game.stream.read(&mut tmp))
            .await
            .unwrap()
            .unwrap();
        assert_eq!(n, 0);
        assert!(!state.games.lock().unwrap().contains_key("single"));
        assert!(!state.games.lock().unwrap().contains_key("second"));
        assert!(state.names.lock().unwrap().is_empty());
    }

    #[tokio::test]
    async fn utf8_frame_rejected_without_panic_or_admission_leak() {
        let (state, _lp, gp, _bp) = boot("off").await;
        let mut conn = Script::connect(gp).await;
        conn.send(types::AUTH, &[("NAME", "é")]).await;
        let mut tmp = [0u8; 32];
        let n = tokio::time::timeout(Duration::from_secs(2), conn.stream.read(&mut tmp))
            .await
            .unwrap()
            .unwrap();
        assert_eq!(n, 0);
        tokio::task::yield_now().await;
        assert_eq!(*state.total_conns.lock().unwrap(), 0);
    }

    /// M3 attack repro: 2048 strangers must not lock newcomers out of
    /// the rate-limiter identity caches. Full caches evict the oldest
    /// entry instead of refusing.
    #[test]
    fn rate_caches_evict_oldest_instead_of_refusing() {
        let (cfg, ports) = test_cfg("off");
        let state = build_state(cfg, ports, "key".into());
        for i in 0..3000u32 {
            let ip = IpAddr::V4(std::net::Ipv4Addr::from(0x0a000000 + i));
            let _ = state.check_rate(ip);
            assert!(state.conn_open(ip));
            state.conn_close(ip);
        }
        assert!(state.msg_times.lock().unwrap().len() <= 2048);
        assert!(state.accept_times.lock().unwrap().len() <= 2048);
        let fresh: IpAddr = "127.0.0.9".parse().unwrap();
        assert!(state.check_rate(fresh), "msg cache refused a newcomer");
        assert!(state.conn_open(fresh), "accept cache refused a newcomer");
        state.conn_close(fresh);
    }

    #[test]
    fn rate_identity_cache_and_accept_churn_are_bounded() {
        let (cfg, ports) = test_cfg("off");
        let state = build_state(cfg, ports, "key".into());
        for i in 0..3000u32 {
            let ip = IpAddr::V4(std::net::Ipv4Addr::from(0x0a000000 + i));
            let _ = state.check_rate(ip);
        }
        assert!(state.msg_times.lock().unwrap().len() <= 2048);
        let ip: IpAddr = "127.0.0.1".parse().unwrap();
        for _ in 0..120 {
            assert!(state.conn_open(ip));
            state.conn_close(ip);
        }
        assert!(!state.conn_open(ip));
    }

    #[tokio::test]
    async fn duplicate_name_gets_numeric_suffix_and_device_binds_buddy() {
        let (state, lp, gp, bp) = boot("off").await;
        let (_ga, _ba) = login(&state, lp, gp, bp, "swift-otter").await;
        let mut g2 = Script::connect(gp).await;
        let did = new_hex(32);
        g2.send(
            types::AUTH,
            &[("NAME", "swift-otter"), ("PASS", "anything"), ("DID", &did)],
        )
        .await;
        let auth = g2.recv().await;
        assert_eq!(auth.get("PERSONAS"), Some("swift-otter2"));
        for _ in 0..4 {
            let _ = g2.recv().await;
        }
        g2.send(types::PERS, &[("PERS", "swift-otter")]).await;
        let pers = g2.recv().await;
        assert_eq!(pers.get("PERS"), Some("swift-otter2"));
        g2.send(types::ONLN, &[("PERS", "swift-otter")]).await;
        assert_eq!(g2.recv().await.get("PERS"), Some("swift-otter2"));
        let mut b = Script::connect(bp).await;
        b.send(
            types::BAUTH,
            &[("USER", "swift-otter/cso/SSX-PS2-2004"), ("DID", &did)],
        )
        .await;
        let ok = b.recv().await;
        assert_eq!(ok.type_str(), Some("AUTH"));
        assert!(ok.get("DID").is_none());
        let mut wrong = Script::connect(bp).await;
        wrong
            .send(
                types::BAUTH,
                &[
                    ("USER", "swift-otter2/cso/SSX-PS2-2004"),
                    ("DID", "00000000000000000000000000000000"),
                ],
            )
            .await;
        let mut tmp = [0u8; 64];
        assert_eq!(
            tokio::time::timeout(Duration::from_secs(2), wrong.stream.read(&mut tmp))
                .await
                .unwrap()
                .unwrap(),
            0
        );
        let mut same_device = Script::connect(gp).await;
        same_device
            .send(types::AUTH, &[("NAME", "another"), ("DID", &did)])
            .await;
        assert_eq!(
            tokio::time::timeout(Duration::from_secs(2), same_device.stream.read(&mut tmp))
                .await
                .unwrap()
                .unwrap(),
            0
        );
        drop(g2);
        for _ in 0..100 {
            if !state.names.lock().unwrap().contains_key("swift-otter2") {
                break;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        assert!(!state.names.lock().unwrap().contains_key("swift-otter2"));
        let mut g3 = Script::connect(gp).await;
        let next_did = new_hex(32);
        g3.send(
            types::AUTH,
            &[
                ("NAME", "swift-otter"),
                ("PASS", "anything"),
                ("DID", &next_did),
            ],
        )
        .await;
        assert_eq!(g3.recv().await.get("PERSONAS"), Some("swift-otter2"));
        let _ = lp;
    }

    /// Free ephemeral UDP port for a test relay (mirrors the relay test).
    async fn free_udp_port() -> u16 {
        let probe = tokio::net::UdpSocket::bind("127.0.0.1:0").await.unwrap();
        let port = probe.local_addr().unwrap().port();
        drop(probe);
        port
    }

    /// Raw one-shot HTTP request against a test health listener.
    async fn http_raw(hp: u16, req: &str) -> String {
        let mut s = tokio::net::TcpStream::connect(format!("127.0.0.1:{hp}"))
            .await
            .unwrap();
        s.write_all(req.as_bytes()).await.unwrap();
        let mut out = Vec::new();
        s.read_to_end(&mut out).await.unwrap();
        String::from_utf8_lossy(&out).into_owned()
    }

    async fn boot_health(state: &Arc<State>, lp: u16, gp: u16) -> u16 {
        let l = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let hp = l.local_addr().unwrap().port();
        let qp = Arc::new(Mutex::new(state_sync::QpStatus::default()));
        tokio::spawn(health::serve(l, lp, gp, state.clone(), qp));
        hp
    }

    /// /statusz on a fresh boot reports all-zero counts and idle:true.
    #[tokio::test]
    async fn statusz_idle_when_fresh() {
        let (state, lp, gp, _bp) = boot("off").await;
        let hp = boot_health(&state, lp, gp).await;
        let r = http_raw(hp, "GET /statusz HTTP/1.1\r\nHost: x\r\n\r\n").await;
        assert!(r.starts_with("HTTP/1.1 200"), "{r}");
        assert!(r.ends_with("\"idle\":true}"), "{r}");
        assert!(r.contains("\"tcp_sessions\":0"), "{r}");
        let _ = state;
    }

    /// netsec-fix-1 F3: the health listener takes a per-IP permit before
    /// spawning; the 9th concurrent silent connection from one IP is
    /// dropped instead of spawning a task.
    #[tokio::test]
    async fn health_per_ip_cap_drops_the_ninth_connection() {
        let (state, lp, gp, _bp) = boot("off").await;
        let hp = boot_health(&state, lp, gp).await;
        let mut held = Vec::new();
        for _ in 0..8 {
            held.push(
                tokio::net::TcpStream::connect(format!("127.0.0.1:{hp}"))
                    .await
                    .unwrap(),
            );
        }
        let mut ninth = tokio::net::TcpStream::connect(format!("127.0.0.1:{hp}"))
            .await
            .unwrap();
        let mut buf = [0u8; 16];
        let n = tokio::time::timeout(Duration::from_secs(5), ninth.read(&mut buf))
            .await
            .expect("drop should be fast")
            .unwrap();
        assert_eq!(n, 0);
        drop(held);
    }

    /// netsec-fix-1 F5: the pairer finds the first eligible pair behind a
    /// blocked head instead of stalling the queue.
    #[test]
    fn pick_pair_skips_blocked_head_and_keeps_order() {
        let a: IpAddr = "10.0.0.1".parse().unwrap();
        let b: IpAddr = "10.0.0.2".parse().unwrap();
        let c: IpAddr = "10.0.0.3".parse().unwrap();
        // One pair already on a's IP with cap 1: a1/a2 blocked, b1/c1 pair.
        let w = vec![
            ("a1".to_string(), Some(a)),
            ("a2".to_string(), Some(a)),
            ("b1".to_string(), Some(b)),
            ("c1".to_string(), Some(c)),
        ];
        let (pick, dead) = pick_pair(&w, &[(a, a)], 98, false, 1);
        assert_eq!(pick, Some(("b1".to_string(), "c1".to_string())));
        assert!(dead.is_empty());
        // Eligible head pairs first.
        let w = vec![("b1".to_string(), Some(b)), ("c1".to_string(), Some(c))];
        let (pick, _) = pick_pair(&w, &[], 98, false, 1);
        assert_eq!(pick, Some(("b1".to_string(), "c1".to_string())));
        // Global ceilings block everybody but still report the dead.
        let w = vec![("gone".to_string(), None), ("b1".to_string(), Some(b))];
        let (pick, dead) = pick_pair(&w, &[], 0, false, 1);
        assert_eq!(pick, None);
        assert_eq!(dead, vec!["gone".to_string()]);
        let (pick, _) = pick_pair(&w, &[], 98, true, 1);
        assert_eq!(pick, None);
        // Dead sessions are pruned while the live pair behind them is picked.
        let w = vec![
            ("gone".to_string(), None),
            ("b1".to_string(), Some(b)),
            ("c1".to_string(), Some(c)),
        ];
        let (pick, dead) = pick_pair(&w, &[], 98, false, 1);
        assert_eq!(pick, Some(("b1".to_string(), "c1".to_string())));
        assert_eq!(dead, vec!["gone".to_string()]);
        // Everyone capped: no pick, nothing pruned.
        let w = vec![("a1".to_string(), Some(a)), ("a2".to_string(), Some(a))];
        let (pick, dead) = pick_pair(&w, &[(a, a)], 98, false, 1);
        assert_eq!(pick, None);
        assert!(dead.is_empty());
    }

    /// Health endpoint: 200 + 429 + HEAD-no-body + 404.
    #[tokio::test]
    async fn healthz_statuses_and_rate_limit() {
        let (state, lp, gp, _bp) = boot("off").await;
        let hp = boot_health(&state, lp, gp).await;
        let r = http_raw(hp, "GET /healthz HTTP/1.1\r\nHost: x\r\n\r\n").await;
        assert!(r.starts_with("HTTP/1.1 200"), "{r}");
        assert!(r.ends_with("ok"), "{r}");
        let r = http_raw(hp, "GET /healthz HTTP/1.1\r\n\r\n").await;
        assert!(r.starts_with("HTTP/1.1 429"), "{r}");
        tokio::time::sleep(Duration::from_millis(1100)).await;
        let r = http_raw(hp, "HEAD /healthz HTTP/1.1\r\n\r\n").await;
        assert!(r.starts_with("HTTP/1.1 200"), "{r}");
        assert!(!r.contains("ok"), "{r}");
        tokio::time::sleep(Duration::from_millis(1100)).await;
        let r = http_raw(hp, "GET /nope HTTP/1.0\r\n\r\n").await;
        assert!(r.starts_with("HTTP/1.1 404"), "{r}");
        let _ = state;
    }

    /// Health stays 200 at capacity: the @dir self-check never quiks,
    /// so FULL cannot page as unreachable.
    #[tokio::test]
    async fn healthz_200_when_full() {
        let (mut cfg, ports) = test_cfg("off");
        cfg.limits.max_pairs = 0;
        let (state, lp, gp, _bp) = boot_cfg(cfg, ports).await;
        let hp = boot_health(&state, lp, gp).await;
        let r = http_raw(hp, "GET /healthz HTTP/1.1\r\n\r\n").await;
        assert!(r.starts_with("HTTP/1.1 200"), "{r}");
        let _ = state;
    }

    /// H1 attack repro: a garbage flood on a fresh relay port must not
    /// starve the pair's handshake — both roles still enroll.
    #[tokio::test]
    async fn handshake_survives_garbage_flood() {
        let (mut cfg, _) = test_cfg("always");
        cfg.limits.max_pairs_per_ip = 100;
        cfg.limits.max_queue_per_ip = 100;
        let rport = free_udp_port().await;
        cfg.relay.port_start = rport;
        cfg.relay.port_end = rport;
        let (state, lp, gp, bp) = boot_cfg(cfg, vec![rport]).await;
        let (mut ga, _ba) = login(&state, lp, gp, bp, "hs-a").await;
        let (mut gb, _bb) = login(&state, lp, gp, bp, "hs-b").await;
        let (sa, sb) = pair_up(&mut ga, &mut gb).await;
        let relay = format!("127.0.0.1:{}", sa.get("RLYPORT").unwrap());
        // Rogue flood: 1000 garbage datagrams, no token, no enroll.
        let rogue = tokio::net::UdpSocket::bind("127.0.0.1:0").await.unwrap();
        let junk = [0x5au8; 64];
        for _ in 0..1000 {
            rogue.send_to(&junk, &relay).await.unwrap();
        }
        // Both roles enroll after the flood; challenges must arrive.
        let ua = tokio::net::UdpSocket::bind("127.0.0.1:0").await.unwrap();
        let ub = tokio::net::UdpSocket::bind("127.0.0.1:0").await.unwrap();
        enroll(&ua, &relay, b'0', sa.get("RLYTOK").unwrap()).await;
        enroll(&ub, &relay, b'1', sb.get("RLYTOK").unwrap()).await;
        let _ = (state, ga, gb);
    }

    /// Drive two logins through quik; returns both +ses bodies.
    async fn pair_up(ga: &mut Script, gb: &mut Script) -> (Message, Message) {
        ga.send(types::QUIK, &[("KIND", "DeathRace")]).await;
        let qa = ga.recv().await;
        ga.expect(&qa, "quik");
        gb.send(types::QUIK, &[("KIND", "DeathRace")]).await;
        let qb = gb.recv().await;
        gb.expect(&qb, "quik");
        let sa = ga.recv().await;
        ga.expect(&sa, "+ses");
        let sb = gb.recv().await;
        gb.expect(&sb, "+ses");
        assert_eq!(sa.get("NAME"), sb.get("NAME"));
        (sa, sb)
    }

    /// Family 1 validation: the pair ceiling holds under a second pair's
    /// attempt, and the waiters pair once the first pair's relay reaps.
    #[tokio::test]
    async fn pair_ceiling_holds_and_waiters_recover() {
        let (mut cfg, _) = test_cfg("always");
        cfg.limits.max_pairs = 1;
        // Localhost clients share one IP: lift the per-IP caps to
        // isolate the global pair ceiling.
        cfg.limits.max_pairs_per_ip = 100;
        cfg.limits.max_queue_per_ip = 100;
        cfg.relay.idle_timeout_secs = 1;
        let rport = free_udp_port().await;
        cfg.relay.port_start = rport;
        cfg.relay.port_end = rport;
        let (state, lp, gp, bp) = boot_cfg(cfg, vec![rport]).await;
        let (mut ga, ba) = login(&state, lp, gp, bp, "ceil-a").await;
        let (mut gb, bb) = login(&state, lp, gp, bp, "ceil-b").await;
        let (mut gc, bc) = login(&state, lp, gp, bp, "ceil-c").await;
        let (mut gd, bd) = login(&state, lp, gp, bp, "ceil-d").await;
        let (sa1, sb1) = pair_up(&mut ga, &mut gb).await;
        assert_eq!(sa1.get("NAME"), sb1.get("NAME"));
        assert_eq!(state.pairs.lock().unwrap().len(), 1);
        gc.send(types::QUIK, &[("KIND", "DeathRace")]).await;
        let r = gc.recv().await;
        gc.expect(&r, "quik");
        gd.send(types::QUIK, &[("KIND", "DeathRace")]).await;
        let r = gd.recv().await;
        gd.expect(&r, "quik");
        assert!(tokio::time::timeout(Duration::from_millis(300), gc.recv())
            .await
            .is_err());
        assert!(tokio::time::timeout(Duration::from_millis(300), gd.recv())
            .await
            .is_err());
        assert_eq!(state.queue.lock().unwrap().len(), 2);
        assert_eq!(state.pairs.lock().unwrap().len(), 1);
        // Drop the first pair; its relay reaps on the 1 s idle deadline
        // and the queued waiters take the recycled port.
        drop(ga);
        drop(gb);
        drop(ba);
        drop(bb);
        let sa = tokio::time::timeout(Duration::from_secs(8), gc.recv())
            .await
            .expect("queued waiter never paired");
        gc.expect(&sa, "+ses");
        let sb = tokio::time::timeout(Duration::from_secs(8), gd.recv())
            .await
            .expect("queued waiter never paired");
        gd.expect(&sb, "+ses");
        assert_eq!(sa.get("NAME"), sb.get("NAME"));
        assert_eq!(state.queue.lock().unwrap().len(), 0);
        assert_eq!(state.pairs.lock().unwrap().len(), 1);
        drop((gc, gd, bc, bd));
    }

    /// Family 2 validation: a flooded pair is capped while a neighbor
    /// pair's trickle delivers fully (per-pair buckets isolate abuse).
    #[tokio::test]
    async fn flood_is_capped_and_neighbor_pair_unaffected() {
        let (mut cfg, _) = test_cfg("always");
        cfg.limits.max_pairs_per_ip = 100;
        cfg.limits.max_queue_per_ip = 100;
        cfg.relay.packets_per_sec = 40.0;
        cfg.relay.burst_packets = 80.0;
        cfg.relay.per_role_per_sec = 20.0;
        cfg.relay.per_role_burst = 40.0;
        cfg.relay.bytes_per_sec_per_pair = 1.0e9;
        cfg.relay.burst_bytes_per_pair = 1.0e9;
        cfg.relay.global_bytes_per_sec = 1.0e9;
        cfg.relay.global_burst_bytes = 1.0e9;
        let p1 = free_udp_port().await;
        let mut p2 = free_udp_port().await;
        while p2 == p1 {
            p2 = free_udp_port().await;
        }
        cfg.relay.port_start = p1.min(p2);
        cfg.relay.port_end = p1.max(p2);
        let (state, lp, gp, bp) = boot_cfg(cfg, vec![p1, p2]).await;
        let (mut ga, _ba) = login(&state, lp, gp, bp, "fld-a").await;
        let (mut gb, _bb) = login(&state, lp, gp, bp, "fld-b").await;
        let (mut gc, _bc) = login(&state, lp, gp, bp, "fld-c").await;
        let (mut gd, _bd) = login(&state, lp, gp, bp, "fld-d").await;
        let (sa, sb) = pair_up(&mut ga, &mut gb).await;
        let (sc, sd) = pair_up(&mut gc, &mut gd).await;
        assert_ne!(sa.get("RLYPORT"), sc.get("RLYPORT"));
        let r1 = format!("127.0.0.1:{}", sa.get("RLYPORT").unwrap());
        let r2 = format!("127.0.0.1:{}", sc.get("RLYPORT").unwrap());
        let ua = tokio::net::UdpSocket::bind("127.0.0.1:0").await.unwrap();
        let ub = tokio::net::UdpSocket::bind("127.0.0.1:0").await.unwrap();
        let uc = tokio::net::UdpSocket::bind("127.0.0.1:0").await.unwrap();
        let ud = tokio::net::UdpSocket::bind("127.0.0.1:0").await.unwrap();
        enroll(&ua, &r1, b'0', sa.get("RLYTOK").unwrap()).await;
        enroll(&ub, &r1, b'1', sb.get("RLYTOK").unwrap()).await;
        enroll(&uc, &r2, b'0', sc.get("RLYTOK").unwrap()).await;
        enroll(&ud, &r2, b'1', sd.get("RLYTOK").unwrap()).await;
        // Flood pair 1 A->B: 300 x 32 B back-to-back. The per-role
        // burst (40) binds first; the rest must drop.
        let flood = [
            b'O', b'5', b'P', b'K', 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17,
            18, 19, 20, 21, 22, 23, 24, 25, 26, 27,
        ];
        for _ in 0..300 {
            ua.send_to(&flood, &r1).await.unwrap();
        }
        // Trickle pair 2 at 50 pps x 10: every one must arrive.
        for i in 0..10u8 {
            let mut pkt = *b"O5KA....................";
            pkt[4] = i;
            uc.send_to(&pkt, &r2).await.unwrap();
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
        let mut tmp = [0u8; 2048];
        let mut got1 = 0;
        loop {
            match tokio::time::timeout(Duration::from_millis(300), ub.recv_from(&mut tmp)).await {
                Ok(Ok((n, _))) => {
                    assert_eq!(&tmp[..n], &flood);
                    got1 += 1;
                }
                _ => break,
            }
        }
        let mut got2 = 0;
        for _ in 0..10 {
            let (n, _) = tokio::time::timeout(Duration::from_secs(2), ud.recv_from(&mut tmp))
                .await
                .expect("neighbor trickle lost a packet")
                .unwrap();
            assert_eq!(n, 24);
            got2 += 1;
        }
        // Ceiling held on the flooded pair (burst + small refill only)
        // while the neighbor delivered 10/10.
        assert!((10..=90).contains(&got1), "flooded pair got {got1}");
        assert_eq!(got2, 10);
        let _ = (state, ga, gb, gc, gd);
    }

    /// Family 3 validation: the per-pair byte bucket caps a large-payload
    /// flood, egress accounting matches exactly, and the monthly cap then
    /// gates new admissions.
    #[tokio::test]
    async fn byte_ceiling_caps_flood_and_monthly_cap_gates_admission() {
        let (mut cfg, _) = test_cfg("always");
        cfg.limits.max_pairs_per_ip = 100;
        cfg.limits.max_queue_per_ip = 100;
        cfg.relay.packets_per_sec = 10000.0;
        cfg.relay.burst_packets = 10000.0;
        cfg.relay.per_role_per_sec = 10000.0;
        cfg.relay.per_role_burst = 10000.0;
        cfg.relay.bytes_per_sec_per_pair = 200.0;
        cfg.relay.burst_bytes_per_pair = 400.0;
        cfg.relay.global_bytes_per_sec = 1.0e9;
        cfg.relay.global_burst_bytes = 1.0e9;
        cfg.relay.monthly_egress_cap_bytes = 100;
        let rport = free_udp_port().await;
        cfg.relay.port_start = rport;
        cfg.relay.port_end = rport;
        let (state, lp, gp, bp) = boot_cfg(cfg, vec![rport]).await;
        let (mut ga, _ba) = login(&state, lp, gp, bp, "byt-a").await;
        let (mut gb, _bb) = login(&state, lp, gp, bp, "byt-b").await;
        let (sa, sb) = pair_up(&mut ga, &mut gb).await;
        let relay = format!("127.0.0.1:{}", sa.get("RLYPORT").unwrap());
        let ua = tokio::net::UdpSocket::bind("127.0.0.1:0").await.unwrap();
        let ub = tokio::net::UdpSocket::bind("127.0.0.1:0").await.unwrap();
        enroll(&ua, &relay, b'0', sa.get("RLYTOK").unwrap()).await;
        enroll(&ub, &relay, b'1', sb.get("RLYTOK").unwrap()).await;
        // 104 B payloads cost 132 B each against a 400 B burst: ~3 pass.
        let mut big = vec![b'O', b'5', b'P', b'K'];
        big.extend(std::iter::repeat(0xabu8).take(100));
        for _ in 0..30 {
            ua.send_to(&big, &relay).await.unwrap();
        }
        let mut tmp = [0u8; 2048];
        let mut got = 0u64;
        loop {
            match tokio::time::timeout(Duration::from_millis(300), ub.recv_from(&mut tmp)).await {
                Ok(Ok((n, _))) => {
                    assert_eq!(n, big.len());
                    got += 1;
                }
                _ => break,
            }
        }
        assert!((1..=6).contains(&got), "byte-capped flood got {got}");
        tokio::time::sleep(Duration::from_millis(100)).await;
        assert_eq!(state.egress_bytes_total.load(Ordering::Relaxed), got * 132);
        // The monthly cap (100 B) is now exceeded: a second pair stays
        // queued instead of admitting.
        let (mut gc, _bc) = login(&state, lp, gp, bp, "byt-c").await;
        let (mut gd, _bd) = login(&state, lp, gp, bp, "byt-d").await;
        gc.send(types::QUIK, &[("KIND", "DeathRace")]).await;
        let r = gc.recv().await;
        gc.expect(&r, "quik");
        gd.send(types::QUIK, &[("KIND", "DeathRace")]).await;
        let r = gd.recv().await;
        gd.expect(&r, "quik");
        assert!(tokio::time::timeout(Duration::from_millis(300), gc.recv())
            .await
            .is_err());
        assert_eq!(state.queue.lock().unwrap().len(), 2);
        assert_eq!(state.pairs.lock().unwrap().len(), 1);
        let _ = (ga, gb, gc, gd);
    }

    /// H2 attack repro: drop-and-repair cycling must neither evade the
    /// per-IP ceiling nor hold the port to the idle deadline. After both
    /// sessions drop, the stored-IP count still sees the pair and the
    /// relay reaps promptly; the next pair recycles the same port.
    #[tokio::test]
    async fn pair_slots_recycle_when_sessions_drop() {
        let (mut cfg, _) = test_cfg("always");
        cfg.limits.max_pairs = 1;
        cfg.limits.max_pairs_per_ip = 100;
        cfg.limits.max_queue_per_ip = 100;
        // Long idle: the reap below must come from the session check.
        cfg.relay.idle_timeout_secs = 60;
        let rport = free_udp_port().await;
        cfg.relay.port_start = rport;
        cfg.relay.port_end = rport;
        let (state, lp, gp, bp) = boot_cfg(cfg, vec![rport]).await;
        let (mut ga, ba) = login(&state, lp, gp, bp, "cyc-a").await;
        let (mut gb, bb) = login(&state, lp, gp, bp, "cyc-b").await;
        let (sa1, _) = pair_up(&mut ga, &mut gb).await;
        assert_eq!(sa1.get("RLYPORT"), Some(rport.to_string()).as_deref());
        assert_eq!(state.pairs.lock().unwrap().len(), 1);
        let home: IpAddr = "127.0.0.1".parse().unwrap();
        assert_eq!(count_pairs_on_ip(&state.pairs.lock().unwrap(), home), 1);
        drop(ga);
        drop(gb);
        drop(ba);
        drop(bb);
        // Still counted while the relay lives (stored formation IPs).
        assert_eq!(count_pairs_on_ip(&state.pairs.lock().unwrap(), home), 1);
        // Relay reaps on the session check, far ahead of the 60 s idle.
        let mut reaped = false;
        for _ in 0..150 {
            if state.pairs.lock().unwrap().is_empty()
                && state.relay_ports.lock().unwrap().contains(&rport)
            {
                reaped = true;
                break;
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
        assert!(reaped, "relay held the port after both sessions dropped");
        assert_eq!(count_pairs_on_ip(&state.pairs.lock().unwrap(), home), 0);
        let (mut gc, _bc) = login(&state, lp, gp, bp, "cyc-c").await;
        let (mut gd, _bd) = login(&state, lp, gp, bp, "cyc-d").await;
        let (sa2, _) = pair_up(&mut gc, &mut gd).await;
        assert_eq!(sa2.get("RLYPORT"), Some(rport.to_string()).as_deref());
        let _ = (gc, gd);
    }

    /// Server-full signal: with max_pairs=0 every quik echo carries
    /// FULL=1 and nobody pairs; waiters stay queued for retry.
    #[tokio::test]
    async fn server_full_flag_when_pair_ceiling_zero() {
        let (mut cfg, ports) = test_cfg("off");
        cfg.limits.max_pairs = 0;
        let (state, lp, gp, bp) = boot_cfg(cfg, ports).await;
        let (mut ga, _ba) = login(&state, lp, gp, bp, "full-a").await;
        let (mut gb, _bb) = login(&state, lp, gp, bp, "full-b").await;
        ga.send(types::QUIK, &[("KIND", "DeathRace")]).await;
        let ea = ga.recv().await;
        ga.expect(&ea, "quik");
        assert_eq!(ea.get("FULL"), Some("1"));
        gb.send(types::QUIK, &[("KIND", "DeathRace")]).await;
        let eb = gb.recv().await;
        gb.expect(&eb, "quik");
        assert_eq!(eb.get("FULL"), Some("1"));
        assert!(tokio::time::timeout(Duration::from_millis(300), ga.recv())
            .await
            .is_err());
        assert_eq!(state.queue.lock().unwrap().len(), 2);
        assert!(state.pairs.lock().unwrap().is_empty());
        let _ = (ga, gb);
    }

    /// M2 attack repro (sessions): 16 IPs x 16 conns must not be able
    /// to hold all 256 names. With max_sessions_per_ip=2, the third
    /// same-IP login is refused and the name table stays at 2.
    #[tokio::test]
    async fn sessions_per_ip_cap_refuses_overflow() {
        let (mut cfg, _) = test_cfg("off");
        cfg.limits.max_sessions_per_ip = 2;
        let (state, lp, gp, bp) = boot_cfg(cfg, vec![]).await;
        let (_g1, _b1) = login(&state, lp, gp, bp, "sess-a").await;
        let (_g2, _b2) = login(&state, lp, gp, bp, "sess-b").await;
        assert_eq!(state.names.lock().unwrap().len(), 2);
        let mut g3 = Script::connect(gp).await;
        let did = new_hex(32);
        g3.send(
            types::AUTH,
            &[("NAME", "sess-c"), ("PASS", "x"), ("DID", &did)],
        )
        .await;
        let mut tmp = [0u8; 64];
        assert_eq!(
            tokio::time::timeout(Duration::from_secs(5), g3.stream.read(&mut tmp))
                .await
                .expect("no close after per-IP session refusal")
                .unwrap(),
            0
        );
        assert_eq!(state.names.lock().unwrap().len(), 2);
        let _ = (state, lp, bp);
    }

    /// M2 attack repro (idle): an unauthenticated game connection must
    /// not idle to the 240 s close. It is shut within ~10 s.
    #[tokio::test]
    async fn unauthenticated_game_conn_closes_fast() {
        let (state, _lp, gp, _bp) = boot("off").await;
        let mut conn = Script::connect(gp).await;
        let mut tmp = [0u8; 64];
        let n = tokio::time::timeout(Duration::from_secs(20), conn.stream.read(&mut tmp))
            .await
            .expect("unauthenticated game conn still open after 20 s")
            .expect("read failed");
        assert_eq!(n, 0, "server wrote to an unauthenticated conn");
        let _ = state;
    }

    /// M1 invariant: the global egress ceiling covers a full house at
    /// per-pair maxima, so one pair's abuse (or 99 honest racers) can
    /// never throttle another pair through the shared bucket. The
    /// monthly cap stays the real backstop.
    #[test]
    fn global_ceiling_covers_full_house() {
        let cfg = Config::default();
        assert!(
            cfg.relay.global_bytes_per_sec
                >= cfg.limits.max_pairs as f64 * cfg.relay.bytes_per_sec_per_pair,
            "global {} < {} pairs x {} B/s",
            cfg.relay.global_bytes_per_sec,
            cfg.limits.max_pairs,
            cfg.relay.bytes_per_sec_per_pair,
        );
    }

    #[test]
    fn minimal_config_serves_four_rooms() {
        // Regression: a config without [[rooms]] must still serve the
        // 4 default rooms (the client stalls waiting for the list).
        let cfg: Config = toml::from_str(
            "[lobby]\nadvertise_ip = \"127.0.0.1\"\n[ports]\nlobby = 1\ngame = 2\nbuddy = 3\n",
        )
        .unwrap();
        assert_eq!(cfg.rooms.len(), 4);
        assert_eq!(cfg.rooms[0].name, "Beginner");
    }

    /// lobby-load-1: the STATE port is a fixed dedicated port just below
    /// the relay range (3657), the range starts at the stock P2P port
    /// (3658), and legacy transition ports can be added.
    #[test]
    fn state_sync_fixed_port_below_relay_range() {
        let cfg = Config::default();
        assert_eq!(cfg.state_sync.port, 3657);
        assert_eq!(cfg.relay.port_start, 3658);
        assert!(cfg.state_sync.port < cfg.relay.port_start);
        assert!(cfg.state_sync.legacy_ports.is_empty());
        let cfg2: Config = toml::from_str(
            "[lobby]\nadvertise_ip = \"127.0.0.1\"\n[state_sync]\nenable = true\nport = 3657\nlegacy_ports = [3757]\n",
        )
        .unwrap();
        assert_eq!(cfg2.state_sync.port, 3657);
        assert_eq!(cfg2.state_sync.legacy_ports, vec![3757]);
    }

    /// lobby-load-1: the relay pool never includes a reserved listener
    /// port (lobby/game/buddy/health or the fixed/legacy STATE ports) and
    /// keeps ascending allocation from `port_start`.
    #[test]
    fn relay_pool_skips_reserved_and_state_ports() {
        let mut cfg = Config::default();
        cfg.relay.port_start = 3657;
        cfg.relay.port_end = 3660;
        cfg.state_sync.port = 3659;
        cfg.state_sync.legacy_ports = vec![3658];
        let pool = relay_pool(&cfg);
        assert_eq!(pool, vec![3660, 3657]); // stack: pop() = 3657 first
        assert!(!pool.contains(&3659));
        assert!(!pool.contains(&3658));
        let mut p = pool.clone();
        assert_eq!(p.pop(), Some(3657));
        assert_eq!(p.pop(), Some(3660));
    }

    /// lobby-load-1: a 1000-pair house must fit the configured admission
    /// caps and FD budget, and the egress ceiling must cover it at
    /// per-pair maxima (the M1 invariant, rescaled with max_pairs).
    #[test]
    fn prod_scale_caps_cover_a_thousand_pairs() {
        let cfg: Config = toml::from_str(include_str!("../../../lobby.toml")).unwrap();
        assert!(cfg.limits.max_pairs >= 990, "max_pairs {}", cfg.limits.max_pairs);
        // 2 players per pair, ~3 TCP conns each, plus relay sockets.
        assert!(cfg.limits.max_connections >= cfg.limits.max_pairs * 2 * 3);
        assert!(cfg.limits.max_sessions >= cfg.limits.max_pairs * 2);
        assert!(cfg.limits.max_queue >= cfg.limits.max_pairs * 2);
        assert!(
            cfg.relay.global_bytes_per_sec
                >= cfg.limits.max_pairs as f64 * cfg.relay.bytes_per_sec_per_pair,
            "global egress {} < full house",
            cfg.relay.global_bytes_per_sec
        );
        // The relay range must actually hold max_pairs+1 ports (3658 first).
        assert!(
            (cfg.relay.port_end - cfg.relay.port_start) as usize + 1 > cfg.limits.max_pairs,
            "relay range too small for max_pairs"
        );
    }

    #[tokio::test]
    async fn buddy_auth_unknown_user_closed() {
        let (_state, _lp, _gp, bp) = boot("off").await;
        let mut b = Script::connect(bp).await;
        b.send(types::DISC, &[]).await;
        let _ = b.recv().await;
        b.send(
            types::BAUTH,
            &[("USER", "nosuchuser/cso/SSX-PS2-2004"), ("PASS", "aaaa")],
        )
        .await;
        // No reply: connection closed (observed §6.1).
        let mut tmp = [0u8; 64];
        let n = tokio::time::timeout(Duration::from_secs(5), b.stream.read(&mut tmp))
            .await
            .expect("timeout waiting for unknown-user close");
        assert_eq!(n.unwrap(), 0);
    }

    /// lobby-buddy-1: the buddy close was hardcoded 300 s; it is now a
    /// `[limits]` knob next to the grace for post-pair cover.
    #[test]
    fn buddy_close_and_grace_knobs() {
        let cfg = Config::default();
        assert_eq!(cfg.limits.buddy_close_secs, 300);
        assert_eq!(cfg.limits.paired_grace_secs, 300);
        let cfg: Config = toml::from_str(
            "[lobby]\nadvertise_ip = \"127.0.0.1\"\n[limits]\nbuddy_close_secs = 7\npaired_grace_secs = 9\n",
        )
        .unwrap();
        assert_eq!(cfg.limits.buddy_close_secs, 7);
        assert_eq!(cfg.limits.paired_grace_secs, 9);
        assert_eq!(cfg.limits.idle_close_secs, 240);
    }

    /// lobby-buddy-1: cover holds while the pair record is live,
    /// survives the relay task's end through the grace, then lapses.
    #[test]
    fn pair_cover_live_pair_then_grace_then_expiry() {
        let (mut cfg, _) = test_cfg("off");
        cfg.limits.paired_grace_secs = 1;
        let state = build_state(cfg, vec![], "testkey".into());
        assert!(!state.pair_cover("solo"));
        state.pairs.lock().unwrap().insert(
            "s1".into(),
            Pair {
                name: "s1".into(),
                a: "cov-a".into(),
                b: "cov-b".into(),
                host: "cov-a".into(),
                seed: 1,
                when: "t".into(),
                token: None,
                relay_tokens: None,
                relay_port: None,
                a_ip: "127.0.0.1".parse().unwrap(),
                b_ip: "127.0.0.1".parse().unwrap(),
            },
        );
        assert!(state.pair_cover("cov-a"));
        assert!(state.pair_cover("cov-b"));
        assert!(!state.pair_cover("solo"));
        state.pairs.lock().unwrap().remove("s1");
        state.note_pair_ended(&["cov-a".to_string(), "cov-b".to_string()]);
        assert!(state.pair_cover("cov-a"));
        assert!(state.pair_cover("cov-b"));
        std::thread::sleep(Duration::from_millis(1100));
        assert!(!state.pair_cover("cov-a"));
        assert!(!state.pair_cover("cov-b"));
    }

    /// lobby-buddy-1: a paired buddy session survives past
    /// buddy_close_secs of total silence, while an unpaired bystander
    /// is still reaped on the same deadline.
    #[tokio::test]
    async fn paired_buddy_survives_close_while_unpaired_reaped() {
        let (mut cfg, _) = test_cfg("always");
        cfg.limits.buddy_close_secs = 2;
        // Localhost clients share one IP: lift the per-IP caps to
        // isolate the close behavior.
        cfg.limits.max_pairs_per_ip = 100;
        cfg.limits.max_queue_per_ip = 100;
        let rport = free_udp_port().await;
        cfg.relay.port_start = rport;
        cfg.relay.port_end = rport;
        let (state, lp, gp, bp) = boot_cfg(cfg, vec![rport]).await;
        let (mut ga, mut ba) = login(&state, lp, gp, bp, "bud-a").await;
        let (mut gb, _bb) = login(&state, lp, gp, bp, "bud-b").await;
        let (_gc, mut bc) = login(&state, lp, gp, bp, "bud-c").await;
        pair_up(&mut ga, &mut gb).await;
        // Past the 2 s close with total buddy-TCP silence.
        tokio::time::sleep(Duration::from_millis(3000)).await;
        // Paired side still answers...
        ba.send(types::PING, &[]).await;
        let r = ba.recv().await;
        ba.expect(&r, "PING");
        // ...while the unpaired bystander was reaped.
        let mut tmp = [0u8; 64];
        let n = tokio::time::timeout(Duration::from_secs(5), bc.stream.read(&mut tmp))
            .await
            .expect("unpaired buddy still open past buddy_close_secs");
        assert_eq!(n.unwrap(), 0, "unpaired buddy got bytes instead of close");
    }

    /// lobby-buddy-1: a paired game session survives past
    /// idle_close_secs of TCP silence (stock answers the PNG probe only
    /// from the lobby pump, which does not run mid-race), while an
    /// unpaired bystander is still reaped.
    #[tokio::test]
    async fn paired_game_survives_idle_close_while_unpaired_reaped() {
        let (mut cfg, _) = test_cfg("always");
        cfg.limits.idle_close_secs = 2;
        // No PNG probes: the reap under test is the silence close, and
        // queued probes would sit ahead of the rank echo below.
        cfg.limits.idle_probe_secs = 3600;
        cfg.limits.max_pairs_per_ip = 100;
        cfg.limits.max_queue_per_ip = 100;
        let rport = free_udp_port().await;
        cfg.relay.port_start = rport;
        cfg.relay.port_end = rport;
        let (state, lp, gp, bp) = boot_cfg(cfg, vec![rport]).await;
        let (mut ga, _ba) = login(&state, lp, gp, bp, "gam-a").await;
        let (mut gb, _bb) = login(&state, lp, gp, bp, "gam-b").await;
        let (mut gc, _bc) = login(&state, lp, gp, bp, "gam-c").await;
        pair_up(&mut ga, &mut gb).await;
        // Past the 30 s authenticated read quantum with total game-TCP
        // silence: paired sides keep cover, the bystander is reaped.
        tokio::time::sleep(Duration::from_secs(36)).await;
        ga.send(
            types::RANK,
            &[("WHEN", "2026.10.18 00:00:00"), ("REPT", "gam-a")],
        )
        .await;
        let r = ga.recv().await;
        ga.expect(&r, "rank");
        let mut tmp = [0u8; 64];
        let n = tokio::time::timeout(Duration::from_secs(5), gc.stream.read(&mut tmp))
            .await
            .expect("unpaired game conn still open past idle_close_secs");
        assert_eq!(n.unwrap(), 0, "unpaired game conn got bytes instead of close");
    }

    /// online-lone-1: a lone queued searcher gets a game-socket keepalive
    /// inside the guest's ~3600-tick silence budget, while an
    /// authenticated bystander that never queued sees no fast probe.
    /// (The stock client drops a search with no inbound game bytes for
    /// ~3600 ticks; the generic 90 s probe lands too late at 60 Hz.)
    #[tokio::test]
    async fn lone_waiter_gets_fast_probe_while_bystander_waits() {
        let (mut cfg, _) = test_cfg("always");
        cfg.limits.waiter_probe_secs = 2;
        cfg.limits.idle_probe_secs = 3600;
        cfg.limits.idle_close_secs = 3600;
        cfg.limits.max_queue_per_ip = 100;
        let rport = free_udp_port().await;
        cfg.relay.port_start = rport;
        cfg.relay.port_end = rport;
        let (state, lp, gp, bp) = boot_cfg(cfg, vec![rport]).await;
        let (mut ga, _ba) = login(&state, lp, gp, bp, "lone-a").await;
        let (mut gc, _bc) = login(&state, lp, gp, bp, "byst-c").await;
        // A queues a lone search; C browses (authenticated, never quiks).
        ga.send(types::QUIK, &[("KIND", "DeathRace")]).await;
        let q = ga.recv().await;
        ga.expect(&q, "quik");
        // The probe fires on the first 30 s authenticated read quantum
        // once the waiter probe has expired. Script::recv panics after 5
        // silent seconds, so pump the raw stream here until one full
        // quantum plus margin passes.
        let deadline = Instant::now() + Duration::from_secs(45);
        let mut got_png = false;
        while !got_png && Instant::now() < deadline {
            let mut tmp = [0u8; 8192];
            match tokio::time::timeout(Duration::from_secs(4), ga.stream.read(&mut tmp)).await {
                Ok(Ok(0)) => break,
                Ok(Ok(n)) => ga.buf.extend_from_slice(&tmp[..n]),
                _ => continue,
            }
            if ga.buf.len() >= 12 {
                let size =
                    u32::from_be_bytes([ga.buf[8], ga.buf[9], ga.buf[10], ga.buf[11]]) as usize;
                if ga.buf.len() >= size {
                    let wire = ga.buf[..size].to_vec();
                    ga.buf.drain(..size);
                    let (m, _) = ssx3_proto::decode_one(&wire).unwrap();
                    got_png = m.type_str() == Some("~png");
                }
            }
        }
        assert!(got_png, "lone waiter got no probe inside waiter_probe_secs");
        // ...while the bystander sees nothing on the same timescale...
        let mut tmp = [0u8; 64];
        let idle = tokio::time::timeout(Duration::from_secs(4), gc.stream.read(&mut tmp)).await;
        assert!(idle.is_err(), "bystander got the fast probe too");
        // ...and A is still queued, never paired, never closed.
        assert!(state.queue.lock().unwrap().contains(&"lone-a".to_string()));
        assert!(state.pairs.lock().unwrap().is_empty());
    }
}

fn serve(
    listener: TcpListener,
    state: Arc<State>,
    kind: &'static str,
) -> tokio::task::JoinHandle<()> {
    tokio::spawn(async move {
        loop {
            let (stream, peer) = match listener.accept().await {
                Ok(x) => x,
                Err(_e) => {
                    warn!("accept failed");
                    continue;
                }
            };
            if !state.conn_open(peer.ip()) {
                warn!(kind, "connection cap exceeded; dropping");
                drop(stream);
                continue;
            }
            let state = state.clone();
            let permit = ConnectionPermit {
                state: state.clone(),
                ip: peer.ip(),
            };
            let task = tokio::spawn(async move {
                let _permit = permit;
                match kind {
                    "lobby" => handle_lobby(state.clone(), stream, peer).await,
                    "game" => handle_game(state.clone(), stream, peer).await,
                    _ => handle_buddy(state.clone(), stream, peer).await,
                }
            });
            tokio::spawn(async move {
                if let Err(err) = task.await {
                    warn!(kind, panic = err.is_panic(), "connection task failed");
                }
            });
        }
    })
}
