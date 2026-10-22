# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
# ssx3-120 one-command builder (builder-ux-1). Needs only `uv`.
#
#   just android ~/SSX3.iso
#   just doctor
#   just install

work := env_var_or_default("SSX3_WORK", home_directory() / "ssx3-work")
jobs := env_var_or_default("SSX3_JOBS", "4")

android iso tricky="" install="" prebuilt="":
    ./build android --ssx3-iso {{iso}} --work {{work}} --jobs {{jobs}} {{if tricky != "" { "--tricky-iso " + tricky } else { "" } }} {{if install != "" { "--install" } else { "" } }} {{if prebuilt != "" { "--prebuilt" } else { "" } }}

doctor:
    ./build doctor --target android

install:
    ./build install --work {{work}}
