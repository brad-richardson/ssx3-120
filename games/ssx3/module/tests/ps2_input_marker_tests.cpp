// SPDX-License-Identifier: GPL-3.0-or-later OR MIT
// split-input-1 #5: SSX 3 input marker (LIM1/LIM2) behavior: the three lim1
// Runs that used to live in ps2xTest/src/pad_input_tests.cpp, driven through
// a file-local pad bench (Lim1PadBench below); the marker test hooks come
// from Ssx3InputMarker.h. Product suite only (the generic build has no module).
#include "MiniTest.h"
#include "Ssx3InputMarker.h"
#include "ps2_runtime.h"
#include "ps2_stubs.h"
#include "runtime/ps2_memory.h"
#include "Stubs/Pad.h"

// Standard library: fixed-width ints, memcpy, file and string handling.
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <string>
#include <vector>

namespace ps2_ssx3_lim1_bench
{
// DualShock 2 digital button word, one bit per button. The pad reports a
// button as held by CLEARING its bit, so "nothing held" reads 0xFFFF.
struct Ds2
{
    static constexpr uint16_t select = 0x0001;
    static constexpr uint16_t l3 = 0x0002;
    static constexpr uint16_t r3 = 0x0004;
    static constexpr uint16_t start = 0x0008;
    static constexpr uint16_t dpadUp = 0x0010;
    static constexpr uint16_t dpadRight = 0x0020;
    static constexpr uint16_t dpadDown = 0x0040;
    static constexpr uint16_t dpadLeft = 0x0080;
    static constexpr uint16_t l2 = 0x0100;
    static constexpr uint16_t r2 = 0x0200;
    static constexpr uint16_t l1 = 0x0400;
    static constexpr uint16_t r1 = 0x0800;
    static constexpr uint16_t triangle = 0x1000;
    static constexpr uint16_t circle = 0x2000;
    static constexpr uint16_t cross = 0x4000;
    static constexpr uint16_t square = 0x8000;
};

// One pad on port 0 / slot 0 with its own guest RAM and EE context. Calls
// go straight into the pad stubs with the MIPS argument registers loaded by
// hand (a0 = port, a1 = slot, a2 = guest buffer).
struct Lim1PadBench
{
    static constexpr uint32_t kReadDst = 0x1000u;          // scePadRead output
    static constexpr uint32_t kPortDma = kReadDst + 0x200u; // scePadPortOpen buffer
    static constexpr uint8_t kStickRest = 0x80u;            // centred analog axis

    std::vector<uint8_t> ram = std::vector<uint8_t>(PS2_RAM_SIZE, 0u);
    R5900Context cpu;

    // GPR low 64 bits <- zero-extended word, high 64 bits cleared.
    void loadArg(int gpr, uint32_t word)
    {
        cpu.r[gpr] = _mm_cvtsi64_si128(static_cast<int64_t>(word));
    }

    // scePadInit, then open port 0 / slot 0.
    void powerOn()
    {
        ps2_stubs::scePadInit(ram.data(), &cpu, nullptr);
        loadArg(4, 0u);
        loadArg(5, 0u);
        loadArg(6, kPortDma);
        ps2_stubs::scePadPortOpen(ram.data(), &cpu, nullptr);
    }

    // Close port 0 / slot 0 (a2 is not an argument here and is left alone).
    void powerOff()
    {
        loadArg(4, 0u);
        loadArg(5, 0u);
        ps2_stubs::scePadPortClose(ram.data(), &cpu, nullptr);
    }

    // Script the next reads: this button word, both sticks at rest.
    static void press(uint16_t word)
    {
        ps2_stubs::setPadOverrideState(word, kStickRest, kStickRest, kStickRest, kStickRest);
    }

    static void unscript() { ps2_stubs::clearPadOverrideState(); }

    // One scePadRead of port 0 / slot 0 into kReadDst.
    void poll()
    {
        loadArg(4, 0u);
        loadArg(5, 0u);
        loadArg(6, kReadDst);
        ps2_stubs::scePadRead(ram.data(), &cpu, nullptr);
    }

    // The button word the guest sees: bytes 2 (low) and 3 (high) of the read.
    uint16_t guestButtons() const
    {
        const uint32_t lo = ram[kReadDst + 2u];
        const uint32_t hi = ram[kReadDst + 3u];
        return static_cast<uint16_t>(lo | (hi << 8u));
    }
};
} // namespace ps2_ssx3_lim1_bench

using ps2_ssx3_lim1_bench::Ds2;
using ps2_ssx3_lim1_bench::Lim1PadBench;

void register_ps2_input_marker_tests()
{
    MiniTest::Case("Ssx3InputMarker", [](TestCase &tc)
                   {
        tc.Run("lim1: the L3+R3 chord writes a TSV window and stamps the padrec", [](TestCase &t)
               {
            namespace fs = std::filesystem;
            const fs::path dir = fs::temp_directory_path() / "lim1_marker_chord_test";
            fs::remove_all(dir);
            const std::string recPath = (fs::temp_directory_path() / "lim1_padrec_test.txt").string();
            std::remove(recPath.c_str());
            Lim1PadBench pad;
            std::vector<uint8_t> &rdram = pad.ram; // the chord test seeds guest RAM directly
            ps2_stubs::clearPadScriptForTest();
            ps2_stubs::clearPadRecordForTest();
            ps2_stubs::clearPadMarkerForTest();
            pad.powerOn();
            t.IsTrue(ps2_stubs::setPadMarkerDirForTest(dir.string().c_str()), "marker should arm");
            t.IsTrue(ps2_stubs::setPadRecordForTest(recPath.c_str()), "recorder should arm");
            ps2_stubs::setPadMarkerInput2ForTest(0x00200000u, 2u);
            ps2_stubs::setPadMarkerRdramForTest(rdram.data());
            for (uint32_t i = 0u; i < 2u; ++i)
            {
                // Input2Record words: raw, press, release, level, repeat, countdown, guard.
                // (Bank at 2 MB: the pad buffer lives at 0x100000 and would clobber it.)
                const uint32_t words[7] = {0x3F800000u, i, 0u, 1u, 0u, 48u, 0x40000000u};
                std::memcpy(&rdram[0x00200000u + 4u + i * 28u], words, sizeof(words));
            }
            ps2_stubs::setPadMarkerTrackerForTest(0x00c00100u, 0xdeadbeefu, 2u, 1u, 0x40c90fdcu);
            ps2_stubs::setPadMarkerGateForTest(0x0badf00du, 0x42u);
            // LIM2 v2: fake rider RAM. TK43d chain root 0x4a28a8 (4.8 MB, in
            // RDRAM); R = 0x00300300.
            const auto w32 = [&](uint32_t addr, uint32_t v) {
                std::memcpy(&rdram[addr], &v, 4u);
            };
            w32(0x4a28a8u, 0x00300000u);
            w32(0x00300084u, 0x00300100u);
            w32(0x0030010cu, 0x00300200u);
            w32(0x00300228u, 0x00300300u);
            w32(0x00300300u + 0x110u, 0x41200000u); // 10.0
            w32(0x00300300u + 0x114u, 0x41a00000u); // 20.0
            w32(0x00300300u + 0x118u, 0x41f00000u); // 30.0
            w32(0x00300300u + 0x1e0u, 0x40400000u); // 3.0
            w32(0x00300300u + 0x1e4u, 0x40800000u); // 4.0
            w32(0x00300300u + 0x1e8u, 0x00000000u); // 0.0 -> |v| = 5
            w32(0x00300300u + 0x370u, 0x00000000u); // qn = (0,1,0)
            w32(0x00300300u + 0x374u, 0x3f800000u);
            w32(0x00300300u + 0x378u, 0x00000000u);
            w32(0x00300300u + 0x454u, 0xc0000000u); // -2.0 depth
            w32(0x00300300u + 0x438u, 0x0000000au);
            w32(0x00300300u + 0x430u, 0x00000099u);
            w32(0x00300300u + 0x460u, 0x40e00000u); // 7.0
            w32(0x00300300u + 0x464u, 0x41000000u); // 8.0
            w32(0x00300300u + 0x468u, 0x41100000u); // 9.0
            w32(0x00300300u + 0x2f4u, 0x00000006u);
            w32(0x00300300u + 0x2f8u, 0x3f000000u); // 0.5 boost
            w32(0x00300300u + 0x2f0u, 0x41a00000u); // 20.0 timer
            w32(0x00c00104u, 0x00000003u);          // S+4 stage 3
            w32(0x00300400u + 0xde0u, 0x00000004u); // rail selector
            w32(0x00300400u + 0xde4u, 0x00000007u);
            ps2_stubs::setPadMarkerSelForTest(0x00300400u, 4u, 7u);

            const uint16_t relaxed = 0xFFFFu;
            const uint16_t chord =
                static_cast<uint16_t>(0xFFFFu & ~Ds2::l3 & ~Ds2::r3);
            const uint64_t trigTick = 1000u;
            const uint64_t trigMs = (trigTick * 100000ull) / 5994ull;
            // One read per tick across the whole 10 s window plus the flush read.
            for (uint64_t tick = 400u; tick <= 1130u; ++tick)
            {
                const uint16_t buttons = (tick == trigTick) ? chord : relaxed;
                pad.press(buttons);
                ps2_stubs::setPadRecordTickForTest(tick);
                ps2_stubs::setPadMarkerTickForTest(tick);
                if (tick == 600u)
                {
                    // In-window award/boost arrivals: the row shows the deltas.
                    ps2_stubs::setPadMarkerAwardForTest(2u, 777u, 0x1234u);
                    ps2_stubs::setPadMarkerBoostForTest(1u, 778u, 0x3f800000u);
                }
                pad.poll();
                if (tick == trigTick)
                {
                    t.Equals(pad.guestButtons(), chord, "the chord passes through to the guest");
                }
            }
            pad.unscript();
            ps2_stubs::closePadRecordForTest();
            ps2_stubs::closePadMarkerForTest();

            std::vector<fs::path> files;
            for (const auto &de : fs::directory_iterator(dir))
            {
                const std::string n = de.path().filename().string();
                if (n.compare(0, 7, "marker-") == 0 && n.size() > 4 &&
                    n.compare(n.size() - 4, 4, ".tsv") == 0)
                    files.push_back(de.path());
            }
            t.Equals(static_cast<uint32_t>(files.size()), static_cast<uint32_t>(1), "one marker file");
            std::string content;
            if (!files.empty())
            {
                std::FILE *f = std::fopen(files[0].string().c_str(), "rb");
                if (f)
                {
                    char buf[4096];
                    size_t n = 0;
                    while ((n = std::fread(buf, 1, sizeof(buf), f)) > 0)
                        content.append(buf, n);
                    std::fclose(f);
                }
            }
            t.IsTrue(content.find("trigger_ms=" + std::to_string(trigMs)) != std::string::npos,
                     "header carries the trigger ms");
            t.IsTrue(content.find("FULL120_FIX=") != std::string::npos, "header carries the FIX echo");
            // Data rows: the driven ticks whose ms falls in [trigMs-8000, trigMs+2000].
            uint32_t wantRows = 0u;
            for (uint64_t tick = 400u; tick <= 1130u; ++tick)
            {
                const uint64_t ms = (tick * 100000ull) / 5994ull;
                if (ms + 8000u >= trigMs && ms <= trigMs + 2000u)
                    ++wantRows;
            }
            uint32_t dataRows = 0u;
            size_t pos = 0u;
            bool sawHeader = false;
            while (pos < content.size())
            {
                const size_t end = content.find('\n', pos);
                const std::string line = content.substr(pos, end == std::string::npos ? end : end - pos);
                if (!line.empty() && line[0] != '#')
                {
                    if (!sawHeader)
                        sawHeader = line.compare(0, 4, "tick") == 0;
                    else
                        ++dataRows;
                }
                if (end == std::string::npos)
                    break;
                pos = end + 1u;
            }
            t.IsTrue(sawHeader, "file has a column header row");
            t.Equals(dataRows, wantRows, "window rows only");
            t.IsTrue(wantRows > 100u, "the 10 s window holds >100 rows at 1 read/tick");
            t.IsTrue(content.find("\tfff9\t") != std::string::npos, "a row shows the chord word");
            t.IsTrue(content.find("00c00100") != std::string::npos, "tracker S column");
            t.IsTrue(content.find("deadbeef") != std::string::npos, "tracker in0 column");
            t.IsTrue(content.find("\t1\t2\t3\t4\t4.5\t5\t") != std::string::npos,
                     "position/velocity float columns");
            t.IsTrue(content.find("0:3f800000,0,0,1,0,30,40000000;1:3f800000,1,0,1,0,30,40000000") !=
                         std::string::npos,
                     "input2 bank column");
            t.IsTrue(content.find("0badf00d") != std::string::npos, "gate a0 column");
            t.IsTrue(content.find("00000042\t00000099\t0\t") != std::string::npos,
                     "gate v0/v1/others columns (v2 appends after)");
            {
                float tf = 0.0f;
                const uint32_t tb = 0x40c90fdcu;
                std::memcpy(&tf, &tb, 4u);
                char want[32];
                std::snprintf(want, sizeof(want), "%.9g", static_cast<double>(tf));
                t.IsTrue(content.find(want) != std::string::npos, "target float column");
            }
            // LIM2 v2: header rev, column count (57 v1 + 36 v2), parse-back.
            t.IsTrue(content.find("# input-marker v2 #1 trigger_ms=" + std::to_string(trigMs)) !=
                         std::string::npos,
                     "header carries the v2 tag");
            {
                size_t hpos = content.find("\ntick\t");
                t.IsTrue(hpos != std::string::npos, "header row found");
                size_t hend = content.find('\n', hpos + 1u);
                uint32_t tabs = 0u;
                for (size_t i = hpos; i < hend; ++i)
                    if (content[i] == '\t')
                        ++tabs;
                t.Equals(tabs + 1u, 93u, "v2 header has 93 columns");
                size_t rpos = content.find('\n', hend + 1u);
                uint32_t rtabs = 0u;
                for (size_t i = hend + 1u; i < rpos; ++i)
                    if (content[i] == '\t')
                        ++rtabs;
                t.Equals(rtabs + 1u, 93u, "v2 data rows have 93 columns");
            }
            t.IsTrue(content.find("\t10\t20\t30\t3\t4\t0\t5\t") != std::string::npos,
                     "v2 live pos/vel/|v| columns");
            t.IsTrue(content.find("\t00000004\t00000007\t00300400\t") != std::string::npos,
                     "v2 live selector columns");
            t.IsTrue(content.find("\t0\t1\t0\t-2\t0000000a\t00000099\t7\t8\t9\t") !=
                         std::string::npos,
                     "v2 query frame columns");
            t.IsTrue(content.find("\t00000006\t0.5\t20\t") != std::string::npos,
                     "v2 uber columns");
            t.IsTrue(content.find("\t1\t00c00100\t3\t") != std::string::npos,
                     "v2 live stage columns");
            t.IsTrue(content.find("\t2\t0\t777\t00001234\t") != std::string::npos,
                     "v2 award columns");
            t.IsTrue(content.find("\t1\t0\t778\t1\n") != std::string::npos,
                     "v2 boost columns");
            std::string rec;
            {
                std::FILE *f = std::fopen(recPath.c_str(), "rb");
                if (f)
                {
                    char buf[256];
                    size_t n = 0;
                    while ((n = std::fread(buf, 1, sizeof(buf), f)) > 0)
                        rec.append(buf, n);
                    std::fclose(f);
                }
            }
            t.IsTrue(rec.find("# MARK " + std::to_string(trigMs)) != std::string::npos,
                     "the padrec carries the MARK stamp");
            std::vector<ps2_stubs::PadScriptEntry> entries;
            t.IsTrue(ps2_stubs::parsePadScript(rec.c_str(), entries), "stamped padrec still parses");

            ps2_stubs::clearPadScriptForTest();
            ps2_stubs::clearPadRecordForTest();
            ps2_stubs::clearPadMarkerForTest();
            pad.powerOff();
            fs::remove_all(dir);
            std::remove(recPath.c_str());
        });

        tc.Run("lim1: single sticks and wide chords do not trigger", [](TestCase &t)
               {
            namespace fs = std::filesystem;
            const fs::path dir = fs::temp_directory_path() / "lim1_marker_chord_neg_test";
            fs::remove_all(dir);
            Lim1PadBench pad;
            ps2_stubs::clearPadScriptForTest();
            ps2_stubs::clearPadRecordForTest();
            ps2_stubs::clearPadMarkerForTest();
            pad.powerOn();
            // Unarmed (default): chord reads are no-ops and pass through.
            pad.press(static_cast<uint16_t>(0xFFFFu & ~Ds2::l3 & ~Ds2::r3));
            pad.poll();
            t.Equals(pad.guestButtons(),
                     static_cast<uint16_t>(0xFFFFu & ~Ds2::l3 & ~Ds2::r3),
                     "unarmed reads pass through");
            t.IsTrue(!fs::exists(dir), "unarmed reads create no dir");
            t.IsTrue(ps2_stubs::setPadMarkerDirForTest(dir.string().c_str()), "marker should arm");

            const uint16_t relaxed = 0xFFFFu;
            const uint16_t l3 = static_cast<uint16_t>(0xFFFFu & ~Ds2::l3);
            const uint16_t r3 = static_cast<uint16_t>(0xFFFFu & ~Ds2::r3);
            const uint16_t chord = static_cast<uint16_t>(l3 & r3);
            const auto read = [&](uint64_t tick, uint16_t buttons) {
                pad.press(buttons);
                ps2_stubs::setPadMarkerTickForTest(tick);
                pad.poll();
            };
            const auto fileCount = [&]() {
                uint32_t n = 0u;
                for (const auto &de : fs::directory_iterator(dir))
                {
                    const std::string name = de.path().filename().string();
                    if (name.compare(0, 7, "marker-") == 0)
                        ++n;
                }
                return n;
            };
            // L3 alone, held and released: nothing.
            read(2000u, l3);
            read(2001u, l3);
            read(2002u, l3);
            read(2003u, relaxed);
            read(2300u, relaxed);
            t.Equals(fileCount(), 0u, "L3 alone writes nothing");
            // L3 then R3 200 ms later: nothing (12 ticks ~ 200 ms).
            read(3000u, l3);
            read(3012u, chord);
            read(3015u, chord);
            read(3016u, relaxed);
            read(3300u, relaxed);
            t.Equals(fileCount(), 0u, "a 200 ms chord writes nothing");
            // L3 then R3 50 ms later: one file (3 ticks ~ 50 ms).
            read(4000u, l3);
            read(4003u, chord);
            read(4005u, chord);
            read(4006u, relaxed);
            read(4300u, relaxed);
            t.Equals(fileCount(), 1u, "a 50 ms chord writes one file");
            // Holding the chord across reads fires once (no repeat).
            read(5000u, chord);
            read(5001u, chord);
            read(5002u, chord);
            read(5003u, relaxed);
            read(5300u, relaxed);
            t.Equals(fileCount(), 2u, "a held chord fires exactly once");

            pad.unscript();
            ps2_stubs::clearPadScriptForTest();
            ps2_stubs::clearPadRecordForTest();
            ps2_stubs::clearPadMarkerForTest();
            pad.powerOff();
            fs::remove_all(dir);
        });

        tc.Run("lim1: the session cap is 50 markers", [](TestCase &t)
               {
            namespace fs = std::filesystem;
            const fs::path dir = fs::temp_directory_path() / "lim1_marker_cap_test";
            fs::remove_all(dir);
            Lim1PadBench pad;
            ps2_stubs::clearPadScriptForTest();
            ps2_stubs::clearPadRecordForTest();
            ps2_stubs::clearPadMarkerForTest();
            pad.powerOn();
            t.IsTrue(ps2_stubs::setPadMarkerDirForTest(dir.string().c_str()), "marker should arm");
            const uint16_t relaxed = 0xFFFFu;
            const uint16_t chord =
                static_cast<uint16_t>(0xFFFFu & ~Ds2::l3 & ~Ds2::r3);
            for (uint32_t k = 0u; k < 55u; ++k)
            {
                const uint64_t tick = 1000u + 20u * k;
                pad.press(chord);
                ps2_stubs::setPadMarkerTickForTest(tick);
                pad.poll();
                pad.press(relaxed);
                ps2_stubs::setPadMarkerTickForTest(tick + 1u);
                pad.poll();
            }
            pad.press(relaxed);
            ps2_stubs::setPadMarkerTickForTest(1000u + 20u * 55u + 300u);
            pad.poll();
            pad.unscript();
            ps2_stubs::closePadMarkerForTest();
            uint32_t n = 0u;
            for (const auto &de : fs::directory_iterator(dir))
            {
                const std::string name = de.path().filename().string();
                if (name.compare(0, 7, "marker-") == 0 && name.size() > 4 &&
                    name.compare(name.size() - 4, 4, ".tsv") == 0)
                    ++n;
            }
            t.Equals(n, 50u, "exactly 50 files despite 55 chords");

            ps2_stubs::clearPadScriptForTest();
            ps2_stubs::clearPadRecordForTest();
            ps2_stubs::clearPadMarkerForTest();
            pad.powerOff();
            fs::remove_all(dir);
        });
    });
}
