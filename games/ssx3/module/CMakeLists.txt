# module-seam-1: the SSX 3 game module (runtime/game_module.h). A static
# library attached to final targets with ps2x_attach_game_module(); the
# runtime never includes these headers. Moved families: README.md.
add_library(ssx3_game_module STATIC
    src/ssx3_module.cpp
    src/fh1_fix_content.cpp
    src/fh1_clock.cpp
    src/fh1_dispatch.cpp
    src/fh1_taps.cpp
    src/anim_pick_guard.cpp
    src/hle_iop.cpp
    src/copied_payload.cpp
    src/input2.cpp
    src/input_marker.cpp
    src/sif_handshake.cpp
    src/tricky_song.cpp
    src/widescreen_default.cpp
    src/tricky_savestate.cpp
    src/movie.cpp
    src/quick_play_direct.cpp
    src/tricky_menu.cpp
    src/tricky_records_patch.cpp
    src/stock_meter.cpp
    src/tricky_intro.cpp
    src/lod_scale.cpp
    src/tricky_layer_tick.cpp
    src/vis_native.cpp
)
target_include_directories(ssx3_game_module
    PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include
    PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/src
    # split-input-1 #3: vis_native reuses the VU vector FMAC core by design
    # (see Ssx3VisNative.h); that header is runtime-private (src/lib/vu).
    PRIVATE ${CMAKE_SOURCE_DIR}/ps2xRuntime/src/lib
)
target_link_libraries(ssx3_game_module PUBLIC ps2_runtime)
set_target_properties(ssx3_game_module PROPERTIES
    PS2X_GAME_MODULE_ENTRY ps2x_ssx3_game_module
    PS2X_GAME_MODULE_HEADER ps2_ssx3_module.h
    # Product suite sources; ps2xTest adds them to ps2x_tests.
    PS2X_GAME_MODULE_TESTS "${CMAKE_CURRENT_SOURCE_DIR}/tests/ps2_ssx3_anim_pick_guard_tests.cpp;${CMAKE_CURRENT_SOURCE_DIR}/tests/ps2_ssx3_module_tests.cpp;${CMAKE_CURRENT_SOURCE_DIR}/tests/ps2_ssx3_tricky_state_tests.cpp;${CMAKE_CURRENT_SOURCE_DIR}/tests/ps2_input_marker_tests.cpp;${CMAKE_CURRENT_SOURCE_DIR}/tests/ps2_ssx3_copied_payload_tests.cpp;${CMAKE_CURRENT_SOURCE_DIR}/tests/ps2_ssx3_sif_handshake_tests.cpp;${CMAKE_CURRENT_SOURCE_DIR}/tests/ps2_snd_tests.cpp;${CMAKE_CURRENT_SOURCE_DIR}/tests/ps2_ssx3_tricky_song_provider_tests.cpp;${CMAKE_CURRENT_SOURCE_DIR}/tests/ps2_ssx3_movie_stub_tests.cpp;${CMAKE_CURRENT_SOURCE_DIR}/tests/ps2_ssx3_vis_native_tests.cpp;${CMAKE_CURRENT_SOURCE_DIR}/tests/ps2_fh1_fix_tests.cpp;${CMAKE_CURRENT_SOURCE_DIR}/tests/ps2_fh1_fh28_tests.cpp;${CMAKE_CURRENT_SOURCE_DIR}/tests/ps2_fh1_life2_tests.cpp;${CMAKE_CURRENT_SOURCE_DIR}/tests/ps2_fh1_savestate_tests.cpp;${CMAKE_CURRENT_SOURCE_DIR}/tests/ps2_fh1_hooktable_tests.cpp;${CMAKE_CURRENT_SOURCE_DIR}/tests/ps2_fh1_flip_tests.cpp"
)

# Same release flags as ps2_runtime, where this code compiled before the move.
include("${CMAKE_SOURCE_DIR}/ps2xRuntime/cmake/ReleaseMode.cmake")
get_property(_ssx3_module_multi_config GLOBAL PROPERTY GENERATOR_IS_MULTI_CONFIG)
if(CMAKE_BUILD_TYPE STREQUAL "Release" OR CMAKE_BUILD_TYPE STREQUAL "RelWithDebInfo" OR _ssx3_module_multi_config)
    EnableFastReleaseMode(ssx3_game_module)
    if(PS2X_DEV_NO_THINLTO)
        set_property(TARGET ssx3_game_module PROPERTY INTERPROCEDURAL_OPTIMIZATION_RELEASE FALSE)
    endif()
endif()
if(ANDROID AND NOT MSVC)
    target_compile_options(ssx3_game_module PRIVATE -gline-tables-only)
endif()
ps2x_bf2_android_march_target(ssx3_game_module)
