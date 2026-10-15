// SPDX-License-Identifier: GPL-3.0-or-later
// OM1 P5: minimal host stubs for the static offline subset (Mac test + iOS).
// Everything here is either never-called (abort body: a call would prove the
// subset wrong) or zero-state identical to the dylib (no EE ever runs).
// Host support only; generated recording tables/assembly remain external.
#include <cstdio>
#include <cstdlib>

// hwIntcIrq: the no-JIT path never posts EE interrupts (EMBED compiles the
// inline irq out; the bridge takes completion via VPU_STAT). Abort if hit.
extern "C" void hwIntcIrq(int n)
{
    std::fprintf(stderr, "[om1] fatal: hwIntcIrq(%d) in no-JIT subset\n", n);
    std::abort();
}
