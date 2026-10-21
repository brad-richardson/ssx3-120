#include "MiniTest.h"
#include "ps2_fh1_full120.h"
#include "runtime/ps2_savestate.h"
void ps2_fh1_linkSavestateSection();
void register_ps2_fh1_life2_tests()
{
    using namespace ps2_fh1;
    MiniTest::Case("Ps2Fh1Life2",[](TestCase &tc){
        tc.Run("opt-in and dispatch in both menu and event phases",[](TestCase &t){
            t.Equals(allMain()&kFixLife2,uint64_t{0},"outside all");
            t.Equals(parseFix("all,life2,-life2").main,allMain(),"off");
            t.Equals(parseFix("all,life2").main,allMain()|kFixLife2,"on");
            HookConfig c;c.mode=Mode::Events;c.main=kFixLife2;
            for(bool active:{false,true}) {c.guestActive=active;auto h=buildHookInterest(c);
                for(auto pc:kLife2Targets)t.IsTrue(hookTableHit(h,1u,pc),"target");
                for(auto pc:kLife2Sources)t.IsTrue(hookTableHit(h,pc,1u),"snapshot writer");}
        });
        tc.Run("live freshness and forced expiry preserve units across flips",[](TestCase &t){
            std::vector<uint8_t> ram(PS2_RAM_SIZE);Life2World w;w.object=0x1000;
            wr32(ram.data(),0x13ec,1);wr32(ram.data(),0x2bf0,6);wr32(ram.data(),0x13f0,3);wr32(ram.data(),0x1400,96);
            wr32(ram.data(),0x1404,77);life2FlipWorld(ram.data(),w,100,true);
            uint32_t x=0;rd32(ram.data(),0x1400,x);t.Equals(x,92u,"four stock ticks become eight 120 ticks");
            rd32(ram.data(),0x2bf0,x);t.Equals(x,12u,"100ms threshold");
            rd32(ram.data(),0x1404,x);t.Equals(x,77u,"render safety wait untouched");
            life2FlipWorld(ram.data(),w,101,false);rd32(ram.data(),0x1400,x);t.Equals(x,97u,"nine half ticks floor to four stock ticks");
            t.Equals(w.half[0],uint8_t{1},"half survives");
            life2FlipWorld(ram.data(),w,103,true);rd32(ram.data(),0x1400,x);t.Equals(103u-x,13u,"half restored after stock time passed");
            // Actual forced writer stores update-threshold, and clears carry.
            wr32(ram.data(),0x1400,103u-12u);w.half[0]=0u;
            life2FlipWorld(ram.data(),w,103,false);rd32(ram.data(),0x1400,x);t.Equals(103u-x,6u,"still immediately expired");
        });
        tc.Run("birth at 120, signed/wrapped age, and pointer reuse",[](TestCase &t){
            std::vector<uint8_t> ram(PS2_RAM_SIZE);Life2World w;w.object=0x1000;
            wr32(ram.data(),0x13ec,1);wr32(ram.data(),0x2bf0,12);wr32(ram.data(),0x13f0,1);wr32(ram.data(),0x1400,0xfffffffeu);
            life2FlipWorld(ram.data(),w,3,false);uint32_t x=0;rd32(ram.data(),0x1400,x);t.Equals(x,1u,"wrapped five half ticks");
            life2FlipWorld(ram.data(),w,3,true);rd32(ram.data(),0x1400,x);t.Equals(x,0xfffffffeu,"exact flip round trip");
            g_life2Worlds={};auto *a=life2World(0x1000,true);a->half[0]=1;*a={};
            auto *b=life2World(0x1000,true);t.Equals(b->half[0],uint8_t{0},"new lifetime");g_life2Worlds={};
        });
        tc.Run("input epochs distinguish stock, 120 offset and mixed rates",[](TestCase &t){
            g_life2Anchors.clear();life2Remember(1,8400,8400);life2Remember(2,8400,16799);life2Remember(3,8400,12000);
            uint32_t x=0;for(auto input:{1u,2u,3u}){t.IsTrue(life2Ordinal(input,8400,x),"owned");t.Equals(x,input==1?8400u:input==2?16799u:12000u,"exact input ordinal");}
            t.IsTrue(life2Ordinal(2,0,x),"zero");t.Equals(x,0u,"first boundary");life2ForgetInput(2);t.IsFalse(life2Ordinal(2,8400,x),"new recording forgets old epoch");g_life2Anchors.clear();
        });
        tc.Run("serialized replay anchors and fractional stream ages round trip",[](TestCase &t){
            ps2_fh1_linkSavestateSection();g_life2Worlds={};g_life2Worlds[0].object=0x1000;g_life2Worlds[0].half[3]=1;g_life2Worlds[0].stamps[3]=42;
            g_life2Anchors={{2,8400,16799}};auto &s=ps2_savestate::registeredSections().at("life2");ps2_savestate::Writer w;s.save(w);
            g_life2Worlds={};g_life2Anchors.clear();ps2_savestate::Reader r(w.buf.data(),w.buf.size());t.IsTrue(s.load(r)&&r.ok()&&r.atEnd(),"loads");
            t.Equals(g_life2Worlds[0].half[3],uint8_t{1},"fraction");uint32_t x=0;t.IsTrue(life2Ordinal(2,8400,x),"recorded epoch");t.Equals(x,16799u,"offset preserved");
            g_life2Worlds={};g_life2Anchors.clear();
        });
    });
}
