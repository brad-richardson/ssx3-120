// SPDX-License-Identifier: GPL-3.0-or-later OR MIT
// sif-handshake-1: moved from ps2xRuntime/src/lib/Kernel/Syscalls/RPC.cpp
// (fork f7a96f23). The runtime keeps the SendCmd call site; the module owns
// the SSX 3 ready-handshake policy behind the game-module SIF hook
// (runtime/game_module.h). Prefer public runtime headers
// (ps2_runtime.h, runtime/ps2_memory.h) over the kernel's private
// Syscalls/Common.h, as K1 did.
#include "Ssx3SifHandshake.h"
#include "ssx3_module_internal.h"

#include "ps2_runtime.h"
#include "runtime/ps2_memory.h"

#include <atomic>
#include <cstdint>
#include <cstring>
#include <iostream>

namespace ps2_syscalls
{
namespace
{
// P1ac: SSX3-only gate for the SIF ready-handshake completion in the
// module's onSifSendCmd below. Set by the ps2_game_overrides descriptor
// (SLUS_207.72, entry 0x100008); the HLE process loads one ELF, so
// a process-wide flag matches the registry's apply-once-at-load
// model.
std::atomic<bool> g_ssx3SifHandshakeEnabled{false};

// Physical RDRAM sits behind the low 29 address bits. Of the eight 512 MB
// EE segments (address >> 29), four reach it: 0 is the direct low window,
// 1 the 0x2000'0000 uncached alias, 4 KSEG0 (cached) and 5 KSEG1
// (uncached). 2, 3 and 6, 7 (TLB-mapped and KSEG2/3) never alias RDRAM.
constexpr uint32_t kEePhysicalBits = 0x1FFF'FFFFu;

constexpr bool eeSegmentAliasesRdram(uint32_t address)
{
    const uint32_t segment = address >> 29;
    return segment == 0u || segment == 1u || segment == 4u || segment == 5u;
}

// Host view of an aligned guest word, with the bounds of the kernel's
// getEeGuestStruct<uint32_t> (Syscalls/Common.h): the scratchpad (and
// its high mirror) first, then any RDRAM alias above. Everything else,
// including a word that would run off the end of either region, reads
// as absent (nullptr).
const uint32_t *sifHandshakeSregIndex(const uint8_t *rdram, uint32_t address)
{
    constexpr uint32_t kWordBytes = sizeof(uint32_t);
    const uint8_t *host = nullptr;
    if (rdram != nullptr && address % kWordBytes == 0u)
    {
        if (ps2IsScratchpadAddress(address))
        {
            const uint32_t spadOffset = ps2ScratchpadOffset(address);
            const uint8_t *spadBase =
                (spadOffset + kWordBytes <= PS2_SCRATCHPAD_SIZE) ? ps2GetScratchpadHostPtr() : nullptr;
            if (spadBase != nullptr)
            {
                host = spadBase + spadOffset;
            }
        }
        else if (eeSegmentAliasesRdram(address))
        {
            const uint32_t physical = address & kEePhysicalBits;
            if (physical + kWordBytes <= PS2_RAM_SIZE)
            {
                host = rdram + physical;
            }
        }
    }
    return reinterpret_cast<const uint32_t *>(host);
}
} // namespace

// Registered by the module table (ssx3_module.cpp) as "ssx3-sif-handshake".
void ps2_syscalls::applySsx3SifHandshake(PS2Runtime &runtime)
{
    // The checked-in runtime can use an older external codegen tree
    // whose _sceSifSendCmd stub still calls the six-argument handler.
    runtime.replaceFunction(0x00426078u, &ps2_syscalls::_sceSifSendCmd);
    g_ssx3SifHandshakeEnabled.store(true, std::memory_order_relaxed);
}

void ps2_syscalls::onSsx3SifSendCmd(uint32_t cid, uint32_t packetAddr, uint32_t packetSize, uint8_t *rdram)
{
    // P1ac: complete the SSX3 EE<->IOP SIF ready-handshake host-side.
    // The game sends SET_SREG(1,1) then spins on sregs[1] (guest
    // 0x52BE04); on HW the IOP's reply arrives via SIF0 DMA and EE
    // set_sreg writes the word, but the HLE has no IOP SIF peer (this
    // send is otherwise a no-op). Any nonzero exits the beqz poll;
    // the value mirrors the sent value and Sony's SetReg(RPCINIT,1).
    if (g_ssx3SifHandshakeEnabled.load(std::memory_order_relaxed) &&
        cid == 0x80000001u)
    {
        constexpr uint32_t kSregIndexOffset = 4u * sizeof(uint32_t);
        const uint32_t *sregIndex = nullptr;
        if (packetAddr != 0u &&
            packetSize >= 5u * sizeof(uint32_t) &&
            packetAddr <= UINT32_MAX - kSregIndexOffset)
        {
            sregIndex = sifHandshakeSregIndex(rdram, packetAddr + kSregIndexOffset);
        }
        if (sregIndex != nullptr && *sregIndex == 1u)
        {
            constexpr uint32_t kSsx3Sregs1Addr = 0x52BE04u;
            uint8_t *sregs1 = getMemPtr(rdram, kSsx3Sregs1Addr);
            if (sregs1 != nullptr)
            {
                const uint32_t one = 1u;
                std::memcpy(sregs1, &one, sizeof(one));
                static int handshakeCount = 0;
                if (handshakeCount < 5)
                {
                    std::cerr << "[sif-handshake] sregs[1]=1" << std::endl;
                    ++handshakeCount;
                }
            }
        }
    }
}

void ps2_syscalls::resetSsx3SifHandshakeForTesting()
{
    g_ssx3SifHandshakeEnabled.store(false, std::memory_order_relaxed);
}
} // namespace ps2_syscalls
