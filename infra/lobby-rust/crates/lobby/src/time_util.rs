// SPDX-License-Identifier: GPL-3.0-or-later OR MIT
//! `WHEN`/`SINCE`/`LAST` timestamps: `2026.10.13 02:13:09`
//! (server wall clock, spec §1.2). Hand-rolled civil date (no chrono).

use std::time::{SystemTime, UNIX_EPOCH};

pub fn stamp() -> String {
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs() as i64;
    stamp_for(secs)
}

fn stamp_for(secs: i64) -> String {
    let days = secs.div_euclid(86400);
    let tod = secs.rem_euclid(86400);
    // Howard Hinnant's days-to-civil.
    let z = days + 719468;
    let era = z.div_euclid(146097);
    let doe = z.rem_euclid(146097);
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = (if mp < 10 { mp + 3 } else { mp - 9 }) as u32;
    let year = (if m <= 2 { y + 1 } else { y }) as i32;
    format!(
        "{year:04}.{m:02}.{d:02} {:02}:{:02}:{:02}",
        (tod / 3600) as u32,
        ((tod % 3600) / 60) as u32,
        (tod % 60) as u32
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn epoch() {
        assert_eq!(stamp_for(0), "1970.01.01 00:00:00");
    }
    #[test]
    fn shape() {
        // 2026-10-13 02:13:09 UTC.
        assert_eq!(stamp_for(1791857589), "2026.10.13 02:13:09");
    }
}
