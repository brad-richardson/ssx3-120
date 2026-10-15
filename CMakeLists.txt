cmake_minimum_required(VERSION 3.22)
project(ssx3-120 NONE)

# Product composition (repo-split-2 step 2). The runtime stays SSX-aware
# inside the pinned engine for now; this file records the composition and
# validates pins. Host runner/Android builds are driven by build.py, which
# configures deps/PS2Recomp directly with the user's external work dir.
# The ssx3_game_module static target and engine hook contract (step 4+)
# attach here once module-seam-1's seams land upstream.

list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/cmake")
include(EnginePin)

ssx3_check_engine_pin("${CMAKE_CURRENT_SOURCE_DIR}/deps/PS2Recomp")

message(STATUS "ssx3-120 product root: ${CMAKE_CURRENT_SOURCE_DIR}")
message(STATUS "ssx3-120 engine: deps/PS2Recomp (see deps.lock.json)")
