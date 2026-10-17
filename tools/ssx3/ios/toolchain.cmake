# iOS device toolchain for the product builder (ios-builder-1).
#
# Xcode generator, arm64 device slice. Code signing stays ENABLED (unlike the
# lab flow, which builds unsigned and signs manually): the builder passes
# CODE_SIGN_STYLE=Automatic + DEVELOPMENT_TEAM + PRODUCT_BUNDLE_IDENTIFIER
# at configure time, so Xcode creates/refreshes the user's provisioning
# profile and signs the build. Staging then adds the disc payload (which
# invalidates the build-time seal) and the builder re-signs the staged app
# with the same identity + profile. Lab provenance: derived from
# local/research/I26/ios-device.toolchain.cmake (signing flipped on; no lab
# paths).
set(CMAKE_SYSTEM_NAME iOS)
set(CMAKE_SYSTEM_PROCESSOR arm64)
set(CMAKE_OSX_SYSROOT iphoneos)
set(CMAKE_OSX_ARCHITECTURES arm64 CACHE STRING "Target arch" FORCE)
set(CMAKE_XCODE_ATTRIBUTE_ONLY_ACTIVE_ARCH YES)
