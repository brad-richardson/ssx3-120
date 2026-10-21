// snd-seam-1 family #8: Tricky-state persistence owned by the module.
// The "trickygems" section (name, version, registration) lives here in the
// K1 static-registration pattern, and the TK34 patch-grow kernel-section
// field is provided here (save/validate/load at the exact field position).
// Course ownership was added in section v2 (preserve-fix-1); v1 remains
// loadable with the Tricky layer off. Without a module the gems section is absent and the kernel
// patch-grow field fails closed (a no-module binary never round-trips
// game state; the suites prove it).
#include "Ssx3TrickyState.h"

#include "ps2_ssx3_patch_grow_state.h"
#include "ps2_ssx3_tricky_gems.h"
#include "ps2_ssx3_tricky_records.h"
#include "ps2_ssx3_tricky_layer.h"
#include "runtime/ps2_memory.h"
#include "runtime/ps2_savestate.h"

#include <algorithm>
#include <cmath>
#include <cstdint>

namespace
{
using ps2_savestate::Reader;
using ps2_savestate::Writer;

void courseSave(Writer &w)
{
    const auto &modes = ps2_ssx3_course::courseModes();
    const auto &menu = ps2_ssx3_tricky::state();
    const auto &files = ps2_ssx3_file_table::state();
    const auto &map = ps2_ssx3_tricky_list::mapState();
    w.b(modes.armed); w.u32(modes.owner);
    w.b(menu.tricky); w.u32(menu.mapController); w.u64(menu.mapEntries);
    w.b(files.enabled); w.b(files.active);
    w.u32(files.base); w.u32(files.stride); w.u32(files.seed); w.u32(files.count);
    w.blob(files.stock); w.u32(static_cast<uint32_t>(files.names.size()));
    for (const auto &name : files.names) w.str(name);
    for (bool have : map.snapHave) w.b(have);
    w.bytes(map.snapArchive, sizeof(map.snapArchive));
    w.bytes(map.snapName, sizeof(map.snapName));
    w.u32(static_cast<uint32_t>(map.activeEvent));
}

bool courseLoad(Reader &r)
{
    namespace ft = ps2_ssx3_file_table;
    namespace list = ps2_ssx3_tricky_list;
    auto &modes = ps2_ssx3_course::courseModes();
    auto &menu = ps2_ssx3_tricky::state();
    if (ps2_savestate::loadingSectionVersion() == 1)
    {
        // A legacy Tricky save has no restore image or mapper owner. Do
        // not infer either from possibly substituted donor rows.
        if (modes.armed || ft::state().enabled || ps2_ssx3_tricky::knob())
            return r.fail("trickycourse: legacy Tricky ownership cannot be recovered");
        if (!r.validationOnly())
        {
            ft::state() = {}; list::mapState() = {}; modes.owner = 0;
            menu.tricky = false; menu.mapController = 0; menu.mapEntries = 0;
        }
        return true;
    }
    const bool armed = r.b(); const uint32_t owner = r.u32();
    const bool tricky = r.b(); const uint32_t controller = r.u32(); const uint64_t entries = r.u64();
    ft::State files;
    files.enabled = r.b(); files.active = r.b();
    files.base = r.u32(); files.stride = r.u32(); files.seed = r.u32(); files.count = r.u32();
    const auto bytes = r.count(ft::kCapacity * 268u);
    files.stock.resize(static_cast<size_t>(bytes));
    r.bytes(files.stock.data(), files.stock.size());
    const uint32_t names = r.u32();
    if (names > ft::kCapacity) return r.fail("trickycourse: too many overlay IDs");
    for (uint32_t i = 0; i < names; ++i)
    {
        const auto n = r.count(255);
        std::string name(static_cast<size_t>(n), '\0');
        r.bytes(name.data(), name.size()); files.names.push_back(std::move(name));
    }
    list::MapState map;
    for (bool &have : map.snapHave) have = r.b();
    r.bytes(map.snapArchive, sizeof(map.snapArchive));
    r.bytes(map.snapName, sizeof(map.snapName));
    const uint32_t active = r.u32();
    if (!r.ok()) return false;
    if (armed != modes.armed || owner > modes.modes.size() || (!armed && owner) ||
        (armed && tricky != (owner != 0)) || controller >= PS2_RAM_SIZE || (controller & 3u) ||
        files.enabled != ft::state().enabled || (files.active && (!files.enabled || !owner)))
        return r.fail("trickycourse: incompatible owner/configuration");
    if (files.stock.empty())
    {
        if (files.active || files.base || files.stride || files.seed || files.count || !files.names.empty())
            return r.fail("trickycourse: missing stock restore image");
    }
    else
    {
        if (!files.enabled || files.stride != 268 || files.stock.size() != ft::kCapacity * 268u ||
            files.base < 0x100000u || (files.base & 3u) ||
            uint64_t(files.base) + files.stock.size() > PS2_RAM_SIZE || !files.count ||
            files.count > ft::kCapacity || files.names.size() > ft::kCapacity - files.count)
            return r.fail("trickycourse: invalid file-table restore layout");
        for (uint32_t i = 1; i < files.count; ++i)
            if (ft::read32(files.stock.data() + (i - 1) * 268) >= ft::read32(files.stock.data() + i * 268))
                return r.fail("trickycourse: stock file IDs not unique/unsigned sorted");
        // Fresh-process preflight precedes the first CD read, which lazily
        // publishes the overlay. The header has already checked the ISO,
        // manifest and overlay content identities. Validate saved IDs without
        // causing a guest read or requiring that later publication to exist.
        std::vector<uint32_t> keys;
        for (uint32_t i = 0; i < files.count; ++i)
            keys.push_back(ft::read32(files.stock.data() + i * 268));
        for (const auto &name : files.names)
        {
            if (name.size() < 2 || name.back() == '/' || name != ft::normalized(name) ||
                name.find("//") != std::string::npos || name.find("/./") != std::string::npos ||
                name.find("/../") != std::string::npos || name.ends_with("/.") || name.ends_with("/..") ||
                std::any_of(name.begin(), name.end(), [](unsigned char c) { return c < 32 || c >= 127; }))
                return r.fail("trickycourse: invalid overlay ID name");
            const uint32_t key = ft::hash(name, files.seed);
            if (std::find(keys.begin(), keys.end(), key) != keys.end())
                return r.fail("trickycourse: overlay ID key collision");
            keys.push_back(key);
        }
        if (const auto *overlay = ps2_cd_overlay::runtimeOverlay())
        {
            if (files.names.size() != overlay->files.size())
                return r.fail("trickycourse: overlay ID set differs");
            for (size_t i = 0; i < files.names.size(); ++i)
                if (files.names[i] != ft::normalized(overlay->files[i].isoPath))
                    return r.fail("trickycourse: overlay ID order differs");
        }
    }
    if (active != 0xffffffffu && active > list::kMapDonorMax)
        return r.fail("trickycourse: invalid mapped donor");
    map.activeEvent = active == 0xffffffffu ? -1 : static_cast<int>(active);
    if (map.activeEvent >= 0 && !map.snapHave[map.activeEvent])
        return r.fail("trickycourse: mapped donor has no restore image");
    for (int i = 0; i <= list::kMapDonorMax; ++i)
        if (!std::memchr(map.snapArchive[i], 0, list::kMapArchiveLen) ||
            !std::memchr(map.snapName[i], 0, list::kMapNameLen))
            return r.fail("trickycourse: unterminated donor restore field");
    if (!r.validationOnly())
    {
        ft::state() = std::move(files); list::mapState() = map; modes.owner = owner;
        menu.tricky = tricky; menu.mapController = controller; menu.mapEntries = entries;
    }
    return true;
}

void recordsSave(Writer &w)
{
    namespace records = ps2_ssx3_tricky_records;
    const auto &s = records::state();
    w.b(records::exactPath()); w.b(s.have); w.b(s.wasInGame); w.b(s.menusClean);
    w.bytes(s.settings.data(), s.settings.size());
    w.bytes(s.records.data(), s.records.size());
    for (auto flags : s.flags) w.u32(flags);
}

bool recordsLoad(Reader &r)
{
    namespace records = ps2_ssx3_tricky_records;
    records::State next;
    const auto version = ps2_savestate::loadingSectionVersion();
    // Version zero denotes direct calls outside the section loader (tests).
    if (version && version < 3)
    {
        if (!records::exactPath() && (ps2_ssx3_course::courseModes().armed ||
            ps2_ssx3_file_table::state().enabled || ps2_ssx3_tricky::knob()))
            return r.fail("trickyrecords: legacy Tricky save lacks pre-race user snapshot");
    }
    else
    {
        if (r.b() != records::exactPath()) return r.fail("trickyrecords: exact setting differs");
        next.have = r.b(); next.wasInGame = r.b(); next.menusClean = r.b();
        r.bytes(next.settings.data(), next.settings.size());
        r.bytes(next.records.data(), next.records.size());
        for (auto &flags : next.flags)
        {
            const auto value = r.u32();
            if (value > 0x1ffu) return r.fail("trickyrecords: invalid profile flags");
            flags = static_cast<uint16_t>(value);
        }
        if ((!next.have && next.menusClean) || (next.wasInGame && next.menusClean))
            return r.fail("trickyrecords: inconsistent snapshot lifetime");
    }
    if (!r.ok()) return false;
    if (!r.validationOnly()) records::state() = next;
    return true;
}

std::string courseReady()
{
    if (ps2_ssx3_tricky::state().redirect) return "Tricky list refresh pending";
    if (ps2_ssx3_tricky_list::driveProgress().driving) return "Tricky navigator driving";
    return {};
}
} // namespace

namespace ps2_tricky_state
{

void trickyGemsSave(Writer &w)
{
    const auto &s = ps2_tk45c::state();
    w.b(ps2_ssx3_tricky_layer::config().gems);
    w.u32(static_cast<uint32_t>(ps2_ssx3_tricky_layer::config().gems ? ps2_tk45c::table().size() : 0u));
    for (bool b : {s.init, s.wasTricky, s.prevValid, s.scanDone, s.pendingClear, s.rehideArmed,
                   s.raceClock.init, s.raceClock.seen, s.spinLogged}) w.b(b);
    w.bytes(s.prev, sizeof(s.prev));
    w.u32(s.lastR); w.u32(s.lastClock); w.u32(s.scanCursor);
    w.u32(s.raceClock.last); w.u64(s.raceClock.lastTick); w.u64(s.raceClock.adoptUntil);
    w.bytes(s.collected, sizeof(s.collected));
    w.bytes(s.spinSeen, sizeof(s.spinSeen));
    courseSave(w);
    recordsSave(w);
}

bool trickyGemsLoad(Reader &r)
{
    ps2_tk45c::State next{};
    const bool savedEnabled = r.b();
    const uint32_t savedCount = r.u32();
    const bool nowEnabled = ps2_ssx3_tricky_layer::config().gems;
    if (savedEnabled != nowEnabled ||
        savedCount != (nowEnabled ? ps2_tk45c::table().size() : 0u))
        return r.fail("trickygems: mode or table size differs");
    next.init = r.b(); next.wasTricky = r.b(); next.prevValid = r.b();
    next.scanDone = r.b(); next.pendingClear = r.b(); next.rehideArmed = r.b();
    next.raceClock.init = r.b(); next.raceClock.seen = r.b(); next.spinLogged = r.b();
    r.bytes(next.prev, sizeof(next.prev));
    next.lastR = r.u32(); next.lastClock = r.u32(); next.scanCursor = r.u32();
    next.raceClock.last = r.u32(); next.raceClock.lastTick = r.u64();
    next.raceClock.adoptUntil = r.u64();
    r.bytes(next.collected, sizeof(next.collected));
    r.bytes(next.spinSeen, sizeof(next.spinSeen));
    if (!r.ok()) return false;
    if (next.scanCursor > PS2_RAM_SIZE || next.scanCursor % ps2_tk45c::kScanChunk != 0u ||
        next.scanDone != (next.scanCursor == PS2_RAM_SIZE))
        return r.fail("trickygems: invalid scan cursor");
    for (float x : next.prev)
        if (!std::isfinite(x)) return r.fail("trickygems: invalid segment");
    if (!courseLoad(r) || !recordsLoad(r)) return false;
    if (!r.validationOnly())
        ps2_tk45c::state() = next; // derived lookup is rebuilt from restored RAM after all sections.
    return true;
}

void patchGrowSave(ps2_savestate::Writer &w)
{
    const auto growLayout = ps2_ssx3_patch_grow::layout();
    const auto growState = ps2_ssx3_patch_grow::snapshot();
    w.u32(growLayout.base);
    w.u32(growLayout.capacity);
    w.b(growState.active);
    w.b(growState.refused);
    w.u32(growState.cache);
    w.u64(growState.moved.size());
    for (const auto &[to, from] : growState.moved)
    {
        w.u32(to);
        w.u32(from);
    }
}

bool patchGrowValidate(ps2_savestate::Reader &r)
{
    const auto layout = ps2_ssx3_patch_grow::layout();
    const uint32_t base = r.u32(), capacity = r.u32();
    if (base != layout.base || capacity != layout.capacity)
        return r.fail("TK34 grow layout differs");
    ps2_ssx3_patch_grow::State grow;
    grow.active = r.b(); grow.refused = r.b(); grow.cache = r.u32();
    grow.moved.resize(static_cast<size_t>(r.count(8u)));
    for (auto &[to, from] : grow.moved) { to = r.u32(); from = r.u32(); }
    if (!r.ok() || !ps2_ssx3_patch_grow::valid(grow))
        return r.fail("invalid TK34 grow state");
    return r.ok();
}

bool patchGrowLoad(ps2_savestate::Reader &r)
{
    const auto growLayout = ps2_ssx3_patch_grow::layout();
    const uint32_t savedGrowBase = r.u32();
    const uint32_t savedGrowCapacity = r.u32();
    if (savedGrowBase != growLayout.base || savedGrowCapacity != growLayout.capacity)
        return r.fail("TK34 grow layout differs");
    ps2_ssx3_patch_grow::State growState;
    growState.active = r.b();
    growState.refused = r.b();
    growState.cache = r.u32();
    growState.moved.resize(static_cast<size_t>(r.count(8u)));
    for (auto &[to, from] : growState.moved)
    {
        to = r.u32();
        from = r.u32();
    }
    if (!r.ok() || !ps2_ssx3_patch_grow::restore(growState))
        return r.fail("invalid TK34 grow state");
    return true;
}

} // namespace ps2_tricky_state

namespace
{
// K1 pattern: static registration in the family TU (this TU is always
// linked — it defines the module entry — and section order is key-sorted,
// so registration timing is inert). v2 owns course/mapper restore data;
// v3 adds the pre-race user records snapshot; legacy stock saves remain readable.
const bool kTrickyGemsRegistered = ps2_savestate::registerSection(
    "trickygems",
    {3u, &ps2_tricky_state::trickyGemsSave, &ps2_tricky_state::trickyGemsLoad, &courseReady, 1u, false,
     &ps2_tricky_state::trickyGemsLoad});
} // namespace
