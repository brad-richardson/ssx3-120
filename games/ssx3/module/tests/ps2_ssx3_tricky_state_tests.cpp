#include "MiniTest.h"
#include "Ssx3TrickyState.h"
#include "ps2_ssx3_file_table.h"
#include "ps2_ssx3_tricky_layer.h"
#include "ps2_ssx3_tricky_list.h"
#include "ps2_ssx3_tricky_records.h"
#include "runtime/ps2_savestate.h"

void register_ps2_ssx3_tricky_state_tests()
{
    MiniTest::Case("SSX3 Tricky course save ownership", [](TestCase &tc) {
        tc.Run("mode-on save restores overlay IDs and true-stock donor snapshots in a fresh timeline", [](TestCase &t) {
            namespace ft = ps2_ssx3_file_table;
            namespace list = ps2_ssx3_tricky_list;
            const auto oldFiles = ft::state();
            const auto oldMap = list::mapState();
            const auto oldModes = ps2_ssx3_course::courseModes();
            const auto oldMenu = ps2_ssx3_tricky::state();
            const auto oldGems = ps2_tk45c::state();
            const auto oldRecords = ps2_ssx3_tricky_records::state();
            const auto oldAliases = ps2_cd_overlay::pendingAliases();
            const bool oldDormant = ps2_cd_overlay::directoryDormant();
            const uint32_t oldVersion = ps2_savestate::loadingSectionVersion();
            auto *oldOverlay = ps2_cd_overlay::runtimeOverlay();
            ps2_savestate::setLoadingSectionVersion(3);
            ps2_tk45c::state() = {};
            auto &records = ps2_ssx3_tricky_records::state();
            records = {}; records.have = true; records.wasInGame = true;
            records.records.fill(0x6d); records.settings.fill(0xa7); records.flags.fill(0x17b);
            std::vector<uint8_t> ram(32u * 1024u * 1024u, 0);
            constexpr uint32_t base = 0x100000, stride = 268;
            ft::write32(ram.data() + ft::kBase, base);
            ft::write32(ram.data() + ft::kCount, 2);
            ft::write32(ram.data() + ft::kNameBytes, 256);
            ft::write32(ram.data() + ft::kSeed, 0x1505);
            ft::write32(ram.data() + base, 0x7fffffffu);
            ft::write32(ram.data() + base + stride, 0x80000000u);
            const auto stock = ram;
            ps2_cd_overlay::Overlay overlay;
            ps2_cd_overlay::OverlayFile file;
            file.isoPath = "/DATA/WORLDS/GARI.BIG"; file.lbn = 1000; file.sizeBytes = 5000;
            overlay.files.push_back(file);
            ps2_cd_overlay::publishRuntimeOverlay(&overlay);
            ft::reset(true);
            auto &modes = ps2_ssx3_course::courseModes();
            modes = {}; modes.armed = true; modes.modes.resize(1); modes.modes[0].name = "Tricky";
            std::string msg;
            t.Equals(ps2_ssx3_course::modeApply(modes, ram.data(), 1, msg), 1, "engage mode");
            ps2_ssx3_tricky::state().tricky = true;
            auto &map = list::mapState();
            map = {}; map.snapHave[14] = true; map.activeEvent = 14;
            std::strcpy(map.snapArchive[14], "BAM"); std::strcpy(map.snapName[14], "Happiness");
            ps2_savestate::Writer saved;
            ps2_tricky_state::trickyGemsSave(saved);
            records = {};
            // A newly booted process has no host ownership image; another
            // timeline may also hold a contaminated donor snapshot.
            ft::reset(true);
            ps2_cd_overlay::runtimeOverlay() = nullptr; // fresh process: no CD read has published it yet
            map = {}; map.snapHave[14] = true;
            std::strcpy(map.snapArchive[14], "ELYS"); std::strcpy(map.snapName[14], "Wrong timeline");
            ps2_ssx3_tricky::state().tricky = false;
            modes.owner = 0;
            ps2_savestate::Writer beforeValidation;
            ps2_tricky_state::trickyGemsSave(beforeValidation);
            ps2_savestate::Reader validator(saved.buf.data(), saved.buf.size());
            validator.setValidationOnly(true);
            t.IsTrue(ps2_tricky_state::trickyGemsLoad(validator), validator.error());
            ps2_savestate::Writer afterValidation;
            ps2_tricky_state::trickyGemsSave(afterValidation);
            t.IsTrue(beforeValidation.buf == afterValidation.buf, "preflight validation mutates no owner");
            t.IsTrue(ps2_cd_overlay::runtimeOverlay() == nullptr, "preflight does not publish the lazy overlay");
            ps2_savestate::Reader reader(saved.buf.data(), saved.buf.size());
            t.IsTrue(ps2_tricky_state::trickyGemsLoad(reader), reader.error());
            t.IsTrue(records.have && records.wasInGame && !records.menusClean, "records snapshot lifetime restored");
            t.Equals(records.records[700], uint8_t(0x6d), "saved user record restored");
            t.Equals(records.settings[20], uint8_t(0xa7), "saved user setting restored");
            t.Equals(records.flags[5], uint16_t(0x17b), "saved profile flag restored");
            t.Equals(modes.owner, 1u, "explicit mode identity restored independently of donor rows");
            t.IsTrue(ft::state().active, "active overlay ownership survived the fresh-process load");
            t.Equals(ft::state().stock.size(), size_t(500 * stride), "true-stock restore image saved");
            t.Equals(std::string(map.snapArchive[14]), std::string("BAM"), "donor snapshot belongs to saved timeline");
            t.Equals(std::string(map.snapName[14]), std::string("Happiness"), "stock donor name restored");
            t.Equals(map.activeEvent, 14, "mapped donor ownership restored");
            // An unpublished overlay still rejects malformed and duplicate
            // saved IDs, without changing ownership during failed preflight.
            const auto loadedFiles = ft::state();
            for (const std::string &invalid : std::vector<std::string>{"/DATA/../GARI.BIG", "/data/GARI.BIG", "/DATA//GARI.BIG", std::string("/DATA/\0GARI", 11)})
            {
                ft::state().names[0] = invalid;
                ps2_savestate::Writer malformedNames; ps2_tricky_state::trickyGemsSave(malformedNames);
                ps2_savestate::Reader invalidNames(malformedNames.buf.data(), malformedNames.buf.size());
                invalidNames.setValidationOnly(true);
                t.IsFalse(ps2_tricky_state::trickyGemsLoad(invalidNames), "malformed unpublished overlay ID rejected");
                ft::state() = loadedFiles;
            }
            ft::state().names.push_back(ft::state().names[0]);
            ps2_savestate::Writer duplicates; ps2_tricky_state::trickyGemsSave(duplicates);
            ps2_savestate::Reader duplicateNames(duplicates.buf.data(), duplicates.buf.size());
            duplicateNames.setValidationOnly(true);
            t.IsFalse(ps2_tricky_state::trickyGemsLoad(duplicateNames), "duplicate unpublished overlay IDs rejected");
            ft::state() = loadedFiles;
            ps2_cd_overlay::publishRuntimeOverlay(&overlay); // first later CD read
            overlay.files[0].isoPath = "/DATA/WORLDS/ELYS.BIG";
            ps2_savestate::Reader wrongOrder(saved.buf.data(), saved.buf.size()); wrongOrder.setValidationOnly(true);
            t.IsFalse(ps2_tricky_state::trickyGemsLoad(wrongOrder), "published overlay still requires exact ID order");
            overlay.files.push_back(file);
            ps2_savestate::Reader wrongCount(saved.buf.data(), saved.buf.size()); wrongCount.setValidationOnly(true);
            t.IsFalse(ps2_tricky_state::trickyGemsLoad(wrongCount), "published overlay still requires exact ID count");
            overlay.files = {file};
            t.IsTrue(ft::transition(ram.data(), ram.size(), false, msg), msg);
            t.IsTrue(std::memcmp(ram.data() + base, stock.data() + base, 500 * stride) == 0,
                     "mode-off after load restores every file-table byte");
            modes.owner = 0; ps2_ssx3_tricky::state().tricky = false; map.activeEvent = -1;
            ps2_savestate::Writer stockSave;
            ps2_tricky_state::trickyGemsSave(stockSave);
            t.Equals(ps2_ssx3_course::modeApply(modes, ram.data(), 1, msg), 1, "visit another timeline");
            ps2_ssx3_tricky::state().tricky = true;
            std::strcpy(map.snapArchive[14], "ELYS");
            ram = stock; // the core memory section restores stock RAM first
            ps2_savestate::Reader stockReader(stockSave.buf.data(), stockSave.buf.size());
            t.IsTrue(ps2_tricky_state::trickyGemsLoad(stockReader), stockReader.error());
            t.IsFalse(ft::state().active, "loading stock clears a later timeline's file owner");
            t.Equals(modes.owner, 0u, "loading stock clears mode owner");
            t.Equals(std::string(map.snapArchive[14]), std::string("BAM"), "stock save clears later donor contamination");

            // v2 appends ownership after the unchanged v1 gem payload.
            const size_t legacyBytes = 5 + 9 + sizeof(ps2_tk45c::state().prev) + 12 + 4 + 8 + 8 +
                sizeof(ps2_tk45c::state().collected) + sizeof(ps2_tk45c::state().spinSeen);
            auto malformed = saved.buf;
            ft::write32(malformed.data() + legacyBytes + 1, 0xffffffffu);
            ps2_savestate::Reader bad(malformed.data(), malformed.size()); bad.setValidationOnly(true);
            t.IsFalse(ps2_tricky_state::trickyGemsLoad(bad), "invalid owner rejected before mutation");
            t.Equals(modes.owner, 0u, "failed validation leaves current timeline untouched");
            std::vector<uint8_t> legacy(saved.buf.begin(), saved.buf.begin() + legacyBytes);
            ps2_savestate::setLoadingSectionVersion(1);
            ps2_savestate::Reader unsafeLegacy(legacy.data(), legacy.size());
            t.IsFalse(ps2_tricky_state::trickyGemsLoad(unsafeLegacy), "legacy active-layer state refuses missing ownership");
            modes = {}; ft::reset(false);
            const char *menuEnv = std::getenv("PS2X_SSX3_TRICKY_MENU");
            const bool hadMenuEnv = menuEnv != nullptr; const std::string oldMenuEnv = menuEnv ? menuEnv : "";
            unsetenv("PS2X_SSX3_TRICKY_MENU");
            ps2_savestate::Reader safeLegacy(legacy.data(), legacy.size());
            t.IsTrue(ps2_tricky_state::trickyGemsLoad(safeLegacy), safeLegacy.error());
            t.IsTrue(safeLegacy.atEnd(), "legacy payload fully consumed");
            t.Equals(map.activeEvent, -1, "legacy stock load clears mapper timeline");
            if (hadMenuEnv) setenv("PS2X_SSX3_TRICKY_MENU", oldMenuEnv.c_str(), 1);
            ps2_savestate::setLoadingSectionVersion(oldVersion);
            records = oldRecords;
            ft::state() = oldFiles; list::mapState() = oldMap;
            modes = oldModes; ps2_ssx3_tricky::state() = oldMenu; ps2_tk45c::state() = oldGems;
            ps2_cd_overlay::runtimeOverlay() = oldOverlay;
            ps2_cd_overlay::setDirectoryDormant(oldDormant);
            ps2_cd_overlay::setModeAliases(oldAliases);
        });
    });
}
