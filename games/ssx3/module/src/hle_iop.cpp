// module-seam-2: moved verbatim from ps2xRuntime/src/lib/ps2_runtime.cpp
// (fork c71407e9). The runtime keeps the mode (PS2Runtime::setHleIopMode);
// the module owns the choice for SSX 3.
#include "ssx3_module_internal.h"

#include "ps2_runtime.h"

// UPR1: SSX 3 runs on the fork's HLE IOP/SIF/RPC paths, never upstream's IOP
// emulator (its IRX modules stay tracked-only loads).
// Registered by the module table (ssx3_module.cpp) as "ssx3-hle-iop".
void ps2_ssx3_hle_iop::applySsx3HleIop(PS2Runtime &runtime)
{
    runtime.setHleIopMode(true);
}
