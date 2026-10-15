# HAL1: app-local libhardware shim, built from source by the app's own CMake.
# Source: ssx3 local/research/N8C2/hwcompat.c (N8C2 diagnostic probe, MIT).
# It answers hw_get_module with -ENOENT (capped logging) so the app-local
# Turnip loads through GE1's HMI path (VKLoader.cpp Ge1LoadTurnipHAL).
# Wired in from the root CMakeLists.txt under ANDROID; packaged because
# android/app/build.gradle lists 'hardware' in its cmake targets.
# A libhardware.so shipped via -Pps2xJniLibsDir is a hard gradle error
# (the build-from-source copy must win; no silent pick).
add_library(hardware SHARED hwcompat.c)

set_target_properties(hardware PROPERTIES
    C_STANDARD 11
    C_STANDARD_REQUIRED ON
    POSITION_INDEPENDENT_CODE ON
)

# -O2 is the N8C2 recipe grade. It is spelled per-target (not via
# CMAKE_C_FLAGS_<CONFIG>) so it wins over gradle's
# -DCMAKE_C_FLAGS_RELWITHDEBINFO=-O3 (target options come last).
target_compile_options(hardware PRIVATE -O2 -fvisibility=hidden)

set_target_properties(hardware PROPERTIES
    LINK_FLAGS "-Wl,-soname,libhardware.so -Wl,--no-undefined"
)

target_link_libraries(hardware PRIVATE log dl)
