from __future__ import annotations

from pathlib import Path
from unittest import mock
from typing import cast

from moncic.distro import DistroFamily
from moncic.distro.debian import DebianDistro
from moncic.exceptions import Fail
from moncic.source import Source
from moncic.source.local import File, Dir, Git
from moncic.source.debian import (
    DebianSource,
    DebianDir,
    DebianDsc,
    SourceInfo,
    GBPInfo,
    DebianGitLegacy,
    DebianGBPTestDebian,
    DebianGBPTestUpstream,
    DebianGBPRelease,
    DSCInfo,
)
from moncic.unittest import make_moncic

from .source import GitFixture, MockBuilder, WorkdirFixture, GitRepo

SID = cast(DebianDistro, DistroFamily.lookup_distro("sid"))


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
            with mock.patch("moncic.source.debian.DebianDsc.prepare_from_file") as patched:
                DebianSource.create_from_file(src, distro=SID)
            patched.assert_called_once()

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
            with mock.patch("moncic.source.debian.DebianDir.prepare_from_dir") as patched:
                DebianSource.create_from_dir(src, distro=SID)
            patched.assert_called_once()

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
            with mock.patch("moncic.source.debian.DebianGitLegacy.prepare_from_git") as patched:
                DebianSource.create_from_git(src, distro=SID)
            patched.assert_called_once()

    def test_from_git_debian_from_upstream(self) -> None:
        git = self.make_git_repo("gitgbpupstream")
        # Initial upstream
        git.add("testfile")
        git.commit("Initial commit")

        # Debian branch
        git.git("checkout", "-b", "debian/sid")
        git.add("debian/changelog", "gitgbpupstream (0.1.0-1) UNRELEASED; urgency=low")
        git.commit()

        # New changes to upstream branch
        git.git("checkout", "main")
        git.add("testfile", "test content")
        git.commit("Updated testfile")

        # TODO: add gdb.conf

        with Source.create_local(source=git.root) as src:
            assert isinstance(src, Git)
            with mock.patch("moncic.source.debian.DebianGBPTestUpstream.prepare_from_git") as patched:
                DebianSource.create_from_git(src, distro=SID)
            patched.assert_called_once()

    def test_from_git_debian_release(self) -> None:
        git = self.make_git_repo("gitgbprelease")
        # Initial upstream
        git.add("testfile")
        git.commit("Initial commit")
        git.git("tag", "upstream/0.1.0")

        # Debian branch
        git.git("checkout", "-b", "debian/unstable")
        git.add("debian/changelog", "moncic-ci (0.1.0-1) UNRELEASED; urgency=low")
        git.add(
            "debian/gbp.conf",
            """
[DEFAULT]
upstream-branch=main
upstream-tag=%(version)s
debian-branch=debian/unstable
""",
        )
        git.commit()
        git.git("tag", "debian/0.1.0-1")

        with Source.create_local(source=git.root) as src:
            assert isinstance(src, Git)
            with mock.patch("moncic.source.debian.DebianGBPRelease.prepare_from_git") as patched:
                DebianSource.create_from_git(src, distro=SID)
            patched.assert_called_once()

    def test_from_git_debian_test(self) -> None:
        git = self.make_git_repo("gitgbptest")

        # Initial upstream
        git.add("testfile")
        git.commit("Initial commit")

        # Debian branch
        git.git("checkout", "-b", "debian/unstable")
        git.add("debian/changelog", "moncic-ci (0.1.0-1) UNRELEASED; urgency=low")
        git.add(
            "debian/gbp.conf",
            """
[DEFAULT]
upstream-branch=main
upstream-tag=%(version)s
debian-branch=debian/unstable
""",
        )
        git.commit()

        # New changes to upstream branch
        git.git("checkout", "main")
        git.add("testfile", "test content")
        git.commit("Updated testfile")

        # Leave the packaging branch as current
        git.git("checkout", "debian/unstable")

        with Source.create_local(source=git.root) as src:
            assert isinstance(src, Git)
            with mock.patch("moncic.source.debian.DebianGBPTestDebian.prepare_from_git") as patched:
                DebianSource.create_from_git(src, distro=SID)
            patched.assert_called_once()


class TestDebianDsc(WorkdirFixture):
    path: Path

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.path = cls.workdir / "moncic-ci_0.1.0-1.dsc"
        cls.path.write_text(
            """Format: 3.0 (quilt)
Source: moncic-ci
Binary: moncic-ci
Version: 0.1.0-1
Files:
 d41d8cd98f00b204e9800998ecf8427e 0 moncic-ci_0.1.0.orig.tar.gz
 d41d8cd98f00b204e9800998ecf8427e 0 moncic-ci_0.1.0-1.debian.tar.xz
"""
        )

        (cls.workdir / "moncic-ci_0.1.0.orig.tar.gz").write_bytes(b"")
        (cls.workdir / "moncic-ci_0.1.0-1.debian.tar.xz").write_bytes(b"")

    def test_prepare_from_file(self) -> None:
        with Source.create_local(source=self.path) as parent:
            assert isinstance(parent, File)
            src = DebianDsc.prepare_from_file(parent, distro=SID)
            assert isinstance(src, DebianDsc)
            self.assertIs(src.parent, parent)
            self.assertEqual(src.path, parent.path)
            self.assertEqual(src.command_log, [])
            self.assertEqual(
                src.source_info,
                DSCInfo(
                    name="moncic-ci",
                    version="0.1.0-1",
                    dsc_filename="moncic-ci_0.1.0-1.dsc",
                    tar_stem="moncic-ci_0.1.0.orig.tar",
                ),
            )


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


class TestDebianLegacy(WorkdirFixture):
    path: Path

    def assertCommonAttributes(self, src: DebianDir, tar_path: Path) -> None:
        assert isinstance(src, DebianDir)
        self.assertEqual(src.path, self.path)
        self.assertEqual(src.command_log, [])
        self.assertEqual(
            src.source_info,
            SourceInfo(
                name="moncic-ci",
                version="0.1.0-1",
                dsc_filename="moncic-ci_0.1.0-1.dsc",
                tar_stem="moncic-ci_0.1.0.orig.tar",
            ),
        )
        self.assertEqual(src.tarball, tar_path)


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


class TestDebianLegacyDir(TestDebianLegacy):
    path: Path

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.path = cls.workdir / "moncic-ci"
        debian_dir = cls.path / "debian"
        debian_dir.mkdir(parents=True)
        (debian_dir / "changelog").write_text("moncic-ci (0.1.0-1) UNRELEASED; urgency=low")

    def test_prepare_from_dir_gz(self) -> None:
        tar_path = self.workdir / "moncic-ci_0.1.0.orig.tar.gz"
        tar_path.touch()
        self.addCleanup(tar_path.unlink)

        with Source.create_local(source=self.path) as parent:
            assert isinstance(parent, Dir)
            src = DebianDir.prepare_from_dir(parent, distro=SID)
            self.assertCommonAttributes(src, tar_path)

    def test_prepare_from_dir_xz(self) -> None:
        tar_path = self.workdir / "moncic-ci_0.1.0.orig.tar.xz"
        tar_path.touch()
        self.addCleanup(tar_path.unlink)

        with Source.create_local(source=self.path) as parent:
            assert isinstance(parent, Dir)
            src = DebianDir.prepare_from_dir(parent, distro=SID)
            self.assertCommonAttributes(src, tar_path)


class TestDebianLegacyGit(TestDebianLegacy, GitFixture):
    git_name = "moncic-ci"
    source_info: SourceInfo

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.git.add("debian/changelog", "moncic-ci (0.1.0-1) UNRELEASED; urgency=low\n")
        cls.git.commit()
        cls.source_info = SourceInfo.create_from_dir(cls.path)

    def test_prepare_from_git_gz(self) -> None:
        tar_path = self.workdir / "moncic-ci_0.1.0.orig.tar.gz"
        tar_path.touch()
        self.addCleanup(tar_path.unlink)

        with Source.create_local(source=self.path) as parent:
            assert isinstance(parent, Git)
            src = DebianGitLegacy.prepare_from_git(parent, distro=SID, source_info=self.source_info)
            self.assertCommonAttributes(src, tar_path)
            self.assertIs(src.repo, parent.repo)
            self.assertTrue(src.readonly)

    def test_prepare_from_git_xz(self) -> None:
        tar_path = self.workdir / "moncic-ci_0.1.0.orig.tar.xz"
        tar_path.touch()
        self.addCleanup(tar_path.unlink)

        with Source.create_local(source=self.path) as parent:
            assert isinstance(parent, Git)
            src = DebianGitLegacy.prepare_from_git(
                parent, distro=SID, source_info=SourceInfo.create_from_dir(self.path)
            )
            self.assertCommonAttributes(src, tar_path)
            self.assertIs(src.repo, parent.repo)
            self.assertTrue(src.readonly)


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


class TestDebianGBPTestUpstream(GitFixture):
    git_name = "moncic-ci"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Initial upstream
        cls.git.add("testfile")
        cls.git.commit("Initial commit")

        # Debian branch
        cls.git.git("checkout", "-b", "debian/sid")
        cls.git.add("debian/changelog", "moncic-ci (0.1.0-1) UNRELEASED; urgency=low")
        cls.git.commit()

        # New changes to upstream branch
        cls.git.git("checkout", "main")
        cls.git.add("testfile", "test content")
        cls.git.commit("Updated testfile")

        # TODO: add gdb.conf

    def test_prepare_from_git(self) -> None:
        with Source.create_local(source=self.path) as parent:
            assert isinstance(parent, Git)
            src = DebianGBPTestUpstream.prepare_from_git(
                parent, distro=SID, packaging_branch=parent.repo.refs["debian/sid"]
            )
            assert isinstance(src, DebianGBPTestUpstream)
            self.assertNotEqual(src.path, self.path)
            self.assertIsNot(src.repo, parent.repo)
            self.assertFalse(src.readonly)
            self.assertEqual(
                src.source_info,
                SourceInfo(
                    name="moncic-ci",
                    version="0.1.0-1",
                    dsc_filename="moncic-ci_0.1.0-1.dsc",
                    tar_stem="moncic-ci_0.1.0.orig.tar",
                ),
            )
            self.assertEqual(src.gbp_args, ["--git-upstream-tree=branch", "--git-upstream-branch=main"])


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


class TestDebianGBPRelease(GitFixture):
    git_name = "moncic-ci"
    source_info: SourceInfo
    gbp_info: GBPInfo

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()

        # Initial upstream
        cls.git.add("testfile")
        cls.git.commit("Initial commit")
        cls.git.git("tag", "upstream/0.1.0")

        # Debian branch
        cls.git.git("checkout", "-b", "debian/unstable")
        cls.git.add("debian/changelog", "moncic-ci (0.1.0-1) UNRELEASED; urgency=low")
        cls.git.add(
            "debian/gbp.conf",
            """
[DEFAULT]
upstream-branch=main
upstream-tag=%(version)s
debian-branch=debian/unstable
""",
        )
        cls.git.commit()
        cls.git.git("tag", "debian/0.1.0-1")

        cls.source_info = SourceInfo.create_from_dir(cls.path)
        cls.gbp_info = cls.source_info.parse_gbp(cls.path / "debian" / "gbp.conf")

    def test_prepare_from_git(self) -> None:
        with Source.create_local(source=self.path) as parent:
            assert isinstance(parent, Git)
            src = DebianGBPRelease.prepare_from_git(
                parent, distro=SID, source_info=self.source_info, gbp_info=self.gbp_info
            )
            assert isinstance(src, DebianGBPRelease)
            self.assertEqual(src.path, self.path)
            self.assertIs(src.repo, parent.repo)
            self.assertTrue(src.readonly)
            self.assertEqual(src.source_info, self.source_info)
            self.assertEqual(src.gbp_info, self.gbp_info)
            self.assertEqual(src.gbp_args, ["--git-upstream-tree=tag"])


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


class TestDebianGBPTestDebian(GitFixture):
    git_name = "moncic-ci"
    source_info: SourceInfo
    gbp_info: GBPInfo

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()

        # Initial upstream
        cls.git.add("testfile")
        cls.git.commit("Initial commit")

        # Debian branch
        cls.git.git("checkout", "-b", "debian/unstable")
        cls.git.add("debian/changelog", "moncic-ci (0.1.0-1) UNRELEASED; urgency=low")
        cls.git.add(
            "debian/gbp.conf",
            """
[DEFAULT]
upstream-branch=main
upstream-tag=%(version)s
debian-branch=debian/unstable
""",
        )
        cls.git.commit()

        # New changes to upstream branch
        cls.git.git("checkout", "main")
        cls.git.add("testfile", "test content")
        cls.git.commit("Updated testfile")

        # Leave the packaging branch as current
        cls.git.git("checkout", "debian/unstable")

        cls.source_info = SourceInfo.create_from_dir(cls.path)
        cls.gbp_info = cls.source_info.parse_gbp(cls.path / "debian" / "gbp.conf")

    def test_prepare_from_git(self) -> None:
        with Source.create_local(source=self.path) as parent:
            assert isinstance(parent, Git)
            src = DebianGBPTestDebian.prepare_from_git(
                parent, distro=SID, source_info=self.source_info, gbp_info=self.gbp_info
            )
            self.assertIsInstance(src, DebianGBPTestDebian)
            assert isinstance(src, DebianGBPTestDebian)
            self.assertNotEqual(src.path, self.path)
            self.assertIsNot(src.repo, parent.repo)
            self.assertFalse(src.readonly)
            self.assertEqual(src.source_info, self.source_info)
            self.assertEqual(src.gbp_info, self.gbp_info)
            self.assertEqual(src.gbp_args, ["--git-upstream-tree=branch"])


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
