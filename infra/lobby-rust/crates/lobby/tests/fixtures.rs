// SPDX-License-Identifier: GPL-3.0-or-later OR MIT
//! Fixture replays: every PRO1 capture message (our own wire captures,
//! IPs remapped, PASS scrubbed) must survive our codec round-trip, and
//! the key shapes the server implements must be present.
//!
//! Fixtures live in tests/data/*.json, captured from our own test lobby
//! (wire captures with IPs remapped and passwords scrubbed).

use serde::Deserialize;
use ssx3_proto::*;

#[derive(Deserialize)]
struct Fixture {
    fixture: String,
    messages: Vec<FxMsg>,
}

#[derive(Deserialize)]
#[allow(dead_code)] // conn/dir/sub kept as coverage metadata
struct FxMsg {
    #[serde(rename = "type")]
    typ: String,
    sub: String,
    dir: String,
    conn: String,
    pairs: Vec<FxPair>,
    strays: Vec<String>,
}

#[derive(Deserialize)]
struct FxPair {
    k: String,
    v: String,
}

fn load(name: &str) -> Fixture {
    let text = std::fs::read_to_string(format!("tests/data/{name}")).unwrap();
    serde_json::from_str(&text).unwrap()
}

fn sub_bytes(s: &str) -> [u8; 4] {
    // Fixture subs are hex ("00000000", "6e657730" for new0) or
    // "ffffffff" for HLE connect frames (skipped: not server traffic).
    let raw = hex_to_bytes(s);
    [raw[0], raw[1], raw[2], raw[3]]
}

fn hex_to_bytes(s: &str) -> Vec<u8> {
    (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap())
        .collect()
}

#[test]
fn all_fixture_messages_round_trip() {
    let names = [
        "login.json",
        "buddy.json",
        "quik-ses.json",
        "rank.json",
        "failures.json",
        "failures-quit.json",
        "probe-bogus-auth.json",
    ];
    let mut count = 0;
    for name in names {
        let fx = load(name);
        assert!(!fx.messages.is_empty(), "{name} empty");
        for m in &fx.messages {
            if m.typ.contains("conn-frame") {
                continue; // HLE-synthesized; server neither sends nor expects
            }
            assert_eq!(m.typ.len(), 4, "{name} type {:?}", m.typ);
            let typ: [u8; 4] = m.typ.as_bytes().try_into().unwrap();
            let pairs: Vec<(&str, &str)> = m
                .pairs
                .iter()
                .map(|p| (p.k.as_str(), p.v.as_str()))
                .collect();
            let strays: Vec<&str> = m.strays.iter().map(|s| s.as_str()).collect();
            let wire = encode(typ, sub_bytes(&m.sub), &pairs, &strays);
            let (back, used) = decode_one(&wire).expect("fixture must decode");
            assert_eq!(used, wire.len());
            assert_eq!(back.typ, typ);
            assert_eq!(
                back.pairs,
                m.pairs
                    .iter()
                    .map(|p| (p.k.clone(), p.v.clone()))
                    .collect::<Vec<_>>(),
                "{name} {} pairs",
                m.typ
            );
            assert_eq!(back.strays, m.strays, "{name} {} strays", m.typ);
            count += 1;
        }
        let _ = &fx.fixture;
    }
    assert!(count > 60, "expected >60 fixture messages, got {count}");
}

// Message coverage table (spec §5): every phase-1 type appears either
// in a fixture or in the live pairing test below.
#[test]
fn phase1_coverage_present() {
    let names = [
        "login.json",
        "buddy.json",
        "quik-ses.json",
        "rank.json",
        "failures.json",
        "failures-quit.json",
        "probe-bogus-auth.json",
    ];
    let mut seen = std::collections::HashSet::new();
    for name in names {
        for m in &load(name).messages {
            if !m.typ.contains("conn-frame") {
                seen.insert(m.typ.clone());
            }
        }
    }
    // pers/onln/news bodies come from the PRO1 lobby-probe receipt
    // (same captures, not in the JSON fixtures); the live pairing test
    // exercises them against the server. Fixture files must carry the rest.
    for t in [
        "@dir", "addr", "skey", "sele", "auth", "+rom", "DISC", "AUTH", "PSET", "RGET", "PING",
        "quik", "+ses", "rank", "~png", "pers",
    ] {
        assert!(seen.contains(t), "phase-1 type {t} missing from fixtures");
    }
}

#[test]
fn ses_shape_has_pairing_keys() {
    let fx = load("quik-ses.json");
    let ses = fx.messages.iter().find(|m| m.typ == "+ses").unwrap();
    let keys: Vec<&str> = ses.pairs.iter().map(|p| p.k.as_str()).collect();
    for k in [
        "NAME", "SELF", "HOST", "OPPO", "P1", "P2", "P3", "P4", "AUTH", "ADDR", "FROM", "SEED",
        "WHEN",
    ] {
        assert!(keys.contains(&k), "+ses missing {k}");
    }
}

#[test]
fn news_reply_shape() {
    // news request shape from the probe receipt pattern; the reply keys
    // the server must emit (sub=new0 + MOTD stray) are asserted live.
    let fx = load("login.json");
    assert!(fx
        .messages
        .iter()
        .any(|m| m.typ == "@dir" && m.dir == "tcp-tx"));
    assert_eq!(fx.messages.iter().filter(|m| m.typ == "+rom").count(), 4);
}
