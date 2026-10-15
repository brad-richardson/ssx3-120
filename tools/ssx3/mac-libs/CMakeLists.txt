# Public Mac Metal GS + microVU build; assembly supplied by ../build.py.
cmake_minimum_required(VERSION 3.22)
project(ARMSX2Mac C CXX OBJC OBJCXX ASM)

set(CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/../vendor-pcsx2/cmake")
set(TOP_CMAKE_WAS_SOURCED TRUE)
set(ENABLE_QT_UI OFF CACHE BOOL "" FORCE)
set(ENABLE_TESTS OFF CACHE BOOL "" FORCE)
set(ENABLE_QT_DEBUGGER OFF CACHE BOOL "" FORCE)
set(ENABLE_SDL_FRONTEND OFF CACHE BOOL "" FORCE)
set(ENABLE_LIBRETRO OFF CACHE BOOL "" FORCE)
set(ENABLE_EERUNNER OFF CACHE BOOL "" FORCE)
set(ENABLE_GSRUNNER OFF CACHE BOOL "" FORCE)
set(ENABLE_VURUNNER OFF CACHE BOOL "" FORCE)
# Portable baseline uses native Metal; no Vulkan/shaderc dependency.
set(USE_VULKAN OFF CACHE BOOL "" FORCE)
# microVU bench harness sources need the recompiler test hooks.
set(ENABLE_RECOMPILER_TEST_HOOKS ON CACHE BOOL "" FORCE)
# CN1A (ARMSX2 base): the adapter links the bundled shaderc_combined itself.
set(ARMSX2_USE_BUNDLED_SHADERC ON CACHE BOOL "" FORCE)

include(Pcsx2Utils)
check_no_parenthesis_in_path()
detect_operating_system()
detect_compiler()
include(BuildParameters)
include(SearchForStuff)
get_git_version_info()
write_svnrev_h()

add_subdirectory("${CMAKE_CURRENT_SOURCE_DIR}/../vendor-pcsx2/common" "${CMAKE_BINARY_DIR}/common")
# Supply libretro headers if the vendor dependency search has not added them.
if(NOT TARGET libretro-headers)
  add_subdirectory("${CMAKE_CURRENT_SOURCE_DIR}/../vendor-pcsx2/3rdparty/libretro" "${CMAKE_BINARY_DIR}/libretro-headers")
endif()
add_subdirectory("${CMAKE_CURRENT_SOURCE_DIR}/../vendor-pcsx2/pcsx2" "${CMAKE_BINARY_DIR}/pcsx2")
add_subdirectory("${CMAKE_CURRENT_SOURCE_DIR}/../adapter" "${CMAKE_BINARY_DIR}/ge1-adapter")


# Portable Mac JIT bridge: no offline code, recordings or private headers.
add_library(mv2_microvu SHARED
    "${CMAKE_CURRENT_SOURCE_DIR}/../mv2-adapter/MicrovuBridge.cpp"
    "${CMAKE_CURRENT_SOURCE_DIR}/../vendor-pcsx2/pcsx2/arm64/microVU-arm64.cpp"
    "${CMAKE_CURRENT_SOURCE_DIR}/../vendor-pcsx2/tests/ctest/core/StubHost.cpp"
    "${CMAKE_CURRENT_SOURCE_DIR}/../vendor-pcsx2/tests/ctest/core/recompilers/harness/RecompilerTestEnvironment.cpp")
target_include_directories(mv2_microvu PRIVATE
    "${CMAKE_BINARY_DIR}/common/include"
    "${CMAKE_CURRENT_SOURCE_DIR}/../vendor-pcsx2"
    "${CMAKE_CURRENT_SOURCE_DIR}/../vendor-pcsx2/pcsx2"
    "${CMAKE_CURRENT_SOURCE_DIR}/../vendor-pcsx2/tests/ctest/core/recompilers"
    "${CMAKE_CURRENT_SOURCE_DIR}/../vendor-pcsx2/tests/ctest/core/recompilers/harness"
    "${PS2RECOMP_SOURCE}/ps2xRuntime/include")
set_target_properties(mv2_microvu PROPERTIES DISABLE_PRECOMPILE_HEADERS ON)
target_compile_options(mv2_microvu PRIVATE -fvisibility=hidden)
target_link_libraries(mv2_microvu PRIVATE PCSX2_FLAGS PCSX2)
set_source_files_properties(
    "${CMAKE_CURRENT_SOURCE_DIR}/../vendor-pcsx2/pcsx2/arm64/microVU-arm64.cpp"
    PROPERTIES COMPILE_DEFINITIONS PS2X_MICROVU_EMBED=1)
