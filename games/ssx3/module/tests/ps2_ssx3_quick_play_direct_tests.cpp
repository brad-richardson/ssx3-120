#include "MiniTest.h"
#include "Ssx3QuickPlayDirect.h"

// qp-cancel-1: Back (event 6) on our Quick Match filter takes the Logoff path
// straight to a fresh Main Menu — idle or searching. The search-in-progress
// flag is deliberately not an input: stock Back on a searching filter closes
// into the hidden Online Main (black gap, "menus gone") and then the
// disconnect-confirm trench.
namespace
{
using namespace ps2_quick_play_direct;
constexpr uint32_t kBack = 6u;
} // namespace

void register_ps2_ssx3_quick_play_direct_tests()
{
    MiniTest::Case("Ps2Ssx3QuickPlayDirect", [](TestCase &tc)
                   {
        tc.Run("idle Back on our ready filter takes Logoff", [](TestCase &t)
               {
            t.IsTrue(qpBackTakesLogoff(kBack, true, true),
                     "event=6, ready, ours must take Logoff");
        });
        tc.Run("searching Back on our ready filter takes Logoff", [](TestCase &t)
               {
            // The +0x6ec searching flag must not route to stock teardown.
            t.IsTrue(qpBackTakesLogoff(kBack, true, true),
                     "searching Back must take the same Logoff path as idle");
        });
        tc.Run("non-Back events stay stock", [](TestCase &t)
               {
            t.IsFalse(qpBackTakesLogoff(5u, true, true), "accept (5) stays stock");
            t.IsFalse(qpBackTakesLogoff(0u, true, true), "event 0 stays stock");
            t.IsFalse(qpBackTakesLogoff(2u, true, true), "event 2 stays stock");
        });
        tc.Run("foreign filters and non-ready stages stay stock", [](TestCase &t)
               {
            t.IsFalse(qpBackTakesLogoff(kBack, true, false),
                      "Back on a foreign filter stays stock");
            t.IsFalse(qpBackTakesLogoff(kBack, false, true),
                      "Back outside the ready stage stays stock");
            t.IsFalse(qpBackTakesLogoff(kBack, false, false),
                      "Back on foreign filter off-stage stays stock");
        }); });
}
