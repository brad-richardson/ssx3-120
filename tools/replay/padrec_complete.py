#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""Tail-complete a PS2X pad recording so it covers through a run-end tick.

A recording stops at the last guest pad read (e.g. force-stop); replaying it
to a fixed run end needs the final input state extended to cover the tail.
This tool appends one entry that holds the recording's last state through
--run-end tick, after verifying the recording is gap-free.

Header-aware: full-line '#' comment lines (padrec v1-v4 headers written by
PS2X_PAD_RECORD_DIR mode) and blank lines are preserved verbatim at the top;
only entry text is parsed, mirroring the PS2X_PAD_SCRIPT parser.

v4 start/duration values are raw guest-vsync ticks. v1-v3 and unversioned
scripts retain millisecond values and the legacy 59.94 Hz end conversion.

Usage:
  padrec_complete.py <recording> <out-full> <run-end-tick>
  padrec_complete.py --self-test
"""
import sys

VSYNC_MS_NUM = 100000
VSYNC_MS_DEN = 5994  # 59.94 Hz vsync clock


def ms_of_tick(t):
    return (t * VSYNC_MS_NUM) // VSYNC_MS_DEN


def tick_of_ms(m):
    t0 = (m * VSYNC_MS_DEN) // VSYNC_MS_NUM
    for t in range(max(0, t0 - 3), t0 + 4):
        if ms_of_tick(t) == m:
            return t
    raise ValueError('no tick for ms=%d' % m)


def split_header(text):
    """Split raw recording text into (header_lines, entry_text).

    Mirrors the C++ parser exactly: after stripping one trailing CR, a line
    is header iff it is empty or starts with '#' at column 0. Anything else
    (including leading-space comments or whitespace-only lines) is entry
    text, which the entry grammar below rejects just as the runtime does.
    """
    header = []
    bodies = []
    for raw in text.split('\n'):
        line = raw[:-1] if raw.endswith('\r') else raw
        if not line or line.startswith('#'):
            header.append(raw)
        else:
            bodies.append(line)
    return header, ''.join(bodies)


def parse_entries(entry_text):
    """Parse 'at:spec:hold,at:spec:hold,...' into [(at, spec, hold)].

    Entry text must be whitespace-free (the C++ padScriptParseU64 accepts
    digits only, button names match exactly); the spec token itself is
    opaque here and validated by the runtime on load.
    """
    if any(c in entry_text for c in ' \t\r\n\v\f'):
        raise ValueError('whitespace in entry text (rejected by runtime parser)')
    out = []
    for part in entry_text.strip().split(','):
        at, spec, hold = part.split(':')
        out.append((int(at), spec, int(hold)))
    return out


def check_contiguous(entries):
    for i in range(1, len(entries)):
        want = entries[i - 1][0] + entries[i - 1][2]
        if entries[i][0] != want:
            raise ValueError('gap/overlap at entry %d: at=%d want=%d'
                             % (i, entries[i][0], want))


def uses_tick_units(text):
    """Match the runtime's first-line v4 marker (including CRLF)."""
    return text.startswith(('# padrec v4\n', '# padrec v4\r\n'))


def complete(text, run_end):
    """Return completed recording text covering through tick run_end."""
    if run_end < 0:
        raise ValueError('run-end tick must be non-negative')
    ticks = uses_tick_units(text)
    if ticks and run_end >= (1 << 64) - 1:
        raise ValueError('v4 exclusive end does not fit a uint64 tick')
    header, body = split_header(text)
    entries = parse_entries(body)
    if not entries:
        raise ValueError('no entries in recording')
    check_contiguous(entries)
    last_end = entries[-1][0] + entries[-1][2]
    need_end = run_end + 1 if ticks else ms_of_tick(run_end + 1)
    head = '\n'.join(header)
    if head:
        head += '\n'
    if last_end >= need_end:
        return head + body.strip() + '\n', False
    hold = need_end - last_end
    full = body.strip() + ',%d:%s:%d' % (last_end, entries[-1][1], hold)
    check_contiguous(parse_entries(full))
    return head + full + '\n', True


def self_test():
    # 1. Basic completion: 2 entries, extend through tick 100.
    rec = '0:idle:16,16:cross:16\n'
    out, extended = complete(rec, 100)
    assert extended, out
    e = parse_entries(split_header(out)[1])
    check_contiguous(e)
    assert tick_of_ms(e[-1][0] + e[-1][2]) >= 100, e[-1]
    assert e[-1][1] == 'cross', e[-1]  # last state preserved
    # 2. Header preserved verbatim, entries still parse.
    rec2 = ('# padrec v1\n# start_utc=2026-09-27T00:00:00Z\n'
            '# knobs SIM_MODE=unset\n\n0:idle:16,16:cross:16\n')
    out2, ext2 = complete(rec2, 100)
    assert ext2 and out2.startswith('# padrec v1\n# start_utc='), out2
    assert '# knobs SIM_MODE=unset\n' in out2, out2
    e2 = parse_entries(split_header(out2)[1])
    check_contiguous(e2)
    assert len(e2) == 3, e2
    # 3. CRLF tolerated.
    rec3 = '# padrec v1\r\n0:idle:16,16:cross:16\r\n'
    out3, ext3 = complete(rec3, 100)
    assert ext3, out3
    check_contiguous(parse_entries(split_header(out3)[1]))
    # 4. Already-covered recording is a no-op copy (header kept).
    # (run_end=0 needs ms < ms_of_tick(1)=16; recording covers [0,32).)
    out4, ext4 = complete(rec2, 0)
    assert not ext4, out4
    assert out4.startswith('# padrec v1\n'), out4
    assert parse_entries(split_header(out4)[1]) == parse_entries('0:idle:16,16:cross:16')
    # 5. Gap detected.
    try:
        complete('0:idle:16,20:cross:16\n', 100)
    except ValueError as ex:
        assert 'gap' in str(ex), ex
    else:
        raise AssertionError('gap not detected')
    # 6. Empty recording rejected.
    try:
        complete('# only a header\n\n', 100)
    except ValueError:
        pass
    else:
        raise AssertionError('empty recording accepted')
    # 7. Strict like the runtime: leading-space comments, whitespace-only
    # lines and spaced entries are rejected, not silently accepted.
    for bad in ('  # indented comment\n0:idle:16\n',
                '0:idle:16\n   \n16:cross:16\n',
                '0:idle:16, 16:cross:16\n'):
        try:
            complete(bad, 100)
        except ValueError:
            pass
        else:
            raise AssertionError('accepted runtime-invalid input: %r' % bad)
    print('padrec_complete self-test: 7/7 OK')


def main(argv):
    if len(argv) == 2 and argv[1] == '--self-test':
        self_test()
        return 0
    if len(argv) != 4:
        sys.stderr.write('usage: padrec_complete.py <recording> <out-full> <run-end-tick>\n')
        return 2
    recf, outf, run_end = argv[1], argv[2], int(argv[3])
    with open(recf) as f:
        text = f.read()
    out, extended = complete(text, run_end)
    with open(outf, 'w') as f:
        f.write(out)
    n = len(parse_entries(split_header(out)[1]))
    print('%s: %d entries, covers through tick %d -> %s'
          % ('completed' if extended else 'already covers', n, run_end, outf))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
