#pragma once

// Wrap the guest SET0001 serializer: restore this user's pre-Tricky
// records/settings before the guest computes its checksum. Stock and
// exact paths chain without changing guest RAM.

#include <cstddef>
#include <cstdint>

class PS2Runtime;

namespace ps2_ssx3_tricky_records_patch
{

void applyTrickyRecordsPatch(PS2Runtime &runtime);

} // namespace ps2_ssx3_tricky_records_patch
