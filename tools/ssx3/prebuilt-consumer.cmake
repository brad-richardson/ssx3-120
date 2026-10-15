# This path consumes only a bundle validated against the current source contract.
if(NOT ANDROID OR NOT ANDROID_ABI STREQUAL "arm64-v8a" OR NOT PS2X_GAME_CODEGEN_DIR)
    message(FATAL_ERROR "Prebuilt consumer requires Android arm64 and local generated game code")
endif()
if(PS2X_VU0_RECOMP_DIR OR PS2X_PGO_DATA OR PS2X_PGO_GENERATE)
    message(FATAL_ERROR "Prebuilt bundle does not support private VU0/PGO inputs; use source libraries")
endif()
file(READ "${CMAKE_ANDROID_NDK}/source.properties" _ndk_properties)
if(NOT _ndk_properties MATCHES "Pkg.Revision[ \t]*=[ \t]*28\\.2\\.13676358")
    message(FATAL_ERROR "Prebuilt consumer requires NDK 28.2.13676358")
endif()
find_package(Python3 REQUIRED COMPONENTS Interpreter)
execute_process(COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tools/ssx3/prebuilt.py"
    verify "${PS2X_PREBUILT_DIR}" RESULT_VARIABLE _verified)
if(NOT _verified EQUAL 0)
    message(FATAL_ERROR "Prebuilt contract or file verification failed")
endif()
file(READ "${PS2X_PREBUILT_DIR}/manifest.json" _manifest)
string(JSON _stamp GET "${_manifest}" contract)
file(STRINGS "${PS2X_PREBUILT_DIR}/runtime-definitions.txt" _definitions)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
add_compile_definitions(USE_SSE2NEON)
add_compile_options(-ffp-contract=off)
include("${CMAKE_CURRENT_SOURCE_DIR}/ps2xRuntime/cmake/ReleaseMode.cmake")
file(GLOB _game_sources CONFIGURE_DEPENDS "${PS2X_GAME_CODEGEN_DIR}/*.cpp")
list(FILTER _game_sources EXCLUDE REGEX "/\\._")
if(NOT EXISTS "${PS2X_GAME_CODEGEN_DIR}/register_functions.cpp")
    message(FATAL_ERROR "Missing local game registration table")
endif()
add_library(ps2_game_objects STATIC ${_game_sources})
target_include_directories(ps2_game_objects PRIVATE
    "${PS2X_GAME_CODEGEN_DIR}" "${CMAKE_CURRENT_SOURCE_DIR}/ps2xRuntime/include"
    "${CMAKE_CURRENT_SOURCE_DIR}/ps2xRuntime/src/lib/Kernel"
    "${CMAKE_CURRENT_SOURCE_DIR}/ps2xIOP/include" "${PS2X_PREBUILT_DIR}/include")
target_compile_definitions(ps2_game_objects PRIVATE ${_definitions})
target_compile_options(ps2_game_objects PRIVATE -march=armv8.2-a+fp16+dotprod -gline-tables-only)
set_target_properties(ps2_game_objects PROPERTIES UNITY_BUILD ON UNITY_BUILD_BATCH_SIZE 32
    INTERPROCEDURAL_OPTIMIZATION_RELEASE TRUE INTERPROCEDURAL_OPTIMIZATION_RELWITHDEBINFO TRUE)
# This contains only a source-contract stamp, never guest data or game functions.
file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/requested-prebuilt.cpp"
    "extern const char g_ps2xRequestedPrebuiltStamp[] = \"${_stamp}\";\n")
add_library(ps2EntryRunner SHARED "${CMAKE_CURRENT_BINARY_DIR}/requested-prebuilt.cpp")
EnableFastReleaseMode(ps2EntryRunner)
target_compile_options(ps2EntryRunner PRIVATE -march=armv8.2-a+fp16+dotprod -gline-tables-only)
target_link_options(ps2EntryRunner PRIVATE -Wl,--undefined=ANativeActivity_onCreate -Wl,-Bsymbolic)
# Keep bootstrap constructors (including both ABI guards), then resolve the
# mutually dependent runtime/game archives without embedding a stub game table.
set(_lib "${PS2X_PREBUILT_DIR}/lib")
target_link_libraries(ps2EntryRunner PRIVATE
    -Wl,--whole-archive "${_lib}/libps2EntryRunner.a" -Wl,--no-whole-archive
    -Wl,--start-group ps2_game_objects "${_lib}/libps2_runtime.a"
    "${_lib}/libps2_gs_shadow.a" "${_lib}/libps2_iop.a"
    "${_lib}/libps2x_soundtouch.a" "${_lib}/libps2x_rcheevos.a" "${_lib}/libraylib.a"
    -Wl,--end-group android EGL GLESv2 OpenSLES log m dl atomic)
if(PS2X_THINLTO_CACHE_DIR)
    target_link_options(ps2EntryRunner PRIVATE "-Wl,--thinlto-cache-dir=${PS2X_THINLTO_CACHE_DIR}"
        "-Wl,--thinlto-cache-policy=cache_size_bytes=8589934592:prune_interval=1h")
endif()
