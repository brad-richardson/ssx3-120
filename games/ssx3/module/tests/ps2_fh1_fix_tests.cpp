#include "MiniTest.h"
#include "ps2_fh1_fix.h"
#include "ps2_fh1_full120.h"
#include "ps2_fh1_ground2.h"
#include "ps2_fh1_normcap.h"
#include "ps2_fh1_input2.h"
#include "ps2_fh1_fh35.h"
#include <cmath>
#include <algorithm>

#include <cstdint>
#include <cstring>
#include <fstream>
#include <regex>
#include <set>
#include <sstream>
#include <string>
#include <vector>

void register_ps2_fh1_fix_tests()
{
    using namespace ps2_fh1;
    MiniTest::Case("Ps2Fh1Fix", [](TestCase &tc)
    {
        tc.Run("INP2 every declared FIX group has a unique bit in its mask", [](TestCase &t)
        {
            // Read the declarations rather than maintain a second group list:
            // future additions must participate, including internal kFixRamp.
            // The two masks deliberately have independent bit namespaces.
            std::ifstream file("ps2xRuntime/include/ps2_fh1_fix.h");
            t.IsTrue(file.is_open(), "run the suite from the fork worktree root");
            if (!file.is_open()) return;
            std::ostringstream buffer;
            buffer << file.rdbuf();
            const std::string source = buffer.str();
            const std::regex member(R"(^\s*(kFix[A-Za-z0-9_]+)\b)");
            const std::regex definition(R"(^\s*(kFix[A-Za-z0-9_]+)\s*=\s*1(ull|u)\s*<<\s*([0-9]+)\s*,)");
            auto check = [&](const char *declaration, unsigned width)
            {
                const size_t at = source.find(declaration);
                t.IsTrue(at != std::string::npos, "FIX enum declaration found");
                if (at == std::string::npos) return;
                const size_t begin = source.find('{', at), end = source.find('}', begin);
                t.IsTrue(begin != std::string::npos && end != std::string::npos, "FIX enum body found");
                if (begin == std::string::npos || end == std::string::npos) return;
                std::istringstream body(source.substr(begin+1, end-begin-1));
                std::set<unsigned> seen;
                std::string line;
                while (std::getline(body, line))
                {
                    std::smatch name, value;
                    if (!std::regex_search(line, name, member)) continue;
                    const std::string group = name[1].str();
                    const bool explicitBit = std::regex_search(line, value, definition);
                    t.IsTrue(explicitBit, group + " declares one explicit bit");
                    if (!explicitBit) continue;
                    const unsigned bit = static_cast<unsigned>(std::stoul(value[3].str()));
                    t.IsTrue(bit < width, group + " fits its mask");
                    t.IsTrue(bit < 32u || value[2].str() == "ull", group + " uses a wide shift when required");
                    t.IsTrue(seen.insert(bit).second, group + " does not alias another FIX group");
                }
                t.IsTrue(!seen.empty(), "all FIX declarations scanned");
            };
            check("enum Fix : uint64_t", 64u);
            check("enum : uint32_t", 32u);
        });
        tc.Run("INP2 input opts are independent of jcam2 life2 and ground2", [](TestCase &t)
        {
            const uint64_t existing = kFixJcam2 | kFixLife2 | kFixGround2;
            t.IsTrue(((kFixInput2 | kFixInputChain) & existing) == 0u, "new bits do not alias current-tip opts");
            t.IsTrue((kFixInput2 & kFixInputChain) == 0u, "counter and chaining bits differ");
            t.Equals(parseFix("jcam2,life2,ground2,input2,inputchain,-input2,-inputchain").main,
                     existing, "removing input opts preserves the other selections");
        });
        tc.Run("INP2 four-stock-frame guard and 24/12 repeats", [](TestCase &t)
        {
            t.IsTrue((allMain() & kFixInput2) != 0u, "input2 in all (PRM1, Brad 10-11)");
            t.Equals(parseFix("all,input2,-input2").main, allMain() & ~static_cast<uint64_t>(kFixInput2), "input2 parses and opts out");
            for (unsigned step : {1u, 2u})
            {
                Input2Record r{}; r.guard = 3u;
                input2Step(r, 1.f, step);
                t.Equals(r.press, 1u, "idle press immediate");
                t.Equals(r.repeat, 1u, "initial repeat pulse");
                for (unsigned half = step; half < 8u; half += step)
                {
                    input2Step(r, 0.f, step);
                    t.Equals(r.level, 1u, "digital hold lasts four stock frames");
                    t.Equals(r.press | r.release, 0u, "edge cleared each guest update");
                }
                input2Step(r, 0.f, step);
                t.Equals(r.release, 1u, "release exactly at four stock frames");
                r = {}; r.guard = 3u;
                for (unsigned half = 0u; half <= 100u; half += step)
                {
                    input2Step(r, 1.f, step);
                    t.Equals(r.repeat, (half == 0u || (half >= 48u && (half-48u)%24u == 0u)) ? 1u : 0u,
                             "repeat stock deadlines");
                }
            }
        });
        tc.Run("INP2 event flips and saved records preserve half updates", [](TestCase &t)
        {
            Input2Record r{}; r.guard = 3u;
            input2Step(r, 1.f, 2u); // stock press
            input2Step(r, 0.f, 1u); // first active half
            Input2Record restored = r; // persistent state is exactly the guest record
            for (unsigned n = 0u; n < 7u; ++n)
            {
                input2Step(r, 0.f, 1u); input2Step(restored, 0.f, 1u);
                t.Equals(r.guard, restored.guard, "reload exact remaining guard");
                t.Equals(r.level, n == 6u ? 0u : 1u, "flip keeps eight-half-frame interval");
            }
            r = {}; r.guard = 3u;
            input2Step(r, 1.f, 1u);
            input2Step(r, 0.f, 1u); // remaining seven halves
            for (unsigned n = 0; n < 3; ++n) input2Step(r, 0.f, 2u);
            t.Equals(r.level, 1u, "exit keeps odd half remaining");
            input2Step(r, 0.f, 2u);
            t.Equals(r.level, 0u, "exit rounds to next available stock update");
            Input2Record legacy{}; legacy.guard = 1u; legacy.level = 1u; legacy.countdown = 10u;
            input2Step(legacy, 1.f, 1u);
            t.Equals(legacy.countdown, 19u, "legacy remaining repeat converts to half units");
            t.Equals(legacy.guard, kInput2Tag | 5u, "legacy guard converts once");
        });
        tc.Run("INP3 phase parking survives a guest-memory checkpoint", [](TestCase &t)
        {
            t.IsTrue((allMain() & kFixInputChain) == 0u, "prototype excluded from all");
            t.Equals(parseFix("all,inputchain,-inputchain").main, allMain(), "separate chain opt-in");
            for (uint32_t phase = 0; phase <= 3; ++phase)
            {
                const uint32_t saved = inputChainPark(phase);
                t.IsTrue(saved > 3u, "no active branch while parked");
                t.Equals(inputChainRestore(saved), phase, "phase restored exactly");
                t.Equals(inputChainPark(saved), saved, "parking idempotent after resume");
                t.Equals(inputChainRestore(phase), phase, "ordinary phase unchanged");
            }
        });

        tc.Run("INP5 neutral admission excludes pending rotation and chaining", [](TestCase &t)
        {
            std::array<uint32_t, 10> angles{};
            t.IsTrue(inputChainNeutral(3u, angles), "neutral idle may run on either half");
            for (uint32_t phase : {0u, 1u, 2u, kInputChainHold | 3u})
                t.IsTrue(!inputChainNeutral(phase, angles), "active or parked phase stays held");
            angles.fill(0x80000000u);
            t.IsTrue(inputChainNeutral(3u, angles), "negative zero is neutral");
            for (unsigned i = 0; i < angles.size(); ++i)
            {
                angles[i] = 0x3f800000u;
                t.IsTrue(!inputChainNeutral(3u, angles), "each target/current/offset/sequence accumulator prevents admission");
                angles[i] = 0x80000000u;
            }
            // INP5 chain-6212: all normalized angle words are zero after
            // publication, but the guest sequence accumulator retains motion.
            angles.fill(0u);
            angles[8] = 0x40561151u; // S+30 at raw6323
            t.IsTrue(!inputChainNeutral(3u, angles), "completed spin is not initial idle");
            angles[8] = 0u; // original guest reset/new-target store
            t.IsTrue(inputChainNeutral(3u, angles), "guest reset restores initial idle");
        });

        tc.Run("FH33 ground2 is opt-in and squares to the bounded stock map", [](TestCase &t)
        {
            t.IsTrue((allMain() & kFixGround2) == 0u, "excluded from all");
            t.Equals(parseFix("all,ground2").main, allMain() | kFixGround2, "opt-in");
            t.Equals(parseFix("all,ground2,-ground2").main, allMain(), "opt-out");
            for (double multiplier : {1.0, 0.5, 2.0})
            {
                const double a = .2*multiplier, b = (.1/60)*multiplier, c = (14.0/60)*multiplier;
                double maxError = 0;
                for (unsigned i = 0; i <= 40000; ++i)
                {
                    const double g = i/10000.0;
                    const double h = ground2Residual(g,a,b,c);
                    const double stock = std::max(0.0,g-std::clamp(a*g,b,c));
                    maxError = std::max(maxError, std::abs(ground2Residual(h,a,b,c)-stock));
                    if (!(h >= 0 && h <= g)) { t.IsTrue(false, "half step contracts"); return; }
                }
                t.IsTrue(maxError < 1e-12, "two half steps equal stock across floor, proportional, cap and transitions");
            }
            t.IsTrue(std::abs(ground2Residual(.06,.2,.1/60,14.0/60)/.06-std::sqrt(.8)) < 1e-14,
                     "proportional retention sqrt(.8)");
        });
        tc.Run("GRD1 ground is opt-in and all minus ground is unchanged", [](TestCase &t)
        {
            t.IsTrue((allMain() & kFixGround) == 0u, "ground excluded from all");
            t.Equals(parseFix("all,-ground").main, allMain(), "old play layer remains equivalent");
            t.Equals(parseFix("all,ground").main, allMain() | kFixGround, "ground remains selectable");
            t.Equals(parseFix("all,ground,-ground").main, allMain(), "opt-in can be removed");
            t.IsTrue((allMain() & kFixGround2) == 0u, "ground2 remains separate");
        });

        tc.Run("JMP3 query is opt-in at bit 50", [](TestCase &t)
        {
            t.Equals(static_cast<uint64_t>(kFixQuery), 1ull << 50, "query uses bit 50");
            t.IsTrue((allMain() & kFixQuery) == 0u, "excluded from all");
            t.Equals(parseFix("all,query").main, allMain() | kFixQuery, "opt-in");
            t.Equals(parseFix("all,query,-query").main, allMain(), "opt-out");
            t.Equals(parseFix("query").main, static_cast<uint64_t>(kFixQuery), "query alone");
        });

        tc.Run("JMP4 query hold selects the owner slot", [](TestCase &t)
        {
            uint32_t ps[kQueryHoldSlots] = {};
            t.Equals(queryHoldSelect(ps, kQueryHoldSlots, 0x144a130u), 0, "first owner takes slot 0");
            ps[0] = 0x144a130u;
            t.Equals(queryHoldSelect(ps, kQueryHoldSlots, 0x144a130u), 0, "owner wins its slot");
            t.Equals(queryHoldSelect(ps, kQueryHoldSlots, 0x144b200u), 1, "second owner takes slot 1");
            for (uint32_t i = 1u; i < kQueryHoldSlots; ++i)
                ps[i] = 0x1450000u + i;
            t.Equals(queryHoldSelect(ps, kQueryHoldSlots, 0x144a130u), 0, "owner still wins when full");
            t.Equals(queryHoldSelect(ps, kQueryHoldSlots, 0x1460000u), -1, "full table misses");
            t.Equals(kQueryHoldStart + 4u * kQueryHoldWords, 0x14cu, "hold covers sp+0xC0..0x14C");
        });

        tc.Run("JDR2 shadowpub is opt-in at bit 53", [](TestCase &t)
        {
            t.Equals(static_cast<uint64_t>(kFixShadowPub), 1ull << 53, "shadowpub uses bit 53");
            t.IsTrue((allMain() & kFixShadowPub) == 0u, "excluded from all");
            t.Equals(parseFix("all,query,shadowpub").main, allMain() | kFixQuery | kFixShadowPub, "opt-in");
            t.Equals(parseFix("all,query,shadowpub,-shadowpub").main, allMain() | kFixQuery, "opt-out");
        });

        tc.Run("JDR2 shadowpub publishes only after two even samples with the live frame held", [](TestCase &t)
        {
            PubSlot<2> s;
            const uint32_t a[2] = {0x3f800000u, 0x40000000u}; // 1, 2
            const uint32_t b[2] = {0x40000000u, 0x40400000u}; // 2, 3
            pubSample(s, a, 10u);
            t.IsTrue(!pubReady(s, a, 11u), "one sample is not enough");
            pubSample(s, b, 12u);
            pubSample(s, b, 12u); // a second call in the same update only refreshes
            t.IsTrue(pubReady(s, b, 13u), "odd update after two consecutive even samples publishes");
            t.IsTrue(!pubReady(s, a, 13u), "a moved live frame never publishes");
            t.IsTrue(!pubReady(s, b, 15u), "a stale sample never publishes");
            t.Equals(pubHalfStep(s.cur[0], s.prev[0]), 2.5f, "held + half the last even step");
            pubSample(s, a, 16u);
            t.IsTrue(!pubReady(s, a, 17u), "samples 4 updates apart never publish");
            t.Equals(kShadowPubOffs[6], 0xaacu, "ground-plane words follow the frame");
        });

        tc.Run("JMP6 vnret is opt-in at bit 52", [](TestCase &t)
        {
            t.Equals(static_cast<uint64_t>(kFixVnRet), 1ull << 52, "vnret uses bit 52");
            t.IsTrue((allMain() & kFixVnRet) == 0u, "excluded from all");
            t.Equals(parseFix("all,vnret").main, allMain() | kFixVnRet, "opt-in");
            t.Equals(parseFix("all,vnret,-vnret").main, allMain(), "opt-out");
            t.Equals(parseFix("vnret").main, static_cast<uint64_t>(kFixVnRet), "vnret alone");
        });

        tc.Run("NRM1 normcap is opt-in at bit 56 and pairs to the bounded stock map", [](TestCase &t)
        {
            t.Equals(static_cast<uint64_t>(kFixNormcap), 1ull << 56, "normcap uses bit 56");
            t.IsTrue((allMain() & kFixNormcap) == 0u, "excluded from all");
            t.Equals(parseFix("all,query,vnret,normcap").main, allMain() | kFixQuery | kFixVnRet | kFixNormcap, "opt-in");
            t.Equals(parseFix("all,query,vnret,normcap,-normcap").main, allMain() | kFixQuery | kFixVnRet, "opt-out");
            t.Equals(parseFix("normcap").main, static_cast<uint64_t>(kFixNormcap), "normcap alone");
            const double kh = std::sqrt(0.6) - 1.0;
            auto pair = [&](const double w[3], const double n[3], double out[3], bool &binds) {
                double h1[3], used1 = 0.0, e1 = 0.0, mag1 = 0.0;
                if (!normcapFirst(w, n, kh, h1, used1, e1, mag1))
                    return false;
                return normcapSecond(h1, n, kh, used1, e1, mag1, out, binds);
            };
            // Captured bound rows: A+100 takeoff/lip, A+100 landing, B late recontact.
            const double rows[4][6] = {
                {158.023941, 1609.033936, 358.280823, -0.05790357, -0.14487608, 0.98775405},
                {-516.690308, 1908.668457, 290.626251, -0.45353130, 0.02129498, 0.89098603},
                {-544.894531, 1892.244263, 191.545029, -0.30095994, -0.06139826, 0.95165825},
                {835.014099, 177.890167, 1362.358521, -0.54401886, -0.28040093, 0.79083425},
            };
            for (const auto &row : rows)
            {
                const double *w = row, *n = row + 3;
                double h2[3], m[3];
                bool binds = false;
                t.IsTrue(pair(w, n, h2, binds), "pair computes");
                t.IsTrue(binds, "captured row binds");
                t.IsTrue(normcapStock(w, n, -0.4, m), "stock computes");
                for (int i = 0; i < 3; ++i)
                    t.IsTrue(std::abs(h2[i] - m[i]) <= 1e-3 * std::max(std::abs(m[i]), 1.0),
                             "bound pair matches stock within 1e-3");
            }
            // Unbound pair: the hook skips the write (guest value stands).
            {
                const double w[3] = {100.0, 2100.0, 300.0};
                const double n[3] = {-0.3, -0.1, 0.9486833};
                double h2[3];
                bool binds = true;
                t.IsTrue(pair(w, n, h2, binds), "unbound pair computes");
                t.IsTrue(!binds, "unbound pair reports no bind");
            }
            // Sweep from deep saturation through the bind boundary to upward.
            double worst = 0.0;
            for (int i = 0; i <= 4000; ++i)
            {
                const double w[3] = {100.0 + i * 0.5, -1500.0 - i * 0.25, 300.0};
                const double n[3] = {-0.3, -0.1, 0.9486833};
                double h2[3], m[3];
                bool binds = false;
                if (!pair(w, n, h2, binds) || !normcapStock(w, n, -0.4, m))
                {
                    t.IsTrue(false, "sweep computes");
                    return;
                }
                for (int c = 0; c < 3; ++c)
                    worst = std::max(worst, std::abs(h2[c] - m[c]) / std::max(std::abs(m[c]), 1.0));
            }
            t.IsTrue(worst < 1e-3, "pair matches stock within 1e-3 across the boundary");
        });

        tc.Run("SEL2 sel5b is opt-in at bit 58 and needs sel5", [](TestCase &t)
        {
            t.Equals(static_cast<uint64_t>(kFixSel5b), 1ull << 58, "sel5b uses bit 58");
            t.IsTrue((allMain() & kFixSel5b) == 0u, "excluded from all");
            t.Equals(parseFix("all,sel5,sel5b").main, allMain() | kFixSel5 | kFixSel5b, "opt-in");
            t.Equals(parseFix("all,sel5,sel5b,-sel5b").main, allMain() | kFixSel5, "opt-out");
            // The charge word flips only with sel5 alongside: sel5b alone is stripped.
            std::vector<uint8_t> ram(PS2_RAM_SIZE, 0u);
            const uint32_t a = 0x1000u;
            auto poke = [&](uint32_t addr, uint32_t v) {
                std::memcpy(ram.data() + (addr & 0x1FFFFFFFu), &v, 4);
            };
            auto peek = [&](uint32_t addr) {
                uint32_t v = 0u;
                std::memcpy(&v, ram.data() + (addr & 0x1FFFFFFFu), 4);
                return v;
            };
            auto stock = [&]() {
                poke(a + 0x10u, 60u);
                poke(a + 0x14u, kSixtieth);
                poke(a + 0x24u, 0x3f800000u);
                poke(0x49beecu, kSixtieth);
                poke(0x49bb90u, kSixtieth);
            };
            stock();
            FixMasks solo;
            solo.main = static_cast<uint64_t>(kFixSel5b);
            applyWords(ram.data(), a, true, solo);
            t.Equals(peek(0x49bb90u), kSixtieth, "sel5b alone leaves the charge word stock");
            stock();
            FixMasks both;
            both.main = static_cast<uint64_t>(kFixSel5) | static_cast<uint64_t>(kFixSel5b);
            applyWords(ram.data(), a, true, both);
            t.Equals(peek(0x49bb90u), kHundredTwentieth, "sel5+sel5b flips the charge word");
            t.Equals(peek(0x49beecu), kHundredTwentieth, "sel5 flips with it");
            applyWords(ram.data(), a, false, both);
            t.Equals(peek(0x49bb90u), kSixtieth, "exit restores stock");
        });

        tc.Run("BST1 boostfill is opt-in at bit 59 and halves the consume step", [](TestCase &t)
        {
            t.Equals(static_cast<uint64_t>(kFixBoostFill), 1ull << 59, "boostfill uses bit 59");
            t.IsTrue((allMain() & kFixBoostFill) == 0u, "excluded from all");
            t.Equals(parseFix("all,boostfill").main, allMain() | kFixBoostFill, "opt-in");
            t.Equals(parseFix("all,boostfill,-boostfill").main, allMain(), "opt-out");
            std::vector<uint8_t> ram(PS2_RAM_SIZE, 0u);
            const uint32_t a = 0x1000u;
            auto poke = [&](uint32_t addr, uint32_t v) {
                std::memcpy(ram.data() + (addr & 0x1FFFFFFFu), &v, 4);
            };
            auto peek = [&](uint32_t addr) {
                uint32_t v = 0u;
                std::memcpy(&v, ram.data() + (addr & 0x1FFFFFFFu), 4);
                return v;
            };
            auto stock = [&]() {
                poke(a + 0x10u, 60u);
                poke(a + 0x14u, kSixtieth);
                poke(a + 0x24u, 0x3f800000u);
                poke(0x49b4fcu, 0x3ac57fccu);
            };
            stock();
            FixMasks off;
            off.main = 0u;
            applyWords(ram.data(), a, true, off);
            t.Equals(peek(0x49b4fcu), 0x3ac57fccu, "bit off leaves the consume word stock");
            stock();
            FixMasks on;
            on.main = static_cast<uint64_t>(kFixBoostFill);
            applyWords(ram.data(), a, true, on);
            t.Equals(peek(0x49b4fcu), 0x3a457fccu, "bit on halves the consume word");
            applyWords(ram.data(), a, false, on);
            t.Equals(peek(0x49b4fcu), 0x3ac57fccu, "exit restores stock");
        });

        tc.Run("FLP2 stuntchg is opt-in at bit 60", [](TestCase &t)
        {
            t.Equals(static_cast<uint64_t>(kFixStuntChg), 1ull << 60, "stuntchg uses bit 60");
            t.IsTrue((allMain() & kFixStuntChg) == 0u, "excluded from all");
            t.Equals(parseFix("all,stuntchg").main, allMain() | kFixStuntChg, "opt-in");
            t.Equals(parseFix("all,stuntchg,-stuntchg").main, allMain(), "opt-out");
        });

        tc.Run("REV1: asymk requires vnret so 0x49c124 always has an owner", [](TestCase &t)
        {
            auto m = [](const char *fix) { return parseFix(fix).main; };
            t.IsTrue(asymkMaskOk(m("query,vnret,asymk")), "query+vnret+asymk on");
            t.IsTrue(asymkMaskOk(m("query,vnret,groundstep,asymk")), "play-shaped set on");
            t.IsTrue(!asymkMaskOk(m("query,groundstep,asymk")),
                     "groundstep no longer substitutes for vnret");
            t.IsTrue(!asymkMaskOk(m("query,asymk")), "no vnret: off");
            t.IsTrue(!asymkMaskOk(m("vnret,asymk")), "no query: off");
            t.IsTrue(!asymkMaskOk(m("query,vnret")), "no asymk bit: off");
            t.IsTrue(!asymkMaskOk(m("all")), "all does not imply asymk");
            // normcap effectiveness across the same boundary (unchanged sets).
            t.IsTrue(normcapMaskOk(m("query,vnret,normcap")), "normcap set effective");
            t.IsTrue(!normcapMaskOk(m("query,vnret,normcap,asymk")),
                     "asymk suppresses normcap");
            t.IsTrue(!normcapMaskOk(m("query,normcap")), "normcap without vnret: off");
            t.IsTrue(normcapMaskSuppressed(m("query,vnret,normcap,asymk")),
                     "play-shaped set: normcap requested but suppressed");
            t.IsTrue(!normcapMaskSuppressed(m("query,vnret,normcap")),
                     "no asymk: not suppressed");
            t.IsTrue(!normcapMaskSuppressed(m("query,vnret,asymk")),
                     "no normcap: not suppressed");
            // With vnret owning the word, the exit flip restores stock even
            // when asymk's last per-update write left k=0.
            std::vector<uint8_t> ram(PS2_RAM_SIZE, 0u);
            const uint32_t a = 0x1000u;
            auto poke = [&](uint32_t addr, uint32_t v) {
                std::memcpy(ram.data() + (addr & 0x1FFFFFFFu), &v, 4);
            };
            auto peek = [&](uint32_t addr) {
                uint32_t v = 0u;
                std::memcpy(&v, ram.data() + (addr & 0x1FFFFFFFu), 4);
                return v;
            };
            poke(a + 0x10u, 60u);
            poke(a + 0x14u, kSixtieth);
            poke(a + 0x24u, 0x3f800000u);
            poke(0x49c124u, 0xbecccccdu); // vnret word (stock)
            poke(0x49c0a8u, 0x3f666666u); // vnret lean-brake word (stock)
            FixMasks masks;
            masks.main = m("query,vnret,groundstep,asymk");
            applyWords(ram.data(), a, true, masks);
            t.Equals(peek(0x49c124u), 0xbe66d021u, "enter writes the vnret value");
            poke(0x49c124u, 0u); // asymk skip-half k=0 at the exit flip
            applyWords(ram.data(), a, false, masks);
            t.Equals(peek(0x49c124u), 0xbecccccdu, "exit restores stock k");
        });

        // REV1 (RV32 F6): the three name tables (fixItem, fh12Item and the
        // bit-name renders) must stay in lockstep, or a FIX bit parses to a
        // different bit than deltas/logs report.
        tc.Run("REV1: FIX parse names and bit names stay in sync", [](TestCase &t)
        {
            std::set<std::string> seen;
            for (unsigned bit = 0; bit < 64u; ++bit)
            {
                const char *name = fixMainBitName(bit);
                if (bit == 14u || bit == 51u)
                {
                    t.IsTrue(name == nullptr, "internal ramp and reserved bit 51 stay unnamed");
                    continue;
                }
                if (bit >= 62u)
                {
                    t.IsTrue(name == nullptr, "unallocated main bits stay unnamed");
                    continue;
                }
                t.IsTrue(name != nullptr, "every allocated main bit has a name");
                if (!name)
                    continue;
                t.Equals(fixItem(name), 1ull << bit, "bit name parses back to its bit");
                t.IsTrue(seen.insert(name).second, "main bit names are unique");
            }
            t.Equals(seen.size(), size_t{60u}, "60 named main bits (62 slots, ramp internal, bit 51 reserved)");
            seen.clear();
            for (unsigned bit = 0; bit < 16u; ++bit)
            {
                const char *name = fix12BitName(bit);
                t.IsTrue(name != nullptr, "every fh12 bit has a name");
                if (!name)
                    continue;
                t.Equals(fh12Item(name), 1u << bit, "fh12 name parses back to its bit");
                t.IsTrue(seen.insert(name).second, "fh12 bit names are unique");
            }
            t.IsTrue(fix12BitName(16u) == nullptr, "fh12 names stop at bit 15");
            // Reverse direction, from the parser bodies in the source (full120
            // slice 1: games/ssx3/module/src/fh1_fix_content.cpp): every name
            // fixItem/fh12Item accepts must render under some bit.
            std::ifstream file("games/ssx3/module/src/fh1_fix_content.cpp");
            t.IsTrue(file.is_open(), "run the suite from the fork worktree root");
            if (!file.is_open())
                return;
            std::ostringstream buffer;
            buffer << file.rdbuf();
            const std::string source = buffer.str();
            const std::regex itemRe(R"re((?:item == "|streq\(item, ")([a-z0-9]+)")re");
            size_t parsed = 0;
            for (auto it = std::sregex_iterator(source.begin(), source.end(), itemRe);
                 it != std::sregex_iterator(); ++it)
            {
                const std::string name = (*it)[1].str();
                ++parsed;
                bool renders = false;
                for (unsigned bit = 0; bit < 62u && !renders; ++bit)
                {
                    const char *n = fixMainBitName(bit);
                    renders = n && name == n;
                }
                for (unsigned bit = 0; bit < 16u && !renders; ++bit)
                {
                    const char *n = fix12BitName(bit);
                    renders = n && name == n;
                }
                t.IsTrue(renders, "every parse name renders under a bit: " + name);
            }
            t.Equals(parsed, size_t{60u + 16u}, "parser accepts exactly the named bits");
        });

        tc.Run("KNC6 bit 51 stays reserved; retired springhold name refuses", [](TestCase &t)
        {
            t.IsTrue((allMain() & (1ull << 51)) == 0u, "reserved bit excluded from all");
            t.IsTrue(fixMainBitName(51u) == nullptr, "reserved bit has no name");
            t.IsTrue(!parseFix("all,springhold").ok, "retired name refuses");
        });

        tc.Run("all includes stick2 and clocksign (FH26, Brad 10-02) and both masks", [](TestCase &t)
        {
            const FixMasks f = parseFix("all");
            t.IsTrue(f.ok, "parses");
            t.Equals(f.main, allMain(), "main = allMain()");
            t.Equals(f.fh12, all12(), "fh12 = all12()");
            t.IsTrue((f.main & kFixStick2) != 0u, "stick2 in all");
            t.IsTrue((f.main & kFixClockSign) != 0u, "clocksign in all");
            t.IsTrue((f.main & kFixLift) == 0u, "lift stays opt-in");
            t.IsTrue((f.main & kFixRamp) == 0u, "ramp is internal");
        });

        tc.Run("minus items opt out, left to right", [](TestCase &t)
        {
            const FixMasks a = parseFix("all,-stick2");
            t.IsTrue(a.ok, "parses");
            t.Equals(a.main, allMain() & ~static_cast<uint64_t>(kFixStick2), "all minus stick2");
            t.Equals(a.fh12, all12(), "fh12 untouched");
            const FixMasks b = parseFix("all,-stick2,-clocksign");
            t.Equals(b.main, allMain() & ~static_cast<uint64_t>(kFixStick2 | kFixClockSign), "pre-FH26 all");
            const FixMasks c = parseFix("all,-particles");
            t.Equals(c.fh12, all12() & ~kFix12Particles, "fh12 opt-out");
            t.Equals(c.main, allMain(), "main untouched");
            const FixMasks d = parseFix("-stick2,all");
            t.Equals(d.main, allMain(), "order matters: a later all re-adds");
            const FixMasks e = parseFix("all,-all,stick");
            t.Equals(e.main, static_cast<uint64_t>(kFixStick), "-all clears");
            t.Equals(e.fh12, 0u, "-all clears fh12");
        });

        tc.Run("single items, lift opt-in, empty items", [](TestCase &t)
        {
            t.Equals(parseFix("stick2").main, static_cast<uint64_t>(kFixStick2), "stick2 alone");
            t.Equals(parseFix("all,lift").main, allMain() | kFixLift, "all plus lift");
            t.Equals(parseFix("spin,clock").fh12, static_cast<uint32_t>(kFix12Spin), "fh12 item");
            t.Equals(parseFix("spin,clock").main, static_cast<uint64_t>(kFixClock), "main item");
            t.IsTrue(parseFix("all,").ok, "trailing comma");
        });

        tc.Run("FH27 bonusflip and loops2 are in all (DEF1, Brad 10-03)", [](TestCase &t)
        {
            t.IsTrue((allMain() & kFixBonusFlip) != 0u, "bonusflip in all");
            t.IsTrue((all12() & kFix12Loops2) != 0u, "loops2 in all");
            const FixMasks f = parseFix("all,-bonusflip,-loops2");
            t.IsTrue(f.ok, "parses");
            t.Equals(f.main, allMain() & ~static_cast<uint64_t>(kFixBonusFlip), "main drops bonusflip");
            t.Equals(f.fh12, all12() & ~kFix12Loops2, "fh12 drops loops2");
        });

        tc.Run("FLK2 flare is opt-in", [](TestCase &t)
        {
            t.IsTrue((all12() & kFix12Flare) == 0u, "flare not in all");
            const FixMasks f = parseFix("all,flare");
            t.IsTrue(f.ok, "parses");
            t.Equals(f.main, allMain(), "main untouched");
            t.Equals(f.fh12, all12() | kFix12Flare, "fh12 adds flare");
            t.Equals(parseFix("all,flare,-flare").fh12, all12(), "opt-out");
        });

        tc.Run("FH28 jcam/pid stay opt-in; pose/c2cap/spawn in all (PRM1, Brad 10-11)", [](TestCase &t)
        {
            const uint64_t out = static_cast<uint64_t>(kFixJcam | kFixPid);
            const uint64_t in = static_cast<uint64_t>(kFixPose | kFixC2Cap | kFixSpawn);
            t.IsTrue((allMain() & out) == 0u, "jcam/pid stay opt-in");
            t.IsTrue((allMain() & in) == in, "pose/c2cap/spawn in all");
            const FixMasks f = parseFix("all,-ground,jcam,pose,pid,c2cap,spawn");
            t.IsTrue(f.ok, "parses");
            t.Equals(f.main, allMain() | out, "all minus ground plus jcam/pid");
            t.Equals(f.fh12, all12(), "fh12 untouched");
            t.Equals(parseFix("jcam").main, static_cast<uint64_t>(kFixJcam), "jcam alone");
            t.Equals(parseFix("pose").main, static_cast<uint64_t>(kFixPose), "pose alone");
            t.Equals(parseFix("pid").main, static_cast<uint64_t>(kFixPid), "pid alone");
            t.Equals(parseFix("c2cap").main, static_cast<uint64_t>(kFixC2Cap), "c2cap alone");
            t.Equals(parseFix("spawn").main, static_cast<uint64_t>(kFixSpawn), "spawn alone");
        });

        tc.Run("FH35 trails corrects only direct half-dt callers", [](TestCase &t)
        {
            static_assert(sizeof(FixMasks{}.main) == sizeof(uint64_t), "main FIX mask must retain bits above 47");
            t.Equals(static_cast<uint64_t>(kFixTrails), 1ull << 48, "trails uses bit 48");
            t.Equals(static_cast<uint64_t>(kFixChase2), 1ull << 49, "chase2 uses bit 49");
            t.IsTrue(((kFixTrails|kFixChase2) & ((1ull << 46)|(1ull << 47)))==0u,"INP2 bits reserved");
            t.IsTrue((allMain() & kFixTrails)==0u,"trails stays opt-in");
            t.IsTrue((allMain() & kFixChase2)!=0u,"chase2 in all (PRM1, Brad 10-11)");
            t.Equals(parseFix("all,trails,chase2").main,allMain()|kFixTrails,"adds trails (chase2 already in)");
            t.Equals(parseFix("all,trails,chase2,-trails,-chase2").main,allMain() & ~static_cast<uint64_t>(kFixChase2),"removes both");
            const float half=fh35Float(0x3c088889u), stock=fh35Float(0x3c888889u);
            for (uint32_t pc : {0x345e4cu,0x345fdcu,0x34600cu,0x346028u})
            {
                t.Equals(fh35Bits(directTrailDt(pc,half)),fh35Bits(stock),"direct stock dt");
                t.Equals(fh35Bits(directTrailDt(pc,stock)),fh35Bits(stock),"already stock unchanged");
                t.Equals(fh35Bits(directTrailDt(pc,0.02f)),fh35Bits(0.02f),"custom dt unchanged");
            }
            for (uint32_t pc : {0x345b7cu,0x345b8cu,0x3718d0u,0x2e1520u,0u})
                t.Equals(fh35Bits(directTrailDt(pc,half)),fh35Bits(half),"control unchanged");
        });
        tc.Run("FH35 chase2 recovers stock retention with exact endpoints", [](TestCase &t)
        {
            for (bool b : {false,true})
            {
                const float k0=fh35Float(b?0x3f055b3fu:0x3f6c0535u), k1=fh35Float(b?0x3f72dce8u:0x3f7c217au);
                const float r0=fh35Float(b?0x3e8aefe0u:0x3f59999au), r1=fh35Float(b?0x3f666666u:0x3f7851ecu);
                float out=-1;
                t.IsTrue(!chase2Retention(k0,k1,0,out) && out==-1,"low endpoint untouched");
                t.IsTrue(!chase2Retention(k0,k1,1,out) && out==-1,"high endpoint untouched");
                for (unsigned i=1;i<1000;++i)
                {
                    const float x=i/1000.0f;
                    t.IsTrue(chase2Retention(k0,k1,x,out),"interior selected");
                    const double expected=double(r0)+(double(r1)-r0)*x;
                    t.IsTrue(std::fabs(double(out)*out-expected)<2e-7,"pair recovers stock lerp");
                }
            }
            float out=42;
            t.IsTrue(!chase2Retention(.8f,.9f,.5f,out) && out==42,"custom endpoints untouched");
            t.Equals(chase2T(.375f,.125f,.625f),.5f,"same midpoint geometry");
            t.Equals(chase2T(-.375f,.125f,.625f),.5f,"absolute direction");
            t.Equals(chase2T(.05f,.1f,.6f),0.0f,"low clamp");
            t.Equals(chase2T(.7f,.1f,.6f),1.0f,"high clamp");
        });

        tc.Run("unknown items refuse", [](TestCase &t)
        {
            const FixMasks f = parseFix("all,stik2");
            t.IsTrue(!f.ok, "refused");
            t.Equals(f.bad, std::string("stik2"), "names the item");
            t.IsTrue(!parseFix("-nope").ok, "unknown opt-out refused");
        });

        tc.Run("SVL1 reverse FIX-name map matches the parser", [](TestCase &t)
        {
            // Every reverse entry must parse back to its bit (both masks).
            for (unsigned b = 0u; b < 64u; ++b)
            {
                const char *name = fixMainBitName(b);
                if (name == nullptr)
                    continue;
                t.Equals(fixItem(name), 1ull << b, std::string("main bit ") + std::to_string(b));
            }
            for (unsigned b = 0u; b < 32u; ++b)
            {
                const char *name = fix12BitName(b);
                if (name == nullptr)
                    continue;
                t.Equals(fh12Item(name), 1u << b, std::string("mask12 bit ") + std::to_string(b));
            }
            // Every parsed name must have a reverse entry (read the parser,
            // INP2 style, so future additions participate).
            std::ifstream file("ps2xRuntime/include/ps2_fh1_fix.h");
            t.IsTrue(file.is_open(), "run the suite from the fork worktree root");
            if (!file.is_open())
                return;
            std::ostringstream buffer;
            buffer << file.rdbuf();
            const std::string source = buffer.str();
            const std::regex item(R"(\(item == \"([a-z0-9]+)\"\) return)");
            for (std::sregex_iterator it(source.begin(), source.end(), item), end; it != end; ++it)
            {
                const std::string name = (*it)[1].str();
                const uint64_t main = fixItem(name);
                const uint32_t m12 = fh12Item(name);
                t.IsTrue((main != 0u) != (m12 != 0u), name + " lives in exactly one mask");
                bool found = false;
                if (main != 0u)
                    for (unsigned b = 0u; b < 64u; ++b)
                    {
                        const char *rev = fixMainBitName(b);
                        if (rev != nullptr && name == rev && main == (1ull << b))
                            found = true;
                    }
                else
                    for (unsigned b = 0u; b < 32u; ++b)
                    {
                        const char *rev = fix12BitName(b);
                        if (rev != nullptr && name == rev && m12 == (1u << b))
                            found = true;
                    }
                t.IsTrue(found, name + " has a reverse entry at its bit");
            }
        });

        tc.Run("SVL1 renderFixDelta is deterministic", [](TestCase &t)
        {
            t.Equals(renderFixDelta(0u, 0u, 0u, 0u), std::string(""), "empty when equal");
            t.Equals(renderFixDelta(0u, 0u, kFixQuery, 0u), std::string("+query"), "gain");
            t.Equals(renderFixDelta(kFixQuery, 0u, 0u, 0u), std::string("-query"), "loss");
            t.Equals(renderFixDelta(kFixBonus, 0u, kFixQuery, kFix12Flare), std::string("-bonus,+query,+flare"),
                     "main ascending, then mask12");
            t.Equals(renderFixDelta(0u, 0u, 1ull << 62, 1u << 16), std::string("+bit62,+bit12.16"),
                     "unnamed bits render by index"); // FLP2: bit 60 is now stuntchg; TRN2: bit 61 is now asymk
            t.Equals(renderFixDelta(1ull << 14, 0u, 0u, 0u), std::string("-bit14"),
                     "internal ramp renders unnamed");
        });

        tc.Run("SVL1 classifyFixDelta same/allow/refuse", [](TestCase &t)
        {
            const uint64_t all = allMain();
            const uint32_t a12 = all12();
            FixDeltaVerdict v = classifyFixDelta(all, a12, all, a12);
            t.IsTrue(v.allow && v.same && v.detail.empty(), "same mask allows empty");
            v = classifyFixDelta(all, a12, all | kFixQuery, a12);
            t.IsTrue(v.allow && !v.same, "+query allows");
            t.Equals(v.detail, std::string("+query"), "+query render");
            v = classifyFixDelta(all, a12, all, a12 | kFix12Flare);
            t.IsTrue(v.allow, "+flare allows");
            v = classifyFixDelta(all, a12, all & ~kFixBonusFlip, a12);
            t.IsTrue(!v.allow, "-bonusflip refuses");
            t.IsTrue(v.detail.find("bonusflip") != std::string::npos, "names bonusflip");
            v = classifyFixDelta(all & ~kFixBonusFlip, a12, all, a12);
            t.IsTrue(!v.allow, "+bonusflip refuses too (both directions)");
            v = classifyFixDelta(all, a12, all & ~kFixInput2, a12);
            t.IsTrue(!v.allow, "input2 ON->OFF refuses");
            t.IsTrue(v.detail.find("input2") != std::string::npos, "names input2");
            v = classifyFixDelta(all, a12, all | kFixInputChain, a12);
            t.IsTrue(v.allow, "inputchain OFF->ON allows (self-converting)");
            t.Equals(v.detail, std::string("+inputchain"), "inputchain render");
            v = classifyFixDelta(kFixLife2, 0u, 0u, 0u);
            t.IsTrue(!v.allow, "life2 ON->OFF refuses");
            t.IsTrue(v.detail.find("life2") != std::string::npos, "names life2");
            v = classifyFixDelta(0u, 0u, kFixLife2, 0u);
            t.IsTrue(v.allow, "life2 OFF->ON allows through to the section gate");
            v = classifyFixDelta(all, a12, all | (1ull << 63), a12);
            t.IsTrue(!v.allow, "unknown main bit refuses");
            t.IsTrue(v.detail.find("0x8000000000000000") != std::string::npos, "names the hex");
            v = classifyFixDelta(all, a12, all, a12 | (1u << 31));
            t.IsTrue(!v.allow, "unknown mask12 bit refuses");
            v = classifyFixDelta(1ull << 14, 0u, 0u, 0u);
            t.IsTrue(!v.allow, "internal ramp bit refuses as unknown");
        });

        tc.Run("SVL1 pair-boundary predicate truth table", [](TestCase &t)
        {
            // Boundary: savedOdd != runGroundStep, only when run has normcap.
            t.IsTrue(fixDeltaPairBoundaryOk(true, false, true), "plain query: odd save ok");
            t.IsTrue(!fixDeltaPairBoundaryOk(false, false, true), "plain query: even save refuses");
            t.IsTrue(fixDeltaPairBoundaryOk(false, true, true), "groundstep: even save ok");
            t.IsTrue(!fixDeltaPairBoundaryOk(true, true, true), "groundstep: odd save refuses");
            t.IsTrue(fixDeltaPairBoundaryOk(false, false, false), "normcap off: no condition");
            t.IsTrue(fixDeltaPairBoundaryOk(true, true, false), "normcap off: no condition either");
        });

        tc.Run("SVL1 parseFixPair accepts the header shape only", [](TestCase &t)
        {
            uint64_t main = 0u;
            uint32_t f12 = 0u;
            t.IsTrue(parseFixPair("123:456", main, f12), "valid");
            t.IsTrue(main == 123u && f12 == 456u, "values");
            t.IsTrue(parseFixPair("18446744073709551615:4294967295", main, f12), "maxima");
            t.IsTrue(main == 0xffffffffffffffffull && f12 == 0xffffffffu, "maxima values");
            t.IsTrue(!parseFixPair("", main, f12), "empty refuses");
            t.IsTrue(!parseFixPair("123", main, f12), "no colon refuses");
            t.IsTrue(!parseFixPair("1:2:3", main, f12), "two colons refuse");
            t.IsTrue(!parseFixPair(":456", main, f12), "empty main refuses");
            t.IsTrue(!parseFixPair("123:", main, f12), "empty mask12 refuses");
            t.IsTrue(!parseFixPair("12a:456", main, f12), "junk refuses");
            t.IsTrue(!parseFixPair("18446744073709551616:0", main, f12), "main overflow refuses");
            t.IsTrue(!parseFixPair("0:4294967296", main, f12), "mask12 overflow refuses");
            t.IsTrue(!parseFixPair("<missing>", main, f12), "missing marker refuses");
        });

        tc.Run("SVL1 sanctioned-load log lines are greppable", [](TestCase &t)
        {
            t.Equals(fixDeltaHeaderLog("2", "2", "111:222", "333:444", "+query"),
                     std::string("[savestate] ALLOW_FIX_DELTA header: mode 2->2 fix 111:222->333:444 "
                                 "delta=+query (section checks pending)"),
                     "header line");
            t.Equals(fixDeltaSectionLog("+query", true),
                     std::string("[savestate] ALLOW_FIX_DELTA fh1: delta=+query saveparity=odd applied "
                                 "bookkeeping, cleared pair caches"),
                     "section line odd");
            t.Equals(fixDeltaSectionLog("-input2", false),
                     std::string("[savestate] ALLOW_FIX_DELTA fh1: delta=-input2 saveparity=even applied "
                                 "bookkeeping, cleared pair caches"),
                     "section line even");
        });
    });
}
