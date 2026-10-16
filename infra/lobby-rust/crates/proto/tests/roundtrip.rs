//! Round-trip property tests + decoder fuzz (no panics on any input).

use proptest::prelude::*;
use ssx3_proto::*;

fn arb_ascii_line() -> impl Strategy<Value = String> {
    prop::collection::vec(
        prop::char::range(0x20 as char, 0x7e as char).prop_filter("no =", |c| *c != '='),
        0..32,
    )
    .prop_map(|cs| cs.into_iter().collect())
}

fn arb_value() -> impl Strategy<Value = String> {
    prop::collection::vec(prop::char::range(0x20 as char, 0x7e as char), 0..64)
        .prop_map(|cs| cs.into_iter().collect())
}

fn arb_msg() -> impl Strategy<Value = Message> {
    (
        prop::array::uniform4(prop::num::u8::ANY),
        prop::array::uniform4(prop::num::u8::ANY),
        prop::collection::vec((arb_ascii_line(), arb_value()), 0..16),
        prop::collection::vec(arb_ascii_line(), 0..4),
    )
        .prop_map(|(typ, sub, pairs, strays)| {
            let pairs = pairs.into_iter().map(|(k, v)| (k, v)).collect();
            Message {
                typ,
                sub,
                pairs,
                strays,
            }
        })
}

proptest! {
    // ASCII-only bodies must round-trip exactly (encode order kept).
    #[test]
    fn round_trip_ascii(msg in arb_msg().prop_filter("ascii body", |m| {
        m.pairs.iter().all(|(k, v)| {
            k.bytes().all(|b| (0x20..=0x7e).contains(&b) && b != b'=')
                && v.bytes().all(|b| (0x20..=0x7e).contains(&b))
                && !k.is_empty()
        }) && m.strays.iter().all(|s| {
            s.bytes().all(|b| (0x20..=0x7e).contains(&b)) && !s.is_empty() && !s.contains('=')
        })
    })) {
        let wire = encode_msg(&msg);
        let (back, used) = decode_one(&wire).expect("encode output must decode");
        prop_assert_eq!(used, wire.len());
        prop_assert_eq!(back, msg);
    }

    // The decoder never panics on arbitrary bytes; it either decodes
    // (and re-encodes to a prefix of the input) or reports an error.
    #[test]
    fn fuzz_no_panic(raw in prop::collection::vec(prop::num::u8::ANY, 0..512)) {
        let mut off = 0;
        while off < raw.len() {
            match decode_one(&raw[off..]) {
                Ok((msg, used)) => {
                    prop_assert!(used >= HEADER_LEN);
                    // Re-encoding must itself decode identically.
                    let wire = encode_msg(&msg);
                    let (again, _) = decode_one(&wire).expect("re-encode must decode");
                    prop_assert_eq!(again, msg);
                    off += used;
                }
                Err(_) => break,
            }
            // decode_one consumes >= 13 bytes on success, so this ends.
            if off == 0 {
                break;
            }
        }
        // Whole-record splitter agrees on the same bytes.
        if let Ok((msgs, left)) = split_messages(&raw) {
            let mut expect = 0;
            for m in &msgs {
                expect += 12 + m.body.len();
                let _ = m.parse();
            }
            prop_assert_eq!(expect + left, raw.len());
        }
    }

    #[test]
    fn url_codec_never_panics(s in prop::string::string_regex(".*").unwrap()) {
        let _ = url_encode(&s);
        let round = url_decode(&url_encode(&s));
        // url_encode escapes %, so decode(encode(x)) == x for UTF-8 x.
        prop_assert_eq!(round, s);
    }
}

#[test]
fn adversarial_frame_limits() {
    let oversized = encode(*b"rank", [0; 4], &[("R", &"x".repeat(4096))], &[]);
    assert!(matches!(
        decode_one(&oversized),
        Err(DecodeError::TooLarge { .. })
    ));
    let too_many: Vec<(String, String)> = (0..65).map(|i| (format!("K{i}"), "v".into())).collect();
    let refs: Vec<(&str, &str)> = too_many
        .iter()
        .map(|(k, v)| (k.as_str(), v.as_str()))
        .collect();
    let dense = encode(*b"quik", [0; 4], &refs, &[]);
    assert_eq!(decode_one(&dense), Err(DecodeError::TooManyFields));
    let long_key = encode(*b"auth", [0; 4], &[("K".repeat(65).as_str(), "x")], &[]);
    assert_eq!(decode_one(&long_key), Err(DecodeError::FieldTooLong));
    let unicode = encode(*b"auth", [0; 4], &[("X", "é")], &[]);
    assert_eq!(decode_one(&unicode), Err(DecodeError::NonAsciiBody));
}
