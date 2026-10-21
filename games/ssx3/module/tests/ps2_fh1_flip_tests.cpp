#include "MiniTest.h"
#include "ps2_fh1_full120.h"

#include <algorithm>
#include <cstdint>
#include <cstring>
#include <vector>

// full120 slice 2: guestFlip's enter path converts the manager word table,
// which lives in the module now — so this Run lives with the module. (The
// FLP3 latch Runs stay shared: the latch itself is runtime state.)

namespace
{
    using namespace ps2_fh1;

    // Twin of the stuntchg latch helpers (ps2_fh1_stuntchg_tests.cpp): each
    // TU keeps its own copy; both touch the same runtime latch state.
    void dirtyLatch()
    {
        for (int i = 0; i < 2; ++i)
        {
            g_stuntchgHigh[i] = true;
            g_stuntchgIdle[i] = 3;
            g_stuntchgDelay[i] = 2;
        }
    }

    bool latchClear()
    {
        for (int i = 0; i < 2; ++i)
            if (g_stuntchgHigh[i] || g_stuntchgIdle[i] != 0 || g_stuntchgDelay[i] != 0)
                return false;
        return true;
    }
} // namespace

void register_ps2_fh1_flip_tests()
{
    MiniTest::Case("Ps2Fh1Flip", [](TestCase &tc)
    {
        tc.Run("REV1: guestFlip resets the latch in both directions", [](TestCase &t)
        {
            std::vector<uint8_t> ram(PS2_RAM_SIZE);
            constexpr uint32_t manager = 0x1000u;
            wr32(ram.data(), kMgrPtr, manager);
            wr32(ram.data(), manager + 0x10u, 60u);
            wr32(ram.data(), manager + 0x14u, kSixtieth);
            wr32(ram.data(), manager + 0x24u, 0x3f800000u);
            dirtyLatch();
            guestFlip(ram.data(), 1u, true);
            t.IsTrue(latchClear(), "enter flip clears high/idle/delay");
            uint32_t rate = 0u;
            rd32(ram.data(), manager + 0x10u, rate);
            t.Equals(rate, 120u, "manager converted on enter");
            dirtyLatch();
            guestFlip(ram.data(), 2u, false);
            t.IsTrue(latchClear(), "exit flip clears high/idle/delay");
            rd32(ram.data(), manager + 0x10u, rate);
            t.Equals(rate, 60u, "manager restored on exit");
        });

        tc.Run("online-stock-2: lsGateWhy mirrors the 0x237950 lockstep gate", [](TestCase &t)
        {
            std::vector<uint8_t> ram(PS2_RAM_SIZE);
            constexpr uint32_t ng = 0x2000u, in = 0x3000u;
            wr32(ram.data(), kNetGamePtr, ng);
            wr32(ram.data(), ng + 0x70u, 13u);
            wr32(ram.data(), in + 0x10u, 2u);
            for (uint32_t i = 0u; i < 2u; ++i)
            {
                wr32(ram.data(), in + 0x14u + 0x18u * i + 4u, 5u);
                wr32(ram.data(), in + 0x14u + 0x18u * i + 8u, 7u);
            }
            t.Equals(lsGateWhy(ram.data(), in), 1u, "state 13 closes");
            wr32(ram.data(), ng + 0x70u, 14u);
            t.Equals(lsGateWhy(ram.data(), in), 0u, "state 14, both queues hold input: open");
            wr32(ram.data(), ng + 0x3f8u, 1u);
            t.Equals(lsGateWhy(ram.data(), in), 2u, "pause hold closes");
            wr32(ram.data(), ng + 0x3f8u, 0u);
            wr32(ram.data(), ng + 0x3fcu, 3u);
            t.Equals(lsGateWhy(ram.data(), in), 3u, "countdown hold closes");
            wr32(ram.data(), ng + 0x3fcu, 0xffffffffu);
            t.Equals(lsGateWhy(ram.data(), in), 0u, "negative countdown is open");
            wr32(ram.data(), in + 0x14u + 0x18u + 8u, 5u);
            t.Equals(lsGateWhy(ram.data(), in), 5u, "player 1 queue empty closes");
            wr32(ram.data(), in + 0x10u, 0u);
            t.Equals(lsGateWhy(ram.data(), in), 0u, "no players: open");
        });

        tc.Run("online-stock-2: the quik search timer becomes 120 s", [](TestCase &t)
        {
            std::vector<uint8_t> ram(PS2_RAM_SIZE);
            constexpr uint32_t obj = 0x5000u, msg = 0x6000u;
            auto put = [&](const char *text) { std::memcpy(ram.data() + msg, text, std::strlen(text) + 1u); };
            auto timer = [&]() {
                uint32_t v = 0u;
                rd32(ram.data(), obj + kMgrTimerOff, v);
                return v;
            };
            put("KIND=0 PERS=stocka");
            wr32(ram.data(), obj + kMgrTimerOff, kStockSearchSecs);
            t.IsFalse(quikSearchWindow(ram.data(), 0x63686174u, msg, obj), "other tag: untouched");
            t.IsFalse(quikSearchWindow(ram.data(), kQuikTag, msg, 0u), "no object: untouched");
            t.IsTrue(quikSearchWindow(ram.data(), kQuikTag, 0x20000000u | msg, 0x20000000u | obj),
                     "quik search (uncached addresses): raised");
            t.Equals(timer(), kQuikSearchSecs, "timer holds 120.0");
            t.IsFalse(quikSearchWindow(ram.data(), kQuikTag, msg, obj), "already raised: untouched");
            wr32(ram.data(), obj + kMgrTimerOff, kStockSearchSecs);
            put("PERS=stocka KIND=*");
            t.IsFalse(quikSearchWindow(ram.data(), kQuikTag, msg, obj), "KIND=* give-up keeps its timer");
            t.Equals(timer(), kStockSearchSecs, "give-up timer stays 20.0");
            wr32(ram.data(), obj + kMgrTimerOff, 0x41f00000u); // 30.0: another state's timer
            put("KIND=0");
            t.IsFalse(quikSearchWindow(ram.data(), kQuikTag, msg, obj), "non-20 s timer: untouched");
        });

        tc.Run("online-stock-2: R5 local backlog trim keeps the stock sampling cadence", [](TestCase &t)
        {
            // Queue model from the guest conditions (0x237720..0x2378c4:
            // room test, then the every-N counter, then N copies pushed),
            // with the host trim + feed at the gate and one consume per
            // update. Starts with the pre-race backlog at the stock target.
            for (uint32_t target : {6u, 36u})
            {
                std::vector<uint8_t> ram(PS2_RAM_SIZE);
                constexpr uint32_t in = 0x3000u, base = 0x4000u, N = 3u, size = 60u;
                wr32(ram.data(), in, target);
                wr32(ram.data(), in + 4u, size);
                wr32(ram.data(), in + 8u, N);
                wr32(ram.data(), in + 0x10u, 1u);
                const uint32_t rec = in + 0x14u;
                wr32(ram.data(), rec, base);
                wr32(ram.data(), rec + 4u, 0u);
                wr32(ram.data(), rec + 8u, target); // backlog of stale entries (tick 0)
                wr32(ram.data(), rec + 0xcu, 0x1234u);
                uint32_t counter = 0u, lastSample = 0u, intervalsOk = 0u, intervals = 0u, maxAge = 0u;
                for (uint32_t tick = 1u; tick <= 120u; ++tick)
                {
                    uint32_t r = 0u, w = 0u;
                    rd32(ram.data(), rec + 4u, r);
                    rd32(ram.data(), rec + 8u, w);
                    if (w - r < target + 1u - N && ++counter % N == 0u)
                    {
                        for (uint32_t k = 0u; k < N; ++k)
                            wr32(ram.data(), base + ((w + k) % size) * 100u + 4u, tick);
                        wr32(ram.data(), rec + 8u, w + N);
                        if (lastSample != 0u && tick > 30u)
                        {
                            ++intervals;
                            intervalsOk += (tick - lastSample == N) ? 1u : 0u;
                        }
                        lastSample = tick;
                    }
                    stock120TrimBacklog(ram.data(), in);
                    stock120GateFeed(ram.data(), in);
                    rd32(ram.data(), rec + 4u, r);
                    uint32_t sampledAt = 0u;
                    rd32(ram.data(), base + (r % size) * 100u + 4u, sampledAt);
                    if (tick > 30u)
                        maxAge = std::max(maxAge, tick - sampledAt);
                    wr32(ram.data(), rec + 4u, r + 1u); // the open gate consumes one
                }
                uint32_t v = 0u;
                rd32(ram.data(), in, v);
                t.Equals(v, target, "delay target left stock");
                t.IsTrue(intervals > 20u && intervalsOk == intervals, "sampled every N updates");
                t.IsTrue(maxAge < N, "consumed input is at most N-1 updates old");
            }
        });
    });
}
