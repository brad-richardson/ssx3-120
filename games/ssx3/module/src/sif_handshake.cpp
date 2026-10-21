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

// Same bounds as the kernel's getEeGuestStruct<uint32_t>
// (Syscalls/Common.h): aligned words in RDRAM, its KSEG0/KSEG1 mirrors,
// or the scratchpad; anything else reads as absent.
const uint32_t *sifHandshakeSregIndex(const uint8_t *rdram, uint32_t address)
{
    if (rdram == nullptr || (address & (alignof(uint32_t) - 1u)) != 0u)
    {
        return nullptr;
    }
    uint32_t offset = 0u;
    bool scratch = false;
    if (ps2IsScratchpadAddress(address))
    {
        scratch = true;
        offset = ps2ScratchpadOffset(address);
        if (sizeof(uint32_t) > PS2_SCRATCHPAD_SIZE || offset > PS2_SCRATCHPAD_SIZE - sizeof(uint32_t))
        {
            return nullptr;
        }
    }
    else if (address < PS2_RAM_SIZE)
    {
        offset = address;
    }
    else if ((address >= 0x20000000u && address < 0x40000000u) ||
             (address >= 0x80000000u && address < 0xC0000000u))
    {
        offset = address & 0x1FFFFFFFu;
    }
    else
    {
        return nullptr;
    }
    if (!scratch && (sizeof(uint32_t) > PS2_RAM_SIZE || offset > PS2_RAM_SIZE - sizeof(uint32_t)))
    {
        return nullptr;
    }
    if (scratch)
    {
        const uint8_t *base = ps2GetScratchpadHostPtr();
        return base ? reinterpret_cast<const uint32_t *>(base + offset) : nullptr;
    }
    return reinterpret_cast<const uint32_t *>(rdram + offset);
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
