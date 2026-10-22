// SPDX-License-Identifier: GPL-3.0-or-later OR MIT
//! Token-bound, per-role UDP relay. The challenge proves that a sender can
//! receive at its claimed endpoint before any game datagram is forwarded.

use std::net::{IpAddr, SocketAddr};
use std::sync::Arc;
use std::time::Duration;
use tokio::net::UdpSocket;
use tokio::time::Instant;
use tracing::info;

pub const REG_MAGIC: &[u8; 8] = b"SSX3RLY2";
pub const CHALLENGE_MAGIC: &[u8; 8] = b"SSX3CHL1";
pub const PROOF_MAGIC: &[u8; 8] = b"SSX3PRF1";
const TOKEN_LEN: usize = 32;
const CHALLENGE_LEN: usize = 8;
/// IPv4 + UDP header overhead counted per forwarded datagram.
pub const WIRE_OVERHEAD_BYTES: usize = 28;

/// Fractional token bucket (lane A): sustained `rate` per second with
/// a `burst` capacity. Starts full, so short bursts (the race opening
/// handshake) pass while a sustained flood is capped at the rate.
pub struct TokenBucket {
    rate: f64,
    capacity: f64,
    tokens: f64,
    last: std::time::Instant,
}

impl TokenBucket {
    pub fn new(rate: f64, burst: f64) -> Self {
        Self {
            rate: rate.max(0.0),
            capacity: burst.max(0.0),
            tokens: burst.max(0.0),
            last: std::time::Instant::now(),
        }
    }

    /// Take `n` tokens; false leaves the bucket unchanged.
    pub fn take(&mut self, n: f64) -> bool {
        let now = std::time::Instant::now();
        let dt = now.duration_since(self.last).as_secs_f64();
        self.last = now;
        self.tokens = (self.tokens + dt * self.rate).min(self.capacity);
        if self.tokens >= n {
            self.tokens -= n;
            true
        } else {
            false
        }
    }
}

#[derive(Clone)]
pub struct Role {
    pub token: String,
    pub expected_ip: IpAddr,
}

struct Pending {
    src: SocketAddr,
    challenge: [u8; CHALLENGE_LEN],
    issued: Instant,
}

fn role_byte(b: u8) -> Option<usize> {
    match b {
        b'0' => Some(0),
        b'1' => Some(1),
        _ => None,
    }
}

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

pub fn parse_hello<'a>(buf: &'a [u8], roles: &[Role; 2], ip: IpAddr) -> Option<usize> {
    if buf.len() != REG_MAGIC.len() + 1 + TOKEN_LEN || !buf.starts_with(REG_MAGIC) {
        return None;
    }
    let role = role_byte(buf[REG_MAGIC.len()])?;
    if roles[role].expected_ip != ip
        || !ct_eq(&buf[REG_MAGIC.len() + 1..], roles[role].token.as_bytes())
    {
        return None;
    }
    Some(role)
}

fn parse_proof(buf: &[u8], roles: &[Role; 2], ip: IpAddr) -> Option<(usize, [u8; 8])> {
    if buf.len() != PROOF_MAGIC.len() + 1 + CHALLENGE_LEN + TOKEN_LEN
        || !buf.starts_with(PROOF_MAGIC)
    {
        return None;
    }
    let role = role_byte(buf[PROOF_MAGIC.len()])?;
    if roles[role].expected_ip != ip
        || !ct_eq(
            &buf[PROOF_MAGIC.len() + 1 + CHALLENGE_LEN..],
            roles[role].token.as_bytes(),
        )
    {
        return None;
    }
    let mut challenge = [0u8; CHALLENGE_LEN];
    challenge.copy_from_slice(&buf[PROOF_MAGIC.len() + 1..PROOF_MAGIC.len() + 1 + CHALLENGE_LEN]);
    Some((role, challenge))
}

/// The socket is bound before +ses is sent. Its ownership moves into this
/// task; dropping it on any exit releases the port and pair record.
/// `members` names both personas so the task reaps the port as soon as
/// both sessions are gone (H2), instead of holding it to the idle or
/// hard deadline; `None` skips that check (direct-spawn tests).
pub fn spawn(
    state: Arc<super::State>,
    pair_name: String,
    port: u16,
    sock: UdpSocket,
    roles: [Role; 2],
    members: Option<[String; 2]>,
) {
    tokio::spawn(async move {
        let idle = Duration::from_secs(state.cfg.relay.idle_timeout_secs);
        let mut idle_deadline = Instant::now() + idle;
        let hard_deadline = Instant::now() + Duration::from_secs(3600);
        // Session check granularity: at most every 5 s, at least every
        // tenth of a second, so short test idles still reap on time.
        let tick = (idle / 4).clamp(Duration::from_millis(100), Duration::from_secs(5));
        let mut endpoints: [Option<SocketAddr>; 2] = [None, None];
        let mut pending: [Option<Pending>; 2] = [None, None];
        let mut buf = [0u8; 2048];
        let mut forwarded = 0u64;
        let mut forwarded_bytes = 0u64;
        let mut dropped = 0u64;
        let mut registered = 0u64;
        let rcfg = state.cfg.relay.clone();
        let mut pair_bucket = TokenBucket::new(rcfg.packets_per_sec, rcfg.burst_packets);
        let mut role_bucket = [
            TokenBucket::new(rcfg.per_role_per_sec, rcfg.per_role_burst),
            TokenBucket::new(rcfg.per_role_per_sec, rcfg.per_role_burst),
        ];
        let mut byte_bucket =
            TokenBucket::new(rcfg.bytes_per_sec_per_pair, rcfg.burst_bytes_per_pair);
        // Handshake attempts (challenge replies cost a send each).
        let mut hs_bucket = [
            TokenBucket::new(rcfg.handshake_per_sec, rcfg.handshake_burst),
            TokenBucket::new(rcfg.handshake_per_sec, rcfg.handshake_burst),
        ];
        info!(port, "relay up");
        loop {
            let deadline = idle_deadline.min(hard_deadline);
            let wait = deadline.min(Instant::now() + tick);
            let (n, src) = match tokio::time::timeout_at(wait, sock.recv_from(&mut buf)).await {
                Ok(Ok(x)) => x,
                Ok(Err(_)) => break,
                Err(_) => {
                    // Tick or deadline expiry (no packet arrived).
                    let now = Instant::now();
                    if now >= deadline {
                        break;
                    }
                    if let Some([a, b]) = &members {
                        let games = state.games.lock().unwrap();
                        if !games.contains_key(a) && !games.contains_key(b) {
                            info!(port, "relay reaped: both sessions gone");
                            break;
                        }
                    }
                    continue;
                }
            };
            let now = Instant::now();
            let data = &buf[..n];
            // Isolation order (H1 fix): parse before any bucket charge.
            // Datagrams matching neither handshake shape drop for free,
            // so garbage cannot starve enrollment. Handshake attempts pay
            // from the small per-role handshake bucket (a challenge reply
            // costs a send); enrolled endpoints skip to their own role
            // and byte budgets, with the pair bucket bounding enrolled
            // traffic only.
            let enrolled = endpoints.iter().position(|e| *e == Some(src));
            if enrolled.is_none() {
                if let Some(role) = parse_hello(data, &roles, src.ip()) {
                    if endpoints[role] == Some(src) {
                        continue;
                    }
                    if !hs_bucket[role].take(1.0) {
                        dropped += 1;
                        continue;
                    }
                    if endpoints[role].is_some() || endpoints[1 - role] == Some(src) {
                        dropped += 1;
                        continue;
                    }
                    if pending[role]
                        .as_ref()
                        .map(|p| {
                            p.src != src || now.duration_since(p.issued) >= Duration::from_secs(5)
                        })
                        .unwrap_or(true)
                    {
                        pending[role] = Some(Pending {
                            src,
                            challenge: rand::random(),
                            issued: now,
                        });
                    }
                    let challenge = pending[role].as_ref().unwrap().challenge;
                    let mut reply = Vec::with_capacity(CHALLENGE_MAGIC.len() + 1 + CHALLENGE_LEN);
                    reply.extend_from_slice(CHALLENGE_MAGIC);
                    reply.push(b'0' + role as u8);
                    reply.extend_from_slice(&challenge);
                    let _ = sock.send_to(&reply, src).await;
                    continue;
                }
                if let Some((role, challenge)) = parse_proof(data, &roles, src.ip()) {
                    if !hs_bucket[role].take(1.0) {
                        dropped += 1;
                        continue;
                    }
                    let valid = pending[role]
                        .as_ref()
                        .map(|p| {
                            p.src == src
                                && p.challenge == challenge
                                && now.duration_since(p.issued) < Duration::from_secs(5)
                        })
                        .unwrap_or(false);
                    if valid && endpoints[role].is_none() && endpoints[1 - role] != Some(src) {
                        endpoints[role] = Some(src);
                        pending[role] = None;
                        idle_deadline = now + idle;
                        registered += 1;
                        info!(port, role, "relay endpoint confirmed");
                    } else {
                        dropped += 1;
                    }
                    continue;
                }
                dropped += 1;
                continue;
            }
            // No raw O5 datagram can enroll an endpoint. Once both roles
            // prove return reachability, only their exact sockets forward.
            let Some(role) = enrolled else {
                dropped += 1;
                continue;
            };
            if !pair_bucket.take(1.0) {
                dropped += 1;
                continue;
            }
            if !role_bucket[role].take(1.0)
                || n < 4
                || !matches!(&data[..4], b"O5HI" | b"O5OK" | b"O5KA" | b"O5PK")
            {
                dropped += 1;
                continue;
            }
            // Bandwidth ceiling: per-pair bytes first, then the global
            // egress bucket shared by all pairs. Drops never refresh
            // idle (an invalid stream cannot hold a port).
            let cost = (n + WIRE_OVERHEAD_BYTES) as f64;
            if !byte_bucket.take(cost) {
                dropped += 1;
                continue;
            }
            if !state.egress_bucket.lock().unwrap().take(cost) {
                dropped += 1;
                continue;
            }
            idle_deadline = now + idle;
            if let Some(dst) = endpoints[1 - role] {
                if sock.send_to(data, dst).await.is_ok() {
                    forwarded += 1;
                    forwarded_bytes += cost as u64;
                    state
                        .egress_bytes_total
                        .fetch_add(cost as u64, std::sync::atomic::Ordering::Relaxed);
                }
            }
        }
        info!(
            port,
            forwarded, forwarded_bytes, dropped, registered, "relay down"
        );
        state.pairs.lock().unwrap().remove(&pair_name);
        // Post-pair grace cover (lobby-buddy-1): results screen and
        // rematch browsing send nothing on either TCP.
        if let Some(m) = &members {
            state.note_pair_ended(m);
        }
        state.relay_ports.lock().unwrap().push(port);
        // Release the port BEFORE re-pairing: try_pair binds it for the
        // next pair, and the bind fails while this socket is still open.
        drop(sock);
        super::try_pair(&state).await;
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn token_bucket_burst_then_sustained_rate() {
        let mut b = TokenBucket::new(10.0, 20.0);
        // Burst: exactly 20 pass back-to-back; the rest fail.
        let mut n = 0;
        for _ in 0..25 {
            if b.take(1.0) {
                n += 1;
            }
        }
        assert_eq!(n, 20);
        // Sustained: ~10 refill over one second (wide bounds for load).
        std::thread::sleep(std::time::Duration::from_millis(1000));
        let mut m = 0;
        for _ in 0..25 {
            if b.take(1.0) {
                m += 1;
            }
        }
        assert!((8..=13).contains(&m), "refilled {m} in 1 s at 10/s");
        // Byte costs behave the same (failed takes leave no debt).
        let mut bytes = TokenBucket::new(100.0, 200.0);
        assert!(bytes.take(200.0));
        assert!(!bytes.take(1.0));
    }
    #[test]
    fn role_and_token_required_for_hello() {
        let roles = [
            Role {
                token: "a".repeat(32),
                expected_ip: "127.0.0.1".parse().unwrap(),
            },
            Role {
                token: "b".repeat(32),
                expected_ip: "127.0.0.2".parse().unwrap(),
            },
        ];
        let mut hello = REG_MAGIC.to_vec();
        hello.push(b'0');
        hello.extend_from_slice(roles[0].token.as_bytes());
        assert_eq!(parse_hello(&hello, &roles, roles[0].expected_ip), Some(0));
        assert_eq!(parse_hello(&hello, &roles, roles[1].expected_ip), None);
        hello[8] = b'1';
        assert_eq!(parse_hello(&hello, &roles, roles[1].expected_ip), None);
        hello[8] = b'0';
        hello[9] = b'x';
        assert_eq!(parse_hello(&hello, &roles, roles[0].expected_ip), None);
        assert_eq!(parse_hello(b"O5HI", &roles, roles[0].expected_ip), None);
    }
}
