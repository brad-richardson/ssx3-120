#!/usr/bin/env python3
"""Fail-loud SSX3 generated-code promotion invariants.

Use --generation-log for every newly generated tree and --runtime-log for each
gate boot. --preflight checks a candidate before boots; final promotion scans
require runtime logs. --legacy-canonical permits scanning the already promoted
tree whose generation log was not retained; it does not waive static checks.
"""
import argparse
import collections
import json
import pathlib
import re
import struct
import sys
import tomllib

COMMENTS = re.compile(r'// 0x([0-9a-fA-F]+): 0x([0-9a-fA-F]+)([^\n]*)')
REGISTRATION = re.compile(r'g_ps2RecompiledFunctionTable\[\d+\] = \w+; // 0x([0-9a-fA-F]+)')
# The one known static edge gap is a configured CD-reader stub whose runtime
# handler is unresolved; its original guest body is generated and dispatches
# this local J target. CGV1 §3 found no executed hit. Review before removing.
DIRECT_TARGET_ALLOWLIST = {
    0x400908: 'CGV1: CD-reader configured stub uses guest fallback; direct J at 0x400bf8',
}
CALLBACK_ALLOWLIST = {}  # No known unresolved executable callback remains.
DROP_ALLOWLIST = {}      # No registered handler drop is accepted.


def elf_words(path):
    data = path.read_bytes()
    shoff = struct.unpack_from('<I', data, 32)[0]
    ents, count = struct.unpack_from('<HH', data, 46)
    sections = []
    for i in range(count):
        s = struct.unpack_from('<10I', data, shoff + i * ents)
        if s[1] != 8 and s[2] & 4:  # SHT_NOBITS excluded, SHF_EXECINSTR required
            sections.append((s[3], s[3] + s[5], s[4]))

    def word(pc):
        for lo, hi, offset in sections:
            if lo <= pc and pc + 4 <= hi:
                return struct.unpack_from('<I', data, offset + pc - lo)[0]
        return None
    return word


def delayed(word):
    op, rs, rt, fn = word >> 26, word >> 21 & 31, word >> 16 & 31, word & 63
    if op in (2, 3, 4, 5, 6, 7, 20, 21, 22, 23):
        return True
    if op == 0 and fn in (8, 9):
        return True
    if op == 1 and rt in (0, 1, 2, 3, 16, 17, 18, 19):
        return True
    return op in (16, 17, 18) and rs == 8


def scan(args):
    word_at = elf_words(args.elf)
    counts = collections.Counter()
    failures = []
    registered = {int(x, 16) for x in REGISTRATION.findall((args.codegen / 'register_functions.cpp').read_text())}
    direct_gaps = set()
    files = sorted(args.codegen.glob('*.cpp'))
    for file in files:
        body = file.read_text()
        counts['translation_units'] += 1
        if 'TODO_NAMED(' in body or 'ps2_stubs::TODO(' in body:
            failures.append(['todo_fallback', file.name])
        if 'throw std::runtime_error("Unhandled ' in body:
            failures.append(['unhandled_instruction', file.name])
        comments = list(COMMENTS.finditer(body))
        for i, match in enumerate(comments):
            pc, raw = int(match[1], 16), int(match[2], 16)
            if word_at(pc) != raw:
                failures.append(['elf_word_mismatch', file.name, hex(pc)])
            if not delayed(raw) or '(Delay Slot)' in match[3]:
                continue
            counts['delayed_occurrences'] += 1
            slot_pc, slot_word = pc + 4, word_at(pc + 4)
            if slot_word is None:
                failures.append(['unreadable_slot', file.name, hex(pc)])
                continue
            # An emitted slot comment must lie between this branch and the
            # next ordinary instruction comment. A NOP may be elided.
            j = i + 1
            emitted = False
            while j < len(comments) and '(Delay Slot)' in comments[j][3]:
                emitted |= int(comments[j][1], 16) == slot_pc and int(comments[j][2], 16) == slot_word
                j += 1
            if slot_word not in (0, 0x24000000) and not emitted:
                failures.append(['omitted_non_nop_slot', file.name, hex(pc), hex(slot_word)])
            elif slot_word in (0, 0x24000000) and not emitted:
                counts['elided_nop_slots'] += 1
            if raw >> 26 in (2, 3):
                target = ((pc + 4) & 0xf0000000) | ((raw & 0x3ffffff) << 2)
                if target not in registered:
                    direct_gaps.add((pc, target))
    counts['registered_entries'] = len(registered)
    for source, target in sorted(direct_gaps):
        counts['unregistered_direct_targets'] += 1
        if target not in DIRECT_TARGET_ALLOWLIST:
            failures.append(['unregistered_direct_target', hex(source), hex(target)])
    cfg = tomllib.loads(args.config.read_text())
    for value in cfg['general'].get('extra_function_starts', []):
        pc = int(value, 0)
        counts['configured_callback_entries'] += 1
        if pc not in registered and pc not in CALLBACK_ALLOWLIST:
            failures.append(['unresolved_executable_callback', hex(pc)])
    if args.generation_log:
        log = args.generation_log.read_text()
        for pattern, category in [
            (r'truncating decode', 'decode_truncation'),
            (r'TODO_NAMED|\bUnhandled instructions: [1-9][0-9]*\b', 'todo_fallback'),
            (r'\bdecode failures: [1-9]', 'decode_failure'),
        ]:
            matches = re.findall(pattern, log, re.I)
            counts[category] += len(matches)
            if matches:
                failures.append([category, len(matches)])
    elif not args.legacy_canonical:
        failures.append(['generation_log_required'])
    if not args.runtime_log and not (args.preflight or args.legacy_canonical):
        failures.append(['runtime_logs_required'])
    for path in args.runtime_log:
        for line in path.read_text(errors='replace').splitlines():
            if 'no-table-entry' not in line and 'missing function' not in line.lower():
                continue
            if any(x in line for x in ('sched/invocation-dispatch', 'sched/dispatchIrq',
                                       'sched/vsync-callback', 'sched/setAlarm')):
                counts['dropped_handlers'] += 1
                if line not in DROP_ALLOWLIST:
                    failures.append(['dropped_registered_handler', path.name, line[:300]])
            elif 'missing function' in line.lower():
                counts['missing_functions'] += 1
                failures.append(['missing_function', path.name, line[:300]])
    result = {'counts': dict(counts), 'allowlists': {
        'direct_targets': {hex(k): v for k, v in DIRECT_TARGET_ALLOWLIST.items()},
        'callbacks': CALLBACK_ALLOWLIST, 'drops': DROP_ALLOWLIST},
        'failures': failures[:100], 'failure_count': len(failures)}
    print(json.dumps(result, indent=2))
    return 1 if failures else 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--codegen', type=pathlib.Path, required=True)
    p.add_argument('--elf', type=pathlib.Path, required=True)
    p.add_argument('--config', type=pathlib.Path, required=True)
    p.add_argument('--generation-log', type=pathlib.Path)
    p.add_argument('--runtime-log', type=pathlib.Path, action='append', default=[])
    p.add_argument('--preflight', action='store_true')
    p.add_argument('--legacy-canonical', action='store_true')
    return scan(p.parse_args())


if __name__ == '__main__':
    sys.exit(main())
