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

/// Pure status classifier (unit-tested, including the 503 branch).
pub fn classify(method: &str, path: &str, selfcheck_ok: bool) -> u16 {
    if (method == "HEAD" || method == "GET") && path == "/healthz" {
        if selfcheck_ok {
            200
        } else {
            503
        }
    } else {
        404
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
) {
    let mut buf = [0u8; 1024];
    let n = match tokio::time::timeout(Duration::from_secs(5), stream.read(&mut buf)).await {
        Ok(Ok(n)) => n,
        _ => return,
    };
    let line = String::from_utf8_lossy(&buf[..n]);
    let mut parts = line.split_whitespace();
    let (method, path) = (parts.next().unwrap_or(""), parts.next().unwrap_or(""));
    let code = if !gate.allow(peer) {
        429
    } else if (method == "HEAD" || method == "GET") && path == "/healthz" {
        classify(method, path, self_check(lobby_port, game_port).await)
    } else {
        classify(method, path, false)
    };
    let body = body(code, method);
    let reply = format!(
        "HTTP/1.1 {code} {}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
        reason(code),
        body.len()
    );
    let _ = stream.write_all(reply.as_bytes()).await;
}

/// Serve health forever on `listener` (one task per connection).
pub async fn serve(listener: TcpListener, lobby_port: u16, game_port: u16) {
    let gate = Arc::new(Gate::default());
    loop {
        let Ok((stream, addr)) = listener.accept().await else {
            break;
        };
        let gate = gate.clone();
        tokio::spawn(handle(stream, addr.ip(), gate, lobby_port, game_port));
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
        assert_eq!(classify("GET", "/nope", true), 404);
        assert_eq!(classify("POST", "/healthz", true), 404);
        assert_eq!(classify("get", "/healthz", true), 404);
        assert_eq!(classify("", "", true), 404);
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
