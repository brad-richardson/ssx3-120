// HLE of the SSX 3 EA SND tick and tag-1 PCM path. The guest-time model
// (sound-clock ticks, cid-0 uploads, tag-3 voice updates, status block)
// always runs: the game waits on SND progress once its sound banks load
// (AU10), so gating it on host audio hangs the title with sound off.
// PS2X_SOUND=1 only enables the host audio output that drains the PCM ring;
// without it the ring overflows and drops frames host-side. Env:
//   PS2X_SND_LOG=<file>    event log (bounded, kMaxLines).
//   PS2X_SND_VOICES=0      music only: skip the SPU voice layer (AU9).
//
// SNDDRV protocol (AU2 Part A, local/research/AU2/REPORT.md):
//   IOP->EE cid 1, +0x10 type: 0 = tick (opt = IOP address the EE DMAs its
//     tag buffer to), 2 = SPU-upload done (opt = the EE's callback id),
//     3 = data only. A tick also writes a 0x240-byte status block to the EE
//     address given in the init RPC (+4); SNDDRV stamps its serial at +0 and
//     +0x23C.
//   EE->IOP cid 0, +0x10 type 1 = SNDIOP_dmqueue(+0x14 id, +0x18 IOP src,
//     +0x1C SPU dst, +0x20 u16 size, +0x22 s16 prio) -> sceSdVoiceTrans.
// While the spike is on, sceSifSetDma calls made from the SND library are
// captured host-side (keyed by IOP address) instead of being copied into
// low EE RDRAM.
//
// Output (AU9): each tick's tag-1 music goes through SNDDRV's 3->4 upsampler
// to 512 frames at 48 kHz and is mixed with the SPU voice layer
// (ps2_snd_spu.h) that the tag-3 record and the cid-0 uploads drive.

#pragma once

#include "ps2_runtime.h"
#include "runtime/ee_scheduler.h"
#include "runtime/ps2_memory.h"
#include "runtime/ps2_pcm_ring.h"
#include "runtime/ps2_savestate.h"
#include "ps2_runtime_macros.h"
#include "ps2_snd_spu.h"

#include <algorithm>
#include <chrono>
#include <cstdarg>

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

namespace ps2_snd_spike
{

inline constexpr uint64_t kMaxLines = 60000ull;
inline constexpr uint64_t kTickCycles = 3145728ull; // 294,912,000 * 384 / 36,000
inline constexpr uint32_t kPcmFramesPerTick = 384u;
inline constexpr uint32_t kPcmBytesPerTick = kPcmFramesPerTick * 2u * sizeof(int16_t);
inline constexpr uint32_t kPacketAddr = 0x01F31000u;   // after the runtime's HLE pools
inline constexpr uint32_t kDonePacketBase = 0x01F31100u; // ring of 16 x 0x20
inline constexpr uint32_t kTagbufIopAddr = 0x0000B3C4u; // SNDDRV's own value (state+0xE0)
inline constexpr uint32_t kStatusBytes = 0x240u;
inline constexpr uint32_t kSndCodeLo = 0x003B0000u;     // EE SND library range (AU1)
inline constexpr uint32_t kSndCodeHi = 0x003D0000u;
inline constexpr uint32_t kIsceSendCmdRa = 0x00426220u; // isceSifSendCmd -> _sceSifSendCmd
inline constexpr uint32_t kTickCounterAddr = 0x0050A8E8u + 0x180u;
inline constexpr uint32_t kRpcSendBuf = 0x0050AD00u;

struct State
{
    std::mutex mutex;
    bool init = false;
    bool enabled = false;
    FILE *log = nullptr;
    uint64_t lines = 0;
    uint32_t handler = 0, handlerData = 0, handlerGp = 0;
    uint32_t statusAddr = 0;
    uint32_t serial = 0;
    uint64_t ticks = 0, cid0 = 0, dmq = 0, done = 0, setdma = 0, tagbufs = 0;
    uint32_t doneRing = 0;
    std::map<uint32_t, std::vector<uint8_t>> iopMem; // IOP dst -> last payload
    bool voices = true;
    ps2_snd_spu::Spu spu;
    ps2_snd_spu::Driver driver;
    ps2_snd_spu::Upsampler34 upLeft, upRight;
    // AU13: PS2X_AUDIO_RESAMPLE=sinc swaps the music upsampler (PS2X_SPU_INTERP sets spu.interp).
    bool sinc = false;
    ps2_snd_spu::SincUpsampler34 sincLeft, sincRight;
};

struct Tag1PcmView
{
    size_t offset = 0;
    size_t size = 0;
};

inline bool findTag1Pcm(const uint8_t *data, size_t size, Tag1PcmView &view)
{
    view = {};
    if (!data)
        return false;
    size_t offset = 0;
    while (offset + 4u <= size)
    {
        uint32_t tag = 0;
        std::memcpy(&tag, data + offset, sizeof(tag));
        if (tag == 6u)
            return false;
        if (tag == 0u || tag == 5u)
        {
            if (offset + 16u > size)
                return false;
            offset += 16u;
            continue;
        }
        if (tag < 1u || tag > 4u || offset + 8u > size)
            return false;
        uint32_t length = 0;
        std::memcpy(&length, data + offset + 4u, sizeof(length));
        if (length > size - offset - 8u)
            return false;
        if (tag == 1u)
        {
            // The mix tag has two reserved words after its length.
            if (length != kPcmBytesPerTick || offset + 16u + length > size)
                return false;
            view = {offset + 16u, kPcmBytesPerTick};
            return true;
        }
        offset += 8u + length;
    }
    return false;
}

// The tag-3 voice record (SNDIOP_processtagbuf: {3, length} then the payload).
inline const uint8_t *findTag3(const uint8_t *data, size_t size)
{
    size_t offset = 0;
    while (data && offset + 8u <= size)
    {
        uint32_t tag = 0, length = 0;
        std::memcpy(&tag, data + offset, 4);
        if (tag == 6u)
            return nullptr;
        if (tag == 0u || tag == 5u)
        {
            offset += 16u;
            continue;
        }
        std::memcpy(&length, data + offset + 4u, 4);
        if (tag == 3u)
            return length >= ps2_snd_spu::kTag3Bytes && offset + 8u + length <= size ? data + offset + 8u : nullptr;
        if (tag < 1u || tag > 4u || length > size - offset - 8u)
            return nullptr;
        offset += (tag == 1u ? 16u : 8u) + length;
    }
    return nullptr;
}

// The PCM ring itself is generic host-audio infrastructure
// (`ps2_pcm_ring::PcmRing` in runtime/ps2_pcm_ring.h); this family only
// produces frames into it (mixTickLocked below).

inline uint64_t ticksForGuestCycles(uint64_t cycles)
{
    return cycles / kTickCycles;
}

inline State &state()
{
    static State s;
    return s;
}

inline void initLocked(State &s)
{
    s.init = true;
    // AU10: no PS2X_SOUND gate. Guest-visible SND state advances on guest
    // time whether or not a host audio device consumes the PCM ring.
    if (const char *p = std::getenv("PS2X_SND_LOG"); p && *p)
        s.log = std::fopen(p, "w");
    s.spu.interp = ps2_snd_spu::parseInterp(std::getenv("PS2X_SPU_INTERP"));
    if (const char *p = std::getenv("PS2X_AUDIO_RESAMPLE"); p && std::strcmp(p, "sinc") == 0)
        s.sinc = true;
    s.enabled = true;
}

inline bool enabled()
{
    State &s = state();
    std::lock_guard<std::mutex> lock(s.mutex);
    if (!s.init)
        initLocked(s);
    return s.enabled;
}

inline void logLocked(State &s, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
inline void logLocked(State &s, const char *fmt, ...)
{
    if (!s.log || s.lines >= kMaxLines)
        return;
    va_list ap;
    va_start(ap, fmt);
    std::vfprintf(s.log, fmt, ap);
    va_end(ap);
    std::fputc('\n', s.log);
    ++s.lines;
    // TLB1: diagnostic boots stop with SIGTERM, bypassing stdio teardown.
    // Persist every complete record before returning to guest execution.
    std::fflush(s.log);
}

inline uint32_t rd32(const uint8_t *rdram, uint32_t addr)
{
    uint32_t v = 0;
    const uint32_t p = addr & PS2_RAM_MASK;
    if (p + 4u <= PS2_RAM_SIZE)
        std::memcpy(&v, rdram + p, 4);
    return v;
}

inline void wr32(uint8_t *rdram, uint32_t addr, uint32_t v)
{
    const uint32_t p = addr & PS2_RAM_MASK;
    if (p + 4u <= PS2_RAM_SIZE)
        std::memcpy(rdram + p, &v, 4);
}

inline GuestInvocation makeHandlerCall(const State &s, uint32_t packet)
{
    GuestInvocation inv{};
    inv.kind = GuestInvocationKind::Interrupt;
    inv.context.pc = s.handler;
    SET_GPR_U32(&inv.context, 4, packet);
    SET_GPR_U32(&inv.context, 5, s.handlerData);
    SET_GPR_U32(&inv.context, 28, s.handlerGp);
    SET_GPR_U32(&inv.context, 29, 0u); // run() assigns the invocation stack
    SET_GPR_U32(&inv.context, 31, 0u);
    return inv;
}

inline void writePacket(uint8_t *rdram, uint32_t at, uint32_t type, uint32_t opt)
{
    wr32(rdram, at + 0x00u, 0x20u); // psize 0x20, no extra data
    wr32(rdram, at + 0x04u, 0u);
    wr32(rdram, at + 0x08u, 1u);    // cid 1
    wr32(rdram, at + 0x0Cu, opt);
    wr32(rdram, at + 0x10u, type);
    for (uint32_t o = 0x14u; o < 0x20u; o += 4u)
        wr32(rdram, at + o, 0u);
}

// sceSifAddCmdHandler (Stubs/SIF.cpp): remember the cid-1 handler.
inline bool noteAddCmdHandler(uint32_t cid, uint32_t handler, uint32_t data, uint32_t gp)
{
    if (!enabled() || cid != 1u)
        return false;
    State &s = state();
    std::lock_guard<std::mutex> lock(s.mutex);
    s.handler = handler;
    s.handlerData = data;
    s.handlerGp = gp;
    logLocked(s, "addcmdhandler cid=1 handler=0x%x data=0x%x gp=0x%x", handler, data, gp);
    return true;
}

// SifCallRpc (Syscalls/RPC.cpp): the SND init call carries the status address.
inline void noteRpc(const uint8_t *rdram, uint32_t sid, uint32_t fno, uint32_t send, uint32_t size)
{
    if (!enabled() || sid != 0x534E44u)
        return;
    State &s = state();
    std::lock_guard<std::mutex> lock(s.mutex);
    char hex[3 * 64 + 1] = {0};
    for (uint32_t i = 0; i < size && i < 64u; ++i)
        std::snprintf(hex + 3 * i, 4, "%02x ", (rd32(rdram, send + (i & ~3u)) >> (8 * (i & 3u))) & 0xFFu);
    if (fno == 0u && size >= 8u)
        s.statusAddr = rd32(rdram, send + 4u);
    logLocked(s, "snd-rpc fno=%u send=0x%x size=%u status=0x%x bytes=[%s]", fno, send, size, s.statusAddr, hex);
}

// EeScheduler::processEvent(SoundTick): deliver one IOP tick in guest time.
// Module hook (runtime/game_module.h `onSndSoundTick`): reports through
// `out` the one handler invocation the runtime must queue; the return tells
// whether an invocation was produced. All state transitions are identical
// to the direct call this replaces.
inline bool onSoundTick(uint8_t *rdram, uint64_t guestCycle, GuestInvocation *out)
{
    if (!rdram || !enabled())
        return false;
    State &s = state();
    std::lock_guard<std::mutex> lock(s.mutex);
    if (s.handler == 0u)
        return false;
    ++s.serial;
    if (s.statusAddr != 0u)
    {
        wr32(rdram, s.statusAddr + 0x000u, s.serial);
        wr32(rdram, s.statusAddr + 0x23Cu, s.serial);
        // SNDIOP_updatevoices: per SPU voice the record word and NAX (0 once
        // the voice has ended); PCSX2 shows 0xFFFFFFFF in the 16 IOP-voice
        // slots after them.
        for (uint32_t v = 0; v < ps2_snd_spu::kVoices; ++v)
        {
            wr32(rdram, s.statusAddr + ps2_snd_spu::kStatusVoiceBase + 8u * v, s.driver.statusWord(v));
            wr32(rdram, s.statusAddr + ps2_snd_spu::kStatusVoiceBase + 4u + 8u * v, s.driver.statusNax(v));
        }
        for (uint32_t o = 0x1C0u; o < 0x23Cu; o += 8u)
            wr32(rdram, s.statusAddr + o, 0xFFFFFFFFu);
    }
    writePacket(rdram, kPacketAddr, 0u, kTagbufIopAddr);
    if (out != nullptr)
        *out = makeHandlerCall(s, kPacketAddr);
    ++s.ticks;
    if (s.ticks <= 8u || (s.ticks % 94u) == 0u)
        logLocked(s, "tick cycle=%llu ticks=%llu counter=0x%x cid0=%llu dmq=%llu done=%llu setdma=%llu tagbufs=%llu underruns=%llu overflows=%llu",
                  (unsigned long long)guestCycle, (unsigned long long)s.ticks, rd32(rdram, kTickCounterAddr),
                  (unsigned long long)s.cid0, (unsigned long long)s.dmq, (unsigned long long)s.done,
                  (unsigned long long)s.setdma, (unsigned long long)s.tagbufs,
                  (unsigned long long)ps2_pcm_ring::pcmRing().underruns(),
                  (unsigned long long)ps2_pcm_ring::pcmRing().overflows());
    return true;
}

// One sound tick: tag-3 voice updates, 512 frames of SPU voices, the tag-1
// music upsampled 3->4 (block 1 = left, block 0 = right; AU8), mixed like the
// SPU2 core chain (music into core 0 with its voices, core 1 voices added).
inline void mixTickLocked(State &s, const uint8_t *pcm, const uint8_t *tag3)
{
    using namespace ps2_snd_spu;
    int16_t right[kTag1Frames], left[kTag1Frames];
    std::memcpy(right, pcm, sizeof(right));
    std::memcpy(left, pcm + sizeof(right), sizeof(left));
    int32_t music[2 * kTickFrames], dry0[2 * kTickFrames] = {}, dry1[2 * kTickFrames] = {};
    if (s.sinc)
    {
        s.sincLeft.run(left, music, 2);
        s.sincRight.run(right, music + 1, 2);
    }
    else
    {
        s.upLeft.run(left, music, 2);
        s.upRight.run(right, music + 1, 2);
    }
    if (s.voices && tag3)
    {
        s.driver.update(tag3, s.spu);
        s.spu.render(dry0, dry1, kTickFrames);
    }
    for (uint32_t i = 0; i < kTickFrames; ++i)
    {
        const int16_t l = clamp16(clamp16(music[2 * i] + dry0[2 * i]) + dry1[2 * i]);
        const int16_t r = clamp16(clamp16(music[2 * i + 1] + dry0[2 * i + 1]) + dry1[2 * i + 1]);
        const uint32_t frame =
            static_cast<uint16_t>(l) | (static_cast<uint32_t>(static_cast<uint16_t>(r)) << 16);
        ps2_pcm_ring::pcmRing().pushFrame(frame);
    }
}

// sceSifSetDma (Stubs/SIF.cpp), one descriptor. Returns true when the spike
// consumed it (SND-originated: captured, not copied into EE RDRAM).
inline bool onSetDma(const uint8_t *rdram, uint64_t vsync, uint32_t ra, uint32_t src, uint32_t dst,
                     uint32_t size, uint32_t attr)
{
    if (!rdram || ra < kSndCodeLo || ra >= kSndCodeHi || !enabled())
        return false;
    State &s = state();
    std::lock_guard<std::mutex> lock(s.mutex);
    ++s.setdma;
    const uint32_t p = src & PS2_RAM_MASK;
    if (size > (16u << 20) || p + size > PS2_RAM_SIZE)
        return true;
    std::vector<uint8_t> bytes(rdram + p, rdram + p + size);
    const bool tagbuf = dst == kTagbufIopAddr;
    if (tagbuf)
    {
        Tag1PcmView view{};
        if (findTag1Pcm(bytes.data(), bytes.size(), view))
        {
            mixTickLocked(s, bytes.data() + view.offset, findTag3(bytes.data(), bytes.size()));
        }
    }
    if (tagbuf)
        ++s.tagbufs;
    logLocked(s, "setdma vsync=%llu ra=0x%x src=0x%x dst=0x%x size=%u attr=0x%x%s", (unsigned long long)vsync, ra,
              src, dst, size, attr, tagbuf ? " tagbuf" : "");
    s.iopMem[dst] = std::move(bytes);
    return true;
}

// Syscalls/RPC.cpp sceSifSendCmd (both entry points). cid 0 = SND command.
// Module hook (runtime/game_module.h `onSndSendCmd`): reports through `out`
// the one done-callback invocation the runtime must queue; the return tells
// whether an invocation was produced. All state transitions are identical
// to the direct call this replaces.
inline bool onSendCmd(uint8_t *rdram, uint64_t vsync, uint32_t ra, uint32_t cid, uint32_t packet,
                      uint32_t psize, GuestInvocation *out)
{
    if (!rdram || cid != 0u || !enabled())
        return false;
    State &s = state();
    std::lock_guard<std::mutex> lock(s.mutex);
    ++s.cid0;
    uint32_t w[12] = {0};
    for (uint32_t i = 0; i < 12u && i * 4u < psize; ++i)
        w[i] = rd32(rdram, packet + 4u * i);
    const uint32_t type = w[4], id = w[5], iopSrc = w[6], spuDst = w[7];
    const uint32_t size = w[8] & 0xFFFFu;
    const int32_t prio = static_cast<int16_t>(w[8] >> 16);
    logLocked(s, "cid0 vsync=%llu ra=0x%x psize=%u type=%u id=0x%x iop=0x%x spu=0x%x size=%u prio=%d w9=0x%x w10=0x%x w11=0x%x",
              (unsigned long long)vsync, ra, psize, type, id, iopSrc, spuDst, size, prio, w[9], w[10], w[11]);
    if (type != 1u)
        return false;
    ++s.dmq;
    auto it = s.iopMem.upper_bound(iopSrc);
    if (it != s.iopMem.begin())
    {
        --it;
        const uint32_t off = iopSrc - it->first;
        if (off < it->second.size())
        {
            const size_t n = std::min<size_t>(size, it->second.size() - off);
            s.spu.writeRam(spuDst, it->second.data() + off, n);
        }
    }
    if (id != 0u && s.handler != 0u)
    {
        const uint32_t at = kDonePacketBase + 0x20u * (s.doneRing++ & 15u);
        writePacket(rdram, at, 2u, id);
        if (out != nullptr)
            *out = makeHandlerCall(s, at);
        ++s.done;
        return true;
    }
    return false;
}

// Savestate providers (runtime/game_module.h `sndSaveState`/`sndLoadState`).
// Moved verbatim from `ps2_savestate.cpp`'s `SndSavestate`, which becomes a
// thin forwarder; the section name, version and registration stay with the
// runtime until family B moves them. Serialized bytes are unchanged.
inline void saveSndState(ps2_savestate::Writer &w)
{
    State &s = state();
    std::lock_guard<std::mutex> lock(s.mutex);
    for (uint32_t v : {s.handler, s.handlerData, s.handlerGp, s.statusAddr, s.serial})
        w.u32(v);
    for (uint64_t v : {s.ticks, s.cid0, s.dmq, s.done, s.setdma, s.tagbufs})
        w.u64(v);
    w.u32(s.doneRing);
    w.u64(s.iopMem.size());
    for (const auto &[addr, bytes] : s.iopMem)
    {
        w.u32(addr);
        w.blob(bytes);
    }
    w.blob(s.spu.m_ram);
    w.pod(s.spu.m_voices);
    w.u64(s.spu.m_uploads);
    w.u64(s.spu.m_uploadBytes);
    w.pod(s.driver.m_word);
    w.pod(s.driver.m_statusWord);
    w.pod(s.driver.m_statusNax);
    w.pod(s.driver.m_statusAddr);
    w.pod(s.driver.m_statusTriggered);
    w.pod(s.driver.m_pitch);
    w.pod(s.driver.m_vol);
    w.u64(s.driver.m_keyOns);
    w.pod(s.upLeft.m_history);
    w.pod(s.upRight.m_history);
}

inline bool loadSndState(ps2_savestate::Reader &r)
{
    State &s = state();
    std::lock_guard<std::mutex> lock(s.mutex);
    for (uint32_t *v : {&s.handler, &s.handlerData, &s.handlerGp, &s.statusAddr, &s.serial})
        *v = r.u32();
    for (uint64_t *v : {&s.ticks, &s.cid0, &s.dmq, &s.done, &s.setdma, &s.tagbufs})
        *v = r.u64();
    s.doneRing = r.u32();
    s.iopMem.clear();
    const uint64_t n = r.count(1u << 20);
    for (uint64_t i = 0; i < n && r.ok(); ++i)
    {
        const uint32_t addr = r.u32();
        s.iopMem[addr] = r.blob();
    }
    std::vector<uint8_t> ram = r.blob();
    if (ram.size() != s.spu.m_ram.size())
        return r.fail("SPU RAM size mismatch");
    s.spu.m_ram = std::move(ram);
    r.pod(s.spu.m_voices);
    s.spu.m_uploads = r.u64();
    s.spu.m_uploadBytes = r.u64();
    r.pod(s.driver.m_word);
    r.pod(s.driver.m_statusWord);
    r.pod(s.driver.m_statusNax);
    r.pod(s.driver.m_statusAddr);
    r.pod(s.driver.m_statusTriggered);
    r.pod(s.driver.m_pitch);
    r.pod(s.driver.m_vol);
    s.driver.m_keyOns = r.u64();
    r.pod(s.upLeft.m_history);
    r.pod(s.upRight.m_history);
    return r.ok();
}

} // namespace ps2_snd_spike
