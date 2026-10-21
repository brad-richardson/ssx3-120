// SPU2 voice model for the SSX 3 SND HLE (AU9), used by ps2_snd_spike.h.
// It advances on the guest-time SND tick whether or not host audio output
// is enabled (AU10).
//
// SSX 3 plays its race and menu sound effects on SPU2 hardware voices. The EE
// uploads sample banks with cid-0 packets (sceSdVoiceTrans into SPU RAM) and
// sends one tag-3 record per sound tick, which SNDDRV's SNDIOP_updatevoices
// (IOP 0x7d1c, local/research/AU2/snddrv-disasm.txt) turns into SPU2 register
// writes. This file models that driver routine and the parts of the SPU2 the
// game uses: 48 voices, PS-ADPCM, pitch, ADSR, voice volumes, key on/off and
// the dry mix. Reverb is not modelled (PCSX2's wet mix is silent in the race).
//
// Parts ported from PCSX2's SPU2 (pcsx2/SPU2/Mixer.cpp XA_decode_block,
// pcsx2/SPU2/ADSR.cpp V_ADSR::Calculate, the Gaussian table in
// ps2_snd_spu_gauss.h). SPDX-FileCopyrightText: 2002-2026 PCSX2 Dev Team.
// PCSX2 is GPL-3.0-or-later; this project is GPL-3.0.

#pragma once

#include "ps2_snd_spu_gauss.h"

#include <algorithm>
#include <array>
#include <cstdint>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <vector>

namespace ps2_savestate
{
class Writer;
class Reader;
} // namespace ps2_savestate

namespace ps2_snd_spike
{
// Savestate providers (defined in ps2_snd_spike.h); the classes below
// befriend them instead of the old runtime forwarder.
void saveSndState(ps2_savestate::Writer &w);
bool loadSndState(ps2_savestate::Reader &r);
} // namespace ps2_snd_spike

// (The old runtime `::SndSavestate` forwarder is gone as of family B; the
// providers below are befriended directly.)

namespace ps2_snd_spu
{

inline constexpr uint32_t kRamBytes = 2u << 20;
inline constexpr uint32_t kVoices = 48u;
inline constexpr uint32_t kVoicesPerCore = 24u;
inline constexpr uint32_t kTickFrames = 512u;       // 48 kHz frames per 93.75 Hz sound tick
inline constexpr uint32_t kTag1Frames = 384u;       // 36 kHz frames per tick in tag 1
inline constexpr uint32_t kDriverLsax = 0x5000u;    // SNDDRV loop address for one-shots (bytes)
inline constexpr uint32_t kDriverEndNax = 0x5040u;  // NAX below this = voice finished
inline constexpr uint16_t kDriverAdsr1 = 0x000Fu;
inline constexpr uint16_t kDriverAdsr2 = 0x0005u;
inline constexpr uint32_t kStatusVoiceBase = 0x3Cu; // EE status block: +0x3C + v*8 word, +0x40 + v*8 NAX
inline constexpr uint32_t kTag3Bytes = 0x2A8u;
inline constexpr uint32_t kTag3VoiceBase = 0x20u;

inline int16_t clamp16(int32_t v)
{
    return static_cast<int16_t>(std::clamp<int32_t>(v, -32768, 32767));
}

// PS-ADPCM: 16-byte block, byte 0 = shift | filter << 4, byte 1 = loop flags,
// 14 bytes = 28 nibbles (low nibble first). From PCSX2 XA_decode_block.
inline void decodeBlock(const uint8_t *block, int16_t out[28], int32_t &prev1, int32_t &prev2)
{
    static constexpr int32_t kFilter[5][2] = {{0, 0}, {60, 0}, {115, -52}, {98, -55}, {122, -60}};
    const int shift = (block[0] & 0xF) + 16;
    int id = (block[0] >> 4) & 0xF;
    if (id > 4)
        id = 0; // PCSX2's table has zero rows past 4
    const int32_t p1 = kFilter[id][0], p2 = kFilter[id][1];
    for (int i = 0; i < 14; ++i)
    {
        const uint32_t b = block[2 + i];
        int32_t d = static_cast<int32_t>((b << 28) & 0xF0000000u);
        int32_t pcm = (d >> shift) + ((p1 * prev1 + p2 * prev2 + 32) >> 6);
        pcm = std::clamp<int32_t>(pcm, -0x8000, 0x7fff);
        out[2 * i] = static_cast<int16_t>(pcm);
        d = static_cast<int32_t>((b << 24) & 0xF0000000u);
        int32_t pcm2 = (d >> shift) + ((p1 * pcm + p2 * prev1 + 32) >> 6);
        pcm2 = std::clamp<int32_t>(pcm2, -0x8000, 0x7fff);
        out[2 * i + 1] = static_cast<int16_t>(pcm2);
        prev2 = pcm;
        prev1 = pcm2;
    }
}

// SPU2 envelope, from PCSX2 V_ADSR (register fields as in PCSX2's union).
struct Adsr
{
    enum Phase { Stopped = 0, Attack, Decay, Sustain, Release };
    int phase = Stopped;
    int32_t value = 0;
    uint32_t counter = 0;
    uint16_t reg1 = 0, reg2 = 0;

    struct Params
    {
        bool decr, exp;
        int shift, step;
        int32_t target;
    };

    Params params(int p) const
    {
        switch (p)
        {
        case Attack:
            return {false, (reg1 & 0x8000u) != 0, (reg1 >> 10) & 0x1F, 7 - ((reg1 >> 8) & 3), 0x7fff};
        case Decay:
            return {true, true, (reg1 >> 4) & 0xF, -8, static_cast<int32_t>(((reg1 & 0xFu) + 1u) << 11)};
        case Sustain:
        {
            const bool decr = (reg2 & 0x4000u) != 0;
            int step = 7 - ((reg2 >> 6) & 3);
            if (decr)
                step = ~step;
            return {decr, (reg2 & 0x8000u) != 0, (reg2 >> 8) & 0x1F, step, 0};
        }
        default:
            return {true, (reg2 & 0x20u) != 0, reg2 & 0x1F, -8, 0};
        }
    }

    void attack()
    {
        phase = Attack;
        counter = 0;
        value = 0;
    }

    void release()
    {
        if (phase != Stopped)
        {
            phase = Release;
            counter = 0;
        }
    }

    // One 48 kHz step; false = the voice stops.
    bool step()
    {
        const Params p = params(phase);
        uint32_t counterInc = 0x8000u >> std::max(0, p.shift - 11);
        int32_t levelInc = p.step << std::max(0, 11 - p.shift);
        if (p.exp)
        {
            if (!p.decr && value > 0x6000)
                counterInc >>= 2;
            if (p.decr)
                levelInc = static_cast<int16_t>((levelInc * value) >> 15);
        }
        counterInc = std::max<uint32_t>(1u, counterInc);
        counter += counterInc;
        if (counter >= 0x8000u)
        {
            counter = 0;
            value = std::clamp<int32_t>(value + levelInc, 0, INT16_MAX);
        }
        if (phase == Sustain)
            return value != 0;
        if ((!p.decr && value >= p.target) || (p.decr && value <= p.target))
            ++phase;
        return phase <= Release;
    }
};

// AU13: voice-layer interpolation between hist[1] and hist[2] (PS2X_SPU_INTERP).
// gauss = the SPU2 hardware table (default, PCSX2's default); cubic = 4-point
// Lagrange, hermite = Catmull-Rom, linear. Output-only: nothing the guest reads
// depends on the sample values.
enum class Interp { Gauss, Cubic, Hermite, Linear };

inline const char *interpName(Interp i)
{
    switch (i)
    {
    case Interp::Cubic: return "cubic";
    case Interp::Hermite: return "hermite";
    case Interp::Linear: return "linear";
    default: return "gauss";
    }
}

inline Interp parseInterp(const char *p)
{
    if (p)
    {
        if (!std::strcmp(p, "cubic")) return Interp::Cubic;
        if (!std::strcmp(p, "hermite")) return Interp::Hermite;
        if (!std::strcmp(p, "linear")) return Interp::Linear;
    }
    return Interp::Gauss;
}

inline int32_t interpolate(Interp mode, const int16_t *h, uint32_t counter)
{
    if (mode == Interp::Gauss)
    {
        const auto &g = interpTable[(counter & 0x0ff0u) >> 4];
        int32_t s = 0;
        for (int k = 0; k < 4; ++k)
            s += (g[k] * h[k]) >> 15;
        return s;
    }
    const float t = static_cast<float>(counter & 0xfffu) * (1.0f / 4096.0f);
    const float a = h[0], b = h[1], c = h[2], d = h[3];
    float y;
    if (mode == Interp::Linear)
        y = b + (c - b) * t;
    else if (mode == Interp::Hermite) // Catmull-Rom
        y = b + 0.5f * t * (c - a + t * (2.0f * a - 5.0f * b + 4.0f * c - d + t * (3.0f * (b - c) + d - a)));
    else // cubic: Lagrange through the four samples
        y = a * (-t * (t - 1.0f) * (t - 2.0f) * (1.0f / 6.0f)) + b * ((t + 1.0f) * (t - 1.0f) * (t - 2.0f) * 0.5f) +
            c * (-(t + 1.0f) * t * (t - 2.0f) * 0.5f) + d * ((t + 1.0f) * t * (t - 1.0f) * (1.0f / 6.0f));
    return static_cast<int32_t>(std::lrintf(std::max(-32768.0f, std::min(32767.0f, y))));
}

struct Voice
{
    uint32_t ssa = 0, lsa = 0, nax = 0; // byte addresses
    uint16_t pitch = 0;
    int32_t volL = 0, volR = 0;         // current volume value (s16 range)
    int32_t targetL = 0, targetR = 0;
    int32_t stepL = 0, stepR = 0;       // linear ramp per frame (see Spu::setVolume)
    uint32_t rampFrames = 0;
    Adsr env;
    bool on = false;
    int16_t block[28] = {};
    uint8_t flags = 0;
    int idx = 0;
    int32_t prev1 = 0, prev2 = 0;
    int16_t hist[4] = {};
    uint32_t counter = 0;
};

// SNDDRV's volume register value for a tag-3 s8 volume, as SPU2 VOLL/VOLR.
inline uint16_t driverVolumeReg(int8_t v)
{
    return v >= 0 ? static_cast<uint16_t>(v * 129) : static_cast<uint16_t>((v << 7) & 0x7f80);
}

// A fixed (non-sweep) VOLL/VOLR register value as the SPU2's volume level.
inline int32_t volumeValue(uint16_t reg)
{
    return static_cast<int16_t>(static_cast<uint16_t>(reg << 1));
}

class Spu
{
    friend void ps2_snd_spike::saveSndState(ps2_savestate::Writer &);
    friend bool ps2_snd_spike::loadSndState(ps2_savestate::Reader &);

public:
    Spu() : m_ram(kRamBytes, 0) {}

    void writeRam(uint32_t byteAddr, const uint8_t *data, size_t size)
    {
        if (!data || byteAddr >= kRamBytes)
            return;
        std::memcpy(m_ram.data() + byteAddr, data, std::min<size_t>(size, kRamBytes - byteAddr));
        ++m_uploads;
        m_uploadBytes += size;
    }

    Voice &voice(uint32_t v) { return m_voices[v]; }
    const Voice &voice(uint32_t v) const { return m_voices[v]; }
    uint16_t envx(uint32_t v) const { return static_cast<uint16_t>(m_voices[v].env.value); }
    uint32_t nax(uint32_t v) const { return m_voices[v].nax; }
    uint64_t uploads() const { return m_uploads; }
    uint64_t uploadBytes() const { return m_uploadBytes; }

    void setPitch(uint32_t v, uint16_t pitch) { m_voices[v].pitch = pitch; }
    void setSsa(uint32_t v, uint32_t addr) { m_voices[v].ssa = addr & (kRamBytes - 1u) & ~15u; }
    void setLsa(uint32_t v, uint32_t addr) { m_voices[v].lsa = addr & (kRamBytes - 1u) & ~15u; }
    void setAdsr(uint32_t v, uint16_t r1, uint16_t r2)
    {
        m_voices[v].env.reg1 = r1;
        m_voices[v].env.reg2 = r2;
    }

    // Direct VOLL/VOLR write (SNDDRV at key on, and when the level is close).
    void setVolumeDirect(uint32_t v, int side, uint16_t reg)
    {
        Voice &vc = m_voices[v];
        (side == 0 ? vc.volL : vc.volR) = volumeValue(reg);
        (side == 0 ? vc.targetL : vc.targetR) = volumeValue(reg);
        (side == 0 ? vc.stepL : vc.stepR) = 0;
    }

    // SNDDRV's spusetvol: small changes are written directly; larger ones use
    // a hardware volume sweep that the driver re-aims every tick. The model
    // ramps linearly to the target over one tick instead of emulating the
    // sweep registers (a labelled approximation).
    void setVolume(uint32_t v, int side, uint16_t reg, bool direct)
    {
        if (direct)
        {
            setVolumeDirect(v, side, reg);
            return;
        }
        Voice &vc = m_voices[v];
        const int32_t target = volumeValue(reg);
        const int32_t cur = side == 0 ? vc.volL : vc.volR;
        (side == 0 ? vc.targetL : vc.targetR) = target;
        (side == 0 ? vc.stepL : vc.stepR) = (target - cur) / static_cast<int32_t>(kTickFrames);
        vc.rampFrames = kTickFrames;
    }

    void keyOn(uint64_t mask)
    {
        for (uint32_t v = 0; v < kVoices; ++v)
        {
            if (!(mask >> v & 1u))
                continue;
            Voice &vc = m_voices[v];
            vc.env.attack();
            vc.on = true;
            vc.nax = vc.ssa;
            vc.prev1 = vc.prev2 = 0;
            std::fill(std::begin(vc.hist), std::end(vc.hist), int16_t{0});
            vc.counter = 0;
            loadBlock(vc);
        }
    }

    void keyOff(uint64_t mask)
    {
        for (uint32_t v = 0; v < kVoices; ++v)
            if (mask >> v & 1u)
                m_voices[v].env.release();
    }

    // Mix `frames` 48 kHz frames of the dry voice outputs: core 0 = voices
    // 0-23, core 1 = voices 24-47 (SNDDRV's voice numbering).
    Interp interp = Interp::Gauss; // AU13 PS2X_SPU_INTERP (not part of the save state)

    void render(int32_t *dry0, int32_t *dry1, uint32_t frames)
    {
        for (uint32_t i = 0; i < frames; ++i)
        {
            int32_t l0 = 0, r0 = 0, l1 = 0, r1 = 0;
            for (uint32_t v = 0; v < kVoices; ++v)
            {
                Voice &vc = m_voices[v];
                if (!vc.on)
                    continue;
                int32_t l = 0, r = 0;
                mixVoice(vc, l, r);
                if (v < kVoicesPerCore)
                {
                    l0 += l;
                    r0 += r;
                }
                else
                {
                    l1 += l;
                    r1 += r;
                }
            }
            dry0[2 * i] = clamp16(l0);
            dry0[2 * i + 1] = clamp16(r0);
            dry1[2 * i] = clamp16(l1);
            dry1[2 * i + 1] = clamp16(r1);
        }
    }

private:
    void loadBlock(Voice &vc)
    {
        const uint8_t *b = m_ram.data() + (vc.nax & (kRamBytes - 16u));
        vc.flags = b[1];
        if (vc.flags & 4u) // loop start
            vc.lsa = vc.nax;
        decodeBlock(b, vc.block, vc.prev1, vc.prev2);
        vc.idx = 0;
    }

    void stop(Voice &vc)
    {
        vc.on = false;
        vc.env.phase = Adsr::Stopped;
        vc.env.value = 0;
    }

    void advance(Voice &vc)
    {
        vc.hist[0] = vc.hist[1];
        vc.hist[1] = vc.hist[2];
        vc.hist[2] = vc.hist[3];
        vc.hist[3] = vc.block[vc.idx];
        if (++vc.idx < 28)
            return;
        if (vc.flags & 1u) // loop end: jump to LSA, mute unless the loop flag is set
        {
            vc.nax = vc.lsa;
            if (!(vc.flags & 2u))
            {
                stop(vc);
                return;
            }
        }
        else
        {
            vc.nax = (vc.nax + 16u) & (kRamBytes - 1u);
        }
        loadBlock(vc);
    }

    void mixVoice(Voice &vc, int32_t &outL, int32_t &outR)
    {
        if (vc.rampFrames)
        {
            vc.volL += vc.stepL;
            vc.volR += vc.stepR;
            if (--vc.rampFrames == 0)
            {
                vc.volL = vc.targetL;
                vc.volR = vc.targetR;
            }
        }
        const int32_t s = interpolate(interp, vc.hist, vc.counter);
        if (!vc.env.step())
        {
            stop(vc);
            return;
        }
        const int32_t value = (s * vc.env.value) >> 15;
        outL = (value * vc.volL) >> 15;
        outR = (value * vc.volR) >> 15;
        vc.counter += std::min<uint32_t>(vc.pitch, 0x3FFFu);
        while (vc.on && vc.counter >= 0x1000u)
        {
            vc.counter -= 0x1000u;
            advance(vc);
        }
    }

    std::vector<uint8_t> m_ram;
    std::array<Voice, kVoices> m_voices{};
    uint64_t m_uploads = 0, m_uploadBytes = 0;
};

// Port of SNDDRV SNDIOP_updatevoices for the 48 SPU voices of a tag-3 record
// (payload +0x20 + v*8: u32 word = keyon serial << 23 | SPU byte address,
// u16 pitch, s8 volume L, s8 volume R), plus the EE status slots it fills.
class Driver
{
    friend void ps2_snd_spike::saveSndState(ps2_savestate::Writer &);
    friend bool ps2_snd_spike::loadSndState(ps2_savestate::Reader &);

public:
    void update(const uint8_t *tag3, Spu &spu)
    {
        uint64_t keyOn = 0, keyOff = 0;
        for (uint32_t v = 0; v < kVoices; ++v)
        {
            const uint8_t *e = tag3 + kTag3VoiceBase + v * 8u;
            uint32_t word = 0;
            uint16_t pitch = 0;
            std::memcpy(&word, e, 4);
            std::memcpy(&pitch, e + 4, 2);
            const int8_t vol[2] = {static_cast<int8_t>(e[6]), static_cast<int8_t>(e[7])};
            if (pitch != m_pitch[v])
            {
                spu.setPitch(v, pitch);
                m_pitch[v] = pitch;
            }
            for (int side = 0; side < 2; ++side)
            {
                if (vol[side] == m_vol[v][side])
                    continue;
                const int delta = std::abs(vol[side] - m_vol[v][side]) / 2;
                spu.setVolume(v, side, driverVolumeReg(vol[side]), delta < 2);
                m_vol[v][side] = vol[side];
            }
            bool triggered = false;
            const uint32_t addr = word & 0x7fffffu;
            if (word != m_word[v])
            {
                if (addr == 0u)
                {
                    keyOff |= 1ull << v;
                    m_word[v] = word;
                }
                else if (spu.envx(v) != 0u)
                {
                    keyOff |= 1ull << v; // still sounding: release now, retrigger next tick
                    triggered = true;
                }
                else
                {
                    spu.setSsa(v, addr);
                    spu.setLsa(v, kDriverLsax);
                    spu.setAdsr(v, kDriverAdsr1, kDriverAdsr2);
                    spu.setVolumeDirect(v, 0, driverVolumeReg(vol[0]));
                    spu.setVolumeDirect(v, 1, driverVolumeReg(vol[1]));
                    keyOn |= 1ull << v;
                    m_word[v] = word;
                    triggered = true;
                }
            }
            m_statusWord[v] = word;
            m_statusTriggered[v] = triggered;
            m_statusAddr[v] = addr;
        }
        spu.keyOn(keyOn);
        spu.keyOff(keyOff);
        for (uint32_t v = 0; v < kVoices; ++v)
        {
            const uint32_t nax = spu.nax(v);
            m_statusNax[v] = m_statusTriggered[v] ? m_statusAddr[v] : (nax < kDriverEndNax ? 0u : nax);
        }
        m_keyOns += static_cast<uint64_t>(__builtin_popcountll(keyOn));
    }

    uint32_t statusWord(uint32_t v) const { return m_statusWord[v]; }
    uint32_t statusNax(uint32_t v) const { return m_statusNax[v]; }
    uint64_t keyOns() const { return m_keyOns; }

private:
    std::array<uint32_t, kVoices> m_word{}, m_statusWord{}, m_statusNax{}, m_statusAddr{};
    std::array<bool, kVoices> m_statusTriggered{};
    std::array<uint16_t, kVoices> m_pitch{};
    std::array<std::array<int8_t, 2>, kVoices> m_vol{};
    uint64_t m_keyOns = 0;
};

// SNDDRV SNDIOP_ee36_iop24_spu48: 3->4 linear interpolation of one channel,
// 384 samples at 36 kHz to 512 at 48 kHz, output j at input position
// 0.75 j - 0.5 (the previous tick's last sample is position -1). AU8 E7.
class Upsampler34
{
    friend void ps2_snd_spike::saveSndState(ps2_savestate::Writer &);
    friend bool ps2_snd_spike::loadSndState(ps2_savestate::Reader &);

public:
    void run(const int16_t *in, int32_t *out, size_t outStride)
    {
        for (uint32_t j = 0; j < kTickFrames; ++j)
        {
            const int32_t q = 3 * static_cast<int32_t>(j) - 2; // quarter-sample position
            const int32_t i = q >= 0 ? q / 4 : -1;
            const int32_t f = q - 4 * i;
            const int32_t a = i < 0 ? m_history : in[i];
            const int32_t b = in[i + 1];
            out[j * outStride] = (a * (4 - f) + b * f) >> 2;
        }
        m_history = in[kTag1Frames - 1u];
    }

private:
    int32_t m_history = 0;
};

// AU13 PS2X_AUDIO_RESAMPLE=sinc: the same 384 -> 512 conversion (output j at
// input position 0.75 j - 0.5) with a Kaiser-windowed sinc instead of the
// driver's linear 3->4 model. Causal: the output is delayed by kHalf input
// samples (0.44 ms at 36 kHz) so no lookahead into the next tick is needed.
// The cutoff sits at 96.7 % of the source's 18 kHz Nyquist.
class SincUpsampler34
{
    friend void ps2_snd_spike::saveSndState(ps2_savestate::Writer &);
    friend bool ps2_snd_spike::loadSndState(ps2_savestate::Reader &);

public:
    static constexpr int kHalf = 16;
    SincUpsampler34()
    {
        const double pi = 3.14159265358979323846;
        for (int phase = 0; phase < 4; ++phase)
        {
            double sum = 0.0;
            double w[2 * kHalf];
            for (int k = 0; k < 2 * kHalf; ++k)
            {
                const double x = (k - kHalf + 1) - phase / 4.0; // tap k: offset from floor(position)
                const double fc = 0.9666;
                const double sinc = x == 0.0 ? fc : std::sin(pi * x * fc) / (pi * x);
                const double r = x / kHalf;
                const double win = std::fabs(r) >= 1.0 ? 0.0 : bessel0(8.0 * std::sqrt(1.0 - r * r)) / bessel0(8.0);
                w[k] = sinc * win;
                sum += w[k];
            }
            for (int k = 0; k < 2 * kHalf; ++k)
                m_kernel[phase][k] = static_cast<float>(w[k] / sum); // unity DC gain
        }
    }

    void run(const int16_t *in, int32_t *out, size_t outStride)
    {
        int32_t buf[kHalf * 2 + kTag1Frames];
        std::memcpy(buf, m_hist, sizeof(m_hist));
        for (uint32_t i = 0; i < kTag1Frames; ++i)
            buf[2 * kHalf + i] = in[i]; // current sample i sits at 2*kHalf + i
        for (uint32_t j = 0; j < kTickFrames; ++j)
        {
            const int32_t q = 3 * static_cast<int32_t>(j) - 2 - 4 * kHalf; // quarter samples, delayed by kHalf
            const int32_t i = q >= 0 ? q / 4 : -((-q + 3) / 4);            // floor
            const float *kern = m_kernel[q - 4 * i];
            float acc = 0.0f;
            for (int k = 0; k < 2 * kHalf; ++k)
                acc += kern[k] * static_cast<float>(buf[i + k - kHalf + 1 + 2 * kHalf]);
            out[j * outStride] = static_cast<int32_t>(std::lrintf(std::max(-32768.0f, std::min(32767.0f, acc))));
        }
        std::memcpy(m_hist, buf + kTag1Frames, sizeof(m_hist));
    }

private:
    static double bessel0(double x)
    {
        double sum = 1.0, term = 1.0;
        for (int k = 1; k < 30; ++k)
        {
            term *= (x / (2.0 * k)) * (x / (2.0 * k));
            sum += term;
        }
        return sum;
    }

    float m_kernel[4][2 * kHalf] = {};
    int32_t m_hist[kHalf * 2] = {};
};

} // namespace ps2_snd_spu
