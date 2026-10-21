#pragma once

// snd-seam-1 Part 5: TK11 Tricky-mode frontend entry. The module owns the
// `ssx3-tricky-menu` override (wrappers + hook originals); the policy
// headers stay runtime-side (read-only includes) until their lanes land —
// notably `ps2_ssx3_tricky_list.h` (tk-menu-2) and
// `ps2_ssx3_course_manifest.h` (tk-modes-1) are never edited here.

class PS2Runtime;

namespace ps2_tricky_menu
{

// Load-time apply (same ELF/entry/crc as the runtime override it replaces).
// Knob off: wraps nothing.
void applyTrickyMenu(PS2Runtime &runtime);

} // namespace ps2_tricky_menu
