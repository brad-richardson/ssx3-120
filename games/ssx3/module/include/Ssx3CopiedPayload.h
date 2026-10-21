#ifndef PS2_SSX3_COPIED_PAYLOAD_H
#define PS2_SSX3_COPIED_PAYLOAD_H

#include <cstdint>

struct R5900Context;

namespace ps2_syscalls
{
// K1: SSX3's 0x42CBD0 installer copies a 0x330-byte syscall payload (five
// TLB helpers plus a 6-entry key/value lookup) from ELF 0x4561C0 to
// 0x80075000, then installs the copy as the GetEntryAddress (0x5B)
// handler. Copied bytes have no function-table entry, so the override
// dispatcher cannot invoke them; this module serves a provenance-checked
// HLE equivalent instead. The copy-integrity gate (RDRAM memcmp of the
// guest's own source vs destination ranges) must pass before any result
// is served, so no game code bytes are baked into the runtime.
bool tryDispatchSsx3CopiedPayload(uint32_t syscallNumber, uint32_t handler, uint8_t *rdram, R5900Context *ctx);

// Flag-gated install census row (no-op unless the quirk is armed).
void noteSsx3CopiedPayloadInstall(uint32_t syscallIndex, uint32_t handler);

bool ssx3CopiedPayloadEnabled();
void resetSsx3CopiedPayloadForTesting();
} // namespace ps2_syscalls

#endif // PS2_SSX3_COPIED_PAYLOAD_H
