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
        });
        tc.Run("Online Main return takes Logoff whatever dismissed it", [](TestCase &t)
               {
            // P17/P22: the accept SHOWS the stock Online Main again. The
            // dismiss caller (0x1a9618 vs 0x1d51dc), the ignored second arg
            // and the filter's parent all vary run to run: only the shown
            // state matching entry's covered state counts.
            constexpr uint32_t kOnlineMain = 0xb853a0u;
            t.IsTrue(qpOnlineMainReturnTakesLogoff(kOnlineMain, true, kOnlineMain),
                     "reshown Online Main must take Logoff");
            t.IsFalse(qpOnlineMainReturnTakesLogoff(0xbc4240u, true, kOnlineMain),
                      "entry switch (filter show) stays stock");
            t.IsFalse(qpOnlineMainReturnTakesLogoff(kOnlineMain, false, kOnlineMain),
                      "reshown Online Main off-stage stays stock");
            t.IsFalse(qpOnlineMainReturnTakesLogoff(kOnlineMain, true, 0u),
                      "no recorded entry state stays stock");
            t.IsFalse(qpOnlineMainReturnTakesLogoff(kOnlineMain, true, 0xbc3b60u),
                      "a different recorded state stays stock");
            t.IsFalse(qpOnlineMainReturnTakesLogoff(0u, true, kOnlineMain),
                      "null shown state stays stock");
        });
        tc.Run("silent-strand fresh show takes one Logoff attempt", [](TestCase &t)
               {
            // P34: pair torn with no dialog; the strand's Online Main is a
            // fresh live object. All guards true -> attempt exactly once.
            t.IsTrue(qpStrandLogoffReady(true, true, false, true, true, false, false),
                     "fresh live non-pair show with dead pair must attempt");
            t.IsFalse(qpStrandLogoffReady(true, true, false, true, true, false, true),
                      "an evaluated show must not attempt again");
            t.IsFalse(qpStrandLogoffReady(true, false, false, true, true, false, false),
                      "live pair must stay stock");
            t.IsFalse(qpStrandLogoffReady(true, true, true, true, true, false, false),
                      "dialog up must stay stock (never preempt: P24)");
            t.IsFalse(qpStrandLogoffReady(true, true, false, false, true, false, false),
                      "stale show must stay stock");
            t.IsFalse(qpStrandLogoffReady(true, true, false, true, false, false, false),
                      "dead shown object must stay stock (P27)");
            t.IsFalse(qpStrandLogoffReady(true, true, false, true, true, true, false),
                      "own pair show must stay stock");
            t.IsFalse(qpStrandLogoffReady(false, true, false, true, true, false, false),
                      "off-stage must stay stock");
        });
        tc.Run("dialog-face strand with live pair takes one Logoff attempt", [](TestCase &t)
               {
            // P42: post-accept strand on the entry object, pair still live
            // (eval D+F+L, P=0). Fire the Part-1 shape on OUR pair, never on
            // the strand object and never while the dialog is up.
            t.IsTrue(qpStrandLiveLogoffReady(true, true, false, true, false),
                     "live pair on fresh strand show must attempt");
            t.IsFalse(qpStrandLiveLogoffReady(true, true, false, true, true),
                      "an attempted strand must not attempt again");
            t.IsFalse(qpStrandLiveLogoffReady(true, false, false, true, false),
                      "dead pair stays stock here (gate A owns it)");
            t.IsFalse(qpStrandLiveLogoffReady(true, true, true, true, false),
                      "dialog up must stay stock (never preempt: P24)");
            t.IsFalse(qpStrandLiveLogoffReady(true, true, false, false, false),
                      "stale strand show must stay stock");
            t.IsFalse(qpStrandLiveLogoffReady(false, true, false, true, false),
                      "off-stage must stay stock");
        }); });
}
