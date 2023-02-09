from __future__ import annotations

import contextlib
import os
import tempfile
import unittest

from moncic.exceptions import Fail
from moncic.source import debian, rpm, source
from moncic.unittest import make_moncic

from .source import GitRepo, MockBuilder


class WorkdirFixtureMixin:
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.stack = contextlib.ExitStack()
        cls.stack.__enter__()
        cls.workdir = cls.stack.enter_context(tempfile.TemporaryDirectory())

    @classmethod
    def tearDownClass(cls):
        cls.stack.__exit__(None, None, None)
        super().tearDownClass()


class GitFixtureMixin(WorkdirFixtureMixin):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.git = cls.stack.enter_context(GitRepo(os.path.join(cls.workdir, "repo")))


class TesttDebianSourceDir(WorkdirFixtureMixin, unittest.TestCase):
    tarball_name = "moncic-ci_0.1.0.orig.tar.gz"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pkg_root = os.path.join(cls.workdir, "moncic-ci")
        debian_dir = os.path.join(cls.pkg_root, "debian")
        os.makedirs(debian_dir, exist_ok=True)

        with open(os.path.join(debian_dir, "changelog"), "wt") as fd:
            print("moncic-ci (0.1.0-1) UNRELEASED; urgency=low", file=fd)

        # Create mock tarball
        # TODO: test also .tar.xz
        with open(os.path.join(cls.workdir, cls.tarball_name), "wb"):
            pass

    def test_build_options(self):
        self.assertEqual(
            [x[0] for x in debian.DebianSourceDir.list_build_options()],
            ["build_profile"])

    def test_detect_local(self):
        isrc = source.InputSource.create(self.pkg_root)
        self.assertIsInstance(isrc, source.LocalDir)

        with self.assertRaises(Fail):
            isrc.detect_source(MockBuilder("rocky9"))

        with MockBuilder("sid") as builder:
            src = isrc.detect_source(builder)
            self.assertIsInstance(src, debian.DebianSourceDir)

    def test_build_source(self):
        isrc = source.InputSource.create(self.pkg_root)
        with make_moncic().session():
            with MockBuilder("sid") as builder:
                src = isrc.detect_source(builder)
                self.assertEqual(src.get_build_class().__name__, "Debian")

                with builder.container() as container:
                    src.gather_sources_from_host(container)
                    self.assertCountEqual(os.listdir(container.source_dir), [self.tarball_name])
            # TODO: @guest_only
            # TODO: def build_source_package(self) -> str:


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


class TestDebianDsc(WorkdirFixtureMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.dsc_file = os.path.join(cls.workdir, "moncic-ci_0.1.0-1.dsc")
        with open(cls.dsc_file, "wt") as fd:
            fd.write("""Format: 3.0 (quilt)
Source: moncic-ci
Binary: moncic-ci
Version: 0.1.0-1
Files:
 d41d8cd98f00b204e9800998ecf8427e 0 moncic-ci_0.1.0.orig.tar.gz
 d41d8cd98f00b204e9800998ecf8427e 0 moncic-ci_0.1.0-1.debian.tar.xz
""")

        with open(os.path.join(cls.workdir, "moncic-ci_0.1.0.orig.tar.gz"), "wb"):
            pass
        with open(os.path.join(cls.workdir, "moncic-ci_0.1.0-1.debian.tar.xz"), "wb"):
            pass

    def test_build_options(self):
        self.assertEqual(
            [x[0] for x in debian.DebianDsc.list_build_options()],
            ["build_profile"])

    def test_detect_local(self):
        isrc = source.InputSource.create(self.dsc_file)
        self.assertIsInstance(isrc, source.LocalFile)

        with self.assertRaises(Fail):
            isrc.detect_source(MockBuilder("rocky9"))

        with MockBuilder("sid") as builder:
            src = isrc.detect_source(builder)
            self.assertIsInstance(src, debian.DebianDsc)

    def test_build_source(self):
        isrc = source.InputSource.create(self.dsc_file)
        with make_moncic().session():
            with MockBuilder("sid") as builder:
                src = isrc.detect_source(builder)
                self.assertEqual(src.get_build_class().__name__, "Debian")

                with builder.container() as container:
                    src.gather_sources_from_host(container)
                    self.assertCountEqual(os.listdir(container.source_dir), [
                        "moncic-ci_0.1.0-1.dsc",
                        "moncic-ci_0.1.0.orig.tar.gz",
                        "moncic-ci_0.1.0-1.debian.tar.xz",
                    ])


class TesttARPA(WorkdirFixtureMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        travis_yml = os.path.join(cls.workdir, ".travis.yml")
        with open(travis_yml, "wt") as out:
            print("foo foo simc/stable bar bar", file=out)

    def test_build_options(self):
        self.assertEqual(
            [x[0] for x in rpm.ARPASource.list_build_options()],
            [])

    def test_detect_local(self):
        isrc = source.InputSource.create(self.workdir)
        self.assertIsInstance(isrc, source.LocalDir)

        with self.assertRaises(Fail):
            isrc.detect_source(MockBuilder("sid"))

        with MockBuilder("rocky9") as builder:
            src = isrc.detect_source(builder)
            self.assertIsInstance(src, rpm.ARPASource)

    def test_build_source(self):
        isrc = source.InputSource.create(self.workdir)
        with make_moncic().session():
            with MockBuilder("rocky9") as builder:
                src = isrc.detect_source(builder)
                self.assertEqual(src.get_build_class().__name__, "ARPA")

                with builder.container() as container:
                    src.gather_sources_from_host(container)
                    self.assertCountEqual(os.listdir(container.source_dir), [])
            # TODO: @guest_only
            # TODO: def build_source_package(self) -> str:


# TODO: class DebianSourcePackage(DebianSource):
