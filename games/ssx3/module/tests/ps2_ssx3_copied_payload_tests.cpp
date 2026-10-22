// SPDX-License-Identifier: GPL-3.0-or-later OR MIT
// Test harness and the module under test.
#include "MiniTest.h"
#include "Ssx3CopiedPayload.h"
#include "game_overrides.h"
#include "ps2_ssx3_module.h"
// Engine: runtime, GPR macros, syscall dispatch, savestate sections.
#include "ps2_runtime.h"
#include "ps2_runtime_macros.h"
#include "ps2_syscalls.h"
#include "runtime/ps2_savestate.h"

#include <cstring>
#include <iterator>
#include <string>
#include <vector>

// module-seam-2: the K1 copied-payload tests (formerly in
// ps2xTest/src/ps2_runtime_kernel_tests.cpp), driven through a file-local
// KernelRig fixture.
// The quirk is armed by the module table and reached through the module's
// syscall hooks, so these run in the product suite only.
using namespace ps2_syscalls;

namespace
{
    constexpr int KE_ERROR = -1;

    // A bare EE for calling kernel syscalls directly: zero-filled guest RAM,
    // an all-zero R5900 context and a runtime to arm overrides on.
    struct KernelRig
    {
        std::vector<uint8_t> ram = std::vector<uint8_t>(PS2_RAM_SIZE, 0u);
        R5900Context cpu{};
        PS2Runtime rt;

        KernelRig()
        {
            // Wipe everything, including what R5900Context's constructor seeds.
            std::memset(static_cast<void *>(&cpu), 0, sizeof cpu);
        }

        uint8_t *mem() { return ram.data(); }

        // Load a GPR the way the recompiled code does (sign-extended word).
        void arg(int gpr, uint32_t word) { SET_GPR_U32(&cpu, gpr, word); }

        // $v0 as a signed word / as the raw word.
        int32_t v0() const { return static_cast<int32_t>(::getRegU32(&cpu, 2)); }
        uint32_t v0Bits() const { return ::getRegU32(&cpu, 2); }

        void store32(uint32_t addr, uint32_t word) { std::memcpy(&ram[addr], &word, sizeof word); }

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

        // Point syscall `num` at guest handler `handler` (SetSyscall-style).
        void setHandler(uint32_t num, uint32_t handler) { rt.setEeSyscallOverride(mem(), num, handler); }

        // Dispatch syscall `num` with the current registers.
        bool syscall(uint32_t num) { return dispatchNumericSyscall(num, mem(), &cpu, &rt); }
    };
}

void register_ps2_ssx3_copied_payload_tests()
{
    MiniTest::Case("Ps2Ssx3CopiedPayload", [](TestCase &tc)
    {
        tc.Run("the module wires the K1 syscall hooks and its savestate section", [](TestCase &t)
        {
            const ps2_game_module::Module &m = ps2x_ssx3_game_module();
            t.IsTrue(m.noteSyscallInstall == &noteSsx3CopiedPayloadInstall, "install hook");
            t.IsTrue(m.serveUnmappedSyscall == &tryDispatchSsx3CopiedPayload, "unmapped-handler hook");
            t.IsTrue(ps2_savestate::registeredSections().count("syscalls:k1") == 1u,
                     "section registered: syscalls:k1");
        });

        tc.Run("SSX3 copied payload lookup serves all six installer keys (K1)", [](TestCase &t)
        {
            KernelRig rig;
            resetSsx3CopiedPayloadForTesting();
            ps2_game_overrides::applyMatching(rig.rt, "SLUS_207.72", 0x00100008u, 0u, false);

            // The game's own six pairs (ELF 0x4564C0, destination 0x80075300).
            constexpr uint32_t kPairs[] = {
                0x55u, 0x80075038u, 0x56u, 0x800750C8u, 0x57u, 0x80075108u,
                0x58u, 0x80075158u, 0x59u, 0x800751A8u, 0x03u, 0x80075330u,
            };
            constexpr uint32_t kSrc = 0x4561C0u;
            constexpr uint32_t kDstPhys = 0x75000u;
            constexpr uint32_t kSize = 0x330u;
            for (uint32_t i = 0; i < kSize; ++i)
            {
                rig.ram[kSrc + i] = static_cast<uint8_t>((i * 31u + 7u) & 0xFFu);
            }
            rig.store32s(kSrc + 0x300u, kPairs);
            std::memcpy(rig.mem() + kDstPhys, rig.mem() + kSrc, kSize);

            rig.setHandler(0x5Bu, 0x80075000u);
            for (size_t i = 0; i < std::size(kPairs); i += 2)
            {
                rig.arg(4, kPairs[i]);
                t.IsTrue(rig.syscall(0x5Bu), "copied-payload lookup should dispatch");
                t.Equals(rig.v0Bits(), kPairs[i + 1],
                         "copied-payload lookup should return the payload's own value");
            }
            rig.arg(4, 0x54u);
            t.IsTrue(rig.syscall(0x5Bu), "a lookup miss should still dispatch");
            t.Equals(rig.v0Bits(), 0u, "a lookup miss should return 0 like the payload");
            resetSsx3CopiedPayloadForTesting();
        });

        tc.Run("SSX3 copied payload helpers validate like the payload (K1)", [](TestCase &t)
        {
            KernelRig rig;
            resetSsx3CopiedPayloadForTesting();
            ps2_game_overrides::applyMatching(rig.rt, "SLUS_207.72", 0x00100008u, 0u, false);

            constexpr uint32_t kSrc = 0x4561C0u;
            constexpr uint32_t kDstPhys = 0x75000u;
            constexpr uint32_t kSize = 0x330u;
            for (uint32_t i = 0; i < kSize; ++i)
            {
                rig.ram[kSrc + i] = static_cast<uint8_t>((i * 31u + 7u) & 0xFFu);
            }
            std::memcpy(rig.mem() + kDstPhys, rig.mem() + kSrc, kSize);

            rig.setHandler(0x55u, 0x80075038u);
            rig.setHandler(0x56u, 0x800750C8u);
            rig.setHandler(0x57u, 0x80075108u);
            rig.setHandler(0x58u, 0x80075158u);
            rig.setHandler(0x59u, 0x800751A8u);

            rig.arg(4, 0x2Fu);
            t.IsTrue(rig.syscall(0x56u), "0x56 should dispatch");
            t.Equals(rig.v0(), 0x2F, "0x56 with a0<0x30 should return the index");
            rig.arg(4, 0x30u);
            t.IsTrue(rig.syscall(0x56u), "0x56 should dispatch");
            t.Equals(rig.v0(), KE_ERROR, "0x56 with a0>=0x30 should return -1");

            constexpr uint32_t kOut0 = 0x00002000u;
            constexpr uint32_t kOut1 = 0x00002100u;
            constexpr uint32_t kOut2 = 0x00002200u;
            constexpr uint32_t kOut3 = 0x00002300u;
            rig.store32(kOut0, 0xDEADBEEFu);
            rig.store32(kOut1, 0xDEADBEEFu);
            rig.store32(kOut2, 0xDEADBEEFu);
            rig.store32(kOut3, 0xDEADBEEFu);
            rig.arg(4, 0x10u);
            rig.arg(5, kOut0);
            rig.arg(6, kOut1);
            rig.arg(7, kOut2);
            rig.arg(8, kOut3);
            t.IsTrue(rig.syscall(0x57u), "0x57 should dispatch");
            t.Equals(rig.v0(), 0x10, "0x57 with a0<0x30 should return the index");
            t.Equals(rig.load32(kOut0), 0u, "0x57 should store empty-TLB zeros");
            t.Equals(rig.load32(kOut1), 0u, "0x57 should store empty-TLB zeros");
            t.Equals(rig.load32(kOut2), 0u, "0x57 should store empty-TLB zeros");
            t.Equals(rig.load32(kOut3), 0u, "0x57 should store empty-TLB zeros");
            rig.arg(4, 0x30u);
            t.IsTrue(rig.syscall(0x57u), "0x57 should dispatch");
            t.Equals(rig.v0(), KE_ERROR, "0x57 with a0>=0x30 should return -1");

            for (const uint32_t a1 : {0x00123456u, 0x309ABCDEu, 0x40FEDCBAu})
            {
                rig.arg(5, a1);
                t.IsTrue(rig.syscall(0x55u), "0x55 should dispatch");
                t.Equals(rig.v0(), 0, "0x55 with a valid top nibble should succeed");
            }
            for (const uint32_t a1 : {0x10123456u, 0x20123456u, 0x50123456u})
            {
                rig.arg(5, a1);
                t.IsTrue(rig.syscall(0x55u), "0x55 should dispatch");
                t.Equals(rig.v0(), KE_ERROR, "0x55 with an invalid top nibble should return -1");
            }

            t.IsTrue(rig.syscall(0x58u), "0x58 should dispatch");
            t.Equals(rig.v0(), KE_ERROR, "0x58 with no HLE mappings should miss with -1");

            rig.arg(4, 0x1001u);
            t.IsTrue(rig.syscall(0x59u), "0x59 should dispatch");
            t.Equals(rig.v0(), KE_ERROR, "0x59 with a misaligned size should return -1");
            rig.arg(4, 0x1000u);
            t.IsTrue(rig.syscall(0x59u), "0x59 should dispatch");
            t.Equals(rig.v0(), KE_ERROR, "0x59 with a small size should return -1");
            rig.arg(4, 0u);
            t.IsTrue(rig.syscall(0x59u), "0x59 should dispatch");
            t.Equals(rig.v0(), 0, "0x59 with size 0 should succeed with 0");
            rig.arg(4, 0x10000000u);
            t.IsTrue(rig.syscall(0x59u), "0x59 should dispatch");
            t.Equals(rig.v0(), 0, "0x59 should allocate wired index 0 first");
            t.IsTrue(rig.syscall(0x59u), "0x59 should dispatch");
            t.Equals(rig.v0(), 1, "0x59 should allocate wired index 1 next");
            resetSsx3CopiedPayloadForTesting();
        });

        tc.Run("SSX3 copied payload refuses broken copies and other games (K1)", [](TestCase &t)
        {
            KernelRig rig;
            resetSsx3CopiedPayloadForTesting();

            constexpr uint32_t kSrc = 0x4561C0u;
            constexpr uint32_t kDstPhys = 0x75000u;
            constexpr uint32_t kSize = 0x330u;
            for (uint32_t i = 0; i < kSize; ++i)
            {
                rig.ram[kSrc + i] = static_cast<uint8_t>((i * 31u + 7u) & 0xFFu);
            }
            std::memcpy(rig.mem() + kDstPhys, rig.mem() + kSrc, kSize);
            rig.setHandler(0x5Bu, 0x80075000u);

            rig.arg(4, 0x55u);
            t.IsTrue(rig.syscall(0x5Bu), "an unarmed quirk should still dispatch");
            t.Equals(rig.v0(), KE_ERROR, "an unarmed quirk should keep the existing KE_ERROR drop path");

            ps2_game_overrides::applyMatching(rig.rt, "SLUS_207.72", 0x00100008u, 0u, false);
            rig.ram[kDstPhys + 0x10u] ^= 0xFFu;
            rig.arg(4, 0x55u);
            t.IsTrue(rig.syscall(0x5Bu), "a broken copy should still dispatch");
            t.Equals(rig.v0(), KE_ERROR, "a broken copy should keep the existing KE_ERROR drop path");

            rig.ram[kDstPhys + 0x10u] ^= 0xFFu;
            rig.setHandler(0x54u, 0x80075000u);
            rig.arg(4, 0x55u);
            t.IsTrue(rig.syscall(0x54u), "an unmatched pair should still dispatch");
            t.Equals(rig.v0(), KE_ERROR,
                     "an unmatched (syscall, handler) pair should keep the KE_ERROR drop path");
            resetSsx3CopiedPayloadForTesting();
        });

    });
}
