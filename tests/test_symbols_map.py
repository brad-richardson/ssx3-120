#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""Coverage + shape check for games/ssx3/ssx3-symbols.tsv (symbols-map-1).

The TSV is well-formed (7 columns, known kind/confidence values, canonical
addresses), sorted with no duplicates, and covers every guest address the
product uses as a hook PC or named constant:

- C++:  ``constexpr ... kName = 0x...`` and ``addSrc/addTgt`` /
  ``sourcePc ==`` / ``targetPc ==`` over a literal or a collected kName, in
  the engine's games/ssx3 module (unit tests excluded) and the runtime
  headers (ps2xRuntime/include, unit tests excluded);
- TOML: ``"name@0x..."`` and bare ``"0x..."`` strings in games/ssx3/ssx3.toml;
- Python/shell: ``K_*`` / ``kName = 0x...`` assignments under tools/ssx3,
  tests and games.

Only values in EE RAM [0x100000, 0x2000000) count; const names containing
Mask/TouchId are bitmasks or host ids (see tests/symbols_allowlist.tsv).
Addresses in the allowlist are exempt; every other required address needs a
map row. Lane REPORT evidence is checked when the map is built, not here.

When this fails after an engine bump: add the row (docs/symbols.md) or, for
a genuine non-guest constant, an allowlist entry with a reason.
Usage: python3 tests/test_symbols_map.py  (run from the repo root)
"""
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAP = ROOT / "games" / "ssx3" / "ssx3-symbols.tsv"
ALLOW = ROOT / "tests" / "symbols_allowlist.tsv"
ENGINE = ROOT / "deps" / "PS2Recomp"

GUEST_LO, GUEST_HI = 0x100000, 0x2000000
KINDS = {"code", "data"}
CONFS = {"verified", "decomp-unverified", "inferred"}

KDEF = re.compile(r"constexpr\s[^;{}]*?\bk([A-Za-z0-9_]+)\s*=\s*0x([0-9a-fA-F]+)")
HOOK = re.compile(r"(?:addSrc|addTgt)\s*\(\s*(0x[0-9a-fA-F]+|k[A-Za-z0-9_]+)")
PCCMP = re.compile(r"(?:sourcePc|targetPc)\s*==\s*(0x[0-9a-fA-F]+|k[A-Za-z0-9_]+)")
TOML_AT = re.compile(r'"[^"@\s]+@0x([0-9a-fA-F]+)"')
TOML_HEXSTR = re.compile(r'"0x([0-9a-fA-F]+)"')
PYK = re.compile(r"^\s*(?:k[A-Z][A-Za-z0-9_]*|K_[A-Z0-9_]+)\s*=\s*0x([0-9a-fA-F]+)")
NAME_SKIP = re.compile(r"Mask|TouchId")


def in_guest(v):
    return GUEST_LO <= v < GUEST_HI


def canon(v):
    return "0x%x" % v


def check_shape():
    errors = []
    lines = MAP.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "address\tkind\tname\trole\tsource\tevidence\tconfidence":
        return ["bad header: %r" % (lines[0] if lines else None)], []
    addrs = []
    for i, line in enumerate(lines[1:], 2):
        cells = line.split("\t")
        if len(cells) != 7:
            errors.append("line %d: %d columns" % (i, len(cells)))
            continue
        addr, kind, name, role, source, evidence, conf = cells
        if not re.fullmatch(r"0x[0-9a-f]+|[A-Z]{1,3}\+0x[0-9a-f]+", addr):
            errors.append("line %d: bad address %r" % (i, addr))
        if kind not in KINDS:
            errors.append("line %d: bad kind %r" % (i, kind))
        if conf not in CONFS:
            errors.append("line %d: bad confidence %r" % (i, conf))
        if not (name and role and source and evidence):
            errors.append("line %d: empty field" % i)
        addrs.append(addr)
    key = lambda a: (0, int(a, 16), "") if a.startswith("0x") else (1, 0, a)
    if addrs != sorted(addrs, key=key):
        errors.append("not sorted by address")
    if len(set(addrs)) != len(addrs):
        errors.append("duplicate addresses")
    return errors, addrs


def collect_required():
    kmap, required, seen = {}, {}, set()

    def note(v, token):
        seen.add(canon(v))
        if in_guest(v):
            required.setdefault(canon(v), []).append(token)

    cfiles = []
    mod = ENGINE / "games" / "ssx3"
    if mod.is_dir():
        for dp, _, fns in os.walk(mod):
            if "tests" in Path(dp).parts:
                continue
            for fn in fns:
                if fn.endswith((".h", ".hpp", ".cpp", ".inl")):
                    cfiles.append(Path(dp) / fn)
    inc = ENGINE / "ps2xRuntime" / "include"
    if inc.is_dir():
        for dp, _, fns in os.walk(inc):
            if "tests" in Path(dp).parts:
                continue
            for fn in fns:
                if fn.endswith((".h", ".hpp", ".inl")):
                    cfiles.append(Path(dp) / fn)
    for p in cfiles:
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        rel = p.relative_to(ROOT).as_posix()
        for i, line in enumerate(lines, 1):
            for m in KDEF.finditer(line):
                v = int(m.group(2), 16)
                seen.add(canon(v))
                if NAME_SKIP.search("k" + m.group(1)):
                    continue
                kmap["k" + m.group(1)] = v
                note(v, "constexpr k%s (%s:%d)" % (m.group(1), rel, i))
    for p in cfiles:
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        rel = p.relative_to(ROOT).as_posix()
        for i, line in enumerate(lines, 1):
            for pat in (HOOK, PCCMP):
                for m in pat.finditer(line):
                    tok = m.group(1)
                    v = int(tok, 16) if tok.startswith("0x") else kmap.get(tok)
                    if v is not None:
                        note(v, "hook %s (%s:%d)" % (tok, rel, i))
    for tp in (ROOT / "games" / "ssx3" / "ssx3.toml",
               ENGINE / "games" / "ssx3" / "ssx3.toml"):
        try:
            lines = tp.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        rel = tp.relative_to(ROOT).as_posix()
        for i, line in enumerate(lines, 1):
            for m in TOML_AT.finditer(line):
                note(int(m.group(1), 16), "toml %s:%d" % (rel, i))
            if "@" not in line:
                for m in TOML_HEXSTR.finditer(line):
                    note(int(m.group(1), 16), "toml %s:%d" % (rel, i))
    for root in (ROOT / "tools" / "ssx3", ROOT / "tests", ROOT / "games"):
        if not root.is_dir():
            continue
        for dp, _, fns in os.walk(root):
            for fn in fns:
                if not fn.endswith((".py", ".sh")):
                    continue
                p = Path(dp) / fn
                try:
                    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
                except OSError:
                    continue
                rel = p.relative_to(ROOT).as_posix()
                for i, line in enumerate(lines, 1):
                    m = PYK.match(line)
                    if m:
                        note(int(m.group(1), 16), "pyconst %s:%d" % (rel, i))
    return required, seen


def main():
    errors, addrs = check_shape()
    have = set(addrs)
    required, seen = collect_required()
    allowed = {}
    for line in ALLOW.read_text(encoding="utf-8").splitlines()[1:]:
        if line.strip():
            a, reason = line.split("\t", 1)
            allowed[a.strip()] = reason.strip()
    missing = sorted(set(required) - set(allowed) - have,
                     key=lambda a: int(a, 16))
    stale = sorted(set(allowed) - seen,
                   key=lambda a: int(a, 16))
    print("map_rows=%d required=%d allowlisted=%d" % (len(have), len(required), len(allowed)))
    for a in missing:
        print("MISSING %s  e.g. %s" % (a, required[a][0]))
        errors.append("missing row " + a)
    for a in stale:
        print("STALE-ALLOWLIST %s (%s)" % (a, allowed[a]))
        errors.append("stale allowlist " + a)
    for e in errors:
        print("BAD %s" % e)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
