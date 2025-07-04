import unittest

from moncic.operations.build import Builder
from moncic.operations.build_arpa import ARPABuilder, RPMBuilder
from moncic.operations.build_debian import DebianBuilder

COMMON_BUILD_PROFILES = ["artifacts_dir", "source_only", "on_success", "on_fail", "on_end"]


class TestBuild(unittest.TestCase):
    def test_build(self) -> None:
        self.assertEqual([x[0] for x in Builder.build_config_class.list_build_options()], COMMON_BUILD_PROFILES)

    def test_debian(self) -> None:
        self.assertEqual(
            [x[0] for x in DebianBuilder.build_config_class.list_build_options()],
            COMMON_BUILD_PROFILES + ["build_profile", "include_source"],
        )

    def test_rpm(self) -> None:
        self.assertEqual([x[0] for x in RPMBuilder.build_config_class.list_build_options()], COMMON_BUILD_PROFILES)

    def test_arpa(self) -> None:
        self.assertEqual([x[0] for x in ARPABuilder.build_config_class.list_build_options()], COMMON_BUILD_PROFILES)
