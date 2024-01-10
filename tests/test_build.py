from __future__ import annotations

import unittest

from moncic.build.build import Build
from moncic.build.debian import Debian
from moncic.build.arpa import RPM, ARPA

COMMON_BUILD_PROFILES = ["artifacts_dir", "source_only", "on_success", "on_fail", "on_end"]


class TestBuild(unittest.TestCase):
    def test_build(self):
        self.assertEqual([x[0] for x in Build.list_build_options()], COMMON_BUILD_PROFILES)

    def test_debian(self):
        self.assertEqual([x[0] for x in Debian.list_build_options()], COMMON_BUILD_PROFILES + ["build_profile"])

    def test_rpm(self):
        self.assertEqual([x[0] for x in RPM.list_build_options()], COMMON_BUILD_PROFILES)

    def test_arpa(self):
        self.assertEqual([x[0] for x in ARPA.list_build_options()], COMMON_BUILD_PROFILES)
