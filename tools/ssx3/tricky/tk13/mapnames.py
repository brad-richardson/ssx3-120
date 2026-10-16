"""TK13: names from a Tricky course .map (ColdFusion map file): section -> {uid: name}.
Sections: MODELS, INTERNAL INSTANCES, SPLINES, MATERIALS, ... (rows `Name UID Ref Hash ...`)."""
import re


def map_names(text):
    out, cur = {}, None
    for line in text.splitlines():
        m = re.match(r'###\s+(.+?) BEGIN', line)
        if m:
            cur = out.setdefault(m.group(1).strip(), {}); continue
        if re.match(r'###\s+.+ END', line):
            cur = None; continue
        if cur is None or line.startswith('#') or not line.strip():
            continue
        f = line.split()
        if len(f) >= 2 and f[1].isdigit():
            cur[int(f[1])] = f[0]
    return out
