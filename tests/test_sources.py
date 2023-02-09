from __future__ import annotations

import contextlib
import os
import tempfile
import unittest

from moncic.exceptions import Fail
from moncic.source import debian, rpm, source
from moncic.unittest import make_moncic

from .source import GitRepo, MockBuilder


class GitFixtureMixin:
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.stack = contextlib.ExitStack()
        cls.stack.__enter__()
        cls.workdir = cls.stack.enter_context(tempfile.TemporaryDirectory())
        cls.git = cls.stack.enter_context(GitRepo(os.path.join(cls.workdir, "repo")))

    @classmethod
    def tearDownClass(cls):
        cls.stack.__exit__(None, None, None)
        super().tearDownClass()


class TesttDebianPlainGit(GitFixtureMixin, unittest.TestCase):
    tarball_name = "moncic-ci_0.1.0.orig.tar.gz"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.git.add("debian/changelog", "moncic-ci (0.1.0-1) UNRELEASED; urgency=low")
        cls.git.commit()
        # Create mock tarball
        # TODO: test also .tar.xz
        with open(os.path.join(cls.workdir, cls.tarball_name), "wb"):
            pass

    def test_build_options(self):
        self.assertEqual(
            [x[0] for x in debian.DebianPlainGit.list_build_options()],
            ["build_profile"])

    def test_detect_local(self):
        isrc = source.InputSource.create(self.git.root)
        self.assertIsInstance(isrc, source.LocalGit)

        with self.assertRaises(Fail):
            isrc.detect_source(MockBuilder("rocky9"))

        with MockBuilder("sid") as builder:
            src = isrc.detect_source(builder)
            self.assertIsInstance(src, debian.DebianPlainGit)

    def test_detect_url(self):
        with self.git.serve() as url:
            isrc = source.InputSource.create(url)
            self.assertIsInstance(isrc, source.URL)

            with self.assertRaises(Fail):
                isrc.detect_source(MockBuilder("rocky9"))

            with MockBuilder("sid") as builder:
                src = isrc.detect_source(builder)
                self.assertIsInstance(src, debian.DebianPlainGit)

    def test_build_source(self):
        isrc = source.InputSource.create(self.git.root)
        with make_moncic().session():
            with MockBuilder("sid") as builder:
                src = isrc.detect_source(builder)
                self.assertEqual(src.get_build_class().__name__, "Debian")

                with builder.container() as container:
                    src.gather_sources_from_host(container)
                    # TODO: since the repo was cloned, using the new repo as
                    # reference it cannot find the tarball
                    self.assertCountEqual(os.listdir(container.source_dir), [self.tarball_name])
            # TODO: @guest_only
            # TODO: def build_source_package(self) -> str:


class DebianGBPTestUpstreamMixin(GitFixtureMixin):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Initial upstream
        cls.git.add("testfile")
        cls.git.commit("Initial commit")

        # Debian branch
        cls.git.git("checkout", "-b", cls.packaging_branch_name)
        cls.git.add("debian/changelog", "moncic-ci (0.1.0-1) UNRELEASED; urgency=low")
        cls.git.commit()

        # New changes to upstream branch
        cls.git.git("checkout", "main")
        cls.git.add("testfile", "test content")
        cls.git.commit("Updated testfile")

        # TODO: add gdb.conf

    def test_build_options(self):
        self.assertEqual(
            [x[0] for x in debian.DebianGBPTestUpstream.list_build_options()],
            ["build_profile"])

    def test_detect_local(self):
        isrc = source.InputSource.create(self.git.root)
        self.assertIsInstance(isrc, source.LocalGit)

        with self.assertRaises(Fail):
            isrc.detect_source(MockBuilder("rocky9"))

        with MockBuilder("sid") as builder:
            src = isrc.detect_source(builder)
            self.assertIsInstance(src, debian.DebianGBPTestUpstream)

    def test_detect_url(self):
        with self.git.serve() as url:
            isrc = source.InputSource.create(url)
            self.assertIsInstance(isrc, source.URL)

            with self.assertRaises(Fail):
                isrc.detect_source(MockBuilder("rocky9"))

            with MockBuilder("sid") as builder:
                src = isrc.detect_source(builder)
                self.assertIsInstance(src, debian.DebianGBPTestUpstream)

    def test_build_source(self):
        isrc = source.InputSource.create(self.git.root)
        with make_moncic().session():
            with MockBuilder("sid") as builder:
                src = isrc.detect_source(builder)
                self.assertEqual(src.get_build_class().__name__, "Debian")

                with builder.container() as container:
                    src.gather_sources_from_host(container)
                    self.assertCountEqual(os.listdir(container.source_dir), [])

                self.assertEqual(src.gbp_args, ['--git-upstream-tree=branch', '--git-upstream-branch=main'])


class TestDebianGBPTestUpstreamUnstable(DebianGBPTestUpstreamMixin, unittest.TestCase):
    packaging_branch_name = "debian/unstable"


class TestDebianGBPTestUpstreamSid(DebianGBPTestUpstreamMixin, unittest.TestCase):
    packaging_branch_name = "debian/sid"


class TestDebianGBPRelease(GitFixtureMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Initial upstream
        cls.git.add("testfile")
        cls.git.commit("Initial commit")
        cls.git.git("tag", "upstream/0.1.0")

        # Debian branch
        cls.git.git("checkout", "-b", "debian/unstable")
        cls.git.add("debian/changelog", "moncic-ci (0.1.0-1) UNRELEASED; urgency=low")
        cls.git.add("debian/gbp.conf", """
[DEFAULT]
upstream-branch=main
upstream-tag=%(version)s
debian-branch=debian/unstable
""")
        cls.git.commit()
        cls.git.git("tag", "debian/0.1.0-1")

    def test_build_options(self):
        self.assertEqual(
            [x[0] for x in debian.DebianGBPRelease.list_build_options()],
            ["build_profile"])

    def test_detect_local(self):
        isrc = source.InputSource.create(self.git.root)
        self.assertIsInstance(isrc, source.LocalGit)

        with self.assertRaises(Fail):
            isrc.detect_source(MockBuilder("rocky9"))

        with MockBuilder("sid") as builder:
            src = isrc.detect_source(builder)
            self.assertIsInstance(src, debian.DebianGBPRelease)

    def test_detect_url(self):
        with self.git.serve() as url:
            isrc = source.InputSource.create(url)
            self.assertIsInstance(isrc, source.URL)

            with self.assertRaises(Fail):
                isrc.detect_source(MockBuilder("rocky9"))

            with MockBuilder("sid") as builder:
                src = isrc.detect_source(builder)
                self.assertIsInstance(src, debian.DebianGBPRelease)

    def test_build_source(self):
        isrc = source.InputSource.create(self.git.root)
        with make_moncic().session():
            with MockBuilder("sid") as builder:
                src = isrc.detect_source(builder)
                self.assertEqual(src.get_build_class().__name__, "Debian")

                with builder.container() as container:
                    src.gather_sources_from_host(container)
                    self.assertCountEqual(os.listdir(container.source_dir), [])

                self.assertEqual(src.gbp_args, ["--git-upstream-tree=tag"])


class TestDebianGBPTestDebian(GitFixtureMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Initial upstream
        cls.git.add("testfile")
        cls.git.commit("Initial commit")

        # Debian branch
        cls.git.git("checkout", "-b", "debian/unstable")
        cls.git.add("debian/changelog", "moncic-ci (0.1.0-1) UNRELEASED; urgency=low")
        cls.git.add("debian/gbp.conf", """
[DEFAULT]
upstream-branch=main
upstream-tag=%(version)s
debian-branch=debian/unstable
""")
        cls.git.commit()

        # New changes to upstream branch
        cls.git.git("checkout", "main")
        cls.git.add("testfile", "test content")
        cls.git.commit("Updated testfile")

        # Leave the packaging branch as current
        cls.git.git("checkout", "debian/unstable")

    def test_build_options(self):
        self.assertEqual(
            [x[0] for x in debian.DebianGBPTestDebian.list_build_options()],
            ["build_profile"])

    def test_detect_local(self):
        isrc = source.InputSource.create(self.git.root)
        self.assertIsInstance(isrc, source.LocalGit)

        with self.assertRaises(Fail):
            isrc.detect_source(MockBuilder("rocky9"))

        with MockBuilder("sid") as builder:
            src = isrc.detect_source(builder)
            self.assertIsInstance(src, debian.DebianGBPTestDebian)

    def test_detect_url(self):
        with self.git.serve() as url:
            isrc = source.InputSource.create(url)
            self.assertIsInstance(isrc, source.URL)

            with self.assertRaises(Fail):
                isrc.detect_source(MockBuilder("rocky9"))

            with MockBuilder("sid") as builder:
                src = isrc.detect_source(builder)
                self.assertIsInstance(src, debian.DebianGBPTestDebian)

    def test_build_source(self):
        isrc = source.InputSource.create(self.git.root)
        with make_moncic().session():
            with MockBuilder("sid") as builder:
                src = isrc.detect_source(builder)
                self.assertEqual(src.get_build_class().__name__, "Debian")

                with builder.container() as container:
                    src.gather_sources_from_host(container)
                    self.assertCountEqual(os.listdir(container.source_dir), [])

                self.assertEqual(src.gbp_args, ["--git-upstream-tree=branch"])


# TODO: class DebianSourceDir(DebianSource):
# TODO: class DebianSourcePackage(DebianSource):
# TODO: class ARPAGit(RPMGit):
