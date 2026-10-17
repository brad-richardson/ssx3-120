//! Minimal HTTP health endpoint for external monitors (UptimeRobot
//! free tier does HEAD only, so the status code is the whole signal).
//!
//! `HEAD`/`GET /healthz` returns 200 when the internal @dir self-check
//! passes — including at capacity/FULL, since matchmaking pressure never
//! touches @dir — 503 when it fails, and 404 elsewhere. One request
//! line is parsed; no body is ever read. Each IP is limited to one
//! request per second (429 beyond that).

use std::collections::{HashMap, VecDeque};
use std::net::{IpAddr, SocketAddr};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};

use crate::state_sync::QpStatus;
use crate::State;

/// Pure status classifier (unit-tested, including the 503 branch).
pub fn classify(method: &str, path: &str, selfcheck_ok: bool) -> u16 {
    if (method == "HEAD" || method == "GET") && path == "/healthz" {
        if selfcheck_ok {
            200
        } else {
            503
        }
    } else if (method == "HEAD" || method == "GET") && path == "/statusz" {
        200
    } else {
        404
    }
}

/// `/statusz` readout (Part 7): counts only — no names, addresses or
/// tokens. `idle` is true only when every count is zero.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct Statusz {
    pub tcp_sessions: usize,
    pub qp_queued: usize,
    pub qp_rooms: usize,
    pub slots_alive: usize,
}

impl Statusz {
    pub fn idle(&self) -> bool {
        self.tcp_sessions == 0
            && self.qp_queued == 0
            && self.qp_rooms == 0
            && self.slots_alive == 0
    }

    pub fn json(&self) -> String {
        format!(
            "{{\"tcp_sessions\":{},\"qp_queued\":{},\"qp_rooms\":{},\"slots_alive\":{},\"idle\":{}}}",
            self.tcp_sessions, self.qp_queued, self.qp_rooms, self.slots_alive,
            self.idle()
        )
    }

    fn assemble(state: &State, qp: &Mutex<QpStatus>) -> Self {
        let q = *qp.lock().unwrap();
        Self {
            tcp_sessions: state.tcp_sessions(),
            qp_queued: q.queued,
            qp_rooms: q.rooms,
            slots_alive: q.slots_alive,
        }
    }
}

fn reason(code: u16) -> &'static str {
    match code {
        200 => "OK",
        404 => "Not Found",
        429 => "Too Many Requests",
        503 => "Service Unavailable",
        _ => "Unknown",
    }
}

fn body(code: u16, method: &str) -> &'static str {
    if method == "HEAD" {
        return "";
    }
    match code {
        200 => "ok",
        404 => "not found",
        429 => "slow down",
        503 => "unhealthy",
        _ => "",
    }
}

/// One-request-per-second gate per IP. Bounded: at most 1024 tracked
/// IPs, oldest first out past that.
#[derive(Default)]
pub struct Gate {
    last: Mutex<HashMap<IpAddr, (Instant, VecDeque<Instant>)>>,
}

impl Gate {
    /// True when `ip` may proceed (records the hit).
    pub fn allow(&self, ip: IpAddr) -> bool {
        let mut map = self.last.lock().unwrap();
        let now = Instant::now();
        if map.len() >= 1024 {
            map.retain(|_, (t, _)| now.duration_since(*t) <= Duration::from_secs(60));
            if map.len() >= 1024 {
                if let Some(oldest) = map
                    .iter()
                    .max_by_key(|(_, (t, _))| std::cmp::Reverse(*t))
                    .map(|(k, _)| *k)
                {
                    map.remove(&oldest);
                }
            }
        }
        let entry = map.entry(ip).or_insert_with(|| (now, VecDeque::new()));
        entry.0 = now;
        let q = &mut entry.1;
        while q
            .front()
            .map(|t| now.duration_since(*t) > Duration::from_secs(1))
            .unwrap_or(false)
        {
            q.pop_front();
        }
        if q.len() >= 1 {
            return false;
        }
        q.push_back(now);
        true
    }
}

/// Self-check: an @dir exchange against the lobby's own directory port.
/// Exercises the accept path and the login flow entry, exactly like the
/// external probe, but over loopback.
pub async fn self_check(lobby_port: u16, game_port: u16) -> bool {
    let addr = SocketAddr::from(([127, 0, 0, 1], lobby_port));
    let mut stream =
        match tokio::time::timeout(Duration::from_secs(2), TcpStream::connect(addr)).await {
            Ok(Ok(s)) => s,
            _ => return false,
        };
    let body = b"PROD=SSX-PS2-2004\nLANG=en\n\x00";
    let mut frame = Vec::with_capacity(12 + body.len());
    frame.extend_from_slice(b"@dir");
    frame.extend_from_slice(&[0, 0, 0, 0]);
    frame.extend_from_slice(&((12 + body.len()) as u32).to_be_bytes());
    frame.extend_from_slice(body);
    if tokio::time::timeout(Duration::from_secs(2), stream.write_all(&frame))
        .await
        .is_err()
    {
        return false;
    }
    let mut buf = [0u8; 512];
    let n = match tokio::time::timeout(Duration::from_secs(2), stream.read(&mut buf)).await {
        Ok(Ok(n)) => n,
        _ => return false,
    };
    n >= 12
        && &buf[..4] == b"@dir"
        && buf[..n]
            .windows(format!("PORT={game_port}").len())
            .any(|w| w == format!("PORT={game_port}").as_bytes())
}

async fn handle(
    mut stream: TcpStream,
    peer: IpAddr,
    gate: Arc<Gate>,
    lobby_port: u16,
    game_port: u16,
    state: Arc<State>,
    qp: Arc<Mutex<QpStatus>>,
) {
    let mut buf = [0u8; 1024];
    let n = match tokio::time::timeout(Duration::from_secs(5), stream.read(&mut buf)).await {
        Ok(Ok(n)) => n,
        _ => return,
    };
    let line = String::from_utf8_lossy(&buf[..n]);
    let mut parts = line.split_whitespace();
    let (method, path) = (parts.next().unwrap_or(""), parts.next().unwrap_or(""));
    let get_or_head = method == "HEAD" || method == "GET";
    let (code, text) = if !gate.allow(peer) {
        (429, body(429, method).to_owned())
    } else if get_or_head && path == "/statusz" {
        let text = if method == "GET" {
            Statusz::assemble(&state, &qp).json()
        } else {
            String::new()
        };
        (200, text)
    } else {
        let c = if get_or_head && path == "/healthz" {
            classify(method, path, self_check(lobby_port, game_port).await)
        } else {
            classify(method, path, false)
        };
        (c, body(c, method).to_owned())
    };
    let reply = format!(
        "HTTP/1.1 {code} {}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{text}",
        reason(code),
        text.len()
    );
    let _ = stream.write_all(reply.as_bytes()).await;
}

/// Health-listener admission (netsec-fix-1 F3): a global + per-IP cap on
/// concurrent health tasks, taken before spawning. Deliberately separate
/// from the game/buddy pool: health checks must consume neither game slots
/// nor `/statusz`'s `tcp_sessions` (the `--when-idle` deploy gate polls
/// `/statusz`, so its own connection must read idle:true on a quiet lobby).
#[derive(Default)]
struct Caps {
    total: usize,
    per_ip: HashMap<IpAddr, usize>,
}

const HEALTH_MAX_TOTAL: usize = 64;
const HEALTH_MAX_PER_IP: usize = 8;

/// Connection permit: released back to the health pool when the task ends.
struct HealthPermit {
    caps: Arc<Mutex<Caps>>,
    ip: IpAddr,
}

impl Drop for HealthPermit {
    fn drop(&mut self) {
        let mut caps = self.caps.lock().unwrap();
        caps.total = caps.total.saturating_sub(1);
        if let Some(n) = caps.per_ip.get(&self.ip).copied() {
            if n <= 1 {
                caps.per_ip.remove(&self.ip);
            } else {
                caps.per_ip.insert(self.ip, n - 1);
            }
        }
    }
}

/// Serve health forever on `listener` (one task per connection).
/// netsec-fix-1 F3: a global + per-IP connection permit is taken before
/// spawning, transient accept errors no longer terminate the listener, and
/// silent connections keep only the 5 s first-read timeout.
pub async fn serve(
    listener: TcpListener,
    lobby_port: u16,
    game_port: u16,
    state: Arc<State>,
    qp: Arc<Mutex<QpStatus>>,
) {
    let gate = Arc::new(Gate::default());
    let caps = Arc::new(Mutex::new(Caps::default()));
    loop {
        let (stream, addr) = match listener.accept().await {
            Ok(x) => x,
            Err(_) => continue,
        };
        let ip = addr.ip();
        let admitted = {
            let mut caps = caps.lock().unwrap();
            let n = caps.per_ip.get(&ip).copied().unwrap_or(0);
            if caps.total >= HEALTH_MAX_TOTAL || n >= HEALTH_MAX_PER_IP {
                false
            } else {
                caps.total += 1;
                caps.per_ip.insert(ip, n + 1);
                true
            }
        };
        if !admitted {
            drop(stream);
            continue;
        }
        let gate = gate.clone();
        let state = state.clone();
        let qp = qp.clone();
        let caps = caps.clone();
        tokio::spawn(async move {
            let _permit = HealthPermit { caps, ip };
            handle(stream, ip, gate, lobby_port, game_port, state, qp).await;
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn classify_table() {
        assert_eq!(classify("HEAD", "/healthz", true), 200);
        assert_eq!(classify("GET", "/healthz", true), 200);
        assert_eq!(classify("HEAD", "/healthz", false), 503);
        assert_eq!(classify("GET", "/healthz", false), 503);
        assert_eq!(classify("GET", "/statusz", true), 200);
        assert_eq!(classify("HEAD", "/statusz", false), 200);
        assert_eq!(classify("POST", "/statusz", true), 404);
        assert_eq!(classify("GET", "/nope", true), 404);
        assert_eq!(classify("POST", "/healthz", true), 404);
        assert_eq!(classify("get", "/healthz", true), 404);
        assert_eq!(classify("", "", true), 404);
    }

    #[test]
    fn idle_rule_needs_every_count_zero() {
        let base = Statusz { tcp_sessions: 0, qp_queued: 0, qp_rooms: 0, slots_alive: 0 };
        assert!(base.idle());
        for s in [
            Statusz { tcp_sessions: 1, ..base },
            Statusz { qp_queued: 1, ..base },
            Statusz { qp_rooms: 1, ..base },
            Statusz { slots_alive: 1, ..base },
        ] {
            assert!(!s.idle());
        }
    }

    #[test]
    fn statusz_json_carries_counts_only() {
        let s = Statusz { tcp_sessions: 2, qp_queued: 1, qp_rooms: 1, slots_alive: 2 };
        assert_eq!(
            s.json(),
            "{\"tcp_sessions\":2,\"qp_queued\":1,\"qp_rooms\":1,\"slots_alive\":2,\"idle\":false}"
        );
        let idle = Statusz { tcp_sessions: 0, qp_queued: 0, qp_rooms: 0, slots_alive: 0 };
        assert!(idle.json().ends_with("\"idle\":true}"));
    }

    #[test]
    fn gate_allows_one_per_second() {
        let gate = Gate::default();
        let ip: IpAddr = "127.0.0.1".parse().unwrap();
        assert!(gate.allow(ip));
        assert!(!gate.allow(ip));
        let other: IpAddr = "127.0.0.2".parse().unwrap();
        assert!(gate.allow(other));
    }
}
