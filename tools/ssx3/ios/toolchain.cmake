# iOS device toolchain for the product builder (ios-builder-1).
#
# Xcode generator, arm64 device slice. Build-time code signing stays OFF:
# the staged bundle gains the disc payload after the build (which would
# invalidate any build-time signature), so the `ios` step signs the staged
# app with the user's team + embedded profile instead. Lab provenance:
# local/research/I26/ios-device.toolchain.cmake (same semantics, lab
# comments and history stripped; no lab paths).
set(CMAKE_SYSTEM_NAME iOS)
set(CMAKE_SYSTEM_PROCESSOR arm64)
set(CMAKE_OSX_SYSROOT iphoneos)
set(CMAKE_OSX_ARCHITECTURES arm64 CACHE STRING "Target arch" FORCE)
set(CMAKE_XCODE_ATTRIBUTE_ONLY_ACTIVE_ARCH YES)
set(CMAKE_XCODE_ATTRIBUTE_CODE_SIGNING_ALLOWED NO)
set(CMAKE_XCODE_ATTRIBUTE_CODE_SIGNING_REQUIRED NO)
