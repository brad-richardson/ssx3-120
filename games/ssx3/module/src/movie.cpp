// snd-seam-1 family #9: moved from ps2xRuntime/src/lib/Kernel/Stubs/.
// Prefer public runtime headers over that dir's private Common.h (K1 did
// the same for the SIF handshake).
#include "Ssx3Movie.h"

#include "ps2_runtime.h"
#include "runtime/ps2_memory.h"

#include <atomic>
#include <cstring>
#include <iostream>
#include <string>

namespace ps2_stubs
{
    namespace
    {
        // P7: shared invocation counter. Deliberately atomic: stubbed movie
        // functions may be called from EE threads while the runner drains.
        std::atomic<uint64_t> g_ssx3movie_invocations{0};

        constexpr size_t kSsx3MoviePathMax = 64;

        // Bounded guest read for diagnostics. Mirrors the KUSEG/KSEG0 strip
        // in ps2_memory.h but hard-fails outside RDRAM instead of wrapping:
        // a wrapped read would log a garbage path. Scratchpad is rejected
        // (movie paths live in RDRAM).
        bool readGuestBytes(const uint8_t *rdram, uint32_t addr, uint8_t *out, size_t count)
        {
            if (rdram == nullptr || out == nullptr || count == 0)
            {
                return false;
            }
            if (addr >= PS2_SCRATCHPAD_BASE && addr < (PS2_SCRATCHPAD_BASE + PS2_SCRATCHPAD_SIZE))
            {
                return false;
            }
            uint32_t phys = addr;
            if ((addr >= 0x20000000u && addr < 0x40000000u) ||
                (addr >= 0x80000000u && addr < 0xC0000000u))
            {
                phys = addr & 0x1FFFFFFFu;
            }
            if (phys >= PS2_RAM_SIZE || count > PS2_RAM_SIZE - phys)
            {
                return false;
            }
            std::memcpy(out, rdram + phys, count);
            return true;
        }

        bool readGuestU32(const uint8_t *rdram, uint32_t addr, uint32_t &out)
        {
            uint8_t bytes[4];
            if (!readGuestBytes(rdram, addr, bytes, sizeof(bytes)))
            {
                return false;
            }
            std::memcpy(&out, bytes, sizeof(out));
            return true;
        }

        // Best-effort guest C string for the log line. Renders "(null)" for a
        // null pointer and "(unreadable)" for anything outside RDRAM, NUL-less
        // within the bound, or non-printable: never throws, never over-reads.
        std::string readGuestPath(const uint8_t *rdram, uint32_t addr)
        {
            if (addr == 0u)
            {
                return "(null)";
            }
            uint8_t bytes[kSsx3MoviePathMax];
            if (!readGuestBytes(rdram, addr, bytes, sizeof(bytes)))
            {
                return "(unreadable)";
            }
            size_t len = 0;
            while (len < sizeof(bytes) && bytes[len] != 0)
            {
                const unsigned char c = bytes[len];
                if (c < 0x20u || c > 0x7Eu)
                {
                    return "(unreadable)";
                }
                ++len;
            }
            if (len == sizeof(bytes))
            {
                return "(unreadable)";
            }
            return std::string(reinterpret_cast<const char *>(bytes), len);
        }

        // One diagnostic line per invocation, on stderr (the stubs' log
        // channel, captured in boot logs). `kind` is "play-complete" for a
        // blocking-play hook or "poll-complete" for a poll hook.
        void logSsx3Movie(const char *kind,
                          uint64_t n,
                          uint32_t pc,
                          uint32_t ra,
                          uint32_t a0,
                          uint32_t a1,
                          const std::string &detail)
        {
            std::cerr << "[ssx3movie] " << kind
                      << " n=" << n
                      << " pc=0x" << std::hex << pc
                      << " ra=0x" << ra
                      << " a0=0x" << a0
                      << " a1=0x" << a1 << std::dec
                      << " " << detail << std::endl;
        }
    }

    void resetSsx3MovieStubState()
    {
        g_ssx3movie_invocations.store(0u, std::memory_order_relaxed);
    }

    uint64_t ssx3MovieStubInvocations()
    {
        return g_ssx3movie_invocations.load(std::memory_order_relaxed);
    }

    void ssx3MoviePlayComplete(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        (void)runtime;
        const uint64_t n = g_ssx3movie_invocations.fetch_add(1u, std::memory_order_relaxed) + 1u;
        const uint32_t pc = ctx != nullptr ? ctx->pc : 0u;
        const uint32_t ra = ctx != nullptr ? getRegU32(ctx, 31) : 0u;
        const uint32_t a0 = ctx != nullptr ? getRegU32(ctx, 4) : 0u;
        const uint32_t a1 = ctx != nullptr ? getRegU32(ctx, 5) : 0u;
        // sub_002534A8 contract: $a1 points at the arg block whose word 0 is
        // the movie-path pointer. Any other hook shape still gets a safe line.
        uint32_t pathPtr = 0u;
        const std::string path = (ctx != nullptr && readGuestU32(rdram, a1, pathPtr))
                                     ? readGuestPath(rdram, pathPtr)
                                     : "(unreadable)";
        logSsx3Movie("play-complete", n, pc, ra, a0, a1, "path=\"" + path + "\"");
        if (ctx != nullptr)
        {
            // The real body returns 1 (success); match it, like the TOML-stub
            // trampoline convention the sceMpeg handlers follow (set $v0, the
            // trampoline returns to $ra).
            setReturnS32(ctx, 1);
        }
    }

    void ssx3MoviePollComplete(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        (void)rdram;
        (void)runtime;
        const uint64_t n = g_ssx3movie_invocations.fetch_add(1u, std::memory_order_relaxed) + 1u;
        const uint32_t pc = ctx != nullptr ? ctx->pc : 0u;
        const uint32_t ra = ctx != nullptr ? getRegU32(ctx, 31) : 0u;
        const uint32_t a0 = ctx != nullptr ? getRegU32(ctx, 4) : 0u;
        const uint32_t a1 = ctx != nullptr ? getRegU32(ctx, 5) : 0u;
        logSsx3Movie("poll-complete", n, pc, ra, a0, a1, "done=1");
        if (ctx != nullptr)
        {
            // Matches the sceMpegIsEnd true convention (1 = stream ended).
            setReturnS32(ctx, 1);
        }
    }
}
