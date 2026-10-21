#ifndef PS2_SSX3_SIF_HANDSHAKE_H
#define PS2_SSX3_SIF_HANDSHAKE_H

#include <cstdint>

class PS2Runtime;
struct R5900Context;

namespace ps2_syscalls
{
// P1ac: SSX3's EE<->IOP SIF ready-handshake. The game sends SET_SREG(1,1)
// then spins on sregs[1] (guest 0x52BE04); on hardware the IOP's reply
// arrives via SIF0 DMA and EE set_sreg writes the word, but the HLE has no
// IOP SIF peer (the send is otherwise a no-op). The module completes it
// host-side. Armed by the "ssx3-sif-handshake" override row; the hook below
// is a no-op until then.
void applySsx3SifHandshake(PS2Runtime &runtime);

// Module hook (runtime/game_module.h): runs at the runtime's SendCmd call
// site with the decoded cid, packet address/size and RDRAM.
void onSsx3SifSendCmd(uint32_t cid, uint32_t packetAddr, uint32_t packetSize, uint8_t *rdram);

void resetSsx3SifHandshakeForTesting();

// Private-entry decoder the override binds at guest 0x00426078. Declared in
// the kernel's Syscalls/RPC.h; forward-declared here so the module never
// includes that private header.
void _sceSifSendCmd(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime);
} // namespace ps2_syscalls

#endif // PS2_SSX3_SIF_HANDSHAKE_H
