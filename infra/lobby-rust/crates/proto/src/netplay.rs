//! SSXNP v2 netplay codec: ordered-relay wire contract for server-hosted lockstep.
//!
//! Implements `docs/research/np-referee-design-2026-10-15.md` §3 (wire contract)
//! for Package 1 (`np-referee-p1`): framing plus every message payload in the §3
//! table, with strict validation before allocation or state mutation.
//!
//! Wire-compatible with the P1a diagnostic subset (`SSXNP/2-P1a`) landed by
//! `np-link-1`: framing, `JOIN`/`CONFIG`/`READY`/`START`/`INPUT`/`COMMIT`, the
//! `SSXNP2-COMMIT` chain domain and the P1a-fixed field values all decode here
//! to identical bytes. P1a source of truth:
//! `local/research/np-link-1/relay/src/main.rs` (codec + `Committer`), fixtures:
//! `local/research/np-link-1/wire-vectors.json` (locked by the `p1a_vectors`
//! test below). P1a deltas upgraded by this package: conflicting duplicates are
//! a violation (P1a kept first + loud log), zero credentials/compat become
//! bound, and the P1a `[next, next+72]` window tightens to §3 `[C+1, C+N+8]`
//! (session layer; this codec only carries the bytes).
//!
//! Loopback-only harnesses may inject the same decoded messages without TLS
//! (design §3, sanctioned). No TLS, transport or legacy-lobby behavior here:
//! this crate only encodes/decodes bytes.

/// Four-byte frame magic.
pub const MAGIC: &[u8; 4] = b"SNP2";
/// Protocol version carried in every frame.
pub const VERSION: u16 = 2;
/// Frame header length: 4 len + 4 magic + 2 ver + 1 kind + 1 flags
/// + 16 session + 8 epoch + 2 sender + 2 reserved + 8 seq.
pub const HEADER_LEN: usize = 48;
/// `frame_bytes` (bytes after the length prefix) must land in this range.
pub const MIN_FRAME_BODY: u32 = 44;
/// Largest `frame_bytes` accepted before allocation.
pub const MAX_FRAME: u32 = 16384;

/// Message kinds (§3 message set).
pub const KIND_JOIN: u8 = 0x01;
pub const KIND_CONFIG: u8 = 0x02;
pub const KIND_READY: u8 = 0x03;
pub const KIND_START: u8 = 0x04;
pub const KIND_INPUT: u8 = 0x10;
pub const KIND_COMMIT: u8 = 0x11;
pub const KIND_ACK: u8 = 0x12;
pub const KIND_NACK: u8 = 0x13;
pub const KIND_CHECKSUM: u8 = 0x20;
pub const KIND_REF_PROGRESS: u8 = 0x21;
pub const KIND_REF_FINISH: u8 = 0x22;
pub const KIND_REF_END_READY: u8 = 0x23;
pub const KIND_FINALIZE: u8 = 0x24;
pub const KIND_QUIT: u8 = 0x30;
pub const KIND_EJECT: u8 = 0x31;
pub const KIND_RESULT: u8 = 0x32;
pub const KIND_CLOSE: u8 = 0x33;
pub const KIND_PING: u8 = 0x40;
pub const KIND_PONG: u8 = 0x41;

/// Relay sender id (server-originated frames).
pub const SENDER_RELAY: u16 = 0xfffe;
/// Referee sender id (private supervisor IPC only; never a client).
pub const SENDER_REFEREE: u16 = 0xffff;

/// Input-kind registry (P1a assignment; Package 1 owns it).
pub const INPUT_PAD6: u8 = 0;
/// Diagnostic-profile command triple (race-v1 carries no raw command words).
pub const INPUT_RIDER12: u8 = 1;

/// State-digest algorithms (§3 `StateDigest`).
pub const STATE_ALG_FNV64_DIAG: u8 = 1;
pub const STATE_ALG_SHA256: u8 = 2;

/// Event types (`Event.type`).
pub const EVENT_DROP_TO_AI: u8 = 1;

/// Result dispositions (`ResultRow.disposition`).
pub const DISP_FINISHED: u8 = 0;
pub const DISP_DNF: u8 = 1;
pub const DISP_EJECTED: u8 = 2;

/// Terminal reason codes (§3 named table; 0 = unspecified, package-local).
pub const REASON_UNSPECIFIED: u16 = 0;
pub const REASON_QUIT: u16 = 1;
pub const REASON_INPUT_TIMEOUT: u16 = 2;
pub const REASON_PROTOCOL_VIOLATION: u16 = 3;
pub const REASON_STATE_MISMATCH: u16 = 4;
pub const REASON_CLIENT_SLOW: u16 = 5;
pub const REASON_BACKGROUND: u16 = 6;
pub const REASON_REFEREE_FAILED: u16 = 7;
pub const REASON_REFEREE_LAG: u16 = 8;
pub const REASON_SESSION_INCONSISTENT: u16 = 9;
pub const REASON_RESULT_TIMEOUT: u16 = 10;
pub const REASON_RESOURCE_LIMIT: u16 = 11;
pub const REASON_SETUP_MISMATCH: u16 = 12;
pub const REASON_SERVER_LOST: u16 = 13;
pub const REASON_RACE_DEADLINE: u16 = 14;
pub const MAX_REASON: u16 = 14;

/// Referee phase values (`REF_PROGRESS.phase`; opaque transport, named here).
pub const PHASE_LOADING: u8 = 0;
pub const PHASE_RUNNING: u8 = 1;
pub const PHASE_DRAINING: u8 = 2;

/// Commit-chain domain separation (§3 bounds).
pub const CHAIN_DOMAIN: &[u8] = b"SSXNP2-COMMIT";
/// Result-id domain separation.
pub const RESULT_DOMAIN: &[u8] = b"SSXNP2-RESULT";

/// Codec failure. Every input maps to one of these; decode never panics.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CodecError {
    ShortHeader,
    BadFrameBytes,
    TrailingBytes,
    BadMagic,
    BadVersion,
    NonzeroFlags,
    NonzeroReserved,
    ZeroSeq,
    BadLength,
    BadCount,
    InvalidEnum,
    Oversize,
}

impl core::fmt::Display for CodecError {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        let s = match self {
            Self::ShortHeader => "short header",
            Self::BadFrameBytes => "bad frame_bytes",
            Self::TrailingBytes => "trailing bytes",
            Self::BadMagic => "bad magic",
            Self::BadVersion => "bad version",
            Self::NonzeroFlags => "nonzero flags",
            Self::NonzeroReserved => "nonzero reserved",
            Self::ZeroSeq => "seq starts at 1",
            Self::BadLength => "bad payload length",
            Self::BadCount => "bad count",
            Self::InvalidEnum => "invalid enum",
            Self::Oversize => "oversize",
        };
        write!(f, "{s}")
    }
}

impl std::error::Error for CodecError {}

// ---------------------------------------------------------------- SHA-256
//
// Minimal std-only SHA-256, copied for wire-compatibility from the P1a relay
// (`local/research/np-link-1/relay/src/main.rs`, `fn sha256`). The COMMIT
// chain and RESULT id must hash identically to P1a; a crate dependency would
// also do, but this keeps `ssx3-proto` dependency-free exactly like P1a.

fn sha256(msg: &[u8]) -> [u8; 32] {
    fn rotr(x: u32, n: u32) -> u32 {
        (x >> n) | (x << (32 - n))
    }
    const K: [u32; 64] = [
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
        0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
        0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
        0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
        0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
        0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
        0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
        0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
    ];
    let mut h: [u32; 8] = [
        0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
    ];
    let bitlen = (msg.len() as u64).wrapping_mul(8);
    let mut padded = msg.to_vec();
    padded.push(0x80);
    while padded.len() % 64 != 56 {
        padded.push(0);
    }
    padded.extend_from_slice(&bitlen.to_be_bytes());
    for block in padded.chunks_exact(64) {
        let mut w = [0u32; 64];
        for i in 0..16 {
            w[i] = u32::from_be_bytes([block[4 * i], block[4 * i + 1], block[4 * i + 2], block[4 * i + 3]]);
        }
        for i in 16..64 {
            let s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >> 3);
            let s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16].wrapping_add(s0).wrapping_add(w[i - 7]).wrapping_add(s1);
        }
        let (mut a, mut b, mut c, mut d, mut e, mut f, mut g, mut hh) =
            (h[0], h[1], h[2], h[3], h[4], h[5], h[6], h[7]);
        for i in 0..64 {
            let s1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
            let ch = (e & f) ^ ((!e) & g);
            let t1 = hh.wrapping_add(s1).wrapping_add(ch).wrapping_add(K[i]).wrapping_add(w[i]);
            let s0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
            let maj = (a & b) ^ (a & c) ^ (b & c);
            let t2 = s0.wrapping_add(maj);
            hh = g;
            g = f;
            f = e;
            e = d.wrapping_add(t1);
            d = c;
            c = b;
            b = a;
            a = t1.wrapping_add(t2);
        }
        h[0] = h[0].wrapping_add(a);
        h[1] = h[1].wrapping_add(b);
        h[2] = h[2].wrapping_add(c);
        h[3] = h[3].wrapping_add(d);
        h[4] = h[4].wrapping_add(e);
        h[5] = h[5].wrapping_add(f);
        h[6] = h[6].wrapping_add(g);
        h[7] = h[7].wrapping_add(hh);
    }
    let mut out = [0u8; 32];
    for i in 0..8 {
        out[4 * i..4 * i + 4].copy_from_slice(&h[i].to_be_bytes());
    }
    out
}

/// SHA-256 over `CHAIN_DOMAIN ‖ session ‖ epoch ‖ prev ‖ body`, where `body`
/// is the canonical COMMIT payload bytes excluding the `chain` field.
/// Packet headers and timing are excluded (§3 bounds). Byte-identical to P1a.
pub fn commit_chain(prev: &[u8; 32], session: &[u8; 16], epoch: u64, body: &[u8]) -> [u8; 32] {
    let mut input = Vec::with_capacity(
        CHAIN_DOMAIN.len() + session.len() + 8 + prev.len() + body.len(),
    );
    input.extend_from_slice(CHAIN_DOMAIN);
    input.extend_from_slice(session);
    input.extend_from_slice(&epoch.to_le_bytes());
    input.extend_from_slice(prev);
    input.extend_from_slice(body);
    sha256(&input)
}

/// Deterministic result id: `SHA-256(RESULT_DOMAIN ‖ session ‖ epoch ‖
/// final_boundary ‖ final_commit_seq ‖ chain ‖ state_alg ‖ state ‖ transcript)`.
/// Retries retain this identical id (§3 `RESULT`).
pub fn result_id(
    session: &[u8; 16],
    epoch: u64,
    final_boundary: u64,
    final_commit_seq: u64,
    chain: &[u8; 32],
    state: &StateDigest,
    transcript: &[u8; 32],
) -> [u8; 32] {
    let mut input = Vec::with_capacity(256);
    input.extend_from_slice(RESULT_DOMAIN);
    input.extend_from_slice(session);
    input.extend_from_slice(&epoch.to_le_bytes());
    input.extend_from_slice(&final_boundary.to_le_bytes());
    input.extend_from_slice(&final_commit_seq.to_le_bytes());
    input.extend_from_slice(chain);
    input.push(state.alg);
    input.extend_from_slice(&state.bytes);
    input.extend_from_slice(transcript);
    sha256(&input)
}

// ---------------------------------------------------------------- frame

/// One decoded SSXNP v2 frame (header plus borrowed payload).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Frame<'a> {
    pub session: &'a [u8; 16],
    pub epoch: u64,
    pub kind: u8,
    pub sender: u16,
    pub seq: u64,
    pub payload: &'a [u8],
}

/// Encode one frame. `payload` is the already-encoded message body.
pub fn encode_frame(
    session: &[u8; 16],
    epoch: u64,
    kind: u8,
    sender: u16,
    seq: u64,
    payload: &[u8],
) -> Vec<u8> {
    let frame_bytes = (MIN_FRAME_BODY as usize + payload.len()) as u32;
    let mut f = Vec::with_capacity(HEADER_LEN + payload.len());
    f.extend_from_slice(&frame_bytes.to_le_bytes());
    f.extend_from_slice(MAGIC);
    f.extend_from_slice(&VERSION.to_le_bytes());
    f.push(kind);
    f.push(0); // flags
    f.extend_from_slice(session);
    f.extend_from_slice(&epoch.to_le_bytes());
    f.extend_from_slice(&sender.to_le_bytes());
    f.extend_from_slice(&0u16.to_le_bytes()); // reserved
    f.extend_from_slice(&seq.to_le_bytes());
    f.extend_from_slice(payload);
    f
}

/// Strict frame validation before allocation or state mutation: length,
/// magic, version, flags, reserved, nonzero seq, exact byte count
/// (no trailing bytes). Session/epoch/sender/kind values are NOT checked
/// here; the session layer binds them (stale epochs must not mutate state,
/// forged senders close only the offender).
pub fn decode_frame(buf: &[u8]) -> Result<Frame<'_>, CodecError> {
    if buf.len() < HEADER_LEN {
        return Err(CodecError::ShortHeader);
    }
    let frame_bytes = u32::from_le_bytes([buf[0], buf[1], buf[2], buf[3]]);
    if frame_bytes < MIN_FRAME_BODY || frame_bytes > MAX_FRAME {
        return Err(CodecError::BadFrameBytes);
    }
    if buf.len() != HEADER_LEN + (frame_bytes as usize) - (MIN_FRAME_BODY as usize) {
        return Err(CodecError::TrailingBytes);
    }
    if &buf[4..8] != MAGIC {
        return Err(CodecError::BadMagic);
    }
    if u16::from_le_bytes([buf[8], buf[9]]) != VERSION {
        return Err(CodecError::BadVersion);
    }
    let kind = buf[10];
    if buf[11] != 0 {
        return Err(CodecError::NonzeroFlags);
    }
    let session: &[u8; 16] = buf[12..28].try_into().map_err(|_| CodecError::ShortHeader)?;
    let epoch = u64::from_le_bytes(buf[28..36].try_into().map_err(|_| CodecError::ShortHeader)?);
    let sender = u16::from_le_bytes([buf[36], buf[37]]);
    if u16::from_le_bytes([buf[38], buf[39]]) != 0 {
        return Err(CodecError::NonzeroReserved);
    }
    let seq = u64::from_le_bytes(buf[40..48].try_into().map_err(|_| CodecError::ShortHeader)?);
    if seq == 0 {
        return Err(CodecError::ZeroSeq);
    }
    Ok(Frame { session, epoch, kind, sender, seq, payload: &buf[HEADER_LEN..] })
}

// ------------------------------------------------------- payload helpers

fn le_u16(b: &[u8]) -> Result<u16, CodecError> {
    b.try_into().map(u16::from_le_bytes).map_err(|_| CodecError::BadLength)
}

fn le_u32(b: &[u8]) -> Result<u32, CodecError> {
    b.try_into().map(u32::from_le_bytes).map_err(|_| CodecError::BadLength)
}

fn le_u64(b: &[u8]) -> Result<u64, CodecError> {
    b.try_into().map(u64::from_le_bytes).map_err(|_| CodecError::BadLength)
}

fn le_i64(b: &[u8]) -> Result<i64, CodecError> {
    b.try_into().map(i64::from_le_bytes).map_err(|_| CodecError::BadLength)
}

fn exact_32(b: &[u8]) -> Result<[u8; 32], CodecError> {
    b.try_into().map_err(|_| CodecError::BadLength)
}

fn no_trail(payload: &[u8], used: usize) -> Result<(), CodecError> {
    if payload.len() == used {
        Ok(())
    } else {
        Err(CodecError::TrailingBytes)
    }
}

/// Wire length of one input sample by kind.
pub fn sample_len(kind: u8) -> Result<usize, CodecError> {
    match kind {
        INPUT_PAD6 => Ok(6),
        INPUT_RIDER12 => Ok(12),
        _ => Err(CodecError::InvalidEnum),
    }
}

// ------------------------------------------------------------------ JOIN

/// `0x01 JOIN`, client→relay: `credential[32], compatibility_id[32],
/// platform_build_id[32], profile:u8`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Join {
    pub credential: [u8; 32],
    pub compatibility_id: [u8; 32],
    pub platform_build_id: [u8; 32],
    pub profile: u8,
}

impl Join {
    pub fn encode(&self) -> Vec<u8> {
        let mut p = Vec::with_capacity(97);
        p.extend_from_slice(&self.credential);
        p.extend_from_slice(&self.compatibility_id);
        p.extend_from_slice(&self.platform_build_id);
        p.push(self.profile);
        p
    }

    pub fn decode(payload: &[u8]) -> Result<Self, CodecError> {
        if payload.len() != 97 {
            return Err(CodecError::BadLength);
        }
        Ok(Self {
            credential: exact_32(&payload[0..32])?,
            compatibility_id: exact_32(&payload[32..64])?,
            platform_build_id: exact_32(&payload[64..96])?,
            profile: payload[96],
        })
    }
}

// ---------------------------------------------------------------- CONFIG

/// One roster entry: `slot:u16, guest_actor:u8, input_kind:u8, fallback:u8`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SlotDesc {
    pub slot: u16,
    pub guest_actor: u8,
    pub input_kind: u8,
    pub fallback: u8,
}

/// `0x02 CONFIG`, relay→all (protocol cap four humans; initial races run two).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Config {
    pub compatibility_id: [u8; 32],
    pub profile: u8,
    pub first_tick: u64,
    pub delay_n: u16,
    pub hash_every: u16,
    pub lag_limit: u16,
    pub schema: u32,
    pub state_alg: u8,
    pub roster_rev: u32,
    pub roster: Vec<SlotDesc>,
}

impl Config {
    pub fn encode(&self) -> Vec<u8> {
        let mut p = Vec::with_capacity(57 + 5 * self.roster.len());
        p.extend_from_slice(&self.compatibility_id);
        p.push(self.profile);
        p.extend_from_slice(&self.first_tick.to_le_bytes());
        p.extend_from_slice(&self.delay_n.to_le_bytes());
        p.extend_from_slice(&self.hash_every.to_le_bytes());
        p.extend_from_slice(&self.lag_limit.to_le_bytes());
        p.extend_from_slice(&self.schema.to_le_bytes());
        p.push(self.state_alg);
        p.extend_from_slice(&self.roster_rev.to_le_bytes());
        p.push(self.roster.len() as u8);
        for s in &self.roster {
            p.extend_from_slice(&s.slot.to_le_bytes());
            p.push(s.guest_actor);
            p.push(s.input_kind);
            p.push(s.fallback);
        }
        p
    }

    pub fn decode(payload: &[u8]) -> Result<Self, CodecError> {
        if payload.len() < 57 {
            return Err(CodecError::BadLength);
        }
        let state_alg = payload[51];
        if state_alg != STATE_ALG_FNV64_DIAG && state_alg != STATE_ALG_SHA256 {
            return Err(CodecError::InvalidEnum);
        }
        let n = payload[56] as usize;
        if n > 4 || payload.len() != 57 + 5 * n {
            return Err(if n > 4 { CodecError::Oversize } else { CodecError::TrailingBytes });
        }
        let mut roster = Vec::with_capacity(n);
        for i in 0..n {
            let o = 57 + 5 * i;
            let input_kind = payload[o + 3];
            if input_kind != INPUT_PAD6 && input_kind != INPUT_RIDER12 {
                return Err(CodecError::InvalidEnum);
            }
            roster.push(SlotDesc {
                slot: le_u16(&payload[o..o + 2])?,
                guest_actor: payload[o + 2],
                input_kind,
                fallback: payload[o + 4],
            });
        }
        Ok(Self {
            compatibility_id: exact_32(&payload[0..32])?,
            profile: payload[32],
            first_tick: le_u64(&payload[33..41])?,
            delay_n: le_u16(&payload[41..43])?,
            hash_every: le_u16(&payload[43..45])?,
            lag_limit: le_u16(&payload[45..47])?,
            schema: le_u32(&payload[47..51])?,
            state_alg,
            roster_rev: le_u32(&payload[52..56])?,
            roster,
        })
    }
}

// ------------------------------------------------------------ state digest

/// `StateDigest = alg:u8 + bytes[32]`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct StateDigest {
    pub alg: u8,
    pub bytes: [u8; 32],
}

impl StateDigest {
    pub fn encode(&self) -> [u8; 33] {
        let mut o = [0u8; 33];
        o[0] = self.alg;
        o[1..].copy_from_slice(&self.bytes);
        o
    }

    pub fn decode(b: &[u8]) -> Result<(Self, usize), CodecError> {
        if b.len() < 33 {
            return Err(CodecError::BadLength);
        }
        if b[0] != STATE_ALG_FNV64_DIAG && b[0] != STATE_ALG_SHA256 {
            return Err(CodecError::InvalidEnum);
        }
        let mut bytes = [0u8; 32];
        bytes.copy_from_slice(&b[1..33]);
        Ok((Self { alg: b[0], bytes }, 33))
    }
}

// ----------------------------------------------------------------- READY

/// `0x03 READY`, all→relay.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Ready {
    pub boundary: u64,
    pub schema: u32,
    pub state: StateDigest,
    pub boot_transcript: [u8; 32],
    pub roster_rev: u32,
}

impl Ready {
    pub fn encode(&self) -> Vec<u8> {
        let mut p = Vec::with_capacity(81);
        p.extend_from_slice(&self.boundary.to_le_bytes());
        p.extend_from_slice(&self.schema.to_le_bytes());
        p.extend_from_slice(&self.state.encode());
        p.extend_from_slice(&self.boot_transcript);
        p.extend_from_slice(&self.roster_rev.to_le_bytes());
        p
    }

    pub fn decode(payload: &[u8]) -> Result<Self, CodecError> {
        if payload.len() != 81 {
            return Err(CodecError::BadLength);
        }
        let (state, _) = StateDigest::decode(&payload[12..45])?;
        Ok(Self {
            boundary: le_u64(&payload[0..8])?,
            schema: le_u32(&payload[8..12])?,
            state,
            boot_transcript: exact_32(&payload[45..77])?,
            roster_rev: le_u32(&payload[77..81])?,
        })
    }
}

// ----------------------------------------------------------------- START

/// `0x04 START`, relay→all.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Start {
    pub first_tick: u64,
    pub first_commit_seq: u64,
    pub roster_rev: u32,
    pub initial_chain: [u8; 32],
    pub countdown_ms: u16,
}

impl Start {
    pub fn encode(&self) -> Vec<u8> {
        let mut p = Vec::with_capacity(54);
        p.extend_from_slice(&self.first_tick.to_le_bytes());
        p.extend_from_slice(&self.first_commit_seq.to_le_bytes());
        p.extend_from_slice(&self.roster_rev.to_le_bytes());
        p.extend_from_slice(&self.initial_chain);
        p.extend_from_slice(&self.countdown_ms.to_le_bytes());
        p
    }

    pub fn decode(payload: &[u8]) -> Result<Self, CodecError> {
        if payload.len() != 54 {
            return Err(CodecError::BadLength);
        }
        Ok(Self {
            first_tick: le_u64(&payload[0..8])?,
            first_commit_seq: le_u64(&payload[8..16])?,
            roster_rev: le_u32(&payload[16..20])?,
            initial_chain: exact_32(&payload[20..52])?,
            countdown_ms: le_u16(&payload[52..54])?,
        })
    }
}

// ----------------------------------------------------------------- INPUT

/// `Pad6 = buttons:u16, rx:u8, ry:u8, lx:u8, ly:u8` (active-low buttons;
/// neutral `ffff 80 80 80 80`). Wire order is buttons,rx,ry,lx,ly (the
/// runtime setter takes buttons,lx,ly,rx,ry; see the design §7 Pad row).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Pad6(pub [u8; 6]);

impl Pad6 {
    /// Server-side race Start filtering, applied BEFORE duplicate comparison
    /// and hash inclusion (§4): Start bit (0x0008) is forced on.
    /// Returns true when a bit actually changed (for census counters).
    pub fn filter_start(bytes: &mut [u8; 6]) -> bool {
        let before = bytes[0];
        bytes[0] |= 0x08;
        bytes[0] != before
    }

    pub fn buttons_le(b: &[u8; 6]) -> u16 {
        u16::from_le_bytes([b[0], b[1]])
    }
}

/// `RiderCmd12 = de4:u32, w0:u32, w1:u32` (diagnostic profile only; race-v1
/// carries no raw guest command words from untrusted clients).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RiderCmd12(pub [u8; 12]);

/// `0x10 INPUT`, client→relay: `first_tick:u64, input_kind:u8,
/// samples:vec8<Input>`; 1..=4 contiguous samples, own bound slot only
/// (no slot field inside the payload to spoof).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InputMsg {
    pub first_tick: u64,
    pub input_kind: u8,
    pub samples: Vec<Vec<u8>>,
}

impl InputMsg {
    pub fn encode(&self) -> Vec<u8> {
        let n = sample_len(self.input_kind).unwrap_or(0);
        let mut p = Vec::with_capacity(10 + n * self.samples.len());
        p.extend_from_slice(&self.first_tick.to_le_bytes());
        p.push(self.input_kind);
        p.push(self.samples.len() as u8);
        for s in &self.samples {
            p.extend_from_slice(s);
        }
        p
    }

    pub fn decode(payload: &[u8]) -> Result<Self, CodecError> {
        if payload.len() < 10 {
            return Err(CodecError::BadLength);
        }
        let input_kind = payload[8];
        let n = sample_len(input_kind)?;
        let count = payload[9] as usize;
        if count < 1 || count > 4 {
            return Err(CodecError::BadCount);
        }
        if payload.len() != 10 + count * n {
            return Err(CodecError::TrailingBytes);
        }
        let mut samples = Vec::with_capacity(count);
        for i in 0..count {
            samples.push(payload[10 + i * n..10 + (i + 1) * n].to_vec());
        }
        Ok(Self { first_tick: le_u64(&payload[0..8])?, input_kind, samples })
    }
}

// ---------------------------------------------------------------- COMMIT

/// `DROP_TO_AI` event body: `slot:u16, reason:u16, evidence_boundary:u64`.
/// Effective tick is the enclosing commit's tick.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct DropToAi {
    pub slot: u16,
    pub reason: u16,
    pub evidence_boundary: u64,
}

impl DropToAi {
    pub fn encode(&self) -> Vec<u8> {
        let mut p = Vec::with_capacity(12);
        p.extend_from_slice(&self.slot.to_le_bytes());
        p.extend_from_slice(&self.reason.to_le_bytes());
        p.extend_from_slice(&self.evidence_boundary.to_le_bytes());
        p
    }

    pub fn decode(b: &[u8]) -> Result<Self, CodecError> {
        if b.len() != 12 {
            return Err(CodecError::BadLength);
        }
        let reason = le_u16(&b[2..4])?;
        // State sync's drop reason (below) sits past the lockstep set.
        if reason > MAX_REASON && reason != REASON_STATE_TIMEOUT {
            return Err(CodecError::InvalidEnum);
        }
        Ok(Self { slot: le_u16(&b[0..2])?, reason, evidence_boundary: le_u64(&b[4..12])? })
    }
}

/// One commit event: `event_seq:u64, type:u8, payload`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Event {
    pub event_seq: u64,
    pub event_type: u8,
    pub drop: DropToAi,
}

impl Event {
    pub fn encode(&self) -> Vec<u8> {
        debug_assert_eq!(self.event_type, EVENT_DROP_TO_AI);
        let mut p = Vec::with_capacity(21);
        p.extend_from_slice(&self.event_seq.to_le_bytes());
        p.push(self.event_type);
        p.extend_from_slice(&self.drop.encode());
        p
    }

    /// Decodes one event at `b`, returning the event and bytes consumed.
    pub fn decode_one(b: &[u8]) -> Result<(Self, usize), CodecError> {
        if b.len() < 9 {
            return Err(CodecError::BadLength);
        }
        let event_seq = le_u64(&b[0..8])?;
        let event_type = b[8];
        if event_type != EVENT_DROP_TO_AI {
            return Err(CodecError::InvalidEnum);
        }
        if b.len() < 21 {
            return Err(CodecError::BadLength);
        }
        Ok((Self { event_seq, event_type, drop: DropToAi::decode(&b[9..21])? }, 21))
    }
}

/// One committed slot input: `slot:u16, kind:u8, bytes` (6/12 by kind).
/// AI slots contribute no wire input.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SlotInput {
    pub slot: u16,
    pub kind: u8,
    pub bytes: Vec<u8>,
}

impl SlotInput {
    pub fn encode(&self) -> Vec<u8> {
        let mut p = Vec::with_capacity(3 + self.bytes.len());
        p.extend_from_slice(&self.slot.to_le_bytes());
        p.push(self.kind);
        p.extend_from_slice(&self.bytes);
        p
    }

    /// Decodes one slot input at `b`, returning it and bytes consumed.
    pub fn decode_one(b: &[u8]) -> Result<(Self, usize), CodecError> {
        if b.len() < 3 {
            return Err(CodecError::BadLength);
        }
        let kind = b[2];
        let n = sample_len(kind)?;
        if b.len() < 3 + n {
            return Err(CodecError::BadLength);
        }
        Ok((
            Self { slot: le_u16(&b[0..2])?, kind, bytes: b[3..3 + n].to_vec() },
            3 + n,
        ))
    }
}

/// `0x11 COMMIT`, relay→all: `commit_seq:u64, tick:u64, roster_rev:u32,
/// events:vec8<Event>, inputs:vec8<SlotInput>, chain[32]`. One interval per
/// commit, contiguous sequence/ticks; events first, then sorted slot inputs.
/// `body_bytes()` is the canonical payload WITHOUT the chain field: the
/// exact bytes the chain covers.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Commit {
    pub commit_seq: u64,
    pub tick: u64,
    pub roster_rev: u32,
    pub events: Vec<Event>,
    pub inputs: Vec<SlotInput>,
    pub chain: [u8; 32],
}

impl Commit {
    pub fn body_bytes(&self) -> Vec<u8> {
        let mut p = Vec::with_capacity(64);
        p.extend_from_slice(&self.commit_seq.to_le_bytes());
        p.extend_from_slice(&self.tick.to_le_bytes());
        p.extend_from_slice(&self.roster_rev.to_le_bytes());
        p.push(self.events.len() as u8);
        for e in &self.events {
            p.extend_from_slice(&e.encode());
        }
        p.push(self.inputs.len() as u8);
        for i in &self.inputs {
            p.extend_from_slice(&i.encode());
        }
        p
    }

    pub fn encode(&self) -> Vec<u8> {
        let mut p = self.body_bytes();
        p.extend_from_slice(&self.chain);
        p
    }

    pub fn decode(payload: &[u8]) -> Result<Self, CodecError> {
        if payload.len() < 20 + 32 {
            return Err(CodecError::BadLength);
        }
        let body_end = payload.len() - 32;
        let commit_seq = le_u64(&payload[0..8])?;
        let tick = le_u64(&payload[8..16])?;
        let roster_rev = le_u32(&payload[16..20])?;
        let mut off = 20;
        if off >= body_end {
            return Err(CodecError::BadLength);
        }
        let nevents = payload[off] as usize;
        off += 1;
        if nevents > 4 {
            return Err(CodecError::Oversize);
        }
        let mut events = Vec::with_capacity(nevents);
        for _ in 0..nevents {
            let (e, used) = Event::decode_one(&payload[off..body_end])?;
            events.push(e);
            off += used;
        }
        if off >= body_end {
            return Err(CodecError::BadLength);
        }
        let ninputs = payload[off] as usize;
        off += 1;
        if ninputs > 4 {
            return Err(CodecError::Oversize);
        }
        let mut inputs = Vec::with_capacity(ninputs);
        for _ in 0..ninputs {
            let (i, used) = SlotInput::decode_one(&payload[off..body_end])?;
            inputs.push(i);
            off += used;
        }
        no_trail(&payload[..body_end], off)?;
        let mut chain = [0u8; 32];
        chain.copy_from_slice(&payload[body_end..]);
        Ok(Self { commit_seq, tick, roster_rev, events, inputs, chain })
    }
}

// ------------------------------------------------------------------- ACK

/// `0x12 ACK`, all→relay. Receipt is the highest contiguous commit
/// stored/validated; completion is the last fully simulated interval, never
/// received/prequeued. Honest implementations assert completion ≤ receipt.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Ack {
    pub received_seq: u64,
    pub received_chain: [u8; 32],
    pub completed_tick: u64,
    pub control_seq: u64,
    pub checksum_boundary: u64,
}

impl Ack {
    pub fn encode(&self) -> Vec<u8> {
        let mut p = Vec::with_capacity(64);
        p.extend_from_slice(&self.received_seq.to_le_bytes());
        p.extend_from_slice(&self.received_chain);
        p.extend_from_slice(&self.completed_tick.to_le_bytes());
        p.extend_from_slice(&self.control_seq.to_le_bytes());
        p.extend_from_slice(&self.checksum_boundary.to_le_bytes());
        p
    }

    pub fn decode(payload: &[u8]) -> Result<Self, CodecError> {
        if payload.len() != 64 {
            return Err(CodecError::BadLength);
        }
        Ok(Self {
            received_seq: le_u64(&payload[0..8])?,
            received_chain: exact_32(&payload[8..40])?,
            completed_tick: le_u64(&payload[40..48])?,
            control_seq: le_u64(&payload[48..56])?,
            checksum_boundary: le_u64(&payload[56..64])?,
        })
    }
}

// ------------------------------------------------------------------ NACK

/// `0x13 NACK`, all→relay: 1..=64 commits; the relay retransmits the
/// retained exact payloads and never reconstructs a different commit.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Nack {
    pub first_missing_seq: u64,
    pub count: u16,
}

impl Nack {
    pub fn encode(&self) -> Vec<u8> {
        let mut p = Vec::with_capacity(10);
        p.extend_from_slice(&self.first_missing_seq.to_le_bytes());
        p.extend_from_slice(&self.count.to_le_bytes());
        p
    }

    pub fn decode(payload: &[u8]) -> Result<Self, CodecError> {
        if payload.len() != 10 {
            return Err(CodecError::BadLength);
        }
        let count = le_u16(&payload[8..10])?;
        if count < 1 || count > 64 {
            return Err(CodecError::BadCount);
        }
        Ok(Self { first_missing_seq: le_u64(&payload[0..8])?, count })
    }
}

// -------------------------------------------------------------- CHECKSUM

/// `0x20 CHECKSUM`, client/referee→relay. Same-boundary comparison only;
/// the relay withholds the referee digest from clients until their reports
/// are fixed (no cheat-proof attestation claim).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Checksum {
    pub boundary: u64,
    pub last_completed_tick: u64,
    pub commit_seq: u64,
    pub commit_chain: [u8; 32],
    pub schema: u32,
    pub state: StateDigest,
    pub transcript: [u8; 32],
    pub transcript_records: u64,
    pub roster_rev: u32,
}

impl Checksum {
    pub fn encode(&self) -> Vec<u8> {
        let mut p = Vec::with_capacity(137);
        p.extend_from_slice(&self.boundary.to_le_bytes());
        p.extend_from_slice(&self.last_completed_tick.to_le_bytes());
        p.extend_from_slice(&self.commit_seq.to_le_bytes());
        p.extend_from_slice(&self.commit_chain);
        p.extend_from_slice(&self.schema.to_le_bytes());
        p.extend_from_slice(&self.state.encode());
        p.extend_from_slice(&self.transcript);
        p.extend_from_slice(&self.transcript_records.to_le_bytes());
        p.extend_from_slice(&self.roster_rev.to_le_bytes());
        p
    }

    pub fn decode(payload: &[u8]) -> Result<Self, CodecError> {
        if payload.len() != 137 {
            return Err(CodecError::BadLength);
        }
        let (state, _) = StateDigest::decode(&payload[60..93])?;
        Ok(Self {
            boundary: le_u64(&payload[0..8])?,
            last_completed_tick: le_u64(&payload[8..16])?,
            commit_seq: le_u64(&payload[16..24])?,
            commit_chain: exact_32(&payload[24..56])?,
            schema: le_u32(&payload[56..60])?,
            state,
            transcript: exact_32(&payload[93..125])?,
            transcript_records: le_u64(&payload[125..133])?,
            roster_rev: le_u32(&payload[133..137])?,
        })
    }
}

// ---------------------------------------------------------- referee IPC

/// `0x21 REF_PROGRESS`, referee→relay (private IPC): every 8 ticks or
/// 50 ms. A heartbeat without tick progress never resets a stalled-referee
/// deadline.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RefProgress {
    pub completed_tick: u64,
    pub commit_seq: u64,
    pub chain: [u8; 32],
    pub last_checkpoint: u64,
    pub phase: u8,
}

impl RefProgress {
    pub fn encode(&self) -> Vec<u8> {
        let mut p = Vec::with_capacity(57);
        p.extend_from_slice(&self.completed_tick.to_le_bytes());
        p.extend_from_slice(&self.commit_seq.to_le_bytes());
        p.extend_from_slice(&self.chain);
        p.extend_from_slice(&self.last_checkpoint.to_le_bytes());
        p.push(self.phase);
        p
    }

    pub fn decode(payload: &[u8]) -> Result<Self, CodecError> {
        if payload.len() != 57 {
            return Err(CodecError::BadLength);
        }
        Ok(Self {
            completed_tick: le_u64(&payload[0..8])?,
            commit_seq: le_u64(&payload[8..16])?,
            chain: exact_32(&payload[16..48])?,
            last_checkpoint: le_u64(&payload[48..56])?,
            phase: payload[56],
        })
    }
}

/// `0x23 REF_END_READY`, referee→relay: the referee observed the agreed
/// race-end condition and requests a terminal boundary, not a result yet.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RefEndReady {
    pub completed_tick: u64,
    pub commit_seq: u64,
    pub chain: [u8; 32],
}

impl RefEndReady {
    pub fn encode(&self) -> Vec<u8> {
        let mut p = Vec::with_capacity(48);
        p.extend_from_slice(&self.completed_tick.to_le_bytes());
        p.extend_from_slice(&self.commit_seq.to_le_bytes());
        p.extend_from_slice(&self.chain);
        p
    }

    pub fn decode(payload: &[u8]) -> Result<Self, CodecError> {
        if payload.len() != 48 {
            return Err(CodecError::BadLength);
        }
        Ok(Self {
            completed_tick: le_u64(&payload[0..8])?,
            commit_seq: le_u64(&payload[8..16])?,
            chain: exact_32(&payload[16..48])?,
        })
    }
}

/// `0x24 FINALIZE`, relay→all: atomically stops appending commits at
/// boundary C+1. Immutable, repeatable, terminal; retransmit is idempotent
/// and receiving it before a missing commit never bypasses the gap.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Finalize {
    pub boundary: u64,
    pub last_commit_seq: u64,
    pub chain: [u8; 32],
    pub roster_rev: u32,
}

impl Finalize {
    pub fn encode(&self) -> Vec<u8> {
        let mut p = Vec::with_capacity(52);
        p.extend_from_slice(&self.boundary.to_le_bytes());
        p.extend_from_slice(&self.last_commit_seq.to_le_bytes());
        p.extend_from_slice(&self.chain);
        p.extend_from_slice(&self.roster_rev.to_le_bytes());
        p
    }

    pub fn decode(payload: &[u8]) -> Result<Self, CodecError> {
        if payload.len() != 52 {
            return Err(CodecError::BadLength);
        }
        Ok(Self {
            boundary: le_u64(&payload[0..8])?,
            last_commit_seq: le_u64(&payload[8..16])?,
            chain: exact_32(&payload[16..48])?,
            roster_rev: le_u32(&payload[48..52])?,
        })
    }
}

// ---------------------------------------------------------------- results

/// `ResultRow = slot:u16, guest_actor:u8, disposition:u8, rank:u8,
/// finish_tick:u64, race_time_ms:u32, score:i64`. `rank=0`/`finish_tick=0`
/// for no classified finish, `race_time_ms=0xffffffff` for unavailable,
/// `score=INT64_MIN` for a mode without score. Ejected players keep the
/// ejected disposition even when their AI finishes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ResultRow {
    pub slot: u16,
    pub guest_actor: u8,
    pub disposition: u8,
    pub rank: u8,
    pub finish_tick: u64,
    pub race_time_ms: u32,
    pub score: i64,
}

impl ResultRow {
    pub const ENCODED: usize = 25;

    pub fn encode(&self) -> [u8; 25] {
        let mut o = [0u8; 25];
        o[0..2].copy_from_slice(&self.slot.to_le_bytes());
        o[2] = self.guest_actor;
        o[3] = self.disposition;
        o[4] = self.rank;
        o[5..13].copy_from_slice(&self.finish_tick.to_le_bytes());
        o[13..17].copy_from_slice(&self.race_time_ms.to_le_bytes());
        o[17..25].copy_from_slice(&self.score.to_le_bytes());
        o
    }

    pub fn decode(b: &[u8]) -> Result<(Self, usize), CodecError> {
        if b.len() < 25 {
            return Err(CodecError::BadLength);
        }
        let disposition = b[3];
        if disposition > DISP_EJECTED {
            return Err(CodecError::InvalidEnum);
        }
        Ok((
            Self {
                slot: le_u16(&b[0..2])?,
                guest_actor: b[2],
                disposition,
                rank: b[4],
                finish_tick: le_u64(&b[5..13])?,
                race_time_ms: le_u32(&b[13..17])?,
                score: le_i64(&b[17..25])?,
            },
            25,
        ))
    }
}

fn decode_rows(b: &[u8]) -> Result<(Vec<ResultRow>, usize), CodecError> {
    if b.is_empty() {
        return Err(CodecError::BadLength);
    }
    let n = b[0] as usize;
    if n > 4 {
        return Err(CodecError::Oversize);
    }
    let mut rows = Vec::with_capacity(n);
    let mut off = 1;
    for _ in 0..n {
        let (r, used) = ResultRow::decode(&b[off..])?;
        rows.push(r);
        off += used;
    }
    Ok((rows, off))
}

/// `0x22 REF_FINISH`, referee→relay (trusted IPC observation only).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RefFinish {
    pub boundary: u64,
    pub final_commit_seq: u64,
    pub chain: [u8; 32],
    pub state: StateDigest,
    pub transcript: [u8; 32],
    pub results: Vec<ResultRow>,
}

impl RefFinish {
    pub fn encode(&self) -> Vec<u8> {
        let mut p = Vec::with_capacity(114 + 25 * self.results.len());
        p.extend_from_slice(&self.boundary.to_le_bytes());
        p.extend_from_slice(&self.final_commit_seq.to_le_bytes());
        p.extend_from_slice(&self.chain);
        p.extend_from_slice(&self.state.encode());
        p.extend_from_slice(&self.transcript);
        p.push(self.results.len() as u8);
        for r in &self.results {
            p.extend_from_slice(&r.encode());
        }
        p
    }

    pub fn decode(payload: &[u8]) -> Result<Self, CodecError> {
        if payload.len() < 114 {
            return Err(CodecError::BadLength);
        }
        let (state, _) = StateDigest::decode(&payload[48..81])?;
        let (results, used) = decode_rows(&payload[113..])?;
        no_trail(payload, 113 + used)?;
        Ok(Self {
            boundary: le_u64(&payload[0..8])?,
            final_commit_seq: le_u64(&payload[8..16])?,
            chain: exact_32(&payload[16..48])?,
            state,
            transcript: exact_32(&payload[81..113])?,
            results,
        })
    }
}

// ------------------------------------------------------------- membership

/// `0x30 QUIT`, client→relay: own connection only, idempotent; initiates
/// the drop at the next uncommitted tick. No guest Start/Quit menu input.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Quit {
    pub reason: u16,
}

impl Quit {
    pub fn encode(&self) -> Vec<u8> {
        self.reason.to_le_bytes().to_vec()
    }

    pub fn decode(payload: &[u8]) -> Result<Self, CodecError> {
        if payload.len() != 2 {
            return Err(CodecError::BadLength);
        }
        let reason = le_u16(payload)?;
        if reason > MAX_REASON {
            return Err(CodecError::InvalidEnum);
        }
        Ok(Self { reason })
    }
}

/// `0x31 EJECT`, relay→client: a notification, never the authoritative
/// membership event (the DROP in the immutable COMMIT is). Best-effort, then
/// close within 250 ms.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Eject {
    pub slot: u16,
    pub reason: u16,
    pub evidence_boundary: u64,
    pub drop_seq: u64,
    pub drop_tick: u64,
}

impl Eject {
    pub fn encode(&self) -> Vec<u8> {
        let mut p = Vec::with_capacity(28);
        p.extend_from_slice(&self.slot.to_le_bytes());
        p.extend_from_slice(&self.reason.to_le_bytes());
        p.extend_from_slice(&self.evidence_boundary.to_le_bytes());
        p.extend_from_slice(&self.drop_seq.to_le_bytes());
        p.extend_from_slice(&self.drop_tick.to_le_bytes());
        p
    }

    pub fn decode(payload: &[u8]) -> Result<Self, CodecError> {
        if payload.len() != 28 {
            return Err(CodecError::BadLength);
        }
        let reason = le_u16(&payload[2..4])?;
        if reason > MAX_REASON {
            return Err(CodecError::InvalidEnum);
        }
        Ok(Self {
            slot: le_u16(&payload[0..2])?,
            reason,
            evidence_boundary: le_u64(&payload[4..12])?,
            drop_seq: le_u64(&payload[12..20])?,
            drop_tick: le_u64(&payload[20..28])?,
        })
    }
}

/// `0x32 RESULT`, relay→clients: issued once only after the referee reaches
/// and validates the finish. Retries retain the identical result id/payload.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NetResult {
    pub result_id: [u8; 32],
    pub final_boundary: u64,
    pub final_commit_seq: u64,
    pub chain: [u8; 32],
    pub state: StateDigest,
    pub transcript: [u8; 32],
    pub results: Vec<ResultRow>,
}

impl NetResult {
    pub fn encode(&self) -> Vec<u8> {
        let mut p = Vec::with_capacity(146 + 25 * self.results.len());
        p.extend_from_slice(&self.result_id);
        p.extend_from_slice(&self.final_boundary.to_le_bytes());
        p.extend_from_slice(&self.final_commit_seq.to_le_bytes());
        p.extend_from_slice(&self.chain);
        p.extend_from_slice(&self.state.encode());
        p.extend_from_slice(&self.transcript);
        p.push(self.results.len() as u8);
        for r in &self.results {
            p.extend_from_slice(&r.encode());
        }
        p
    }

    pub fn decode(payload: &[u8]) -> Result<Self, CodecError> {
        if payload.len() < 146 {
            return Err(CodecError::BadLength);
        }
        let (state, _) = StateDigest::decode(&payload[80..113])?;
        let (results, used) = decode_rows(&payload[145..])?;
        no_trail(payload, 145 + used)?;
        Ok(Self {
            result_id: exact_32(&payload[0..32])?,
            final_boundary: le_u64(&payload[32..40])?,
            final_commit_seq: le_u64(&payload[40..48])?,
            chain: exact_32(&payload[48..80])?,
            state,
            transcript: exact_32(&payload[113..145])?,
            results,
        })
    }
}

/// `0x33 CLOSE`, relay→all: terminal, never a drop or recoverable pause.
/// `validated=1` only for the preceding `RESULT`; otherwise a zero id.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Close {
    pub reason: u16,
    pub last_commit_seq: u64,
    pub last_referee_tick: u64,
    pub validated: u8,
    pub result_id: [u8; 32],
}

impl Close {
    pub fn encode(&self) -> Vec<u8> {
        let mut p = Vec::with_capacity(51);
        p.extend_from_slice(&self.reason.to_le_bytes());
        p.extend_from_slice(&self.last_commit_seq.to_le_bytes());
        p.extend_from_slice(&self.last_referee_tick.to_le_bytes());
        p.push(self.validated);
        p.extend_from_slice(&self.result_id);
        p
    }

    pub fn decode(payload: &[u8]) -> Result<Self, CodecError> {
        if payload.len() != 51 {
            return Err(CodecError::BadLength);
        }
        let reason = le_u16(&payload[0..2])?;
        if reason > MAX_REASON {
            return Err(CodecError::InvalidEnum);
        }
        if payload[18] > 1 {
            return Err(CodecError::InvalidEnum);
        }
        Ok(Self {
            reason,
            last_commit_seq: le_u64(&payload[2..10])?,
            last_referee_tick: le_u64(&payload[10..18])?,
            validated: payload[18],
            result_id: exact_32(&payload[19..51])?,
        })
    }
}

// ------------------------------------------------------------------ ping

/// `0x40 PING` / `0x41 PONG`: RTT only. The origin value is echoed and never
/// feeds a guest clock or chooses event order.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Ping {
    pub nonce: u64,
    pub origin_monotonic_us: u64,
}

impl Ping {
    pub fn encode(&self) -> Vec<u8> {
        let mut p = Vec::with_capacity(16);
        p.extend_from_slice(&self.nonce.to_le_bytes());
        p.extend_from_slice(&self.origin_monotonic_us.to_le_bytes());
        p
    }

    pub fn decode(payload: &[u8]) -> Result<Self, CodecError> {
        if payload.len() != 16 {
            return Err(CodecError::BadLength);
        }
        Ok(Self {
            nonce: le_u64(&payload[0..8])?,
            origin_monotonic_us: le_u64(&payload[8..16])?,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn unhex(h: &str) -> Vec<u8> {
        assert!(h.len() % 2 == 0);
        (0..h.len())
            .step_by(2)
            .map(|i| u8::from_str_radix(&h[i..i + 2], 16).expect("hex"))
            .collect()
    }

    fn hex(b: &[u8]) -> String {
        b.iter().map(|x| format!("{x:02x}")).collect()
    }

    #[test]
    fn sha256_anchor() {
        assert_eq!(
            hex(&super::sha256(b"abc")),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
    }

    /// P1a wire-compatibility lock: every `SSXNP/2-P1a` vector from
    /// `local/research/np-link-1/wire-vectors.json` decodes here with
    /// identical fields, and re-encodes to the identical bytes. Any drift
    /// breaks this test before it can break a P1a peer.
    #[test]
    fn p1a_vectors_decode_and_round_trip() {
        let zeros16 = [0u8; 16];
        let cases: &[(&str, &str, u8, u16, u64)] = &[
            ("JOIN-A", "8d000000534e50320200010000000000000000000000000000000000000000000000000000000000010000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000001", KIND_JOIN, 0, 1),
            ("JOIN-B", "8d000000534e50320200010000000000000000000000000000000000000000000000000001000000010000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000001", KIND_JOIN, 1, 1),
            ("CONFIG", "6f000000534e503202000200000000000000000000000000000000000000000000000000feff00000100000000000000000000000000000000000000000000000000000000000000000000000000000001a00f000000000000080078001e000100000001010000000200000000010100010101", KIND_CONFIG, SENDER_RELAY, 1),
            ("READY", "7d000000534e503202000300000000000000000000000000000000000000000000000000000000000200000000000000a00f00000000000001000000010000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000001000000", KIND_READY, 0, 2),
            ("START", "62000000534e503202000400000000000000000000000000000000000000000000000000feff00000100000000000000a00f00000000000001000000000000000100000000000000000000000000000000000000000000000000000000000000000000000000", KIND_START, SENDER_RELAY, 1),
            ("INPUT-A-4000", "3c000000534e503202001000000000000000000000000000000000000000000000000000000000000300000000000000a00f0000000000000001f7ff80804080", KIND_INPUT, 0, 3),
            ("INPUT-B-4000", "42000000534e503202001000000000000000000000000000000000000000000000000000010000000200000000000000a00f0000000000000101000000001122334400000000", KIND_INPUT, 1, 2),
            ("COMMIT-4000", "7a000000534e503202001100000000000000000000000000000000000000000000000000feff000002000000000000000100000000000000a00f000000000000010000000002000000ffff80804080010001000000001122334400000000ba9373de9ed78e4a341deaf9957517fe76ce8ff47b613467196848698f8b4726", KIND_COMMIT, SENDER_RELAY, 2),
            ("COMMIT-4001", "7a000000534e503202001100000000000000000000000000000000000000000000000000feff000003000000000000000200000000000000a10f000000000000010000000002000000ffff80804080010001000000001122334400000000b915650f134a85bd585d8efdcf677edc5a3203afcb0e0c2f783daed526a4147b", KIND_COMMIT, SENDER_RELAY, 3),
        ];
        for (name, hx, kind, sender, seq) in cases {
            let raw = unhex(hx);
            let f = decode_frame(&raw).unwrap_or_else(|e| panic!("{name}: {e}"));
            assert_eq!(f.kind, *kind, "{name} kind");
            assert_eq!(f.sender, *sender, "{name} sender");
            assert_eq!(f.seq, *seq, "{name} seq");
            assert_eq!(f.session, &zeros16, "{name} session");
            assert_eq!(f.epoch, 0, "{name} epoch");
            // Re-encode reproduces the P1a bytes exactly.
            let back = encode_frame(f.session, f.epoch, f.kind, f.sender, f.seq, f.payload);
            assert_eq!(hex(&back), *hx, "{name} round-trip");
        }
        // P1a payload shapes decode with P1a-fixed values.
        let cfg_raw = unhex(cases[2].1);
        let cfg_f = decode_frame(&cfg_raw).unwrap();
        let cfg = Config::decode(cfg_f.payload).unwrap();
        assert_eq!(cfg.first_tick, 4000);
        assert_eq!(cfg.delay_n, 8);
        assert_eq!(cfg.hash_every, 120);
        assert_eq!(cfg.lag_limit, 30);
        assert_eq!(cfg.schema, 1);
        assert_eq!(cfg.state_alg, STATE_ALG_FNV64_DIAG);
        assert_eq!(cfg.roster.len(), 2);
        assert_eq!(cfg.roster[0].slot, 0);
        assert_eq!(cfg.roster[0].input_kind, INPUT_PAD6);
        assert_eq!(cfg.roster[1].input_kind, INPUT_RIDER12);
        let c0_raw = unhex(cases[7].1);
        let c0_f = decode_frame(&c0_raw).unwrap();
        let c0 = Commit::decode(c0_f.payload).unwrap();
        assert_eq!((c0.commit_seq, c0.tick), (1, 4000));
        assert!(c0.events.is_empty());
        assert_eq!(c0.inputs.len(), 2);
        // Start filtering was applied to the A tuple on the wire (f7->ff).
        assert_eq!(&c0.inputs[0].bytes, &[0xff, 0xff, 0x80, 0x80, 0x40, 0x80]);
        // Recomputed chain matches the P1a genesis chain (same domain).
        let body = c0.body_bytes();
        let chain = commit_chain(&[0u8; 32], &zeros16, 0, &body);
        assert_eq!(hex(&chain), "ba9373de9ed78e4a341deaf9957517fe76ce8ff47b613467196848698f8b4726");
        assert_eq!(chain, c0.chain);
        let c1_raw = unhex(cases[8].1);
        let c1 = Commit::decode(decode_frame(&c1_raw).unwrap().payload).unwrap();
        assert_eq!(commit_chain(&c0.chain, &zeros16, 0, &c1.body_bytes()), c1.chain);
        assert_eq!(hex(&c1.chain), "b915650f134a85bd585d8efdcf677edc5a3203afcb0e0c2f783daed526a4147b");
    }

    #[test]
    fn frame_strict_rejections() {
        let good = encode_frame(&[0u8; 16], 0, KIND_PING, 3, 9, &Ping { nonce: 1, origin_monotonic_us: 2 }.encode());
        let mut bad = good.clone();
        bad.push(0);
        assert_eq!(decode_frame(&bad).unwrap_err(), CodecError::TrailingBytes);
        let mut m = good.clone();
        m[4] = b'X';
        assert_eq!(decode_frame(&m).unwrap_err(), CodecError::BadMagic);
        let mut m = good.clone();
        m[8] = 3;
        assert_eq!(decode_frame(&m).unwrap_err(), CodecError::BadVersion);
        let mut m = good.clone();
        m[11] = 1;
        assert_eq!(decode_frame(&m).unwrap_err(), CodecError::NonzeroFlags);
        let mut m = good.clone();
        m[38] = 1;
        assert_eq!(decode_frame(&m).unwrap_err(), CodecError::NonzeroReserved);
        let mut m = good.clone();
        m[40..48].copy_from_slice(&0u64.to_le_bytes());
        assert_eq!(decode_frame(&m).unwrap_err(), CodecError::ZeroSeq);
        let mut m = good.clone();
        m[0] = 10;
        assert_eq!(decode_frame(&m).unwrap_err(), CodecError::BadFrameBytes);
        assert_eq!(decode_frame(&good[..10]).unwrap_err(), CodecError::ShortHeader);
    }

    #[test]
    fn every_message_round_trips() {
        let z32 = [7u8; 32];
        let z16 = [9u8; 16];
        let st = StateDigest { alg: STATE_ALG_SHA256, bytes: z32 };
        let row = ResultRow {
            slot: 1, guest_actor: 1, disposition: DISP_FINISHED, rank: 2,
            finish_tick: 25854, race_time_ms: 215_436, score: 1_250_000,
        };
        assert_eq!(ResultRow::decode(&row.encode()).unwrap().0, row);
        let msgs: Vec<(u8, Vec<u8>)> = vec![
            (KIND_JOIN, Join { credential: z32, compatibility_id: z32, platform_build_id: z32, profile: 1 }.encode()),
            (KIND_CONFIG, Config {
                compatibility_id: z32, profile: 1, first_tick: 4000, delay_n: 8,
                hash_every: 120, lag_limit: 30, schema: 1, state_alg: 1,
                roster_rev: 1,
                roster: vec![
                    SlotDesc { slot: 0, guest_actor: 0, input_kind: INPUT_PAD6, fallback: 1 },
                    SlotDesc { slot: 1, guest_actor: 1, input_kind: INPUT_RIDER12, fallback: 1 },
                ],
            }.encode()),
            (KIND_READY, Ready { boundary: 4000, schema: 1, state: st, boot_transcript: z32, roster_rev: 1 }.encode()),
            (KIND_START, Start { first_tick: 4000, first_commit_seq: 1, roster_rev: 1, initial_chain: z32, countdown_ms: 0 }.encode()),
            (KIND_INPUT, InputMsg { first_tick: 4000, input_kind: INPUT_PAD6, samples: vec![vec![0xff, 0xff, 0x80, 0x80, 0x80, 0x80]; 4] }.encode()),
            (KIND_INPUT, InputMsg { first_tick: 4001, input_kind: INPUT_RIDER12, samples: vec![vec![3u8; 12]] }.encode()),
            (KIND_ACK, Ack { received_seq: 9, received_chain: z32, completed_tick: 4008, control_seq: 3, checksum_boundary: 3960 }.encode()),
            (KIND_NACK, Nack { first_missing_seq: 7, count: 3 }.encode()),
            (KIND_CHECKSUM, Checksum {
                boundary: 4080, last_completed_tick: 4079, commit_seq: 81, commit_chain: z32,
                schema: 1, state: st, transcript: z32, transcript_records: 160, roster_rev: 1,
            }.encode()),
            (KIND_REF_PROGRESS, RefProgress { completed_tick: 4079, commit_seq: 80, chain: z32, last_checkpoint: 3960, phase: PHASE_RUNNING }.encode()),
            (KIND_REF_END_READY, RefEndReady { completed_tick: 25854, commit_seq: 21855, chain: z32 }.encode()),
            (KIND_FINALIZE, Finalize { boundary: 25855, last_commit_seq: 21855, chain: z32, roster_rev: 2 }.encode()),
            (KIND_REF_FINISH, RefFinish { boundary: 25855, final_commit_seq: 21855, chain: z32, state: st, transcript: z32, results: vec![row] }.encode()),
            (KIND_QUIT, Quit { reason: REASON_QUIT }.encode()),
            (KIND_EJECT, Eject { slot: 1, reason: REASON_STATE_MISMATCH, evidence_boundary: 4080, drop_seq: 82, drop_tick: 4081 }.encode()),
            (KIND_RESULT, NetResult {
                result_id: z32, final_boundary: 25855, final_commit_seq: 21855, chain: z32,
                state: st, transcript: z32, results: vec![row],
            }.encode()),
            (KIND_CLOSE, Close { reason: REASON_QUIT, last_commit_seq: 21855, last_referee_tick: 25854, validated: 1, result_id: z32 }.encode()),
            (KIND_PING, Ping { nonce: 42, origin_monotonic_us: 1_000 }.encode()),
            (KIND_PONG, Ping { nonce: 42, origin_monotonic_us: 1_000 }.encode()),
        ];
        for (kind, payload) in &msgs {
            let frame = encode_frame(&z16, 77, *kind, 3, 5, payload);
            let f = decode_frame(&frame).unwrap();
            assert_eq!((f.kind, f.sender, f.seq, f.epoch), (*kind, 3, 5, 77));
            assert_eq!(f.session, &z16);
            let back = encode_frame(f.session, f.epoch, f.kind, f.sender, f.seq, f.payload);
            assert_eq!(back, frame, "kind {kind:#x} round-trip");
        }
        // Typed re-decode of a sample of the new payloads.
        assert_eq!(Join::decode(&msgs[0].1).unwrap().profile, 1);
        assert_eq!(Nack::decode(&msgs[7].1).unwrap().count, 3);
        assert_eq!(Quit::decode(&msgs[13].1).unwrap().reason, REASON_QUIT);
        assert_eq!(Close::decode(&msgs[16].1).unwrap().validated, 1);
        assert_eq!(Commit::decode(&{
            let c = Commit {
                commit_seq: 82, tick: 4081, roster_rev: 2,
                events: vec![Event { event_seq: 1, event_type: EVENT_DROP_TO_AI,
                    drop: DropToAi { slot: 1, reason: REASON_INPUT_TIMEOUT, evidence_boundary: 4080 } }],
                inputs: vec![SlotInput { slot: 0, kind: INPUT_PAD6, bytes: vec![0xff; 6] }],
                chain: z32,
            };
            c.encode()
        }).unwrap().events.len(), 1);
    }

    #[test]
    fn payload_strictness() {
        // INPUT count 0 and 5 rejected; trailing byte rejected; bad kind rejected.
        let mut p = InputMsg { first_tick: 1, input_kind: INPUT_PAD6, samples: vec![vec![0u8; 6]] }.encode();
        let mut zero = p.clone();
        zero[9] = 0;
        zero.truncate(10);
        assert_eq!(InputMsg::decode(&zero).unwrap_err(), CodecError::BadCount);
        p.push(0);
        assert_eq!(InputMsg::decode(&p).unwrap_err(), CodecError::TrailingBytes);
        let bad_kind = InputMsg { first_tick: 1, input_kind: 9, samples: vec![vec![0u8; 1]] }.encode();
        assert_eq!(InputMsg::decode(&bad_kind).unwrap_err(), CodecError::InvalidEnum);
        // COMMIT caps: 5 events / 5 inputs rejected.
        let z32 = [0u8; 32];
        let ev = Event { event_seq: 1, event_type: EVENT_DROP_TO_AI,
            drop: DropToAi { slot: 0, reason: 1, evidence_boundary: 1 } };
        let c = Commit {
            commit_seq: 1, tick: 1, roster_rev: 1,
            events: vec![ev.clone(), ev.clone(), ev.clone(), ev.clone(), ev.clone()],
            inputs: vec![], chain: z32,
        };
        assert_eq!(Commit::decode(&c.encode()).unwrap_err(), CodecError::Oversize);
        // CONFIG roster cap 5 rejected.
        let cfg = Config {
            compatibility_id: z32, profile: 1, first_tick: 1, delay_n: 8,
            hash_every: 120, lag_limit: 30, schema: 1, state_alg: 1, roster_rev: 1,
            roster: vec![SlotDesc { slot: 0, guest_actor: 0, input_kind: 0, fallback: 1 }; 5],
        };
        assert_eq!(Config::decode(&cfg.encode()).unwrap_err(), CodecError::Oversize);
        // Unknown reason / disposition / state alg rejected.
        assert_eq!(Quit::decode(&[15u8, 0]).unwrap_err(), CodecError::InvalidEnum);
        assert_eq!(Close::decode(&{
            let mut v = Close { reason: 1, last_commit_seq: 1, last_referee_tick: 1, validated: 2, result_id: z32 }.encode();
            v[18] = 2;
            v
        }).unwrap_err(), CodecError::InvalidEnum);
        let mut st = StateDigest { alg: 9, bytes: z32 }.encode().to_vec();
        assert!(StateDigest::decode(&st).is_err());
        st[0] = 1;
        assert!(StateDigest::decode(&st[..32]).is_err());
    }
}

// ---------------------------------------------------------------- state sync
// (netsync-spike-1 Part 3: 120Hz state-sync extension on the v2 base above,
// which is untouched. `KIND_STATE` carries one rider's full ghost sample;
// `KIND_EVENT` carries a standalone `Event` (DROP_TO_AI) outside commits.)

/// `0x14 STATE`, client→relay→peer: one rider sample per update.
pub const KIND_STATE: u8 = 0x14;
/// `0x16 EVENT`, relay→peer: standalone event (DROP_TO_AI only).
pub const KIND_EVENT: u8 = 0x16;
/// Drop reason: no STATE from the slot within the timeout.
pub const REASON_STATE_TIMEOUT: u16 = 15;
/// Peer-view finish report follows the owner report by this many ticks.
pub const FINISH_PEER_WINDOW_TICKS: u64 = 1000;

fn lef32(b: &[u8]) -> Result<f32, CodecError> {
    if b.len() != 4 {
        return Err(CodecError::BadLength);
    }
    Ok(f32::from_le_bytes([b[0], b[1], b[2], b[3]]))
}

/// `StateUpdate` wire payload (all LE, 111 bytes = 91 core + 20 input tail):
/// `slot:u16, tick:u64, pos:3f, quat:4f, vel:3f, boost:f32, uber:u8,
/// wipe:u8, ctl:u8, lap:u8, chk:u32, dist:f32, fin:u8, ftime:f32,
/// peer_tick:u64, peer_time_ms:u32, digest:u64`.
/// `digest` is FNV-1a/64 over the preceding 103 bytes
/// (`STATE_ALG_FNV64_DIAG`).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct StateUpdate {
    pub slot: u16,
    pub tick: u64,
    pub pos: [f32; 3],
    pub quat: [f32; 4],
    pub vel: [f32; 3],
    pub boost: f32,
    pub uber: u8,
    pub wipe: u8,
    pub ctl: u8,
    pub lap: u8,
    pub chk: u32,
    pub dist: f32,
    pub fin: u8,
    pub ftime: f32,
    pub peer_tick: u64,
    pub peer_time_ms: u32,
    pub in_tick: u64,
    pub in_de4: u32,
    pub in_w0: u32,
    pub in_w1: u32,
    pub digest: u64,
}

impl StateUpdate {
    pub const ENCODED: usize = 111;

    pub fn encode(&self) -> [u8; 111] {
        let mut o = [0u8; 111];
        o[0..2].copy_from_slice(&self.slot.to_le_bytes());
        o[2..10].copy_from_slice(&self.tick.to_le_bytes());
        for (i, v) in self.pos.iter().enumerate() {
            o[10 + 4 * i..14 + 4 * i].copy_from_slice(&v.to_le_bytes());
        }
        for (i, v) in self.quat.iter().enumerate() {
            o[22 + 4 * i..26 + 4 * i].copy_from_slice(&v.to_le_bytes());
        }
        for (i, v) in self.vel.iter().enumerate() {
            o[38 + 4 * i..42 + 4 * i].copy_from_slice(&v.to_le_bytes());
        }
        o[50..54].copy_from_slice(&self.boost.to_le_bytes());
        o[54] = self.uber;
        o[55] = self.wipe;
        o[56] = self.ctl;
        o[57] = self.lap;
        o[58..62].copy_from_slice(&self.chk.to_le_bytes());
        o[62..66].copy_from_slice(&self.dist.to_le_bytes());
        o[66] = self.fin;
        o[67..71].copy_from_slice(&self.ftime.to_le_bytes());
        o[71..79].copy_from_slice(&self.peer_tick.to_le_bytes());
        o[79..83].copy_from_slice(&self.peer_time_ms.to_le_bytes());
        o[83..91].copy_from_slice(&self.in_tick.to_le_bytes());
        o[91..95].copy_from_slice(&self.in_de4.to_le_bytes());
        o[95..99].copy_from_slice(&self.in_w0.to_le_bytes());
        o[99..103].copy_from_slice(&self.in_w1.to_le_bytes());
        let digest = fnv64(&o[..103]).to_le_bytes();
        o[103..111].copy_from_slice(&digest);
        o
    }

    pub fn decode(b: &[u8]) -> Result<Self, CodecError> {
        if b.len() != 111 {
            return Err(CodecError::BadLength);
        }
        if fnv64(&b[..103]) != le_u64(&b[103..111])? {
            return Err(CodecError::BadLength);
        }
        let mut pos = [0f32; 3];
        let mut quat = [0f32; 4];
        let mut vel = [0f32; 3];
        for (i, v) in pos.iter_mut().enumerate() {
            *v = lef32(&b[10 + 4 * i..14 + 4 * i])?;
        }
        for (i, v) in quat.iter_mut().enumerate() {
            *v = lef32(&b[22 + 4 * i..26 + 4 * i])?;
        }
        for (i, v) in vel.iter_mut().enumerate() {
            *v = lef32(&b[38 + 4 * i..42 + 4 * i])?;
        }
        Ok(Self {
            slot: le_u16(&b[0..2])?,
            tick: le_u64(&b[2..10])?,
            pos,
            quat,
            vel,
            boost: lef32(&b[50..54])?,
            uber: b[54],
            wipe: b[55],
            ctl: b[56],
            lap: b[57],
            chk: le_u32(&b[58..62])?,
            dist: lef32(&b[62..66])?,
            fin: b[66],
            ftime: lef32(&b[67..71])?,
            peer_tick: le_u64(&b[71..79])?,
            peer_time_ms: le_u32(&b[79..83])?,
            in_tick: le_u64(&b[83..91])?,
            in_de4: le_u32(&b[91..95])?,
            in_w0: le_u32(&b[95..99])?,
            in_w1: le_u32(&b[99..103])?,
            digest: le_u64(&b[103..111])?,
        })
    }
}

fn fnv64(b: &[u8]) -> u64 {
    let mut h: u64 = 0xcbf29ce484222325;
    for &x in b {
        h ^= x as u64;
        h = h.wrapping_mul(0x100000001b3);
    }
    h
}

// ---------------------------------------------------------- quick play v0
// (online-v0-1: 120<->120 Quick Play matchmaking + START barrier on the
// state-sync endpoint. A client sends `QP_JOIN` once it waits at the race
// card; the server pairs two compatible waiters into a room and answers both
// with `QP_START` naming the room (frame session), the client's slot and the
// remaining lead to the shared GO instant. JOIN is resent until START
// arrives, and every resend is answered with the same room and the time left,
// so a lost datagram never splits the start. `QP_GO` reports when the client
// actually started, for the server's start-skew log.)

/// `0x50 QP_JOIN`, client->server: one waiter at the race card.
pub const KIND_QP_JOIN: u8 = 0x50;
/// `0x51 QP_WAIT`, server->client: queued (or refused) while unpaired.
pub const KIND_QP_WAIT: u8 = 0x51;
/// `0x54 QP_START`, server->client: paired; GO after `go_in_ms`.
pub const KIND_QP_START: u8 = 0x54;
/// `0x55 QP_GO`, client->server: start report (skew evidence only).
pub const KIND_QP_GO: u8 = 0x55;
/// Quick Play protocol revision carried in `QP_JOIN`.
pub const QP_PROTO: u16 = 1;
/// The only simulation rate this build pairs (Brad 10-16: 120<->120 only).
pub const QP_RATE_120: u8 = 120;
/// `QP_WAIT` statuses.
pub const QP_WAIT_QUEUED: u8 = 0;
pub const QP_WAIT_FULL: u8 = 1;
pub const QP_WAIT_INCOMPATIBLE: u8 = 2;

/// `QP_JOIN = proto:u16, client:[16], rate:u8, course:u8` (20 bytes).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct QpJoin {
    pub proto: u16,
    pub client: [u8; 16],
    pub rate: u8,
    pub course: u8,
}

impl QpJoin {
    pub const ENCODED: usize = 20;

    pub fn encode(&self) -> Vec<u8> {
        let mut p = Vec::with_capacity(Self::ENCODED);
        p.extend_from_slice(&self.proto.to_le_bytes());
        p.extend_from_slice(&self.client);
        p.push(self.rate);
        p.push(self.course);
        p
    }

    pub fn decode(payload: &[u8]) -> Result<Self, CodecError> {
        if payload.len() != Self::ENCODED {
            return Err(CodecError::BadLength);
        }
        let mut client = [0u8; 16];
        client.copy_from_slice(&payload[2..18]);
        Ok(Self { proto: le_u16(&payload[0..2])?, client, rate: payload[18], course: payload[19] })
    }
}

/// `QP_WAIT = status:u8, waiting:u16` (3 bytes).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct QpWait {
    pub status: u8,
    pub waiting: u16,
}

impl QpWait {
    pub const ENCODED: usize = 3;

    pub fn encode(&self) -> Vec<u8> {
        let mut p = vec![self.status];
        p.extend_from_slice(&self.waiting.to_le_bytes());
        p
    }

    pub fn decode(payload: &[u8]) -> Result<Self, CodecError> {
        if payload.len() != Self::ENCODED {
            return Err(CodecError::BadLength);
        }
        if payload[0] > QP_WAIT_INCOMPATIBLE {
            return Err(CodecError::InvalidEnum);
        }
        Ok(Self { status: payload[0], waiting: le_u16(&payload[1..3])? })
    }
}

/// `QP_START = start_id:u32, slot:u8, players:u8, course:u8, seq:u8,
/// go_in_ms:u16` (10 bytes). The room id rides in the frame session; `seq`
/// counts this seat's START sends (0 = first) so the skew log can match the
/// client's report to the send time.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct QpStart {
    pub start_id: u32,
    pub slot: u8,
    pub players: u8,
    pub course: u8,
    pub seq: u8,
    pub go_in_ms: u16,
}

impl QpStart {
    pub const ENCODED: usize = 10;

    pub fn encode(&self) -> Vec<u8> {
        let mut p = Vec::with_capacity(Self::ENCODED);
        p.extend_from_slice(&self.start_id.to_le_bytes());
        p.push(self.slot);
        p.push(self.players);
        p.push(self.course);
        p.push(self.seq);
        p.extend_from_slice(&self.go_in_ms.to_le_bytes());
        p
    }

    pub fn decode(payload: &[u8]) -> Result<Self, CodecError> {
        if payload.len() != Self::ENCODED {
            return Err(CodecError::BadLength);
        }
        Ok(Self {
            start_id: le_u32(&payload[0..4])?,
            slot: payload[4],
            players: payload[5],
            course: payload[6],
            seq: payload[7],
            go_in_ms: le_u16(&payload[8..10])?,
        })
    }
}

/// `QP_GO = start_id:u32, seq:u8, rtt_us:u32, recv_to_go_us:u32,
/// go_tick:u64` (21 bytes): the START the client used, its RTT estimate,
/// the wall time from that START's arrival to the race clock's first tick,
/// and that tick.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct QpGo {
    pub start_id: u32,
    pub seq: u8,
    pub rtt_us: u32,
    pub recv_to_go_us: u32,
    pub go_tick: u64,
}

impl QpGo {
    pub const ENCODED: usize = 21;

    pub fn encode(&self) -> Vec<u8> {
        let mut p = Vec::with_capacity(Self::ENCODED);
        p.extend_from_slice(&self.start_id.to_le_bytes());
        p.push(self.seq);
        p.extend_from_slice(&self.rtt_us.to_le_bytes());
        p.extend_from_slice(&self.recv_to_go_us.to_le_bytes());
        p.extend_from_slice(&self.go_tick.to_le_bytes());
        p
    }

    pub fn decode(payload: &[u8]) -> Result<Self, CodecError> {
        if payload.len() != Self::ENCODED {
            return Err(CodecError::BadLength);
        }
        Ok(Self {
            start_id: le_u32(&payload[0..4])?,
            seq: payload[4],
            rtt_us: le_u32(&payload[5..9])?,
            recv_to_go_us: le_u32(&payload[9..13])?,
            go_tick: le_u64(&payload[13..21])?,
        })
    }
}

#[cfg(test)]
mod state_tests {
    use super::*;
    fn sample() -> StateUpdate {
        StateUpdate {
            slot: 1, tick: 30703, pos: [8195.5, -42985.25, 91071.5],
            quat: [-0.2662, 0.1706, -0.5357, 0.7830],
            vel: [894.64, 342.0, -764.07], boost: 0.5, uber: 3, wipe: 0,
            ctl: 0, lap: 1, chk: 0x000038a1, dist: 6496.0, fin: 1,
            ftime: 241.5, peer_tick: 30496, peer_time_ms: 239500,
            in_tick: 30703, in_de4: 5, in_w0: 0x11223344, in_w1: 0x55667788,
            digest: 0,
        }
    }
    #[test]
    fn state_round_trip() {
        let s = sample();
        let enc = s.encode();
        assert_eq!(enc.len(), StateUpdate::ENCODED);
        let back = StateUpdate::decode(&enc).unwrap();
        assert_eq!(back.slot, s.slot);
        assert_eq!(back.tick, s.tick);
        assert_eq!(back.pos, s.pos);
        assert_eq!(back.quat, s.quat);
        assert_eq!(back.vel, s.vel);
        assert_eq!((back.boost, back.uber, back.ctl, back.chk, back.dist), (s.boost, s.uber, s.ctl, s.chk, s.dist));
        assert_eq!((back.fin, back.ftime, back.peer_tick, back.peer_time_ms), (s.fin, s.ftime, s.peer_tick, s.peer_time_ms));
        assert_eq!((back.in_tick, back.in_de4, back.in_w0, back.in_w1), (s.in_tick, s.in_de4, s.in_w0, s.in_w1));
    }
    #[test]
    fn quickplay_round_trips() {
        let j = QpJoin { proto: QP_PROTO, client: [7u8; 16], rate: QP_RATE_120, course: 0 };
        assert_eq!(j.encode().len(), QpJoin::ENCODED);
        assert_eq!(QpJoin::decode(&j.encode()).unwrap(), j);
        let w = QpWait { status: QP_WAIT_FULL, waiting: 3 };
        assert_eq!(QpWait::decode(&w.encode()).unwrap(), w);
        let s = QpStart { start_id: 0xdeadbeef, slot: 1, players: 2, course: 0, seq: 2, go_in_ms: 1500 };
        assert_eq!(s.encode().len(), QpStart::ENCODED);
        assert_eq!(QpStart::decode(&s.encode()).unwrap(), s);
        let g = QpGo { start_id: 9, seq: 0, rtt_us: 42_000, recv_to_go_us: 1_480_000, go_tick: 123_456 };
        assert_eq!(g.encode().len(), QpGo::ENCODED);
        assert_eq!(QpGo::decode(&g.encode()).unwrap(), g);
        assert_eq!(QpJoin::decode(&j.encode()[..19]).unwrap_err(), CodecError::BadLength);
        assert_eq!(QpWait::decode(&[9, 0, 0]).unwrap_err(), CodecError::InvalidEnum);
        assert_eq!(QpStart::decode(&[0u8; 11]).unwrap_err(), CodecError::BadLength);
        assert_eq!(QpGo::decode(&[0u8; 20]).unwrap_err(), CodecError::BadLength);
    }
    #[test]
    fn state_strict_rejections() {
        let enc = sample().encode();
        assert_eq!(StateUpdate::decode(&enc[..110]).unwrap_err(), CodecError::BadLength);
        let mut bad = enc;
        bad[0] ^= 0xff;
        assert_eq!(StateUpdate::decode(&bad).unwrap_err(), CodecError::BadLength);
        let mut bad = enc;
        bad[110] ^= 0x01;
        assert_eq!(StateUpdate::decode(&bad).unwrap_err(), CodecError::BadLength);
        let frame = encode_frame(&[0u8; 16], 0, KIND_STATE, 3, 9, &enc);
        assert_eq!(decode_frame(&frame).unwrap().kind, KIND_STATE);
        let mut frame = encode_frame(&[0u8; 16], 0, KIND_EVENT, 3, 9, &Event {
            event_seq: 7, event_type: EVENT_DROP_TO_AI,
            drop: DropToAi { slot: 1, reason: REASON_STATE_TIMEOUT, evidence_boundary: 40000 },
        }.encode());
        frame[10] = KIND_EVENT;
        assert_eq!(decode_frame(&frame).unwrap().kind, KIND_EVENT);
    }
}
