#include "MiniTest.h"
#include "Ssx3CopiedPayload.h"
#include "game_overrides.h"
#include "ps2_runtime.h"
#include "ps2_runtime_macros.h"
#include "ps2_syscalls.h"
#include "ps2_ssx3_module.h"
#include "runtime/ps2_savestate.h"

#include <cstring>
#include <iterator>
#include <string>
#include <vector>

// module-seam-2: the K1 copied-payload tests, moved verbatim from
// ps2xTest/src/ps2_runtime_kernel_tests.cpp with the file's small helpers.
// The quirk is armed by the module table and reached through the module's
// syscall hooks, so these run in the product suite only.
using namespace ps2_syscalls;

namespace
{
    constexpr int KE_ERROR = -1;

    struct TestEnv
    {
        std::vector<uint8_t> rdram;
        R5900Context ctx{};
        PS2Runtime runtime;

        TestEnv() : rdram(PS2_RAM_SIZE, 0)
        {
            std::memset(&ctx, 0, sizeof(ctx));
        }
    };

    void setRegU32(R5900Context &ctx, int reg, uint32_t value)
    {
        SET_GPR_U32(&ctx, reg, value);
    }

    int32_t getRegS32(const R5900Context &ctx, int reg)
    {
        return static_cast<int32_t>(::getRegU32(&ctx, reg));
    }

    void writeGuestU32(uint8_t *rdram, uint32_t addr, uint32_t value)
    {
        std::memcpy(rdram + addr, &value, sizeof(value));
    }

    void writeGuestWords(uint8_t *rdram, uint32_t addr, const uint32_t *words, size_t count)
    {
        for (size_t i = 0; i < count; ++i)
        {
            writeGuestU32(rdram, addr + static_cast<uint32_t>(i * sizeof(uint32_t)), words[i]);
        }
    }

    uint32_t readGuestU32(const uint8_t *rdram, uint32_t addr)
    {
        uint32_t value = 0;
        std::memcpy(&value, rdram + addr, sizeof(value));
        return value;
    }

    bool callSyscall(uint32_t syscallNumber, uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        return dispatchNumericSyscall(syscallNumber, rdram, ctx, runtime);
    }
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
            TestEnv env;
            resetSsx3CopiedPayloadForTesting();
            ps2_game_overrides::applyMatching(env.runtime, "SLUS_207.72", 0x00100008u, 0u, false);

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
                env.rdram[kSrc + i] = static_cast<uint8_t>((i * 31u + 7u) & 0xFFu);
            }
            writeGuestWords(env.rdram.data(), kSrc + 0x300u, kPairs, std::size(kPairs));
            std::memcpy(env.rdram.data() + kDstPhys, env.rdram.data() + kSrc, kSize);

            env.runtime.setEeSyscallOverride(env.rdram.data(), 0x5Bu, 0x80075000u);
            for (size_t i = 0; i < std::size(kPairs); i += 2)
            {
                setRegU32(env.ctx, 4, kPairs[i]);
                t.IsTrue(callSyscall(0x5Bu, env.rdram.data(), &env.ctx, &env.runtime),
                         "copied-payload lookup should dispatch");
                t.Equals(static_cast<uint32_t>(getRegS32(env.ctx, 2)), kPairs[i + 1],
                         "copied-payload lookup should return the payload's own value");
            }
            setRegU32(env.ctx, 4, 0x54u);
            t.IsTrue(callSyscall(0x5Bu, env.rdram.data(), &env.ctx, &env.runtime),
                     "a lookup miss should still dispatch");
            t.Equals(static_cast<uint32_t>(getRegS32(env.ctx, 2)), 0u,
                     "a lookup miss should return 0 like the payload");
            resetSsx3CopiedPayloadForTesting();
        });

        tc.Run("SSX3 copied payload helpers validate like the payload (K1)", [](TestCase &t)
        {
            TestEnv env;
            resetSsx3CopiedPayloadForTesting();
            ps2_game_overrides::applyMatching(env.runtime, "SLUS_207.72", 0x00100008u, 0u, false);

            constexpr uint32_t kSrc = 0x4561C0u;
            constexpr uint32_t kDstPhys = 0x75000u;
            constexpr uint32_t kSize = 0x330u;
            for (uint32_t i = 0; i < kSize; ++i)
            {
                env.rdram[kSrc + i] = static_cast<uint8_t>((i * 31u + 7u) & 0xFFu);
            }
            std::memcpy(env.rdram.data() + kDstPhys, env.rdram.data() + kSrc, kSize);

            env.runtime.setEeSyscallOverride(env.rdram.data(), 0x55u, 0x80075038u);
            env.runtime.setEeSyscallOverride(env.rdram.data(), 0x56u, 0x800750C8u);
            env.runtime.setEeSyscallOverride(env.rdram.data(), 0x57u, 0x80075108u);
            env.runtime.setEeSyscallOverride(env.rdram.data(), 0x58u, 0x80075158u);
            env.runtime.setEeSyscallOverride(env.rdram.data(), 0x59u, 0x800751A8u);

            setRegU32(env.ctx, 4, 0x2Fu);
            t.IsTrue(callSyscall(0x56u, env.rdram.data(), &env.ctx, &env.runtime), "0x56 should dispatch");
            t.Equals(getRegS32(env.ctx, 2), 0x2F, "0x56 with a0<0x30 should return the index");
            setRegU32(env.ctx, 4, 0x30u);
            t.IsTrue(callSyscall(0x56u, env.rdram.data(), &env.ctx, &env.runtime), "0x56 should dispatch");
            t.Equals(getRegS32(env.ctx, 2), KE_ERROR, "0x56 with a0>=0x30 should return -1");

            constexpr uint32_t kOut0 = 0x00002000u;
            constexpr uint32_t kOut1 = 0x00002100u;
            constexpr uint32_t kOut2 = 0x00002200u;
            constexpr uint32_t kOut3 = 0x00002300u;
            writeGuestU32(env.rdram.data(), kOut0, 0xDEADBEEFu);
            writeGuestU32(env.rdram.data(), kOut1, 0xDEADBEEFu);
            writeGuestU32(env.rdram.data(), kOut2, 0xDEADBEEFu);
            writeGuestU32(env.rdram.data(), kOut3, 0xDEADBEEFu);
            setRegU32(env.ctx, 4, 0x10u);
            setRegU32(env.ctx, 5, kOut0);
            setRegU32(env.ctx, 6, kOut1);
            setRegU32(env.ctx, 7, kOut2);
            setRegU32(env.ctx, 8, kOut3);
            t.IsTrue(callSyscall(0x57u, env.rdram.data(), &env.ctx, &env.runtime), "0x57 should dispatch");
            t.Equals(getRegS32(env.ctx, 2), 0x10, "0x57 with a0<0x30 should return the index");
            t.Equals(readGuestU32(env.rdram.data(), kOut0), 0u, "0x57 should store empty-TLB zeros");
            t.Equals(readGuestU32(env.rdram.data(), kOut1), 0u, "0x57 should store empty-TLB zeros");
            t.Equals(readGuestU32(env.rdram.data(), kOut2), 0u, "0x57 should store empty-TLB zeros");
            t.Equals(readGuestU32(env.rdram.data(), kOut3), 0u, "0x57 should store empty-TLB zeros");
            setRegU32(env.ctx, 4, 0x30u);
            t.IsTrue(callSyscall(0x57u, env.rdram.data(), &env.ctx, &env.runtime), "0x57 should dispatch");
            t.Equals(getRegS32(env.ctx, 2), KE_ERROR, "0x57 with a0>=0x30 should return -1");

            for (const uint32_t a1 : {0x00123456u, 0x309ABCDEu, 0x40FEDCBAu})
            {
                setRegU32(env.ctx, 5, a1);
                t.IsTrue(callSyscall(0x55u, env.rdram.data(), &env.ctx, &env.runtime),
                         "0x55 should dispatch");
                t.Equals(getRegS32(env.ctx, 2), 0, "0x55 with a valid top nibble should succeed");
            }
            for (const uint32_t a1 : {0x10123456u, 0x20123456u, 0x50123456u})
            {
                setRegU32(env.ctx, 5, a1);
                t.IsTrue(callSyscall(0x55u, env.rdram.data(), &env.ctx, &env.runtime),
                         "0x55 should dispatch");
                t.Equals(getRegS32(env.ctx, 2), KE_ERROR, "0x55 with an invalid top nibble should return -1");
            }

            t.IsTrue(callSyscall(0x58u, env.rdram.data(), &env.ctx, &env.runtime), "0x58 should dispatch");
            t.Equals(getRegS32(env.ctx, 2), KE_ERROR, "0x58 with no HLE mappings should miss with -1");

            setRegU32(env.ctx, 4, 0x1001u);
            t.IsTrue(callSyscall(0x59u, env.rdram.data(), &env.ctx, &env.runtime), "0x59 should dispatch");
            t.Equals(getRegS32(env.ctx, 2), KE_ERROR, "0x59 with a misaligned size should return -1");
            setRegU32(env.ctx, 4, 0x1000u);
            t.IsTrue(callSyscall(0x59u, env.rdram.data(), &env.ctx, &env.runtime), "0x59 should dispatch");
            t.Equals(getRegS32(env.ctx, 2), KE_ERROR, "0x59 with a small size should return -1");
            setRegU32(env.ctx, 4, 0u);
            t.IsTrue(callSyscall(0x59u, env.rdram.data(), &env.ctx, &env.runtime), "0x59 should dispatch");
            t.Equals(getRegS32(env.ctx, 2), 0, "0x59 with size 0 should succeed with 0");
            setRegU32(env.ctx, 4, 0x10000000u);
            t.IsTrue(callSyscall(0x59u, env.rdram.data(), &env.ctx, &env.runtime), "0x59 should dispatch");
            t.Equals(getRegS32(env.ctx, 2), 0, "0x59 should allocate wired index 0 first");
            t.IsTrue(callSyscall(0x59u, env.rdram.data(), &env.ctx, &env.runtime), "0x59 should dispatch");
            t.Equals(getRegS32(env.ctx, 2), 1, "0x59 should allocate wired index 1 next");
            resetSsx3CopiedPayloadForTesting();
        });

        tc.Run("SSX3 copied payload refuses broken copies and other games (K1)", [](TestCase &t)
        {
            TestEnv env;
            resetSsx3CopiedPayloadForTesting();

            constexpr uint32_t kSrc = 0x4561C0u;
            constexpr uint32_t kDstPhys = 0x75000u;
            constexpr uint32_t kSize = 0x330u;
            for (uint32_t i = 0; i < kSize; ++i)
            {
                env.rdram[kSrc + i] = static_cast<uint8_t>((i * 31u + 7u) & 0xFFu);
            }
            std::memcpy(env.rdram.data() + kDstPhys, env.rdram.data() + kSrc, kSize);
            env.runtime.setEeSyscallOverride(env.rdram.data(), 0x5Bu, 0x80075000u);

            setRegU32(env.ctx, 4, 0x55u);
            t.IsTrue(callSyscall(0x5Bu, env.rdram.data(), &env.ctx, &env.runtime),
                     "an unarmed quirk should still dispatch");
            t.Equals(getRegS32(env.ctx, 2), KE_ERROR,
                     "an unarmed quirk should keep the existing KE_ERROR drop path");

            ps2_game_overrides::applyMatching(env.runtime, "SLUS_207.72", 0x00100008u, 0u, false);
            env.rdram[kDstPhys + 0x10u] ^= 0xFFu;
            setRegU32(env.ctx, 4, 0x55u);
            t.IsTrue(callSyscall(0x5Bu, env.rdram.data(), &env.ctx, &env.runtime),
                     "a broken copy should still dispatch");
            t.Equals(getRegS32(env.ctx, 2), KE_ERROR,
                     "a broken copy should keep the existing KE_ERROR drop path");

            env.rdram[kDstPhys + 0x10u] ^= 0xFFu;
            env.runtime.setEeSyscallOverride(env.rdram.data(), 0x54u, 0x80075000u);
            setRegU32(env.ctx, 4, 0x55u);
            t.IsTrue(callSyscall(0x54u, env.rdram.data(), &env.ctx, &env.runtime),
                     "an unmatched pair should still dispatch");
            t.Equals(getRegS32(env.ctx, 2), KE_ERROR,
                     "an unmatched (syscall, handler) pair should keep the KE_ERROR drop path");
            resetSsx3CopiedPayloadForTesting();
        });

    });
}
