from __future__ import annotations

import os
import unittest
from pathlib import Path

from moncic.distro import DistroFamily
from moncic.exceptions import Fail
from moncic.source import Source
from moncic.source.local import File, Dir, Git
from moncic.source.debian import DebianSource, DebianDir, DebianDsc, SourceInfo, DebianGitLegacy
from moncic.unittest import make_moncic

from .source import GitFixture, MockBuilder, WorkdirFixture, GitRepo

SID = DistroFamily.lookup_distro("sid")


class TestDebianSource(WorkdirFixture):
    file: Path
    dsc: Path

    def make_git_repo(self, name: str) -> GitRepo:
        git = GitRepo(self.workdir / name)
        git.__enter__()
        self.addCleanup(git.__exit__, None, None, None)
        return git

    def test_from_file_plain(self) -> None:
        path = self.workdir / "file"
        path.touch()
        with Source.create_local(source=path) as src:
            assert isinstance(src, File)
            with self.assertRaisesRegexp(Fail, f"{path}: cannot detect source type"):
                DebianSource.create_from_file(src, distro=SID)

    def test_from_file_dsc(self) -> None:
        path = self.workdir / "file.dsc"
        path.touch()
        with Source.create_local(source=path) as src:
            assert isinstance(src, File)
            newsrc = DebianSource.create_from_file(src, distro=SID)
            assert isinstance(newsrc, DebianDsc)

    def test_from_dir_empty(self) -> None:
        path = self.workdir / "dir"
        path.mkdir()
        with Source.create_local(source=path) as src:
            assert isinstance(src, Dir)
            with self.assertRaisesRegexp(Fail, f"{path}: cannot detect source type"):
                DebianSource.create_from_dir(src, distro=SID)

    def test_from_dir_debian(self) -> None:
        path = self.workdir / "debsource-test"
        path.mkdir()
        tar_path = self.workdir / "debsource_0.1.0.orig.tar.gz"
        tar_path.touch()
        debiandir = path / "debian"
        debiandir.mkdir()
        changelog = debiandir / "changelog"
        changelog.write_text("debsource (0.1.0-1) UNRELEASED; urgency=low\n")

        with Source.create_local(source=path) as src:
            assert isinstance(src, Dir)
            newsrc = DebianSource.create_from_dir(src, distro=SID)
            assert isinstance(newsrc, DebianDir)
            self.assertEqual(newsrc.path, path)
            self.assertEqual(
                newsrc.source_info,
                SourceInfo(
                    name="debsource",
                    version="0.1.0-1",
                    dsc_filename="debsource_0.1.0-1.dsc",
                    tar_stem="debsource_0.1.0.orig.tar",
                ),
            )
            self.assertEqual(newsrc.tarball, tar_path)

    def test_from_git_empty(self) -> None:
        git = self.make_git_repo("git")
        with Source.create_local(source=git.root) as src:
            assert isinstance(src, Git)
            with self.assertRaisesRegexp(Fail, f"{git.root}: cannot detect source type"):
                DebianSource.create_from_git(src, distro=SID)

    def test_from_git_debian_legacy(self) -> None:
        git = self.make_git_repo("gitlegacy")
        tar_path = self.workdir / "gitlegacy_0.1.0.orig.tar.gz"
        tar_path.touch()
        git.add("debian/changelog", "gitlegacy (0.1.0-1) UNRELEASED; urgency=low\n")
        git.commit()

        with Source.create_local(source=git.root) as src:
            assert isinstance(src, Git)
            newsrc = DebianSource.create_from_git(src, distro=SID)
            assert isinstance(newsrc, DebianGitLegacy)
            self.assertEqual(newsrc.path, git.root)
            self.assertIs(newsrc.repo, src.repo)
            self.assertEqual(newsrc.readonly, src.readonly)
            self.assertEqual(
                newsrc.source_info,
                SourceInfo(
                    name="gitlegacy",
                    version="0.1.0-1",
                    dsc_filename="gitlegacy_0.1.0-1.dsc",
                    tar_stem="gitlegacy_0.1.0.orig.tar",
                ),
            )
            self.assertEqual(newsrc.tarball, tar_path)


#     def test_from_git_empty(self) -> None:
#         path = self.workdir / "dir"
#         path.mkdir()
#         with Source.create(source=path) as src:
#             assert isinstance(src, Dir)
#             with self.assertRaisesRegexp(Fail, f"{path}: cannot detect source type"):
#                 DebianSource.create_from_dir(src)
#
#     def test_from_git_debian(self) -> None:
#         path = self.workdir / "debian"
#         path.mkdir()
#         with Source.create(source=path) as src:
#             assert isinstance(src, Dir)
#             newsrc = DebianSource.create_from_dir(src)
#             assert isinstance(newsrc, DebianDir)


# class DebianSourceDirMixin(WorkdirFixtureMixin):
#     tarball_name: str
#
#     @classmethod
#     def setUpClass(cls):
#         super().setUpClass()
#         cls.pkg_root = cls.workdir / "moncic-ci"
#         debian_dir = cls.pkg_root / "debian"
#         os.makedirs(debian_dir, exist_ok=True)
#
#         with open(os.path.join(debian_dir, "changelog"), "wt") as fd:
#             print("moncic-ci (0.1.0-1) UNRELEASED; urgency=low", file=fd)
#
#         # Create mock tarball
#         (cls.workdir / cls.tarball_name).write_bytes(b"")
#
#     def test_detect_local(self):
#         with InputSource.create(self.pkg_root) as isrc:
#             self.assertIsInstance(isrc, inputsource.LocalDir)
#
#             with self.assertRaises(Fail):
#                 isrc.detect_source(ROCKY9)
#
#             src = isrc.detect_source(SID)
#             self.assertIsInstance(src, debian.DebianSourceDir)
#
#     def test_build_source(self):
#         with InputSource.create(self.pkg_root) as isrc:
#             src = isrc.detect_source(SID)
#             self.assertEqual(src.get_build_class().__name__, "Debian")
#             build = src.make_build(distro=SID)
#             with (
#                 make_moncic() as moncic,
#                 moncic.session(),
#                 MockBuilder("sid", build) as builder,
#                 builder.container() as container,
#             ):
#                 src.gather_sources_from_host(builder.build, container)
#                 self.assertCountEqual(os.listdir(container.source_dir), [self.tarball_name])
#                 # TODO: @guest_only
#                 # TODO: def build_source_package(self) -> str:
#
#
# class TestDebianSourceDir1(DebianSourceDirMixin, unittest.TestCase):
#     tarball_name = "moncic-ci_0.1.0.orig.tar.gz"
#
#
# class TestDebianSourceDir2(DebianSourceDirMixin, unittest.TestCase):
#     tarball_name = "moncic-ci_0.1.0.orig.tar.xz"
#
#
# class DebianPlainGitMixin(GitFixtureMixin):
#     tarball_name: str
#     skip_tarball: bool = False
#
#     @classmethod
#     def setUpClass(cls):
#         super().setUpClass()
#         cls.git.add("testfile")
#         cls.git.commit("Initial")
#         cls.git.add("debian/changelog", "moncic-ci (0.1.0-1) UNRELEASED; urgency=low")
#         cls.git.commit("Debianized")
#         # Create mock tarball
#         if not cls.skip_tarball:
#             (cls.workdir / cls.tarball_name).write_bytes(b"")
#
#     def test_detect_local(self):
#         with InputSource.create(self.git.root) as isrc:
#             self.assertIsInstance(isrc, inputsource.LocalGit)
#
#             with self.assertRaises(Fail):
#                 isrc.detect_source(ROCKY9)
#
#             src = isrc.detect_source(SID)
#             self.assertIsInstance(src, debian.DebianPlainGit)
#
#     def test_detect_url(self):
#         with self.git.serve() as url:
#             with InputSource.create(url) as isrc:
#                 self.assertIsInstance(isrc, inputsource.URL)
#
#                 with self.assertRaises(Fail):
#                     isrc.detect_source(ROCKY9)
#
#                 src = isrc.detect_source(SID)
#                 self.assertIsInstance(src, debian.DebianPlainGit)
#
#     def test_build_source(self):
#         with InputSource.create(self.git.root) as isrc:
#             src = isrc.detect_source(SID)
#             self.assertEqual(src.get_build_class().__name__, "Debian")
#             build = src.make_build(distro=SID)
#             with (
#                 make_moncic() as moncic,
#                 moncic.session(),
#                 MockBuilder("sid", build) as builder,
#                 builder.container() as container,
#             ):
#                 src.gather_sources_from_host(builder.build, container)
#                 self.assertCountEqual(os.listdir(container.source_dir), [self.tarball_name])
#                 # TODO: @guest_only
#                 # TODO: def build_source_package(self) -> str:
#
#
# class TesttDebianPlainGit1(DebianPlainGitMixin, unittest.TestCase):
#     tarball_name = "moncic-ci_0.1.0.orig.tar.gz"
#     skip_tarball = False
#
#
# class TesttDebianPlainGit2(DebianPlainGitMixin, unittest.TestCase):
#     tarball_name = "moncic-ci_0.1.0.orig.tar.xz"
#     skip_tarball = False
#
#
# class TesttDebianPlainGit3(DebianPlainGitMixin, unittest.TestCase):
#     # Test without tarball: a .tar.xz one gets generated from git
#     tarball_name = "moncic-ci_0.1.0.orig.tar.xz"
#     skip_tarball = True
#
#
# class DebianGBPTestUpstreamMixin(GitFixtureMixin):
#     @classmethod
#     def setUpClass(cls):
#         super().setUpClass()
#         # Initial upstream
#         cls.git.add("testfile")
#         cls.git.commit("Initial commit")
#
#         # Debian branch
#         cls.git.git("checkout", "-b", cls.packaging_branch_name)
#         cls.git.add("debian/changelog", "moncic-ci (0.1.0-1) UNRELEASED; urgency=low")
#         cls.git.commit()
#
#         # New changes to upstream branch
#         cls.git.git("checkout", "main")
#         cls.git.add("testfile", "test content")
#         cls.git.commit("Updated testfile")
#
#         # TODO: add gdb.conf
#
#     def test_detect_local(self):
#         with InputSource.create(self.git.root) as isrc:
#             self.assertIsInstance(isrc, inputsource.LocalGit)
#
#             with self.assertRaises(Fail):
#                 isrc.detect_source(ROCKY9)
#
#             src = isrc.detect_source(SID)
#             self.assertIsInstance(src, debian.DebianGBPTestUpstream)
#
#     def test_detect_url(self):
#         with self.git.serve() as url:
#             with InputSource.create(url) as isrc:
#                 self.assertIsInstance(isrc, inputsource.URL)
#
#                 with self.assertRaises(Fail):
#                     isrc.detect_source(ROCKY9)
#
#                 src = isrc.detect_source(SID)
#                 self.assertIsInstance(src, debian.DebianGBPTestUpstream)
#
#     def test_build_source(self):
#         with InputSource.create(self.git.root) as isrc:
#             src = isrc.detect_source(SID)
#             self.assertEqual(src.get_build_class().__name__, "Debian")
#             build = src.make_build(distro=SID)
#             with (
#                 make_moncic() as moncic,
#                 moncic.session(),
#                 MockBuilder("sid", build) as builder,
#                 builder.container() as container,
#             ):
#                 src.gather_sources_from_host(builder.build, container)
#                 self.assertCountEqual(os.listdir(container.source_dir), [])
#
#             self.assertEqual(src.gbp_args, ["--git-upstream-tree=branch", "--git-upstream-branch=main"])
#
#
# class TestDebianGBPTestUpstreamUnstable(DebianGBPTestUpstreamMixin, unittest.TestCase):
#     packaging_branch_name = "debian/unstable"
#
#
# class TestDebianGBPTestUpstreamSid(DebianGBPTestUpstreamMixin, unittest.TestCase):
#     packaging_branch_name = "debian/sid"
#
#
# class TestDebianGBPRelease(GitFixtureMixin, unittest.TestCase):
#     @classmethod
#     def setUpClass(cls):
#         super().setUpClass()
#         # Initial upstream
#         cls.git.add("testfile")
#         cls.git.commit("Initial commit")
#         cls.git.git("tag", "upstream/0.1.0")
#
#         # Debian branch
#         cls.git.git("checkout", "-b", "debian/unstable")
#         cls.git.add("debian/changelog", "moncic-ci (0.1.0-1) UNRELEASED; urgency=low")
#         cls.git.add(
#             "debian/gbp.conf",
#             """
# [DEFAULT]
# upstream-branch=main
# upstream-tag=%(version)s
# debian-branch=debian/unstable
# """,
#         )
#         cls.git.commit()
#         cls.git.git("tag", "debian/0.1.0-1")
#
#     def test_detect_local(self):
#         with InputSource.create(self.git.root) as isrc:
#             self.assertIsInstance(isrc, inputsource.LocalGit)
#
#             with self.assertRaises(Fail):
#                 isrc.detect_source(ROCKY9)
#
#             src = isrc.detect_source(SID)
#             self.assertIsInstance(src, debian.DebianGBPRelease)
#
#     def test_detect_url(self):
#         with self.git.serve() as url:
#             with InputSource.create(url) as isrc:
#                 self.assertIsInstance(isrc, inputsource.URL)
#
#                 with self.assertRaises(Fail):
#                     isrc.detect_source(ROCKY9)
#
#                 src = isrc.detect_source(SID)
#                 self.assertIsInstance(src, debian.DebianGBPRelease)
#
#     def _test_build_source(self, path):
#         with InputSource.create(path) as isrc:
#             src = isrc.detect_source(SID)
#             self.assertIsInstance(src, debian.DebianGBPRelease)
#             self.assertEqual(src.get_build_class().__name__, "Debian")
#             build = src.make_build(distro=SID)
#             self.assertTrue(build.source.host_path.is_dir())
#             with (
#                 make_moncic() as moncic,
#                 moncic.session(),
#                 MockBuilder("sid", build) as builder,
#                 builder.container() as container,
#             ):
#                 src.gather_sources_from_host(builder.build, container)
#                 self.assertCountEqual(os.listdir(container.source_dir), [])
#
#             self.assertEqual(src.gbp_args, ["--git-upstream-tree=tag"])
#
#     def test_build_source_git(self):
#         self._test_build_source(self.git.root)
#
#     def test_build_source_url(self):
#         with self.git.serve() as url:
#             self._test_build_source(url)
#
#
# class TestDebianGBPTestDebian(GitFixtureMixin, unittest.TestCase):
#     @classmethod
#     def setUpClass(cls):
#         super().setUpClass()
#         # Initial upstream
#         cls.git.add("testfile")
#         cls.git.commit("Initial commit")
#
#         # Debian branch
#         cls.git.git("checkout", "-b", "debian/unstable")
#         cls.git.add("debian/changelog", "moncic-ci (0.1.0-1) UNRELEASED; urgency=low")
#         cls.git.add(
#             "debian/gbp.conf",
#             """
# [DEFAULT]
# upstream-branch=main
# upstream-tag=%(version)s
# debian-branch=debian/unstable
# """,
#         )
#         cls.git.commit()
#
#         # New changes to upstream branch
#         cls.git.git("checkout", "main")
#         cls.git.add("testfile", "test content")
#         cls.git.commit("Updated testfile")
#
#         # Leave the packaging branch as current
#         cls.git.git("checkout", "debian/unstable")
#
#     def test_detect_local(self):
#         with InputSource.create(self.git.root) as isrc:
#             self.assertIsInstance(isrc, inputsource.LocalGit)
#
#             with self.assertRaises(Fail):
#                 isrc.detect_source(ROCKY9)
#
#             src = isrc.detect_source(SID)
#             self.assertIsInstance(src, debian.DebianGBPTestDebian)
#
#     def test_detect_url(self):
#         with self.git.serve() as url:
#             with InputSource.create(url) as isrc:
#                 self.assertIsInstance(isrc, inputsource.URL)
#
#                 with self.assertRaises(Fail):
#                     isrc.detect_source(ROCKY9)
#
#                 src = isrc.detect_source(SID)
#                 self.assertIsInstance(src, debian.DebianGBPTestDebian)
#
#     def test_build_source(self):
#         with InputSource.create(self.git.root) as isrc:
#             src = isrc.detect_source(SID)
#             self.assertEqual(src.get_build_class().__name__, "Debian")
#             build = src.make_build(distro=SID)
#             with (
#                 make_moncic() as moncic,
#                 moncic.session(),
#                 MockBuilder("sid", build) as builder,
#                 builder.container() as container,
#             ):
#                 src.gather_sources_from_host(builder.build, container)
#                 self.assertCountEqual(os.listdir(container.source_dir), [])
#
#             self.assertEqual(src.gbp_args, ["--git-upstream-tree=branch"])
#
#
# class TestDebianDsc(WorkdirFixtureMixin, unittest.TestCase):
#     @classmethod
#     def setUpClass(cls):
#         super().setUpClass()
#         cls.dsc_file = cls.workdir / "moncic-ci_0.1.0-1.dsc"
#         cls.dsc_file.write_text(
#             """Format: 3.0 (quilt)
# Source: moncic-ci
# Binary: moncic-ci
# Version: 0.1.0-1
# Files:
#  d41d8cd98f00b204e9800998ecf8427e 0 moncic-ci_0.1.0.orig.tar.gz
#  d41d8cd98f00b204e9800998ecf8427e 0 moncic-ci_0.1.0-1.debian.tar.xz
# """
#         )
#
#         (cls.workdir / "moncic-ci_0.1.0.orig.tar.gz").write_bytes(b"")
#         (cls.workdir / "moncic-ci_0.1.0-1.debian.tar.xz").write_bytes(b"")
#
#     def test_detect_local(self):
#         with InputSource.create(self.dsc_file) as isrc:
#             self.assertIsInstance(isrc, inputsource.LocalFile)
#
#             with self.assertRaises(Fail):
#                 isrc.detect_source(ROCKY9)
#
#             src = isrc.detect_source(SID)
#             self.assertIsInstance(src, debian.DebianDsc)
#
#     def test_build_source(self):
#         with InputSource.create(self.dsc_file) as isrc:
#             src = isrc.detect_source(SID)
#             self.assertEqual(src.get_build_class().__name__, "Debian")
#             build = src.make_build(distro=SID)
#             with (
#                 make_moncic() as moncic,
#                 moncic.session(),
#                 MockBuilder("sid", build) as builder,
#                 builder.container() as container,
#             ):
#                 src.gather_sources_from_host(builder.build, container)
#                 self.assertCountEqual(
#                     os.listdir(container.source_dir),
#                     [
#                         "moncic-ci_0.1.0-1.dsc",
#                         "moncic-ci_0.1.0.orig.tar.gz",
#                         "moncic-ci_0.1.0-1.debian.tar.xz",
#                     ],
#                 )
