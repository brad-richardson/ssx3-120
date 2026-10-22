// SPDX-License-Identifier: GPL-3.0-or-later OR MIT
//! SSX 3 lobby wire protocol: framing plus phase-1 message types.
//!
//! Clean-room implementation from `docs/research/ssx3-online-protocol.md`
//! (spec) and PRO1's JSON fixtures. Every TCP message on ports
//! 11000/10901/13505 is:
//!
//! ```text
//! type[4] sub[4] size-be[4] body[size-12]
//! ```
//!
//! `body` is `KEY=value` lines separated by `\n`, terminated by one NUL
//! byte (the NUL is inside `size`). An empty body is just the NUL.
//! A body may also carry non-`KEY=value` stray lines (the `news`
//! MOTD, e.g. `WIP Server`); strays are preserved, never dropped.

/// SSXNP v2 netplay codec (framing + message set), plus the state-sync
/// extension (`KIND_STATE`, `KIND_EVENT`, `REASON_STATE_TIMEOUT`).
/// Wired from np-referee-p1 `5302992`; the v2 base is untouched.
pub mod netplay;

use std::str::Utf8Error;

/// 12-byte header length. Minimum legal message size.
pub const HEADER_LEN: usize = 12;

/// Fixture maximum is 1144 bytes (`rank`); 4096 leaves room for
/// longer race results while bounding admission memory.
pub const MAX_MESSAGE_SIZE: usize = 4096;
pub const MAX_FIELDS: usize = 64;
pub const MAX_KEY: usize = 64;
pub const MAX_VALUE: usize = 4096;

pub fn type_limit(typ: [u8; 4]) -> usize {
    if typ == *b"rank" {
        4096
    } else {
        2048
    }
}

/// A decoded message. `typ` is the raw 4 bytes (`auth`, `+ses`, `AUTH`,
/// ...); printable types are available via [`Message::type_str`].
/// `sub` is almost always `00 00 00 00`; the `news` reply uses `new0`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Message {
    pub typ: [u8; 4],
    pub sub: [u8; 4],
    /// Ordered key/value pairs (`KEY=value` lines, in wire order).
    pub pairs: Vec<(String, String)>,
    /// Non-`KEY=value` lines (MOTD strays), in wire order.
    pub strays: Vec<String>,
}

impl Message {
    pub fn new(typ: [u8; 4], sub: [u8; 4]) -> Self {
        Self {
            typ,
            sub,
            pairs: Vec::new(),
            strays: Vec::new(),
        }
    }

    /// ASCII type tag if printable, else `None` (use raw bytes then).
    pub fn type_str(&self) -> Option<&str> {
        let s = std::str::from_utf8(&self.typ).ok()?;
        if s.bytes().all(|b| (0x20..=0x7e).contains(&b)) {
            Some(s)
        } else {
            None
        }
    }

    pub fn get(&self, key: &str) -> Option<&str> {
        self.pairs
            .iter()
            .find(|(k, _)| k == key)
            .map(|(_, v)| v.as_str())
    }
}

/// Decode failure. Never panics; any input maps to one of these.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DecodeError {
    /// Fewer than 12 bytes available for the header.
    TruncatedHeader {
        have: usize,
    },
    /// `size` field < 12 (body would underflow the header).
    BadSize {
        size: u32,
    },
    /// `size` exceeds [`MAX_MESSAGE_SIZE`].
    TooLarge {
        size: u32,
    },
    /// Fewer body bytes available than `size` claims.
    TruncatedBody {
        need: usize,
        have: usize,
    },
    /// Body is not ASCII (values are ASCII except URL-encoded buddy
    /// `PSET` fields, which are still ASCII).
    NonAsciiBody,
    /// Body does not end with the NUL terminator.
    MissingNul,
    TooManyFields,
    FieldTooLong,
}

impl std::fmt::Display for DecodeError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::TruncatedHeader { have } => write!(f, "truncated header ({have} bytes)"),
            Self::BadSize { size } => write!(f, "bad size {size}"),
            Self::TooLarge { size } => write!(f, "message too large ({size} bytes)"),
            Self::TruncatedBody { need, have } => {
                write!(f, "truncated body (need {need}, have {have})")
            }
            Self::NonAsciiBody => write!(f, "non-ASCII body"),
            Self::MissingNul => write!(f, "body missing NUL terminator"),
            Self::TooManyFields => write!(f, "too many fields"),
            Self::FieldTooLong => write!(f, "field too long"),
        }
    }
}

impl std::error::Error for DecodeError {}

/// Split one TCP record into its concatenated messages.
///
/// Returns the messages plus trailing-byte count (a record should
/// normally split evenly; our HLE splits coalesced bytes on the
/// 12-byte header because the game ignores coalesced blobs).
pub fn split_messages(raw: &[u8]) -> Result<(Vec<RawMessage<'_>>, usize), DecodeError> {
    let mut out = Vec::new();
    let mut off = 0;
    while off < raw.len() {
        if raw.len() - off < HEADER_LEN {
            break;
        }
        let size = u32::from_be_bytes([raw[off + 8], raw[off + 9], raw[off + 10], raw[off + 11]]);
        if size < HEADER_LEN as u32 {
            return Err(DecodeError::BadSize { size });
        }
        if size as usize > MAX_MESSAGE_SIZE {
            return Err(DecodeError::TooLarge { size });
        }
        if raw.len() - off < size as usize {
            break;
        }
        let mut typ = [0u8; 4];
        let mut sub = [0u8; 4];
        typ.copy_from_slice(&raw[off..off + 4]);
        if size as usize > type_limit(typ) {
            return Err(DecodeError::TooLarge { size });
        }
        sub.copy_from_slice(&raw[off + 4..off + 8]);
        out.push(RawMessage {
            typ,
            sub,
            body: &raw[off + HEADER_LEN..off + size as usize],
        });
        off += size as usize;
    }
    Ok((out, raw.len() - off))
}

/// One framed message borrowing the record bytes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RawMessage<'a> {
    pub typ: [u8; 4],
    pub sub: [u8; 4],
    /// Body including the trailing NUL.
    pub body: &'a [u8],
}

impl RawMessage<'_> {
    /// Parse the body into pairs + strays. The body must be ASCII and
    /// NUL-terminated (empty body = just the NUL).
    pub fn parse(&self) -> Result<Message, DecodeError> {
        parse_body(self.typ, self.sub, self.body)
    }
}

/// Decode exactly one message from the front of `raw`.
/// Returns the message and bytes consumed, or the decode error.
/// A clean `Ok` with trailing bytes left is normal (concatenation).
pub fn decode_one(raw: &[u8]) -> Result<(Message, usize), DecodeError> {
    if raw.len() < HEADER_LEN {
        return Err(DecodeError::TruncatedHeader { have: raw.len() });
    }
    let size = u32::from_be_bytes([raw[8], raw[9], raw[10], raw[11]]);
    if size < HEADER_LEN as u32 {
        return Err(DecodeError::BadSize { size });
    }
    if size as usize > MAX_MESSAGE_SIZE {
        return Err(DecodeError::TooLarge { size });
    }
    if raw.len() < size as usize {
        return Err(DecodeError::TruncatedBody {
            need: size as usize,
            have: raw.len(),
        });
    }
    let mut typ = [0u8; 4];
    let mut sub = [0u8; 4];
    typ.copy_from_slice(&raw[..4]);
    if size as usize > type_limit(typ) {
        return Err(DecodeError::TooLarge { size });
    }
    sub.copy_from_slice(&raw[4..8]);
    let msg = parse_body(typ, sub, &raw[HEADER_LEN..size as usize])?;
    Ok((msg, size as usize))
}

fn parse_body(typ: [u8; 4], sub: [u8; 4], body: &[u8]) -> Result<Message, DecodeError> {
    if body.is_empty() || *body.last().unwrap() != 0 {
        return Err(DecodeError::MissingNul);
    }
    let text = std::str::from_utf8(&body[..body.len() - 1])
        .map_err(|_: Utf8Error| DecodeError::NonAsciiBody)?;
    // The body must be ASCII; reject control characters other than \n
    // so a hostile peer cannot smuggle framing past log scrubbers.
    if text.bytes().any(|b| (b < 0x20 && b != b'\n') || b > 0x7e) {
        return Err(DecodeError::NonAsciiBody);
    }
    let mut msg = Message::new(typ, sub);
    let mut fields = 0;
    for line in text.split('\n') {
        if line.is_empty() {
            continue;
        }
        fields += 1;
        let max_fields = if typ == *b"rank" { 128 } else { MAX_FIELDS };
        if fields > max_fields {
            return Err(DecodeError::TooManyFields);
        }
        match line.split_once('=') {
            Some((k, v)) => {
                if k.len() > MAX_KEY || v.len() > MAX_VALUE {
                    return Err(DecodeError::FieldTooLong);
                }
                msg.pairs.push((k.to_string(), v.to_string()));
            }
            None => {
                if line.len() > MAX_VALUE {
                    return Err(DecodeError::FieldTooLong);
                }
                msg.strays.push(line.to_string());
            }
        }
    }
    Ok(msg)
}

/// Encode a message: `type[4] sub[4] size-be[4] body NUL`.
/// Strays are emitted after the pairs, one per line (the `news`
/// MOTD slot).
pub fn encode(typ: [u8; 4], sub: [u8; 4], pairs: &[(&str, &str)], strays: &[&str]) -> Vec<u8> {
    let mut body = Vec::new();
    for (k, v) in pairs {
        body.extend_from_slice(k.as_bytes());
        body.push(b'=');
        body.extend_from_slice(v.as_bytes());
        body.push(b'\n');
    }
    for s in strays {
        body.extend_from_slice(s.as_bytes());
        body.push(b'\n');
    }
    body.push(0);
    let mut out = Vec::with_capacity(HEADER_LEN + body.len());
    out.extend_from_slice(&typ);
    out.extend_from_slice(&sub);
    out.extend_from_slice(&((HEADER_LEN + body.len()) as u32).to_be_bytes());
    out.extend_from_slice(&body);
    out
}

/// Encode an already-parsed [`Message`] back to the wire.
pub fn encode_msg(msg: &Message) -> Vec<u8> {
    let pairs: Vec<(&str, &str)> = msg
        .pairs
        .iter()
        .map(|(k, v)| (k.as_str(), v.as_str()))
        .collect();
    let strays: Vec<&str> = msg.strays.iter().map(|s| s.as_str()).collect();
    encode(msg.typ, msg.sub, &pairs, &strays)
}

/// Well-known type tags (spec §3, §5). Lowercase = game/lobby port,
/// UPPERCASE = buddy port 13505.
pub mod types {
    pub const DIR: [u8; 4] = *b"@dir";
    pub const ADDR: [u8; 4] = *b"addr";
    pub const SKEY: [u8; 4] = *b"skey";
    pub const SELE: [u8; 4] = *b"sele";
    pub const AUTH: [u8; 4] = *b"auth";
    pub const ROM: [u8; 4] = *b"+rom";
    pub const PERS: [u8; 4] = *b"pers";
    pub const ONLN: [u8; 4] = *b"onln";
    pub const NEWS: [u8; 4] = *b"news";
    pub const QUIK: [u8; 4] = *b"quik";
    pub const SES: [u8; 4] = *b"+ses";
    pub const RANK: [u8; 4] = *b"rank";
    pub const PNG: [u8; 4] = *b"~png";
    pub const CHAL: [u8; 4] = *b"chal";
    // Buddy (13505).
    pub const DISC: [u8; 4] = *b"DISC";
    pub const BAUTH: [u8; 4] = *b"AUTH";
    pub const PSET: [u8; 4] = *b"PSET";
    pub const RGET: [u8; 4] = *b"RGET";
    pub const PING: [u8; 4] = *b"PING";
    /// `news` reply sub tag (the only nonzero sub observed).
    pub const SUB_NEW0: [u8; 4] = *b"new0";
    pub const SUB_ZERO: [u8; 4] = [0, 0, 0, 0];
}

/// URL-encode a buddy `PSET` STAT/PROD value (`%3d` for `=`, `%0a`
/// for newline, `%22` for quote; spec §1.2). The stock client sends
/// values already encoded; the server echoes them verbatim, but the
/// encoder is needed to *build* presence replies and tests.
pub fn url_encode(raw: &str) -> String {
    let mut out = String::with_capacity(raw.len());
    for b in raw.bytes() {
        match b {
            b'=' => out.push_str("%3d"),
            b'\n' => out.push_str("%0a"),
            b'"' => out.push_str("%22"),
            b'%' => out.push_str("%25"),
            0x20..=0x7e => out.push(b as char),
            _ => out.push_str(&format!("%{b:02x}")),
        }
    }
    out
}

/// Inverse of [`url_encode`]. Malformed `%` runs are kept literally
/// (never fail on peer input).
pub fn url_decode(enc: &str) -> String {
    let bytes = enc.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' && i + 2 < bytes.len() + 1 {
            if let (Some(h), Some(l)) = (hex_val(bytes.get(i + 1)), hex_val(bytes.get(i + 2))) {
                out.push(h * 16 + l);
                i += 3;
                continue;
            }
        }
        out.push(bytes[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

fn hex_val(b: Option<&u8>) -> Option<u8> {
    match b.copied()? {
        c @ b'0'..=b'9' => Some(c - b'0'),
        c @ b'a'..=b'f' => Some(c - b'a' + 10),
        c @ b'A'..=b'F' => Some(c - b'A' + 10),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_body_round_trip() {
        let wire = encode(types::PING, types::SUB_ZERO, &[], &[]);
        assert_eq!(wire.len(), 13);
        let (msg, used) = decode_one(&wire).unwrap();
        assert_eq!(used, 13);
        assert_eq!(msg.typ, types::PING);
        assert!(msg.pairs.is_empty() && msg.strays.is_empty());
    }

    #[test]
    fn concatenated_split() {
        let a = encode(types::QUIK, types::SUB_ZERO, &[("KIND", "DeathRace")], &[]);
        let b = encode(types::PNG, types::SUB_ZERO, &[], &[]);
        let mut rec = a.clone();
        rec.extend_from_slice(&b);
        let (msgs, left) = split_messages(&rec).unwrap();
        assert_eq!(msgs.len(), 2);
        assert_eq!(left, 0);
        assert_eq!(msgs[0].parse().unwrap().get("KIND"), Some("DeathRace"));
    }

    #[test]
    fn news_stray_survives() {
        let wire = encode(
            types::NEWS,
            types::SUB_NEW0,
            &[("BUDDYPORT", "13505")],
            &["WIP Server"],
        );
        let (msg, _) = decode_one(&wire).unwrap();
        assert_eq!(msg.sub, types::SUB_NEW0);
        assert_eq!(msg.get("BUDDYPORT"), Some("13505"));
        assert_eq!(msg.strays, vec!["WIP Server".to_string()]);
        assert_eq!(encode_msg(&msg), wire);
    }

    #[test]
    fn url_round_trip() {
        let raw = "en=\"is playing SSX 3\"\nP=ssx3\n";
        assert_eq!(url_decode(&url_encode(raw)), raw);
    }
}
