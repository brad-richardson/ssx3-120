#pragma once

// HNG2: rider animation-variant pick guard (PS2X_SSX3_ANIM_PICK_GUARD, default
// on; "0" restores the stock trap for reference).
//
// sub_311710(table=a0, event=a1, mask=a2) picks one variant of an animation
// event: entry table[event] = {start:i16, count:i16}; count == 1 returns the
// record directly, otherwise it sums the weights of records
// 0x449960[start..start+count) whose flags & mask == mask, draws BXrand and
// divides by the sum. A zero sum hits the compiler's divide-by-zero trap
// (0x3117c0 beql s1,zero / 0x3117c4 break 7), which raises a breakpoint
// exception and freezes the EE at 0x80000080. The stock table gives every
// multi-variant event a variant for every rider mask; events 432/433 have
// no records (count 0). Event 432 is reached through the pose wrapper
// 0x312660 when a sequence animation lookup returns the 0xFFFF "no animation"
// key ([gp+0x2860]), e.g. in a full-120 background replay that has drifted
// from its live run (Brad 10-08, Ruthless Ridge, twice). Stock cannot get
// past that point, so guarding it changes nothing a stock run reaches.
//
// Guarded result: anim id 0 (event 0's only record, flags 0xffffffff), whose
// handle sits in the always-resident base bank. Every picker caller indexes
// the per-anim handle table [gp+0xD8C]+0x1030 with the result (0x3126d0
// pose wrapper, 0x312960 cRiderAnimBase_play, ...). That table has 518
// entries (anim ids 0..0x205), so play()'s "none" value 0x207 would read past
// it in the pose wrapper, and a 0xFFFFFFFF handle would make 0x313c50 index
// bank 0xFF. The guard checks the anim-0 handle is readable and not the
// no-animation key; otherwise it leaves the stock path (and its trap) alone.

#include "ps2_runtime.h"

#include <cstdint>
#include <cstdlib>
#include <cstring>

namespace ps2_ssx3_anim_pick
{
inline constexpr uint32_t kPicker = 0x311710u;
inline constexpr uint32_t kRecords = 0x449960u;   // {anim, weight, flags} x 472
inline constexpr uint32_t kRecordSize = 12u;
inline constexpr uint32_t kHandleTableGpOff = 0xd8cu;
inline constexpr uint32_t kHandleTableOff = 0x1030u;
inline constexpr uint32_t kNoAnimKeyGpOff = 0x2860u;
inline constexpr uint32_t kSafeAnim = 0u;
inline constexpr uint32_t kMaxSpan = 0x1000u; // wider ranges are not the picker's table; leave them stock
inline constexpr uint32_t kRamSize = 0x2000000u;

// Unset/empty/anything but "0" = on.
inline bool parseEnabled(const char *v) noexcept { return !(v && std::strcmp(v, "0") == 0); }
inline bool enabled() noexcept
{
    static const bool on = parseEnabled(std::getenv("PS2X_SSX3_ANIM_PICK_GUARD"));
    return on;
}

inline bool rd32(const uint8_t *ram, uint32_t a, uint32_t &v) noexcept
{
    a &= 0x1fffffffu;
    if (a > kRamSize - 4u)
        return false;
    std::memcpy(&v, ram + a, 4u);
    return true;
}

enum class Decision
{
    Stock,  // run the original picker
    Guard,  // zero total weight and the safe anim is resident: return kSafeAnim
    Unsafe, // zero total weight but no resident safe anim: original (stock trap)
};

// Mirrors 0x311710's arithmetic (32-bit, signed start/count halves).
inline bool zeroWeight(const uint8_t *ram, uint32_t table, uint32_t event, uint32_t mask, bool &zero) noexcept
{
    uint32_t entry = 0u;
    if (!rd32(ram, table + (event << 2), entry))
        return false;
    const int32_t start = static_cast<int16_t>(entry & 0xffffu);
    const int32_t count = static_cast<int16_t>(entry >> 16);
    if (count == 1)
    {
        zero = false;
        return true;
    }
    const int32_t end = static_cast<int32_t>(static_cast<uint32_t>(start) + static_cast<uint32_t>(count));
    uint32_t sum = 0u;
    if (start < end)
    {
        if (static_cast<uint32_t>(end - start) > kMaxSpan)
            return false;
        for (int32_t i = start; i < end; ++i)
        {
            const uint32_t rec = kRecords + static_cast<uint32_t>(i) * kRecordSize;
            uint32_t weight = 0u, flags = 0u;
            if (!rd32(ram, rec + 4u, weight) || !rd32(ram, rec + 8u, flags))
                return false;
            if ((flags & mask) == mask)
                sum += weight;
        }
    }
    zero = sum == 0u;
    return true;
}

inline bool safeAnimResident(const uint8_t *ram, uint32_t gp) noexcept
{
    uint32_t table = 0u, handle = 0u, noAnim = 0u;
    if (!rd32(ram, gp + kHandleTableGpOff, table) || table == 0u ||
        !rd32(ram, table + kHandleTableOff + kSafeAnim * 4u, handle) ||
        !rd32(ram, gp + kNoAnimKeyGpOff, noAnim))
        return false;
    return handle != 0xffffffffu && handle != noAnim;
}

inline Decision decide(const uint8_t *ram, uint32_t table, uint32_t event, uint32_t mask, uint32_t gp) noexcept
{
    bool zero = false;
    if (!zeroWeight(ram, table, event, mask, zero) || !zero)
        return Decision::Stock;
    return safeAnimResident(ram, gp) ? Decision::Guard : Decision::Unsafe;
}

// The wrapper's work at the picker entry. Only a fresh call (pc == entry)
// is examined; a checkpoint resume (pc 0x311790/0x3117b8/0x3117f8) and every
// non-zero-weight pick run the original. Guard: $v0 = kSafeAnim and return to
// $ra, as the picker's own epilogue would (callee-saved registers and $sp are
// untouched). Returns true when the original must not run.
inline bool guardEntry(uint8_t *ram, R5900Context *ctx, Decision *out = nullptr) noexcept
{
    if (!ram || !ctx || ctx->pc != kPicker)
        return false;
    const uint32_t event = getRegU32(ctx, 5), mask = getRegU32(ctx, 6);
    const Decision d = decide(ram, getRegU32(ctx, 4), event, mask, getRegU32(ctx, 28));
    if (out)
        *out = d;
    if (d != Decision::Guard)
        return false;
    setReturnU32(ctx, kSafeAnim);
    ctx->pc = getRegU32(ctx, 31);
    return true;
}
// HNG3 (Brad 10-12 21:35, PB61): the second path. cRiderAnimBase_play
// (0x3128e8: a0 this, a1 event, a2 arg, f12 blend) asks the picker through
// the RiderAnim vtable (0x312924 -> 0x104cf8 -> 0x311710) and then, with the
// SAME event, indexes the static AIAnimEventMap record table 0x446990
// (0x1C bytes per event; 437 events, the heap map being allocated as
// 0x6D4 = 437*4 bytes at 0x31187c) through 0x3115c8 and reads [rec+0xC] as
// an index into the table at 0x48d808 (0x312994 lw $t1). Event 0x6873
// (26739) got past the picker (the HNG2 guard above returned anim 0 for its
// zero weight) and the "record" at 0x4fd624 fed a garbage index, so the
// load at 0x312994 hit 0xfca0cfc8: address error -> 0x80000080, EE frozen.
// Stock has no range check here; every table the wrapper indexes stops at
// event 436, so a valid run never passes an event >= 437 and the guard
// changes only runs that would fault.
//
// Guarded result: the wrapper's own "no animation" exit (anim 0x207 at
// 0x312934 -> epilogue 0x3129b8): $v0 = 0x1B6 and return to $ra. Nothing is
// skipped that stock would have kept: the first store in 0x3128e8 is at
// 0x3129b4, after the faulting load. The sibling 0x3129e0 (play by event,
// no picker) reads the same record and is not wrapped here (HNG3 report).
inline constexpr uint32_t kPlay = 0x3128e8u;
inline constexpr uint32_t kEventCount = 437u;       // AIAnimEventMap events 0..436
inline constexpr uint32_t kPlayNoneResult = 0x1b6u; // what the 0x207 path returns in $v0

inline bool playEventOutOfRange(uint32_t event) noexcept { return event >= kEventCount; }

// The wrapper's work at the play entry. Only a fresh call (pc == entry) is
// examined; a checkpoint resume inside the function runs the original.
// Guard: $v0 = kPlayNoneResult and return to $ra, as the 0x207 path would
// (callee-saved registers and $sp are untouched: the prologue has not run).
// Returns true when the original must not run.
inline bool guardPlayEntry(R5900Context *ctx) noexcept
{
    if (!ctx || ctx->pc != kPlay)
        return false;
    if (!playEventOutOfRange(getRegU32(ctx, 5)))
        return false;
    setReturnU32(ctx, kPlayNoneResult);
    ctx->pc = getRegU32(ctx, 31);
    return true;
}
} // namespace ps2_ssx3_anim_pick
