// module-seam-2: moved from ps2xRuntime/src/lib/Kernel/Syscalls/
// Ssx3CopiedPayload.cpp (fork c71407e9). The kernel reaches it through the
// module's syscall hooks (runtime/game_module.h); state and helpers are
// unchanged.
#include "Ssx3CopiedPayload.h"
#include "ssx3_module_internal.h"

#include "ps2_runtime.h"
#include "runtime/ps2_memory.h"

#include <atomic>
#include <cstring>
#include <iostream>

namespace ps2_syscalls
{
namespace
{
// K1 quirk descriptor: SSX3 (SLUS_207.72) 0x42CBD0 installer geometry.
// Source/destination/size describe the guest's own Copy(0x5A) event; the
// runtime verifies the copy instead of trusting the addresses alone.
constexpr uint32_t kPayloadSrc = 0x4561C0u;
constexpr uint32_t kPayloadDst = 0x80075000u;
constexpr uint32_t kPayloadSize = 0x330u;
constexpr uint32_t kTableOffset = 0x300u;
constexpr uint32_t kTableEntries = 6u;

// (syscall number, helper entry offset) pairs from the payload's own
// lookup table at dst+0x300. Only exact pairs are served.
struct HelperEntry
{
    uint32_t syscallNumber;
    uint32_t offset;
};
constexpr HelperEntry kHelpers[] = {
    {0x55u, 0x38u}, // PutTLBEntry shape: mtc0 set + tlbwr + tlbp
    {0x56u, 0xC8u}, // SetTLBEntry shape: indexed tlbwi
    {0x57u, 0x108u}, // GetTLBEntry shape: tlbr + 4 stores
    {0x58u, 0x158u}, // ProbeTLBEntry shape: tlbp + conditional stores
    {0x59u, 0x1A8u}, // ExpandScratchPad shape: probe + wired-index alloc
};

std::atomic<bool> g_k1Enabled{false};
// Semantic counters, independent of PS2X_DROP_SILENCE (always-on stderr).
std::atomic<uint64_t> g_lookupServed{0};
std::atomic<uint64_t> g_helperServed{0};
std::atomic<uint64_t> g_installLogged{0};
std::atomic<uint64_t> g_provenanceFail{0};
// HLE stand-in for the COP0 Wired register the 0x59 helper allocates from.
std::atomic<uint32_t> g_wiredAlloc{0};

bool copyIntact(const uint8_t *rdram)
{
    const uint8_t *src = getConstMemPtr(rdram, kPayloadSrc);
    const uint8_t *dst = getConstMemPtr(rdram, kPayloadDst);
    if (!src || !dst)
    {
        return false;
    }
    return std::memcmp(src, dst, kPayloadSize) == 0;
}

bool readTableWord(const uint8_t *rdram, uint32_t index, uint32_t &key, uint32_t &value)
{
    const uint8_t *base = getConstMemPtr(rdram, kPayloadDst + kTableOffset);
    if (!base)
    {
        return false;
    }
    std::memcpy(&key, base + index * 8u, sizeof(key));
    std::memcpy(&value, base + index * 8u + 4u, sizeof(value));
    return true;
}

// Payload entry +0x0: linear search of the 6 pairs at dst+0x300 for a0,
// value on hit, 0 on miss. Reads the DESTINATION table like the payload.
bool emulateLookup(const uint8_t *rdram, R5900Context *ctx)
{
    const uint32_t key = getRegU32(ctx, 4);
    for (uint32_t i = 0; i < kTableEntries; ++i)
    {
        uint32_t entryKey = 0u;
        uint32_t entryValue = 0u;
        if (!readTableWord(rdram, i, entryKey, entryValue))
        {
            return false;
        }
        if (entryKey == key)
        {
            const uint64_t seq = g_lookupServed.fetch_add(1u, std::memory_order_relaxed) + 1u;
            std::cerr << "[k1] lookup#" << seq << " key=0x" << std::hex << key << " value=0x" << entryValue
                      << std::dec << std::endl;
            setReturnU32(ctx, entryValue);
            return true;
        }
    }
    const uint64_t seq = g_lookupServed.fetch_add(1u, std::memory_order_relaxed) + 1u;
    std::cerr << "[k1] lookup#" << seq << " key=0x" << std::hex << key << " value=0x0 miss=1" << std::dec
              << std::endl;
    setReturnU32(ctx, 0u);
    return true;
}

bool writeGuestWord(uint8_t *rdram, uint32_t addr, uint32_t value)
{
    uint8_t *ptr = getMemPtr(rdram, addr);
    if (!ptr)
    {
        return false;
    }
    std::memcpy(ptr, &value, sizeof(value));
    return true;
}

// Payload-implied validation per helper, decoded from the game's own
// bytes at the destination base. Success shapes that need TLB state the
// HLE does not model are documented at each return.
bool emulateHelper(uint32_t syscallNumber, uint8_t *rdram, R5900Context *ctx)
{
    const uint32_t a0 = getRegU32(ctx, 4);
    const uint32_t a1 = getRegU32(ctx, 5);
    const uint32_t a2 = getRegU32(ctx, 6);
    const uint32_t a3 = getRegU32(ctx, 7);
    int32_t result = -1;
    switch (syscallNumber)
    {
    case 0x55u:
    {
        // Valid iff (a1>>24)&0xF0 is 0x00, 0x30, or 0x40; else -1.
        // Payload returns the post-write probe index; the HLE models no
        // TLB, so success returns index 0 (assumption, traced).
        const uint32_t v1 = (a1 >> 24) & 0xF0u;
        result = (v1 == 0x00u || v1 == 0x30u || v1 == 0x40u) ? 0 : -1;
        break;
    }
    case 0x56u:
        // a0 < 0x30 else -1; success returns the index (input, exact).
        result = (a0 < 0x30u) ? static_cast<int32_t>(a0) : -1;
        break;
    case 0x57u:
    {
        // a0 < 0x30 else -1; success stores 4 COP0 words to
        // [a1],[a2],[a3],[t0] and returns the index. The HLE stores
        // zeros (empty-TLB read; assumption, traced).
        if (a0 >= 0x30u)
        {
            result = -1;
            break;
        }
        const uint32_t t0 = getRegU32(ctx, 8);
        const bool ok = writeGuestWord(rdram, a1, 0u) && writeGuestWord(rdram, a2, 0u) &&
                        writeGuestWord(rdram, a3, 0u) && writeGuestWord(rdram, t0, 0u);
        result = ok ? static_cast<int32_t>(a0) : -1;
        break;
    }
    case 0x58u:
        // Probe with no HLE mappings always misses (assumption, traced).
        result = -1;
        break;
    case 0x59u:
    {
        // Fail iff (a0&0xFFF)!=0 or a0 in [1,0x0FFFFFFF]. a0==0
        // succeeds with 0; other valid sizes allocate a wired index.
        // The HLE probe always misses, matching the 0x58 rule above.
        if ((a0 & 0xFFFu) != 0u || (a0 != 0u && a0 <= 0x0FFFFFFFu))
        {
            result = -1;
            break;
        }
        result = (a0 == 0u) ? 0 : static_cast<int32_t>(g_wiredAlloc.fetch_add(1u, std::memory_order_relaxed));
        break;
    }
    default:
        return false;
    }
    const uint64_t seq = g_helperServed.fetch_add(1u, std::memory_order_relaxed) + 1u;
    std::cerr << "[k1] helper#" << seq << " n=0x" << std::hex << syscallNumber << " a0=0x" << a0 << " a1=0x"
              << a1 << " a2=0x" << a2 << " a3=0x" << a3 << " result=0x" << static_cast<uint32_t>(result)
              << std::dec << std::endl;
    setReturnS32(ctx, result);
    return true;
}
} // namespace

// Registered by the module table (ssx3_module.cpp) as "ssx3-copied-payload".
void applySsx3CopiedPayload(PS2Runtime &runtime)
{
    (void)runtime;
    g_k1Enabled.store(true, std::memory_order_relaxed);
    std::cerr << "[k1] armed name=ssx3-copied-payload elf=SLUS_207.72" << std::endl;
}

bool tryDispatchSsx3CopiedPayload(uint32_t syscallNumber, uint32_t handler, uint8_t *rdram, R5900Context *ctx)
{
    if (!g_k1Enabled.load(std::memory_order_relaxed) || !rdram || !ctx)
    {
        return false;
    }
    const bool isLookup = (syscallNumber == 0x5Bu && handler == kPayloadDst);
    bool isHelper = false;
    for (const HelperEntry &entry : kHelpers)
    {
        if (syscallNumber == entry.syscallNumber && handler == kPayloadDst + entry.offset)
        {
            isHelper = true;
            break;
        }
    }
    if (!isLookup && !isHelper)
    {
        return false;
    }
    if (!copyIntact(rdram))
    {
        const uint64_t seq = g_provenanceFail.fetch_add(1u, std::memory_order_relaxed) + 1u;
        std::cerr << "[k1] provenance-FAIL#" << seq << " n=0x" << std::hex << syscallNumber << " handler=0x"
                  << handler << std::dec << " why=copy-mismatch" << std::endl;
        return false;
    }
    if (isLookup)
    {
        return emulateLookup(rdram, ctx);
    }
    return emulateHelper(syscallNumber, rdram, ctx);
}

void noteSsx3CopiedPayloadInstall(uint32_t syscallIndex, uint32_t handler)
{
    if (!g_k1Enabled.load(std::memory_order_relaxed))
    {
        return;
    }
    const uint64_t seq = g_installLogged.fetch_add(1u, std::memory_order_relaxed) + 1u;
    std::cerr << "[k1] install#" << seq << " n=0x" << std::hex << syscallIndex << " handler=0x" << handler
              << std::dec << std::endl;
}

bool ssx3CopiedPayloadEnabled()
{
    return g_k1Enabled.load(std::memory_order_relaxed);
}

void resetSsx3CopiedPayloadForTesting()
{
    g_k1Enabled.store(false, std::memory_order_relaxed);
    g_lookupServed.store(0u, std::memory_order_relaxed);
    g_helperServed.store(0u, std::memory_order_relaxed);
    g_installLogged.store(0u, std::memory_order_relaxed);
    g_provenanceFail.store(0u, std::memory_order_relaxed);
    g_wiredAlloc.store(0u, std::memory_order_relaxed);
}
} // namespace ps2_syscalls

// SS1 save states: the HLE Wired allocator is guest-visible (returned by the
// 0x59 helper).
#include "runtime/ps2_savestate.h"
namespace
{
    void k1SavestateSave(ps2_savestate::Writer &w) { w.u32(ps2_syscalls::g_wiredAlloc.load()); }
    bool k1SavestateLoad(ps2_savestate::Reader &r)
    {
        ps2_syscalls::g_wiredAlloc.store(r.u32());
        return r.ok();
    }
    const bool kK1SavestateRegistered =
        ps2_savestate::registerSection("syscalls:k1", {1u, &k1SavestateSave, &k1SavestateLoad, nullptr});
}
