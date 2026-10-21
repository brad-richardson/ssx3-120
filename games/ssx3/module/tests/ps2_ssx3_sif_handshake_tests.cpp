#include "MiniTest.h"
#include "Ssx3SifHandshake.h"
#include "game_overrides.h"
#include "ps2_runtime.h"
#include "ps2_runtime_macros.h"
#include "ps2_syscalls.h"
#include "ps2_ssx3_module.h"

#include <cstddef>
#include <cstdint>
#include <cstring>
#include <iterator>
#include <vector>

// sif-handshake-1: the P1ac SIF handshake hook wiring.
// snd-seam-1: the four P1ac behavior tests moved here from
// ps2xTest/src/ps2_runtime_kernel_tests.cpp (sif-handshake-1 follow-up)
// with private TestEnv + guest helpers (copies of the shared kernel-test
// versions). The shared suite keeps no copy; the generic suite keeps only
// a no-handshake assertion (game_module_none_tests.cpp).
using namespace ps2_syscalls;

namespace
{
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
        writeGuestU32(rdram, addr + static_cast<uint32_t>(i * sizeof(uint32_t)), words[i]);
}

uint32_t readGuestU32(const uint8_t *rdram, uint32_t addr)
{
    uint32_t value = 0;
    std::memcpy(&value, rdram + addr, sizeof(value));
    return value;
}
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
            TestEnv env;
            resetSsx3SifHandshakeForTesting();

            constexpr uint32_t kPacketAddr = 0x00009000u;
            constexpr uint32_t kSregs1Addr = 0x52BE04u;
            constexpr uint32_t kPacket[] = {0u, 0u, 0u, 0u, 1u, 1u};
            writeGuestWords(env.rdram.data(), kPacketAddr, kPacket, std::size(kPacket));

            setRegU32(env.ctx, 4, 0x80000001u);
            setRegU32(env.ctx, 5, kPacketAddr);
            setRegU32(env.ctx, 6, 0x18u);
            setRegU32(env.ctx, 7, 0u);
            setRegU32(env.ctx, 29, 0x00100000u);
            sceSifSendCmd(env.rdram.data(), &env.ctx, &env.runtime);

            t.Equals(getRegS32(env.ctx, 2), 1, "SendCmd must still return 1 with the gate off");
            t.Equals(readGuestU32(env.rdram.data(), kSregs1Addr), 0u,
                     "sregs[1] must stay 0 when the SSX3 override did not apply");
            resetSsx3SifHandshakeForTesting();
        });
        tc.Run("SIF handshake writes sregs[1]=1 for SSX3 SET_SREG(1,1) (P1ac)", [](TestCase &t)
               {
            TestEnv env;
            resetSsx3SifHandshakeForTesting();
            ps2_game_overrides::applyMatching(env.runtime, "SLUS_207.72", 0x00100008u, 0u, false);

            constexpr uint32_t kPacketAddr = 0x00009000u;
            constexpr uint32_t kSregs1Addr = 0x52BE04u;
            constexpr uint32_t kPacket[] = {0u, 0u, 0u, 0u, 1u, 1u};
            writeGuestWords(env.rdram.data(), kPacketAddr, kPacket, std::size(kPacket));

            setRegU32(env.ctx, 4, 0x80000001u);
            setRegU32(env.ctx, 5, kPacketAddr);
            setRegU32(env.ctx, 6, 0x18u);
            setRegU32(env.ctx, 7, 0u);
            setRegU32(env.ctx, 29, 0x00100000u);
            sceSifSendCmd(env.rdram.data(), &env.ctx, &env.runtime);

            t.Equals(getRegS32(env.ctx, 2), 1, "SendCmd must still return 1");
            t.Equals(readGuestU32(env.rdram.data(), kSregs1Addr), 1u,
                     "SSX3 SET_SREG(1,1) must complete the handshake with sregs[1]=1");
            resetSsx3SifHandshakeForTesting();
        });
        tc.Run("private SIF SendCmd uses seven registers for SET_SREG", [](TestCase &t)
               {
            TestEnv env;
            resetSsx3SifHandshakeForTesting();
            ps2_game_overrides::applyMatching(env.runtime, "SLUS_207.72", 0x00100008u, 0u, false);

            constexpr uint32_t kPacketAddr = 0x00009000u;
            constexpr uint32_t kPacket[] = {0u, 0u, 0u, 0u, 1u, 1u};
            writeGuestWords(env.rdram.data(), kPacketAddr, kPacket, std::size(kPacket));
            setRegU32(env.ctx, 4, 0x80000001u);
            setRegU32(env.ctx, 5, 1u); // mode, not packet address
            setRegU32(env.ctx, 6, kPacketAddr);
            setRegU32(env.ctx, 7, 0x18u);
            setRegU32(env.ctx, 8, 0u);
            setRegU32(env.ctx, 9, 0u);
            setRegU32(env.ctx, 10, 0u);
            _sceSifSendCmd(env.rdram.data(), &env.ctx, &env.runtime);

            t.Equals(getRegS32(env.ctx, 2), 1, "private SendCmd must return success");
            t.Equals(readGuestU32(env.rdram.data(), 0x52BE04u), 1u,
                     "private SendCmd must read the packet from a2");
            resetSsx3SifHandshakeForTesting();
        });
        tc.Run("SIF handshake ignores other cids and sreg slots (P1ac)", [](TestCase &t)
               {
            TestEnv env;
            resetSsx3SifHandshakeForTesting();
            ps2_game_overrides::applyMatching(env.runtime, "SLUS_207.72", 0x00100008u, 0u, false);

            constexpr uint32_t kPacketAddr = 0x00009000u;
            constexpr uint32_t kSregs1Addr = 0x52BE04u;

            constexpr uint32_t kBindPacket[] = {0u, 0u, 0u, 0u, 1u, 1u};
            writeGuestWords(env.rdram.data(), kPacketAddr, kBindPacket, std::size(kBindPacket));
            setRegU32(env.ctx, 4, 0x00000019u);
            setRegU32(env.ctx, 5, kPacketAddr);
            setRegU32(env.ctx, 6, 0x18u);
            setRegU32(env.ctx, 7, 0u);
            setRegU32(env.ctx, 29, 0x00100000u);
            sceSifSendCmd(env.rdram.data(), &env.ctx, &env.runtime);
            t.Equals(getRegS32(env.ctx, 2), 1, "SendCmd must still return 1 for other cids");
            t.Equals(readGuestU32(env.rdram.data(), kSregs1Addr), 0u,
                     "a non-SET_SREG cid must not touch sregs[1]");

            constexpr uint32_t kOtherSlot[] = {0u, 0u, 0u, 0u, 2u, 1u};
            writeGuestWords(env.rdram.data(), kPacketAddr, kOtherSlot, std::size(kOtherSlot));
            setRegU32(env.ctx, 4, 0x80000001u);
            sceSifSendCmd(env.rdram.data(), &env.ctx, &env.runtime);
            t.Equals(getRegS32(env.ctx, 2), 1, "SendCmd must still return 1 for other sreg slots");
            t.Equals(readGuestU32(env.rdram.data(), kSregs1Addr), 0u,
                     "SET_SREG for a slot other than 1 must not touch sregs[1]");
            resetSsx3SifHandshakeForTesting();
        }); });
}
