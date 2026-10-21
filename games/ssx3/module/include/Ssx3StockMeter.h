#pragma once

// snd-seam-1 Part 5 slice 2: stock-meter suppression on Tricky courses.
// The module owns the `ssx3-tricky-stock-meter` override; the layer policy
// (`ps2_ssx3_tricky_layer.h`) stays runtime-side, read-only.

class PS2Runtime;

namespace ps2_stock_meter
{

// Load-time apply (same ELF/entry/crc as the runtime override it replaces).
// Knob off (`PS2X_SSX3_TRICKY_HUD` unset): wraps nothing.
void applyTrickyStockMeter(PS2Runtime &runtime);

} // namespace ps2_stock_meter
