#pragma once

class PS2Runtime;

namespace ps2_ssx3_tricky_intro
{
// Tricky-only fixed start-gate pose during race phase 1. Default on;
// PS2X_SSX3_TRICKY_FIXED_INTRO=0 retains the donor flyover exactly.
void applyTrickyFixedIntro(PS2Runtime &runtime);
}
