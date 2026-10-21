#include "MiniTest.h"
#include "ps2_snd_spike.h"
#include "ps2_syscalls.h"
#include "runtime/ps2_pcm_ring.h"

#include <algorithm>
#include <array>
#include <cstdlib>
#include <cstdio>
#include <memory>
#include <cstdint>
#include <cstring>
#include <vector>
#if !defined(_WIN32)
#include <sys/stat.h>
#include <unistd.h>
#endif

namespace
{
    void putU32(std::vector<uint8_t> &data, size_t offset, uint32_t value)
    {
        std::memcpy(data.data() + offset, &value, sizeof(value));
    }
}

void register_ps2_snd_tests()
{
    MiniTest::Case("Ps2SoundHle", [](TestCase &tc)
    {
#if !defined(_WIN32)
        tc.Run("SND diagnostic record reaches the file before stop-kill", [](TestCase &t)
        {
            auto s = std::make_unique<ps2_snd_spike::State>();
            s->log = std::tmpfile();
            t.IsTrue(s->log != nullptr, "temporary sound log opens");
            if (!s->log)
                return;
            // Force full buffering: neither newline nor this small record can
            // reach the file unless the production logger flushes it.
            std::array<char, 8192> buffer{};
            std::setvbuf(s->log, buffer.data(), _IOFBF, buffer.size());
            ps2_snd_spike::logLocked(*s, "tick probe=%u", 1u);
            struct stat info{};
            const int rc = fstat(fileno(s->log), &info);
            // fstat reads kernel metadata without flushing the FILE stream.
            t.Equals(rc, 0, "file metadata readable before fclose");
            t.Equals(info.st_size, static_cast<off_t>(sizeof("tick probe=1\n") - 1u),
                     "first complete record persisted before the 64-record boundary");
            t.Equals(s->lines, uint64_t(1), "record count unchanged");
            std::fclose(s->log);
            s->log = nullptr;
        });
#endif

        tc.Run("SND tick cadence follows guest cycles at 93.75 Hz", [](TestCase &t)
        {
        using namespace ps2_snd_spike;
        t.Equals(kTickCycles, 3145728ull, "one tick is exactly 3,145,728 EE cycles");
        t.Equals(ticksForGuestCycles(10u * kTickCycles), 10ull, "ten periods yield ten ticks");
        t.Equals(ticksForGuestCycles(10u * kTickCycles + kTickCycles - 1u), 10ull,
                 "one cycle before the next deadline does not yield an extra tick");
        t.Equals(ticksForGuestCycles(10u * kTickCycles + kTickCycles), 11ull,
                 "the next period boundary yields tick eleven");
        });

        tc.Run("tag buffer parser extracts exactly 384 stereo s16 frames", [](TestCase &t)
        {
        using namespace ps2_snd_spike;
        std::vector<uint8_t> data(16u + 16u + kPcmBytesPerTick + 16u, 0u);
        putU32(data, 0u, 0u); // tag 0: 16-byte fixed record
        putU32(data, 16u, 1u);
        putU32(data, 20u, kPcmBytesPerTick);
        putU32(data, 24u, 0u); // two reserved tag-1 header words
        putU32(data, 28u, 0u);
        for (size_t i = 0; i < kPcmBytesPerTick; ++i)
            data[32u + i] = static_cast<uint8_t>(i & 0xffu);
        putU32(data, 32u + kPcmBytesPerTick, 5u);
        putU32(data, 36u + kPcmBytesPerTick, 17u);
        Tag1PcmView view{};
        t.IsTrue(findTag1Pcm(data.data(), data.size(), view), "tag 1 should parse");
        t.Equals(view.offset, static_cast<size_t>(32u), "PCM begins after the 16-byte tag-1 header");
        t.Equals(view.size, static_cast<size_t>(kPcmBytesPerTick), "PCM is 1,536 bytes");
        t.Equals(data[view.offset + 511u], static_cast<uint8_t>(0xffu), "PCM payload remains byte exact");
        t.IsFalse(findTag1Pcm(data.data(), 31u, view), "truncated tag header must be rejected");
        });

        tc.Run("PCM ring interleaves the planar tag-1 channels", [](TestCase &t)
        {
        using namespace ps2_snd_spike;
        std::vector<uint8_t> pcm(kPcmBytesPerTick, 0u);
        const size_t frames = kPcmBytesPerTick / 4u;
        for (size_t i = 0; i < frames; ++i)
        {
            // First block = right channel, second block = left (PCSX2 SPU2 input).
            const uint16_t l = static_cast<uint16_t>(i), r = static_cast<uint16_t>(0x8000u + i);
            std::memcpy(pcm.data() + 2u * i, &r, 2u);
            std::memcpy(pcm.data() + 2u * (frames + i), &l, 2u);
        }
        static ps2_pcm_ring::PcmRing ring; // 128 KiB of slots: keep it off the stack
        ring.push(pcm.data(), pcm.size());
        uint32_t frame = 0;
        t.IsTrue(ring.pop(frame), "first frame");
        t.Equals(frame, 0x80000000u, "frame 0 = left[0] | right[0] << 16");
        t.IsTrue(ring.pop(frame), "second frame");
        t.Equals(frame, 0x80010001u, "frame 1 = left[1] | right[1] << 16");
        for (size_t i = 2; i < frames; ++i)
            ring.pop(frame);
        t.Equals(frame, 0x817f017fu, "frame 383 = left[383] | right[383] << 16");
        t.IsFalse(ring.pop(frame), "exactly 384 frames per tick");
        });

        tc.Run("_sceSifSendCmd decodes the seven argument registers unconditionally", [](TestCase &t)
        {
        using namespace ps2_snd_spike;
        std::array<uint32_t, 11> gpr{};
        gpr[4] = 0x11u; // cid
        gpr[5] = 0x22u; // mode, ignored by this runtime
        gpr[6] = 0x33u; // packet
        gpr[7] = 0x44u; // packet size
        gpr[8] = 0x55u; // source extra
        gpr[9] = 0x66u; // destination extra
        gpr[10] = 0x77u; // extra size
        const ps2_syscalls::SendCmdArgs args = ps2_syscalls::decodeSendCmdArgs(gpr.data(), gpr.size());
        t.Equals(args.cid, 0x11u, "a0 supplies cid");
        t.Equals(args.packet, 0x33u, "a2 supplies packet");
        t.Equals(args.packetSize, 0x44u, "a3 supplies packet size");
        t.Equals(args.srcExtra, 0x55u, "a4 supplies extra source");
        t.Equals(args.dstExtra, 0x66u, "a5 supplies extra destination");
        t.Equals(args.extraSize, 0x77u, "a6 supplies extra size");
        const ps2_syscalls::SendCmdArgs shortArgs = ps2_syscalls::decodeSendCmdArgs(gpr.data(), 10u);
        t.Equals(shortArgs.packet, 0u, "incomplete register frame is rejected");
        });

        tc.Run("PS-ADPCM block decode matches hand-computed filter vectors", [](TestCase &t)
        {
        using namespace ps2_snd_spu;
        // Filter 0, shift 0: sample = nibble << 12, low nibble first.
        uint8_t b0[16] = {0x00, 0x00, 0xF1};
        int16_t out[28];
        int32_t p1 = 0, p2 = 0;
        decodeBlock(b0, out, p1, p2);
        t.Equals(out[0], int16_t(4096), "nibble 1 at shift 0 is 4096");
        t.Equals(out[1], int16_t(-4096), "nibble F is -1 << 12");
        t.Equals(out[2], int16_t(0), "zero nibble with filter 0 stays 0");
        // Filter 1 (60/64), shift 12: prediction only after a 4096 history.
        uint8_t b1[16] = {0x1C, 0x00, 0x00};
        p1 = 4096;
        p2 = 0;
        decodeBlock(b1, out, p1, p2);
        t.Equals(out[0], int16_t(3840), "(60 * 4096 + 32) >> 6");
        t.Equals(out[1], int16_t(3600), "(60 * 3840 + 32) >> 6");
        // Filter 2 (115, -52) uses both history samples.
        uint8_t b2[16] = {0x2C, 0x00, 0x00};
        p1 = 1000;
        p2 = 2000;
        decodeBlock(b2, out, p1, p2);
        t.Equals(out[0], int16_t((115 * 1000 - 52 * 2000 + 32) >> 6), "filter 2 first sample");
        });

        tc.Run("SNDDRV envelope: instant attack, 64-sample linear release", [](TestCase &t)
        {
        using namespace ps2_snd_spu;
        Adsr env;
        env.reg1 = kDriverAdsr1;
        env.reg2 = kDriverAdsr2;
        env.attack();
        int steps = 0;
        while (env.value < 0x7fff && steps < 100)
        {
            env.step();
            ++steps;
        }
        t.Equals(steps, 3, "linear attack, shift 0, step 7 << 11 per sample reaches 0x7fff in 3 samples");
        for (int i = 0; i < 10; ++i)
            env.step();
        t.Equals(env.value, int32_t(0x7fff), "sustain holds at full level");
        env.release();
        int rel = 0;
        while (env.step())
            ++rel;
        t.Equals(env.value, int32_t(0), "release reaches zero");
        t.IsTrue(rel >= 62 && rel <= 64, "release shift 5 is -512 per sample: about 64 samples");
        });

        tc.Run("3->4 upsampler follows SNDDRV positions and carries history", [](TestCase &t)
        {
        using namespace ps2_snd_spu;
        Upsampler34 up;
        int16_t ramp[kTag1Frames];
        for (uint32_t i = 0; i < kTag1Frames; ++i)
            ramp[i] = static_cast<int16_t>(4 * i);
        int32_t out[kTickFrames];
        up.run(ramp, out, 1);
        t.Equals(out[0], int32_t(0), "j = 0 sits halfway between the (zero) history and x0 = 0");
        t.Equals(out[1], int32_t(1), "j = 1 is at input position 0.25");
        t.Equals(out[2], int32_t(4), "j = 2 is exactly x1");
        t.Equals(out[511], int32_t(4 * 382 + 3), "j = 511 is at position 382.75");
        up.run(ramp, out, 1);
        t.Equals(out[0], int32_t((4 * 383) / 2), "the next tick starts from the saved last sample");
        });

        tc.Run("AU13 sinc upsampler has unity DC gain and a 16-sample ramp delay", [](TestCase &t)
        {
        using namespace ps2_snd_spu;
        SincUpsampler34 up;
        int16_t dc[kTag1Frames], ramp[kTag1Frames];
        for (uint32_t i = 0; i < kTag1Frames; ++i)
        {
            dc[i] = 10000;
            ramp[i] = static_cast<int16_t>(20 * i);
        }
        int32_t out[kTickFrames];
        up.run(dc, out, 1);
        up.run(dc, out, 1);
        for (uint32_t j = 0; j < kTickFrames; ++j)
            t.IsTrue(std::abs(out[j] - 10000) <= 2, "constant input stays constant once the history is filled");
        SincUpsampler34 up2;
        up2.run(ramp, out, 1);
        up2.run(ramp, out, 1);
        // output j sits at input position 0.75 j - 0.5 - 16 (a linear ramp is reproduced exactly)
        t.IsTrue(std::abs(out[100] - static_cast<int32_t>(20.0 * (0.75 * 100 - 0.5 - 16.0))) <= 3, "ramp delayed by 16 samples");
        });

        tc.Run("AU13 voice interpolators: gauss is the default, others hit the sample points", [](TestCase &t)
        {
        using namespace ps2_snd_spu;
        Spu spu;
        t.IsTrue(spu.interp == Interp::Gauss, "default interpolation is gauss");
        t.IsTrue(parseInterp(nullptr) == Interp::Gauss && parseInterp("bogus") == Interp::Gauss, "unknown text selects gauss");
        t.IsTrue(parseInterp("cubic") == Interp::Cubic && parseInterp("hermite") == Interp::Hermite &&
                     parseInterp("linear") == Interp::Linear, "names parse");
        const int16_t h[4] = {1000, 2000, 5000, 3000};
        for (Interp m : {Interp::Cubic, Interp::Hermite, Interp::Linear})
        {
            t.Equals(interpolate(m, h, 0x000u), int32_t(2000), "phase 0 is hist[1]");
            t.IsTrue(std::abs(interpolate(m, h, 0x800u) - 3500) < 900, "phase 0.5 lies between hist[1] and hist[2]");
        }
        t.Equals(interpolate(Interp::Linear, h, 0x800u), int32_t(3500), "linear midpoint");
        });

        tc.Run("driver keys a one-shot voice on and reports it ended through NAX", [](TestCase &t)
        {
        using namespace ps2_snd_spu;
        Spu spu;
        // Two blocks at 0x6000: a loud block, then a loop-end (no repeat) block.
        uint8_t blocks[32] = {};
        blocks[0] = 0x00;
        for (int i = 2; i < 16; ++i)
            blocks[i] = 0x77; // +7 << 12 on both nibbles
        blocks[17] = 0x01;
        spu.writeRam(0x6000u, blocks, sizeof(blocks));
        std::vector<uint8_t> tag3(kTag3Bytes, 0u);
        const uint32_t word = (1u << 23) | 0x6000u;
        std::memcpy(tag3.data() + kTag3VoiceBase + 8u * 5u, &word, 4);
        const uint16_t pitch = 0x1000u;
        std::memcpy(tag3.data() + kTag3VoiceBase + 8u * 5u + 4u, &pitch, 2);
        tag3[kTag3VoiceBase + 8u * 5u + 6u] = 100; // left only
        Driver drv;
        drv.update(tag3.data(), spu);
        t.Equals(drv.keyOns(), uint64_t(1), "one key on");
        t.Equals(drv.statusNax(5), 0x6000u, "a just-triggered voice reports its start address");
        int32_t d0[2 * kTickFrames], d1[2 * kTickFrames];
        spu.render(d0, d1, kTickFrames);
        int32_t peakL = 0, peakR = 0;
        for (uint32_t i = 0; i < kTickFrames; ++i)
        {
            peakL = std::max(peakL, std::abs(d0[2 * i]));
            peakR = std::max(peakR, std::abs(d0[2 * i + 1]));
        }
        t.IsTrue(peakL > 10000, "voice 5 (core 0) is audible on the left");
        t.Equals(peakR, int32_t(0), "volume R 0 keeps the right silent");
        t.IsTrue(!spu.voice(5).on, "the loop-end block without repeat stops the voice");
        t.Equals(spu.nax(5), kDriverLsax, "NAX lands on SNDDRV's LSAX");
        drv.update(tag3.data(), spu); // same word: no retrigger
        t.Equals(drv.keyOns(), uint64_t(1), "an unchanged word does not retrigger");
        t.Equals(drv.statusNax(5), 0u, "a finished voice reports NAX 0");
        t.Equals(drv.statusWord(5), word, "the record word is echoed");
        });

        tc.Run("keyed-off voice ENVX decays to zero with no output device, then retriggers", [](TestCase &t)
        {
        using namespace ps2_snd_spu;
        // No audio output is initialized anywhere in this test: envelope
        // decay is guest-time state, not host-callback state (AU10).
        Spu spu;
        uint8_t block[16] = {};
        for (int i = 2; i < 16; ++i)
            block[i] = 0x77; // +7 << 12 on both nibbles, no loop flags
        spu.writeRam(0x6000u, block, sizeof(block));
        std::vector<uint8_t> tag3(kTag3Bytes, 0u);
        const uint16_t pitch = 0x1000u;
        std::memcpy(tag3.data() + kTag3VoiceBase + 8u * 5u + 4u, &pitch, 2);
        tag3[kTag3VoiceBase + 8u * 5u + 6u] = 100;
        tag3[kTag3VoiceBase + 8u * 5u + 7u] = 100;
        auto setWord = [&](uint32_t word)
        {
            std::memcpy(tag3.data() + kTag3VoiceBase + 8u * 5u, &word, 4);
        };
        Driver drv;
        setWord((1u << 23) | 0x6000u);
        drv.update(tag3.data(), spu);
        t.Equals(drv.keyOns(), uint64_t(1), "one key on");
        int32_t d0[2 * kTickFrames], d1[2 * kTickFrames];
        spu.render(d0, d1, 64);
        t.Equals(spu.envx(5), uint16_t(0x7fff), "attack completes, sustain holds at full level");
        setWord((2u << 23) | 0x0u); // key off: same voice, zero address
        drv.update(tag3.data(), spu);
        spu.render(d0, d1, kTickFrames);
        t.Equals(spu.envx(5), uint16_t(0), "release reaches ENVX 0 within one tick");
        t.IsTrue(!spu.voice(5).on, "the released voice stops");
        setWord((3u << 23) | 0x6000u); // retrigger at ENVX 0: no retry spin
        drv.update(tag3.data(), spu);
        t.Equals(drv.keyOns(), uint64_t(2), "ENVX 0 retriggers immediately");
        t.IsTrue(spu.voice(5).on, "the retriggered voice sounds");
        spu.render(d0, d1, 1);
        t.IsTrue(spu.envx(5) != 0u, "the retriggered envelope rises");
        });

        tc.Run("tag-3 record is found between tag 0 and tag 1", [](TestCase &t)
        {
        using namespace ps2_snd_spike;
        std::vector<uint8_t> data(0x8F0u, 0u);
        putU32(data, 0x10u, 3u);
        putU32(data, 0x14u, ps2_snd_spu::kTag3Bytes);
        putU32(data, 0x2C0u, 1u);
        putU32(data, 0x2C4u, kPcmBytesPerTick);
        putU32(data, 0x8D0u, 5u);
        putU32(data, 0x8E0u, 6u);
        t.IsTrue(findTag3(data.data(), data.size()) == data.data() + 0x18u, "payload starts after {3, length}");
        Tag1PcmView view{};
        t.IsTrue(findTag1Pcm(data.data(), data.size(), view), "tag 1 still parses");
        t.Equals(view.offset, size_t(0x2D0u), "tag-1 PCM after its 16-byte header");
        });
    });
}
