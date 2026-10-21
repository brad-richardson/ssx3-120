#include "MiniTest.h"
#include "ps2_fh1_full120.h"
#include "runtime/ps2_savestate.h"

#include <cstdint>
#include <string>

void ps2_fh1_linkSavestateSection();

namespace
{
    // Every field the fh1 section carries, set to distinct non-default values.
    void setDistinct()
    {
        using namespace ps2_fh1;
        g_schedActive = g_commitActive = g_guestActive = g_flipPending = false;
        g_stockInit = true;
        g_patched = false;
        g_rngOdd = true;
        g_lcgHeld = true;
        g_passRan = g_anyAir = g_finished = g_clockRan = g_startEdge = g_rcActive = g_postArmed = true;
        g_divNext = 2u;
        g_divThis = 3u;
        g_divEnded = 4u;
        g_producerCalls = 5u;
        g_lastStamp10s = 6u;
        g_raceTickCalls = 7u;
        g_raceTick2Calls = 8u;
        g_sessionCalls = 9u;
        g_rngUpdates = 10u;
        g_lcgSaved = 11u;
        g_drawK = 12u;
        g_stockHalf.store(13u);
        g_stockHalfExact.store(14u);
        g_drawn = 15u;
        g_drawSkipped = 16u;
        g_lastTick = 17u;
        g_rcEntryHalf = 18u;
        g_rcOffset = -19;
        g_rcEntryS = 20;
        g_post.target = 21u;
        g_post.sp = 22u;
        g_post.obj = 23u;
        g_post.kind = 2u;
        for (uint32_t i = 0; i < 6u; ++i)
            g_post.saved[i] = 30u + i;
        for (uint32_t i = 0; i < g_bonusSeen.size(); ++i)
            g_bonusSeen[i] = {0x5c2400u + i * 0x100u, 40u + i};
    }

    void setFresh()
    {
        using namespace ps2_fh1;
        g_schedActive = g_commitActive = g_guestActive = g_flipPending = g_stockInit = g_patched = false;
        g_rngOdd = g_lcgHeld = g_passRan = g_anyAir = g_finished = g_clockRan = g_startEdge = g_rcActive = false;
        g_postArmed = false;
        g_divNext = g_divThis = g_divEnded = 1u;
        g_producerCalls = g_raceTickCalls = g_raceTick2Calls = g_sessionCalls = g_rngUpdates = g_lcgSaved = g_drawK = 0u;
        g_lastStamp10s = 0xffffffffu;
        g_stockHalf.store(0u);
        g_stockHalfExact.store(0u);
        g_drawn = g_drawSkipped = g_lastTick = g_rcEntryHalf = 0u;
        g_rcOffset = g_rcEntryS = 0;
        g_post = PostCall{};
        g_bonusSeen = {};
    }

    std::vector<uint8_t> saveFh1()
    {
        ps2_savestate::Writer w;
        ps2_savestate::registeredSections().at("fh1").save(w);
        return w.buf;
    }

    bool loadFh1(const std::vector<uint8_t> &buf)
    {
        ps2_savestate::Reader r(buf.data(), buf.size());
        return ps2_savestate::registeredSections().at("fh1").load(r) && r.ok() && r.atEnd();
    }

    // REV1/RV32 F1: every unsaved host cache (the events+query play set),
    // dirtied as a mid-race quick load would find it.
    void dirtyUnsavedCaches()
    {
        using namespace ps2_fh1;
        for (uint32_t i = 0; i < kQueryHoldSlots; ++i)
        {
            g_queryHold[i].p = 0x10000u + i;
            g_queryHold[i].v0 = 7u + i;
            g_queryHold[i].w[0] = 9u + i;
        }
        for (int i = 0; i < kNormcapSlots; ++i)
        {
            g_normcapSlots[i].rider = 0x20000u + static_cast<uint32_t>(i);
            g_normcapSlots[i].preValid = true;
            g_normcapSlots[i].budgetValid = true;
            g_normcapOwners[i].pa = 0x30000u + static_cast<uint32_t>(i);
            g_normcapOwners[i].r = 0x40000u + static_cast<uint32_t>(i);
        }
        g_normcap.target = 1u;
        g_normcapArmed = true;
        g_shadowPub[0].key = 0x50000u;
        g_shadowPub[0].cur[0] = 3u;
        g_pub.target = 2u;
        g_pub.n = 4u;
        g_pubArmed = true;
        g_trailRingRngSaved[0] = 5u;
        g_trailRingRngSp = 6u;
        g_trailRingRngArmed = true;
        for (int i = 0; i < 2; ++i)
        {
            g_stuntchgHigh[i] = true;
            g_stuntchgIdle[i] = 5;
            g_stuntchgDelay[i] = 3;
        }
    }

    bool unsavedCachesClear()
    {
        using namespace ps2_fh1;
        for (uint32_t i = 0; i < kQueryHoldSlots; ++i)
            if (g_queryHold[i].p != 0u || g_queryHold[i].v0 != 0u || g_queryHold[i].w[0] != 0u)
                return false;
        for (int i = 0; i < kNormcapSlots; ++i)
            if (g_normcapSlots[i].rider != 0u || g_normcapSlots[i].preValid ||
                g_normcapSlots[i].budgetValid || g_normcapOwners[i].pa != 0u ||
                g_normcapOwners[i].r != 0u)
                return false;
        if (g_normcap.target != 0u || g_normcapArmed)
            return false;
        if (g_shadowPub[0].key != 0u || g_shadowPub[0].cur[0] != 0u || g_pub.target != 0u ||
            g_pub.n != 0u || g_pubArmed)
            return false;
        if (g_trailRingRngSaved[0] != 0u || g_trailRingRngSp != 0u || g_trailRingRngArmed)
            return false;
        for (int i = 0; i < 2; ++i)
            if (g_stuntchgHigh[i] || g_stuntchgIdle[i] != 0 || g_stuntchgDelay[i] != 0)
                return false;
        return true;
    }
} // namespace

void register_ps2_fh1_savestate_tests()
{
    MiniTest::Case("Ps2Fh1Savestate", [](TestCase &tc)
    {
        tc.Run("INP2 inputchain owns its pool across odd and even exits", [](TestCase &t)
        {
            using namespace ps2_fh1;
            const FixMasks masks = parseFix("stick,stick2,inputchain");
            const std::array<uint32_t, 7> addresses = {
                0x49bbe8u, 0x49bbf8u, 0x49bbfcu, 0x49bc98u, 0x49bca4u, 0x49bc94u, 0x49bca0u};
            const std::array<uint32_t, 7> stock = {
                kSixtieth, kSixtieth, kSixtieth, kSixtieth, kSixtieth, 0x3d4cc9c7u, 0x3d4cd331u};
            const std::array<uint32_t, 7> active = {
                kHundredTwentieth, kHundredTwentieth, kHundredTwentieth, kHundredTwentieth,
                kHundredTwentieth, 0x3cccc9c7u, 0x3cccd331u};
            std::vector<uint8_t> ram(PS2_RAM_SIZE);
            constexpr uint32_t manager = 0x1000u;
            for (bool odd : {false, true})
            {
                wr32(ram.data(), manager+0x10u, 60u);
                wr32(ram.data(), manager+0x14u, kSixtieth);
                wr32(ram.data(), manager+0x24u, 0x3f800000u);
                for (size_t i = 0; i < addresses.size(); ++i)
                {
                    wr32(ram.data(), addresses[i], stock[i]);
                    t.IsTrue(fh28PoolOverride(addresses[i], masks.main), "owned word excluded from shared validation");
                    t.IsTrue(!fh28PoolOverride(addresses[i], kFixStick|kFixStick2), "off path retains ordinary conversion");
                }
                t.IsTrue(!fh28PoolOverride(0x49bbe4u, masks.main), "neighbor word is not excluded");
                applyWords(ram.data(), manager, true, masks);
                for (size_t i = 0; i < addresses.size(); ++i)
                {
                    uint32_t got = 0u; rd32(ram.data(), addresses[i], got);
                    t.Equals(got, active[i], "inputchain initializes converted pool at entry");
                }
                inputChainPoolStep(ram.data(), odd);
                for (size_t i = 0; i < addresses.size(); ++i)
                {
                    uint32_t got = 0u; rd32(ram.data(), addresses[i], got);
                    t.Equals(got, odd ? 0u : stock[i], "same odd/even cadence as the runtime");
                }
                // Exercise the real shared validator: either cadence value
                // used to abort here before any stock restore could run.
                applyWords(ram.data(), manager, false, masks);
                for (size_t i = 0; i < addresses.size(); ++i)
                {
                    uint32_t got = 0u; rd32(ram.data(), addresses[i], got);
                    t.Equals(got, stock[i], "exit succeeds and all seven words are stock");
                }
                uint32_t rate = 0u; rd32(ram.data(), manager+0x10u, rate);
                t.Equals(rate, 60u, "shared manager restore still runs");
            }
        });
        tc.Run("fh1 is registered, v1, optional (pre-FH27 files still load)", [](TestCase &t)
        {
            ps2_fh1_linkSavestateSection();
            const auto &sections = ps2_savestate::registeredSections();
            t.IsTrue(sections.count("fh1") == 1u, "section registered");
            const auto &h = sections.at("fh1");
            t.Equals(h.version, 1u, "version 1");
            t.Equals(h.minLoadVersion, 0u, "exact version");
            t.IsTrue(h.optional, "optional");
            t.IsTrue(!sections.at("stub:sif").optional, "other sections stay required");
        });

        tc.Run("round trip restores every field (same mode)", [](TestCase &t)
        {
            setDistinct();
            const std::vector<uint8_t> a = saveFh1();
            setFresh();
            t.IsTrue(loadFh1(a), "loads");
            t.IsTrue(saveFh1() == a, "re-save is byte-identical");
            t.Equals(ps2_fh1::g_stockHalfExact.load(), uint64_t{14u}, "stock-time accounting");
            t.Equals(ps2_fh1::g_rcOffset, int64_t{-19}, "render-clock offset");
            t.Equals(ps2_fh1::g_post.saved[5], 35u, "post-call record");
            t.Equals(ps2_fh1::g_bonusSeen[7].state, 0x5c2b00u, "bonusflip list");
            t.IsTrue(ps2_fh1::g_rngOdd && ps2_fh1::g_lcgHeld, "parity and LCG hold");
            setFresh();
        });

        tc.Run("another mode: converted state refused, inactive state leaves fresh state", [](TestCase &t)
        {
            setDistinct();
            ps2_fh1::g_guestActive = true;
            std::vector<uint8_t> active = saveFh1();
            active[0] = static_cast<uint8_t>((active[0] + 1u) % 3u); // saved under another PS2X_SSX3_FULL120
            setFresh();
            t.IsTrue(!loadFh1(active), "converted guest words refuse");
            for (bool *flag : {&ps2_fh1::g_schedActive, &ps2_fh1::g_commitActive, &ps2_fh1::g_flipPending,
                               &ps2_fh1::g_patched})
            {
                setDistinct();
                *flag = true;
                std::vector<uint8_t> b = saveFh1();
                b[0] = static_cast<uint8_t>((b[0] + 1u) % 3u);
                setFresh();
                t.IsTrue(!loadFh1(b), "each converted flag refuses");
            }
            setDistinct();
            std::vector<uint8_t> idle = saveFh1();
            idle[0] = static_cast<uint8_t>((idle[0] + 1u) % 3u);
            setFresh();
            t.IsTrue(loadFh1(idle), "inactive state loads");
            t.Equals(ps2_fh1::g_stockHalfExact.load(), uint64_t{0u}, "fresh accounting kept");
            t.IsTrue(!ps2_fh1::g_stockInit, "fresh: re-derived at the next VBlank");
            setDistinct();
            std::vector<uint8_t> fix = saveFh1();
            fix[1] ^= 1u; // another FIX mask, same mode
            ps2_fh1::g_guestActive = true;
            std::vector<uint8_t> fixActive = saveFh1();
            fixActive[1] ^= 1u;
            setFresh();
            t.IsTrue(loadFh1(fix), "inactive, other FIX mask: loads");
            t.IsTrue(!loadFh1(fixActive), "converted, other FIX mask: refused");
            setFresh();
        });

        tc.Run("INP2 mask cannot change across an inactive tagged-counter state", [](TestCase &t)
        {
            setFresh();
            std::vector<uint8_t> idle = saveFh1();
            // The saved main mask is little-endian after the mode byte.
            // Use the group value so later FIX allocations cannot stale this fixture.
            for (unsigned byte = 0; byte < sizeof(uint64_t); ++byte)
                idle[1u + byte] ^= static_cast<uint8_t>(ps2_fh1::kFixInput2 >> (8u * byte));
            t.IsTrue(!loadFh1(idle), "tagged counters require the same input2 selection even outside an event");
            setFresh();
        });

        tc.Run("REV1: fh1 load clears the unsaved stuntchg latch", [](TestCase &t)
        {
            setFresh();
            const std::vector<uint8_t> buf = saveFh1();
            for (int i = 0; i < 2; ++i)
            {
                ps2_fh1::g_stuntchgHigh[i] = true;
                ps2_fh1::g_stuntchgIdle[i] = 5;
                ps2_fh1::g_stuntchgDelay[i] = 3;
            }
            t.IsTrue(loadFh1(buf), "loads");
            for (int i = 0; i < 2; ++i)
            {
                t.IsFalse(ps2_fh1::g_stuntchgHigh[i], "high latch cleared on load");
                t.Equals(ps2_fh1::g_stuntchgIdle[i], 0, "idle cleared on load");
                t.Equals(ps2_fh1::g_stuntchgDelay[i], 0, "delay cleared on load");
            }
            setFresh();
        });

        tc.Run("REV1 host det: quick load equals fresh load for unsaved caches", [](TestCase &t)
        {
            // Fresh-process baseline: serialized state fresh, caches zeroed.
            setFresh();
            ps2_fh1::clearHostCachesForLoad();
            t.IsTrue(unsavedCachesClear(), "fresh baseline caches are zero");
            const std::vector<uint8_t> buf = saveFh1();
            // Quick load: the same buffer loaded over dirtied live caches
            // (events+query play set) must land on the fresh baseline, and
            // the serialized round trip must be unaffected by the clear.
            dirtyUnsavedCaches();
            t.IsTrue(!unsavedCachesClear(), "caches dirtied before the load");
            t.IsTrue(loadFh1(buf), "quick load applies");
            t.IsTrue(unsavedCachesClear(), "quick load caches equal fresh load");
            t.IsTrue(saveFh1() == buf, "serialized state round-trips across the clear");
            setFresh();
            ps2_fh1::clearHostCachesForLoad();
        });

        tc.Run("REV1: play-build pool-word mismatch returns false, never aborts", [](TestCase &t)
        {
            using namespace ps2_fh1;
            t.IsFalse(kFull120PlayBuild, "the suite is a dev/test build (abort path compiled in)");
            std::vector<uint8_t> ram(PS2_RAM_SIZE);
            constexpr uint32_t manager = 0x1000u;
            auto stock = [&]() {
                wr32(ram.data(), manager + 0x10u, 60u);
                wr32(ram.data(), manager + 0x14u, kSixtieth);
                wr32(ram.data(), manager + 0x24u, 0x3f800000u);
                wr32(ram.data(), 0x49c124u, 0xbecccccdu);
                wr32(ram.data(), 0x49c0a8u, 0x3f666666u);
            };
            const FixMasks masks = parseFix("vnret");
            // Enter with an unexpected pool word: refused, nothing written.
            stock();
            wr32(ram.data(), 0x49c124u, 0xdeadbeefu);
            t.IsFalse(applyWords(ram.data(), manager, true, masks, true),
                      "enter mismatch refused (play build)");
            uint32_t v = 0u;
            rd32(ram.data(), manager + 0x10u, v);
            t.Equals(v, 60u, "manager untouched after enter refusal");
            rd32(ram.data(), 0x49c124u, v);
            t.Equals(v, 0xdeadbeefu, "unexpected word left as found");
            // Clean enter, then exit with a corrupted converted word: stock
            // words are force-written and the refusal is still reported.
            stock();
            t.IsTrue(applyWords(ram.data(), manager, true, masks, true), "clean enter applies");
            wr32(ram.data(), 0x49c0a8u, 0xdeadbeefu);
            t.IsFalse(applyWords(ram.data(), manager, false, masks, true),
                      "exit mismatch refused (play build)");
            rd32(ram.data(), manager + 0x10u, v);
            t.Equals(v, 60u, "manager stock after forced exit");
            rd32(ram.data(), 0x49c124u, v);
            t.Equals(v, 0xbecccccdu, "vnret word stock after forced exit");
            rd32(ram.data(), 0x49c0a8u, v);
            t.Equals(v, 0x3f666666u, "lean word stock after forced exit");
            // Session refusal settles every staged transition at stock.
            g_schedActive = g_commitActive = g_flipPending = g_guestActive = g_patched = true;
            refuseFull120ForSession("test");
            t.IsTrue(full120Refused(), "refusal latched");
            t.IsTrue(!g_schedActive && !g_commitActive && !g_flipPending && !g_guestActive &&
                         !g_patched,
                     "staged transitions cleared");
            t.Equals(vblankDivisor(), 1u, "stock divisor while refused");
            t.Equals(eeClockShiftNow(), 0u, "stock EE shift while refused");
            t.IsFalse(hooksOn(), "hooks off while refused");
            g_full120Refused.store(false); // leave the process as we found it
            setFresh();
        });

        tc.Run("truncated payload refuses", [](TestCase &t)
        {
            setFresh();
            std::vector<uint8_t> a = saveFh1();
            a.resize(a.size() - 1u);
            t.IsTrue(!loadFh1(a), "refused");
            setFresh();
        });
    });
}
