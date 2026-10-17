# ssx3-120 one-command builder (builder-ux-1). Needs only `uv`.
#
#   make android ISO=/path/to/SSX3.iso [TRICKY=/path/to/Tricky.iso] [WORK=~/ssx3-work] [JOBS=4] [INSTALL=1] [PREBUILT=1]
#   make doctor
#   make install [WORK=...]
#
# WORK defaults to ~/ssx3-work. INSTALL=1 installs to a connected device
# after the build (needs adb + exactly one device, or --serial via ./build).
# PREBUILT=1 opts in to a verified prebuilt support release when one matches
# this checkout. TRICKY is accepted and validated but the Tricky port arrives
# in a later version, so the build stops with that message for now.

WORK ?= $(HOME)/ssx3-work
JOBS ?= 4
ISO ?=
TRICKY ?=
INSTALL ?=
PREBUILT ?=

android:
	@test -n "$(ISO)" || (echo 'usage: make android ISO=/path/to/SSX3.iso [TRICKY=...] [INSTALL=1] [PREBUILT=1] [WORK=...] [JOBS=...]' >&2; exit 1)
	@set -eu; set -- android --ssx3-iso "$(ISO)" --work "$(WORK)" --jobs $(JOBS); \
	if [ -n "$(TRICKY)" ]; then set -- "$$@" --tricky-iso "$(TRICKY)"; fi; \
	if [ -n "$(INSTALL)" ]; then set -- "$$@" --install; fi; \
	if [ -n "$(PREBUILT)" ]; then set -- "$$@" --prebuilt; fi; \
	./build "$$@"

doctor:
	./build doctor --target android

install:
	./build install --work "$(WORK)"

.PHONY: android doctor install
