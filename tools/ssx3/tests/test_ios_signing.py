# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""iOS signing get-task-allow follows the provisioning profile.

Every public user signs with a development profile (Xcode automatic
signing), which requires get-task-allow=true; distribution/ad-hoc profiles
carry false. release-pb71-1: sign_staged_app reads the selected profile's
own entitlement instead of hard-coding false for public builds (--dev
stays true). A development profile with a false binary refuses install
with 0xe8008015.

No network, no disc, no Xcode: pure-dict tests of the profile reader.
"""
import importlib.util
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

SPEC = importlib.util.spec_from_file_location('ssx3_build_signing', HERE / 'build.py')
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


def dev_profile():
    return {'Entitlements': {'application-identifier': 'LQ3V7772Q2.*',
                             'get-task-allow': True,
                             'com.apple.developer.team-identifier': 'LQ3V7772Q2'},
            'Name': 'iOS Team Provisioning Profile: *'}


def dist_profile():
    return {'Entitlements': {'application-identifier': 'LQ3V7772Q2.com.ssx3recomp.lq3v7772q2',
                             'get-task-allow': False,
                             'com.apple.developer.team-identifier': 'LQ3V7772Q2'},
            'Name': 'SSX3 Ad Hoc'}


class ProfileGetTaskAllowTest(unittest.TestCase):
    def test_development_profile_true(self):
        self.assertIs(build.profile_plist_get_task_allow(dev_profile()), True)

    def test_distribution_profile_false(self):
        self.assertIs(build.profile_plist_get_task_allow(dist_profile()), False)

    def test_missing_key_defaults_false(self):
        self.assertIs(build.profile_plist_get_task_allow({'Entitlements': {}}), False)

    def test_missing_entitlements_defaults_false(self):
        self.assertIs(build.profile_plist_get_task_allow({}), False)

    def test_unreadable_defaults_false(self):
        self.assertIs(build.profile_plist_get_task_allow(None), False)

    def test_sign_uses_profile_value(self):
        src = (HERE / 'build.py').read_text()
        self.assertIn('profile_get_task_allow(profile)', src)


if __name__ == '__main__':
    unittest.main()
