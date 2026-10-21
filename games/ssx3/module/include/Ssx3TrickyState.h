#pragma once

// snd-seam-1 family #8: TK34 patch-grow kernel-section field providers. The
// runtime's kernel save/validate/load call these at the exact field position
// (null = fail closed: a no-module binary never round-trips game state).
// The "trickygems" section needs no table entry (K1 static registration in
// the family TU).

namespace ps2_savestate
{
class Writer;
class Reader;
} // namespace ps2_savestate

namespace ps2_tricky_state
{

void trickyGemsSave(ps2_savestate::Writer &w);
bool trickyGemsLoad(ps2_savestate::Reader &r);

void patchGrowSave(ps2_savestate::Writer &w);
bool patchGrowValidate(ps2_savestate::Reader &r);
bool patchGrowLoad(ps2_savestate::Reader &r);

} // namespace ps2_tricky_state
