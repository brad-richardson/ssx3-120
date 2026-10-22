// SPDX-License-Identifier: GPL-3.0-or-later OR MIT
// Test harness and the module under test.
#include "MiniTest.h"
#include "Ssx3SifHandshake.h"
#include "game_overrides.h"
#include "ps2_ssx3_module.h"
// Engine: runtime, GPR macros, SIF stubs.
#include "ps2_runtime.h"
#include "ps2_runtime_macros.h"
#include "ps2_syscalls.h"

#include <cstddef>
#include <cstdint>
#include <cstring>
#include <iterator>
#include <vector>

// sif-handshake-1: the P1ac SIF handshake hook wiring.
// snd-seam-1: the four P1ac behavior tests moved here from
// ps2xTest/src/ps2_runtime_kernel_tests.cpp (sif-handshake-1 follow-up)
// with a file-local SifRig fixture. The shared suite keeps no copy; the
// generic suite keeps only a no-handshake assertion
// (game_module_none_tests.cpp).
using namespace ps2_syscalls;

namespace
{
// A bare EE for calling the SIF stubs directly: zero-filled guest RAM, an
// all-zero R5900 context and a runtime for the game override to land on.
struct SifRig
{
    std::vector<uint8_t> ram = std::vector<uint8_t>(PS2_RAM_SIZE, 0u);
    R5900Context cpu{};
    PS2Runtime rt;

    SifRig()
    {
        // Wipe everything, including what R5900Context's constructor seeds.
        std::memset(static_cast<void *>(&cpu), 0, sizeof cpu);
    }

    // Load a GPR the way the recompiled code does (sign-extended word).
    void arg(int gpr, uint32_t word) { SET_GPR_U32(&cpu, gpr, word); }

    // $v0 as a signed word.
    int32_t v0() const { return static_cast<int32_t>(::getRegU32(&cpu, 2)); }

    template <size_t N>
    void store32s(uint32_t addr, const uint32_t (&words)[N])
    {
        std::memcpy(&ram[addr], words, sizeof words);
    }

    uint32_t load32(uint32_t addr) const
    {
        uint32_t word = 0u;
        std::memcpy(&word, &ram[addr], sizeof word);
        return word;
    }

    // Run one stub (rdram, ctx, runtime signature) against this rig.
    void call(void (*stub)(uint8_t *, R5900Context *, PS2Runtime *)) { stub(ram.data(), &cpu, &rt); }
};
} // namespace

void register_ps2_ssx3_sif_handshake_tests()
{
    MiniTest::Case("Ps2Ssx3SifHandshake", [](TestCase &tc)
                   {
        tc.Run("the module wires the P1ac SendCmd hook", [](TestCase &t)
               {
            const ps2_game_module::Module &m = ps2x_ssx3_game_module();
            t.IsTrue(m.onSifSendCmd == &onSsx3SifSendCmd, "sendcmd hook");
            t.Equals(m.abiVersion, ps2_game_module::kAbiVersion, "abi");
        });
        tc.Run("SIF handshake stays off without the SSX3 override (P1ac gate)", [](TestCase &t)
               {
            SifRig rig;
            resetSsx3SifHandshakeForTesting();

            constexpr uint32_t kPacketAddr = 0x00009000u;
            constexpr uint32_t kSregs1Addr = 0x52BE04u;
            constexpr uint32_t kPacket[] = {0u, 0u, 0u, 0u, 1u, 1u};
            rig.store32s(kPacketAddr, kPacket);

            rig.arg(4, 0x80000001u);
            rig.arg(5, kPacketAddr);
            rig.arg(6, 0x18u);
            rig.arg(7, 0u);
            rig.arg(29, 0x00100000u);
            rig.call(sceSifSendCmd);

            t.Equals(rig.v0(), 1, "SendCmd must still return 1 with the gate off");
            t.Equals(rig.load32(kSregs1Addr), 0u,
                     "sregs[1] must stay 0 when the SSX3 override did not apply");
            resetSsx3SifHandshakeForTesting();
        });
        tc.Run("SIF handshake writes sregs[1]=1 for SSX3 SET_SREG(1,1) (P1ac)", [](TestCase &t)
               {
            SifRig rig;
            resetSsx3SifHandshakeForTesting();
            ps2_game_overrides::applyMatching(rig.rt, "SLUS_207.72", 0x00100008u, 0u, false);

            constexpr uint32_t kPacketAddr = 0x00009000u;
            constexpr uint32_t kSregs1Addr = 0x52BE04u;
            constexpr uint32_t kPacket[] = {0u, 0u, 0u, 0u, 1u, 1u};
            rig.store32s(kPacketAddr, kPacket);

            rig.arg(4, 0x80000001u);
            rig.arg(5, kPacketAddr);
            rig.arg(6, 0x18u);
            rig.arg(7, 0u);
            rig.arg(29, 0x00100000u);
            rig.call(sceSifSendCmd);

            t.Equals(rig.v0(), 1, "SendCmd must still return 1");
            t.Equals(rig.load32(kSregs1Addr), 1u,
                     "SSX3 SET_SREG(1,1) must complete the handshake with sregs[1]=1");
            resetSsx3SifHandshakeForTesting();
        });
        tc.Run("private SIF SendCmd uses seven registers for SET_SREG", [](TestCase &t)
               {
            SifRig rig;
            resetSsx3SifHandshakeForTesting();
            ps2_game_overrides::applyMatching(rig.rt, "SLUS_207.72", 0x00100008u, 0u, false);

            constexpr uint32_t kPacketAddr = 0x00009000u;
            constexpr uint32_t kPacket[] = {0u, 0u, 0u, 0u, 1u, 1u};
            rig.store32s(kPacketAddr, kPacket);
            rig.arg(4, 0x80000001u);
            rig.arg(5, 1u); // mode, not packet address
            rig.arg(6, kPacketAddr);
            rig.arg(7, 0x18u);
            rig.arg(8, 0u);
            rig.arg(9, 0u);
            rig.arg(10, 0u);
            rig.call(_sceSifSendCmd);

            t.Equals(rig.v0(), 1, "private SendCmd must return success");
            t.Equals(rig.load32(0x52BE04u), 1u,
                     "private SendCmd must read the packet from a2");
            resetSsx3SifHandshakeForTesting();
        });
        tc.Run("SIF handshake ignores other cids and sreg slots (P1ac)", [](TestCase &t)
               {
            SifRig rig;
            resetSsx3SifHandshakeForTesting();
            ps2_game_overrides::applyMatching(rig.rt, "SLUS_207.72", 0x00100008u, 0u, false);

            constexpr uint32_t kPacketAddr = 0x00009000u;
            constexpr uint32_t kSregs1Addr = 0x52BE04u;

            constexpr uint32_t kBindPacket[] = {0u, 0u, 0u, 0u, 1u, 1u};
            rig.store32s(kPacketAddr, kBindPacket);
            rig.arg(4, 0x00000019u);
            rig.arg(5, kPacketAddr);
            rig.arg(6, 0x18u);
            rig.arg(7, 0u);
            rig.arg(29, 0x00100000u);
            rig.call(sceSifSendCmd);
            t.Equals(rig.v0(), 1, "SendCmd must still return 1 for other cids");
            t.Equals(rig.load32(kSregs1Addr), 0u,
                     "a non-SET_SREG cid must not touch sregs[1]");

            constexpr uint32_t kOtherSlot[] = {0u, 0u, 0u, 0u, 2u, 1u};
            rig.store32s(kPacketAddr, kOtherSlot);
            rig.arg(4, 0x80000001u);
            rig.call(sceSifSendCmd);
            t.Equals(rig.v0(), 1, "SendCmd must still return 1 for other sreg slots");
            t.Equals(rig.load32(kSregs1Addr), 0u,
                     "SET_SREG for a slot other than 1 must not touch sregs[1]");
            resetSsx3SifHandshakeForTesting();
        }); });
}
