// User-specific records quarantine. See
// Ssx3TrickyRecordsPatch.h and ps2_ssx3_tricky_records.h.
#include "Ssx3TrickyRecordsPatch.h"

#include "ps2_ssx3_tricky_records.h"
#include "ps2_runtime.h"
#include "runtime/ps2_memory.h"

#include <cstdio>
#include <cstring>

namespace
{
// SLUS_207.72 SET0001 serializer, before it gathers records/settings
// and computes the image CRC. Stock/exact calls chain without RAM writes.
constexpr uint32_t kSerializePc = 0x00152758u;
constexpr uint32_t kSerializeW0 = 0x27bdffa0u;
constexpr uint32_t kSerializeW1 = 0x7fb00050u;
PS2Runtime::RecompiledFunction g_serializeOrig = nullptr;

uint32_t rd32(const uint8_t *ram, uint32_t va)
{
    uint32_t v = 0;
    std::memcpy(&v, ram + va, 4);
    return v;
}

void serializeWrap(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
{
    namespace records = ps2_ssx3_tricky_records;
    if (!records::exactPath() && records::restore(records::state(), rdram, PS2_RAM_SIZE))
        std::fprintf(stderr, "[ssx3-tricky-records] restored user's pre-race records/settings before CRC\n");
    g_serializeOrig(rdram, ctx, runtime);
}
} // namespace

namespace ps2_ssx3_tricky_records_patch
{

void applyTrickyRecordsPatch(PS2Runtime &runtime)
{
    const uint8_t *ram = runtime.memory().getRDRAM();
    if (!ram)
    {
        std::fprintf(stderr, "[ssx3-tricky-records] refused: no RAM\n");
        return;
    }
    if (rd32(ram, kSerializePc) != kSerializeW0 || rd32(ram, kSerializePc + 4u) != kSerializeW1)
    {
        std::fprintf(stderr, "[ssx3-tricky-records] refused: 0x%x entry mismatch; nothing wrapped\n",
                     kSerializePc);
        return;
    }
    if (!(g_serializeOrig = runtime.lookupFunction(kSerializePc)) ||
        !runtime.replaceFunction(kSerializePc, &serializeWrap))
    {
        std::fprintf(stderr, "[ssx3-tricky-records] cannot wrap 0x%x\n", kSerializePc);
        std::abort();
    }
    std::fprintf(stderr, "[ssx3-tricky-records] armed: records serializer guard at 0x%x\n", kSerializePc);
}

} // namespace ps2_ssx3_tricky_records_patch
