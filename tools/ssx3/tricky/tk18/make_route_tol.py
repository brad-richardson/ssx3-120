#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""TK18: TK12 make_route.py with a donor race-chain tolerance: make_route_tol.py TOL DONOR.AIP EXPERIMENT.JSON OUT.txt [--z]"""
import functools, runpy, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tk12'))
import wlib  # noqa: F401
import race_course
race_course.donor_race_chain = functools.partial(race_course.donor_race_chain, tolerance=float(sys.argv.pop(1)))
sys.argv[0] = str(Path(__file__).resolve().parents[1] / 'tk12' / 'make_route.py')
runpy.run_path(sys.argv[0], run_name='__main__')
