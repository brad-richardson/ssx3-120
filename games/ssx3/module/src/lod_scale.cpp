// snd-seam-1 Part 5 slice 3: draw-distance scaling, moved verbatim from
// ps2xRuntime/src/lib/ps2_runtime.cpp (the `ssx3-lod-scale` override).
// The manifest stays runtime-side (read-only include); the Tricky-scope
// predicate only reads it.

// LOD1: draw distance (PS2X_SSX3_LOD_SCALE=<f>, default off;
// ps2_ssx3_lod.h). The wrapper scales the far argument ($f14) at the entry
// of the renderer's projection setter and calls the original, so an EE
// checkpoint inside the setter resumes as usual. Knob off: nothing is wrapped.
// TLS1: PS2X_SSX3_TRICKY_LOD_SCALE=<f> applies only on Tricky courses and
// wins over LOD_SCALE there.
#include "Ssx3LodScale.h"

#include "ps2_runtime.h"
#include "ps2_ssx3_course_manifest.h"
#include "ps2_ssx3_lod.h"
#include "runtime/ps2_memory.h"

#include <cstdio>
#include <cstdlib>
#include <cstdint>

namespace
{
    PS2Runtime::RecompiledFunction g_lodSetPerspective = nullptr;

    void lodSetPerspectiveWrapper(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        // The Tricky layer's own predicate (tkUberPostWrapper shape): course
        // modes armed and the current mode is not Stock.
        size_t mode = 0u;
        if (rdram != nullptr)
        {
            const ps2_ssx3_course::Modes &ms = ps2_ssx3_course::courseModes();
            mode = ms.armed ? ps2_ssx3_course::modeCurrent(ms, rdram) : 0u;
        }
        const float s = ps2_ssx3_lod::selectScale(ps2_ssx3_lod::scale(), ps2_ssx3_lod::trickyScale(),
                                                 mode != 0u);
        if (s != 0.0f)
        {
            ctx->f[14] *= s;
            // One-shot: proves the Tricky scope applied (and on which mode).
            static bool loggedTrickyApply = false;
            if (!loggedTrickyApply && mode != 0u && ps2_ssx3_lod::trickyScale() != 0.0f &&
                ps2_ssx3_lod::trickyScale() == s)
            {
                loggedTrickyApply = true;
                std::fprintf(stderr, "[ssx3-lod] tricky scale x%g applied (mode %zu)\n",
                             static_cast<double>(s), mode);
            }
        }
        g_lodSetPerspective(rdram, ctx, runtime);
    }
} // namespace

namespace ps2_lod_scale
{

void applyLodScale(PS2Runtime &runtime)
{
    const float s = ps2_ssx3_lod::scale();
    const float t = ps2_ssx3_lod::trickyScale();
    if (s == 0.0f && t == 0.0f)
        return;
    if (s < 0.0f)
    {
        std::fprintf(stderr, "[ssx3-lod] refused PS2X_SSX3_LOD_SCALE=%s (want %g..%g)\n",
                     std::getenv("PS2X_SSX3_LOD_SCALE"), static_cast<double>(ps2_ssx3_lod::kMinScale),
                     static_cast<double>(ps2_ssx3_lod::kMaxScale));
        std::abort();
    }
    if (t < 0.0f)
    {
        std::fprintf(stderr, "[ssx3-lod] refused PS2X_SSX3_TRICKY_LOD_SCALE=%s (want %g..%g)\n",
                     std::getenv("PS2X_SSX3_TRICKY_LOD_SCALE"),
                     static_cast<double>(ps2_ssx3_lod::kMinScale),
                     static_cast<double>(ps2_ssx3_lod::kMaxScale));
        std::abort();
    }
    g_lodSetPerspective = runtime.lookupFunction(ps2_ssx3_lod::kSetPerspective);
    if (!g_lodSetPerspective || !runtime.replaceFunction(ps2_ssx3_lod::kSetPerspective, &lodSetPerspectiveWrapper))
    {
        std::fprintf(stderr, "[ssx3-lod] cannot wrap 0x%x\n", ps2_ssx3_lod::kSetPerspective);
        std::abort();
    }
    if (s != 0.0f && t != 0.0f)
        std::fprintf(stderr,
                     "[ssx3-lod] armed: far plane x%g (x%g on Tricky courses) at 0x%x "
                     "(streaming and fog stock)\n",
                     static_cast<double>(s), static_cast<double>(t), ps2_ssx3_lod::kSetPerspective);
    else if (t != 0.0f)
        std::fprintf(stderr, "[ssx3-lod] armed: far plane x%g on Tricky courses at 0x%x (streaming and fog stock)\n",
                     static_cast<double>(t), ps2_ssx3_lod::kSetPerspective);
    else
        std::fprintf(stderr, "[ssx3-lod] armed: far plane x%g at 0x%x (streaming and fog stock)\n",
                     static_cast<double>(s), ps2_ssx3_lod::kSetPerspective);
}

} // namespace ps2_lod_scale
