import re
import tempfile
import unittest
from functools import cached_property
from pathlib import Path
from typing import override

from moncic.distro import Distro, DistroFamily
from moncic.image import RunnableImage
from moncic.mock.image import MockRunnableImage
from moncic.mock.images import MockImages
from moncic.mock.session import MockSession
from moncic.operations.build import Builder
from moncic.operations.build_arpa import ARPABuilder, RPMBuilder
from moncic.operations.build_debian import DebianBuilder
from moncic.source import Source
from moncic.source.distro import DistroSource
from moncic.source.rpm import ARPASource
from moncic.unittest import MockMoncicTestCase
from moncic.unittest.sources import Package, SourcesTestCase

COMMON_BUILD_PROFILES = [
    "artifacts_dir",
    "source_only",
    "on_success",
    "on_fail",
    "on_end",
]

BUILD_SCRIPT_LEAD = [
    "echo 'Create rpbmuild directory tree'",
    "mkdir -p /root/rpmbuild/BUILD /root/rpmbuild/BUILDROOT /root/rpmbuild/RPMS"
    " /root/rpmbuild/SOURCES /root/rpmbuild/SPECS /root/rpmbuild/SRPMS",
    "echo 'Install build dependencies'",
]


class TestBuild(unittest.TestCase):
    def test_build(self) -> None:
        self.assertEqual(
            [x[0] for x in Builder.build_config_class.list_build_options()],
            COMMON_BUILD_PROFILES,
        )

    def test_debian(self) -> None:
        self.assertEqual(
            [
                x[0]
                for x in DebianBuilder.build_config_class.list_build_options()
            ],
            COMMON_BUILD_PROFILES + ["build_profile", "include_source"],
        )

    def test_rpm(self) -> None:
        self.assertEqual(
            [x[0] for x in RPMBuilder.build_config_class.list_build_options()],
            COMMON_BUILD_PROFILES,
        )

    def test_arpa(self) -> None:
        self.assertEqual(
            [x[0] for x in ARPABuilder.build_config_class.list_build_options()],
            COMMON_BUILD_PROFILES,
        )


class TestBuildARPA(SourcesTestCase, MockMoncicTestCase, unittest.TestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.session = self.enterContext(MockSession(self.moncic))
        self.build_config = ARPABuilder.build_config_class()
        self.distro_name: str = "fedora:44"

    @cached_property
    def distro(self) -> Distro:
        """Return the distro used for the mock build."""
        return DistroFamily.lookup_distro(self.distro_name)

    def source(
        self, package: Package, style: str | None = None
    ) -> DistroSource:
        """Return the source to build."""
        local_source = self.enterContext(
            Source.create_local(source=package.path)
        )
        return DistroSource.create_from_local(
            local_source, distro=self.distro, style=style
        )

    def image(self) -> RunnableImage:
        """Return the OS image to use for the build."""
        images = MockImages(self.session)
        return MockRunnableImage(
            images=images, name="build", distro=self.distro
        )

    def test_fedora_dir(self) -> None:
        package = self.get_package("hello")
        source = self.source(package)
        image = self.image()
        container = self.enterContext(image.container())
        with ARPABuilder[ARPASource](
            source, image, self.build_config
        ) as builder:
            builder.build(container)
        with self.match_run_log(self.session.run_log) as m:
            script = m.assertPopScript(re.compile(r"^Build .+/hello"))
            self.assertEqual(
                script.lines,
                [
                    *BUILD_SCRIPT_LEAD,
                    "dnf builddep -y"
                    " /srv/moncic-ci/source/hello/fedora/SPECS/hello.spec",
                    "git config --global --add safe.directory"
                    " /srv/moncic-ci/source/hello",
                    "(cd /srv/moncic-ci/source/hello &&"
                    " git archive --prefix=hello/"
                    " --format=tar.gz -o /root/rpmbuild/SOURCES/hello.tar.gz"
                    " HEAD)",
                    "spectool -g -R --define 'srcarchivename hello'"
                    " /srv/moncic-ci/source/hello/fedora/SPECS/hello.spec",
                    "rpmbuild -ba --define 'srcarchivename hello'"
                    " /srv/moncic-ci/source/hello/fedora/SPECS/hello.spec",
                ],
            )
            m.assertEmpty()

    def test_no_fedora_dir(self) -> None:
        tmpdir = Path(self.enterContext(tempfile.TemporaryDirectory()))
        pkgdir = tmpdir / "hello"
        pkgdir.mkdir()
        package = self.get_package("hello").as_spec_in_root(pkgdir)
        source = self.source(package)
        image = self.image()
        container = self.enterContext(image.container())
        with ARPABuilder[ARPASource](
            source, image, self.build_config
        ) as builder:
            builder.build(container)
        with self.match_run_log(self.session.run_log) as m:
            script = m.assertPopScript(
                re.compile(rf"^Build {re.escape(pkgdir.as_posix())}")
            )
            self.assertEqual(
                script.lines,
                [
                    *BUILD_SCRIPT_LEAD,
                    "dnf builddep -y /srv/moncic-ci/source/hello/hello.spec",
                    "spectool -g -R /srv/moncic-ci/source/hello/hello.spec",
                    "rpmbuild -ba /srv/moncic-ci/source/hello/hello.spec",
                ],
            )
            m.assertEmpty()

    def test_no_fedora_dir_with_patches(self) -> None:
        tmpdir = Path(self.enterContext(tempfile.TemporaryDirectory()))
        pkgdir = tmpdir / "hello"
        pkgdir.mkdir()
        package = self.get_package("hello").as_spec_in_root(pkgdir)
        (pkgdir / "foo.patch").touch()
        (pkgdir / "bar.patch").touch()
        source = self.source(package)
        image = self.image()
        container = self.enterContext(image.container())
        with ARPABuilder[ARPASource](
            source, image, self.build_config
        ) as builder:
            builder.build(container)
        with self.match_run_log(self.session.run_log) as m:
            script = m.assertPopScript(
                re.compile(rf"^Build {re.escape(pkgdir.as_posix())}")
            )
            self.assertEqual(
                script.lines,
                [
                    *BUILD_SCRIPT_LEAD,
                    "dnf builddep -y /srv/moncic-ci/source/hello/hello.spec",
                    "(cd /srv/moncic-ci/source/hello && cp bar.patch foo.patch"
                    " /root/rpmbuild/SOURCES/)",
                    "spectool -g -R /srv/moncic-ci/source/hello/hello.spec",
                    "rpmbuild -ba /srv/moncic-ci/source/hello/hello.spec",
                ],
            )
            m.assertEmpty()
