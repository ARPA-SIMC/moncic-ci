from __future__ import annotations

import abc
import contextlib
import tempfile
from pathlib import Path
from unittest import mock
from typing import cast, Generator, ContextManager

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
    DebianDirGit,
    DebianGBPTestDebian,
    DebianGBPTestUpstream,
    DebianGBPRelease,
    DSCInfo,
)

from .source import (
    GitFixture,
    WorkdirFixture,
    GitRepo,
    create_lint_version_fixture_path,
    create_lint_version_fixture_git,
)

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
            with self.assertRaisesRegex(Fail, f"{path}: cannot detect source type"):
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
            with self.assertRaisesRegex(Fail, f"{path}: cannot detect source type"):
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
            with self.assertRaisesRegex(Fail, f"{git.root}: cannot detect source type"):
                DebianSource.create_from_git(src, distro=SID)

    def test_from_git_debian_legacy(self) -> None:
        git = self.make_git_repo("gitlegacy")
        tar_path = self.workdir / "gitlegacy_0.1.0.orig.tar.gz"
        tar_path.touch()
        git.add("debian/changelog", "gitlegacy (0.1.0-1) UNRELEASED; urgency=low\n")
        git.commit()

        with Source.create_local(source=git.root) as src:
            assert isinstance(src, Git)
            with mock.patch("moncic.source.debian.DebianDir.prepare_from_git") as patched:
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

    def test_lint_path_is_packaging(self) -> None:
        path = self.workdir / "file.dsc"
        path.write_text(
            """Format: 3.0 (quilt)
Source: moncic-ci
Binary: moncic-ci
Version: 0.1.0-1
Files:
 d41d8cd98f00b204e9800998ecf8427e 0 moncic-ci_0.1.0.orig.tar.gz
 d41d8cd98f00b204e9800998ecf8427e 0 moncic-ci_0.1.0-1.debian.tar.xz
"""
        )
        with Source.create_local(source=path) as parent:
            assert isinstance(parent, File)
            src = DebianSource.create_from_file(parent, distro=SID)
            assert isinstance(src, DebianDsc)
            self.assertFalse(src.lint_path_is_packaging(Path("test")))
            self.assertFalse(src.lint_path_is_packaging(Path("test.spec)")))
            self.assertTrue(src.lint_path_is_packaging(Path("debian")))
            self.assertTrue(src.lint_path_is_packaging(Path("debian/control")))
            self.assertTrue(src.lint_path_is_packaging(Path("debian/foo/bar/baz")))
            self.assertFalse(src.lint_path_is_packaging(Path("upstream/control")))


class TestDebianDsc(WorkdirFixture):
    path: Path
    source_info: DSCInfo

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
        cls.source_info = DSCInfo(
            name="moncic-ci",
            version="0.1.0-1",
            dsc_filename="moncic-ci_0.1.0-1.dsc",
            tar_stem="moncic-ci_0.1.0.orig.tar",
            file_list=["moncic-ci_0.1.0.orig.tar.gz", "moncic-ci_0.1.0-1.debian.tar.xz"],
        )

    @contextlib.contextmanager
    def source(self) -> Generator[DebianDsc, None, None]:
        with Source.create_local(source=self.path) as parent:
            assert isinstance(parent, File)
            src = DebianDsc.prepare_from_file(parent, distro=SID)
            assert isinstance(src, DebianDsc)
            self.assertIs(src.parent, parent)
            yield src

    def test_prepare_from_file(self) -> None:
        with self.source() as src:
            self.assertEqual(src.path, src.parent.path)
            self.assertEqual(src.command_log, [])
            self.assertEqual(
                src.source_info,
                self.source_info,
            )

    def test_derivation(self) -> None:
        with self.source() as src:
            self.assertEqual(
                src.derive_kwargs(),
                {
                    "parent": src,
                    "name": self.path.as_posix(),
                    "path": self.path,
                    "distro": SID,
                    "source_info": self.source_info,
                },
            )

    def test_collect_build_artifacts(self) -> None:
        with self.source() as src:
            with tempfile.TemporaryDirectory() as destdir_str:
                destdir = Path(destdir_str)
                src.collect_build_artifacts(destdir)

                self.assertEqual(
                    sorted(p.name for p in destdir.iterdir()),
                    [
                        "moncic-ci_0.1.0-1.debian.tar.xz",
                        "moncic-ci_0.1.0-1.dsc",
                        "moncic-ci_0.1.0.orig.tar.gz",
                    ],
                )

    def test_build_source_package(self) -> None:
        with self.source() as src:
            self.assertEqual(src.build_source_package(), src.path)

    def test_lint_find_versions(self):
        with self.source() as src:
            self.assertEqual(src.lint_find_versions(), {"debian-release": "0.1.0-1", "debian-upstream": "0.1.0"})
            self.assertEqual(
                src.lint_find_versions(allow_exec=True), {"debian-release": "0.1.0-1", "debian-upstream": "0.1.0"}
            )


class TestDebianLegacy(WorkdirFixture, abc.ABC):
    path: Path
    source_info: SourceInfo

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.source_info = SourceInfo(
            name="moncic-ci",
            version="0.1.0-1",
            dsc_filename="moncic-ci_0.1.0-1.dsc",
            tar_stem="moncic-ci_0.1.0.orig.tar",
        )

    @abc.abstractmethod
    def source(self) -> ContextManager[DebianSource]: ...

    def create_tar(self, name: str) -> Path:
        tar_path = self.workdir / name
        tar_path.touch()
        self.addCleanup(tar_path.unlink)
        return tar_path

    def assertCommonAttributes(self, src: DebianDir) -> None:
        assert isinstance(src, DebianDir)
        self.assertEqual(src.path, self.path)
        self.assertEqual(src.command_log, [])
        self.assertEqual(src.source_info, self.source_info)

    def test_build_source_package(self) -> None:
        with self.source() as src:
            mock_result = Path("result.dsc")

            with mock.patch("subprocess.run") as subprocess_run:
                with mock.patch("moncic.source.debian.DebianSource._find_built_dsc", return_value=mock_result):
                    dsc_path = src.build_source_package()

            self.assertEqual(dsc_path, mock_result)
            subprocess_run.assert_called_once_with(
                ["dpkg-buildpackage", "-S", "--no-sign", "--no-pre-clean"], check=True, cwd=src.path
            )

    def test_collect_build_artifacts_gz(self) -> None:
        self.create_tar("moncic-ci_0.1.0.orig.tar.gz")
        with self.source() as src:
            with tempfile.TemporaryDirectory() as destdir_str:
                destdir = Path(destdir_str)
                src.collect_build_artifacts(destdir)

                self.assertEqual(
                    sorted(p.name for p in destdir.iterdir()),
                    ["moncic-ci_0.1.0.orig.tar.gz"],
                )

    def test_collect_build_artifacts_xz(self) -> None:
        self.create_tar("moncic-ci_0.1.0.orig.tar.xz")
        with self.source() as src:
            with tempfile.TemporaryDirectory() as destdir_str:
                destdir = Path(destdir_str)
                src.collect_build_artifacts(destdir)

                self.assertEqual(
                    sorted(p.name for p in destdir.iterdir()),
                    ["moncic-ci_0.1.0.orig.tar.xz"],
                )

    def test_lint_find_versions(self):
        with self.source() as src:
            self.assertEqual(
                src.lint_find_versions(),
                {
                    "autotools": "1.1",
                    "meson": "1.2",
                    "cmake": "1.3",
                    "news": "1.4",
                    "debian-release": "0.1.0-1",
                    "debian-upstream": "0.1.0",
                },
            )
            self.assertEqual(
                src.lint_find_versions(allow_exec=True),
                {
                    "autotools": "1.1",
                    "meson": "1.2",
                    "cmake": "1.3",
                    "news": "1.4",
                    "setup.py": "1.5",
                    "debian-release": "0.1.0-1",
                    "debian-upstream": "0.1.0",
                },
            )


class TestDebianDir(TestDebianLegacy):
    path: Path

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.path = cls.workdir / "moncic-ci"
        cls.path.mkdir(parents=True)
        create_lint_version_fixture_path(cls.path)
        debian_dir = cls.path / "debian"
        debian_dir.mkdir(parents=True, exist_ok=True)
        (debian_dir / "changelog").write_text("moncic-ci (0.1.0-1) UNRELEASED; urgency=low")

    @contextlib.contextmanager
    def source(self) -> Generator[DebianDir, None, None]:
        with Source.create_local(source=self.path) as parent:
            assert isinstance(parent, Dir)
            src = DebianDir.prepare_from_dir(parent, distro=SID)
            assert isinstance(src, DebianDir)
            self.assertIs(src.parent, parent)
            yield src

    def test_prepare_from_dir(self) -> None:
        with self.source() as src:
            self.assertCommonAttributes(src)

    def test_derivation(self) -> None:
        with self.source() as src:
            self.assertEqual(
                src.derive_kwargs(),
                {
                    "parent": src,
                    "name": self.path.as_posix(),
                    "path": self.path,
                    "distro": SID,
                    "source_info": self.source_info,
                },
            )

    def test_collect_build_artifacts_missing_tar(self) -> None:
        with self.source() as src:
            with tempfile.TemporaryDirectory() as destdir_str:
                destdir = Path(destdir_str)
                with self.assertRaisesRegex(Fail, "Tarball \S* not found"):
                    src.collect_build_artifacts(destdir)


class TestDebianDirGit(TestDebianLegacy, GitFixture):
    git_name = "moncic-ci"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        create_lint_version_fixture_git(cls.git)
        cls.git.add("testfile")
        cls.git.add("debian/changelog", "moncic-ci (0.1.0-1) UNRELEASED; urgency=low\n")
        cls.git.commit()

    @contextlib.contextmanager
    def source(self) -> Generator[DebianDirGit, None, None]:
        with Source.create_local(source=self.path) as parent:
            assert isinstance(parent, Git)
            src = DebianDir.prepare_from_git(parent, distro=SID, source_info=self.source_info)
            assert isinstance(src, DebianDirGit)
            self.assertIs(src.parent, parent)
            yield src

    def assertCommonGitAttributes(
        self,
        src: DebianDirGit,
    ) -> None:
        super().assertCommonAttributes(src)
        assert src.parent
        assert isinstance(src.parent, Git)
        self.assertIs(src.repo, src.parent.repo)
        self.assertTrue(src.readonly)

    def test_prepare_from_git(self) -> None:
        with self.source() as src:
            self.assertCommonGitAttributes(src)

    def test_derivation(self) -> None:
        with self.source() as src:
            self.assertEqual(
                src.derive_kwargs(),
                {
                    "parent": src,
                    "name": self.path.as_posix(),
                    "path": self.path,
                    "readonly": True,
                    "repo": src.repo,
                    "distro": SID,
                    "source_info": self.source_info,
                },
            )

    def test_collect_build_artifacts_missing_tar(self) -> None:
        with self.source() as src:
            with tempfile.TemporaryDirectory() as destdir_str:
                destdir = Path(destdir_str)
                src.collect_build_artifacts(destdir)

                self.assertEqual(
                    sorted(p.name for p in destdir.iterdir()),
                    ["moncic-ci_0.1.0.orig.tar.xz"],
                )


# Avoid running test from the base fixture outside derived classes
del TestDebianLegacy


class TestDebianGBPTestUpstream(GitFixture):
    git_name = "moncic-ci"
    source_info: SourceInfo
    gbp_info: GBPInfo

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Initial upstream
        cls.git.add("testfile")
        create_lint_version_fixture_git(cls.git, rpm=False, debian=False)
        cls.git.commit("Initial commit")

        # Debian branch
        cls.git.git("checkout", "-b", "debian/sid")
        cls.git.add("debian/changelog", "moncic-ci (0.1.0-1) UNRELEASED; urgency=low")
        cls.git.commit()

        # New changes to upstream branch
        cls.git.git("checkout", "main")
        cls.git.add("testfile", "test content")
        cls.git.commit("Updated testfile")

        cls.source_info = SourceInfo(
            name="moncic-ci",
            version="0.1.0-1",
            dsc_filename="moncic-ci_0.1.0-1.dsc",
            tar_stem="moncic-ci_0.1.0.orig.tar",
        )

        # TODO: add gdb.conf

        # Default computed gbp.conf values
        cls.gbp_info = GBPInfo(
            upstream_branch="upstream",
            upstream_tag="upstream/0.1.0",
            debian_branch="master",
            debian_tag="debian/0.1.0-1",
        )

    @contextlib.contextmanager
    def source(self) -> Generator[DebianGBPTestUpstream, None, None]:
        with Source.create_local(source=self.path) as parent:
            assert isinstance(parent, Git)
            src = DebianGBPTestUpstream.prepare_from_git(
                parent, distro=SID, packaging_branch=parent.repo.refs["debian/sid"]
            )
            assert isinstance(src, DebianGBPTestUpstream)
            assert isinstance(src.parent, Git)
            assert isinstance(src.parent.parent, Git)
            self.assertIsNot(src.parent, parent)
            self.assertIs(src.parent.parent, parent)
            yield src

    def test_prepare_from_git(self) -> None:
        with self.source() as src:
            assert isinstance(src.parent, Git)
            assert isinstance(src.parent.parent, Git)
            self.assertNotEqual(src.path, self.path)
            self.assertIs(src.repo, src.parent.repo)
            self.assertIsNot(src.repo, src.parent.parent.repo)
            self.assertFalse(src.readonly)
            self.assertEqual(src.source_info, self.source_info)
            self.assertEqual(src.gbp_info, self.gbp_info)
            self.assertEqual(src.gbp_args, ["--git-upstream-tree=branch", "--git-upstream-branch=main"])

    def test_derivation(self) -> None:
        self.maxDiff = None
        with self.source() as src:
            self.assertEqual(
                src.derive_kwargs(),
                {
                    "parent": src,
                    "name": self.path.as_posix(),
                    "path": src.path,
                    "readonly": False,
                    "repo": src.repo,
                    "distro": SID,
                    "source_info": self.source_info,
                    "gbp_info": self.gbp_info,
                    "gbp_args": ["--git-upstream-tree=branch", "--git-upstream-branch=main"],
                    "packaging_branch": "debian/sid",
                },
            )

    def test_collect_build_artifacts(self) -> None:
        with self.source() as src:
            with tempfile.TemporaryDirectory() as destdir_str:
                destdir = Path(destdir_str)
                src.collect_build_artifacts(destdir)

                self.assertEqual(
                    sorted(p.name for p in destdir.iterdir()),
                    [],
                )

    def test_build_source_package(self) -> None:
        with self.source() as src:
            mock_result = Path("result.dsc")

            with mock.patch("subprocess.run") as subprocess_run:
                with mock.patch("moncic.source.debian.DebianSource._find_built_dsc", return_value=mock_result):
                    dsc_path = src.build_source_package()

            self.assertEqual(dsc_path, mock_result)
            subprocess_run.assert_called_once_with(
                [
                    "gbp",
                    "buildpackage",
                    "--git-ignore-new",
                    "-d",
                    "-S",
                    "--no-sign",
                    "--no-pre-clean",
                    "--git-upstream-tree=branch",
                    "--git-upstream-branch=main",
                ],
                check=True,
                cwd=src.path,
            )

    def test_lint_find_versions(self):
        with self.source() as src:
            self.assertEqual(
                src.lint_find_versions(),
                {
                    "autotools": "1.1",
                    "meson": "1.2",
                    "cmake": "1.3",
                    "news": "1.4",
                    "debian-release": "0.1.0-1",
                    "debian-upstream": "0.1.0",
                },
            )
            self.assertEqual(
                src.lint_find_versions(allow_exec=True),
                {
                    "autotools": "1.1",
                    "meson": "1.2",
                    "cmake": "1.3",
                    "news": "1.4",
                    "setup.py": "1.5",
                    "debian-release": "0.1.0-1",
                    "debian-upstream": "0.1.0",
                },
            )


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
        create_lint_version_fixture_git(cls.git, rpm=False, debian=False)
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

    @contextlib.contextmanager
    def source(self) -> Generator[DebianGBPRelease, None, None]:
        with Source.create_local(source=self.path) as parent:
            assert isinstance(parent, Git)
            src = DebianGBPRelease.prepare_from_git(
                parent, distro=SID, source_info=self.source_info, gbp_info=self.gbp_info
            )
            assert isinstance(src, DebianGBPRelease)
            self.assertIs(src.parent, parent)
            yield src

    def test_prepare_from_git(self) -> None:
        with self.source() as src:
            assert isinstance(src.parent, Git)
            self.assertEqual(src.path, self.path)
            self.assertIs(src.repo, src.parent.repo)
            self.assertTrue(src.readonly)
            self.assertEqual(src.source_info, self.source_info)
            self.assertEqual(src.gbp_info, self.gbp_info)
            self.assertEqual(src.gbp_args, ["--git-upstream-tree=tag"])

    def test_derivation(self) -> None:
        self.maxDiff = None
        with self.source() as src:
            self.assertEqual(
                src.derive_kwargs(),
                {
                    "parent": src,
                    "name": self.path.as_posix(),
                    "path": src.path,
                    "readonly": True,
                    "repo": src.repo,
                    "distro": SID,
                    "source_info": self.source_info,
                    "gbp_info": self.gbp_info,
                    "gbp_args": ["--git-upstream-tree=tag"],
                },
            )

    def test_collect_build_artifacts(self) -> None:
        with self.source() as src:
            with tempfile.TemporaryDirectory() as destdir_str:
                destdir = Path(destdir_str)
                src.collect_build_artifacts(destdir)

                self.assertEqual(
                    sorted(p.name for p in destdir.iterdir()),
                    [],
                )

    def test_build_source_package(self) -> None:
        with self.source() as src:
            mock_result = Path("result.dsc")

            with mock.patch("subprocess.run") as subprocess_run:
                with mock.patch("moncic.source.debian.DebianSource._find_built_dsc", return_value=mock_result):
                    dsc_path = src.build_source_package()

            self.assertEqual(dsc_path, mock_result)
            subprocess_run.assert_called_once_with(
                [
                    "gbp",
                    "buildpackage",
                    "--git-ignore-new",
                    "-d",
                    "-S",
                    "--no-sign",
                    "--no-pre-clean",
                    "--git-upstream-tree=tag",
                ],
                check=True,
                cwd=src.path,
            )

    def test_lint_find_versions(self):
        with self.source() as src:
            self.assertEqual(
                src.lint_find_versions(),
                {
                    "autotools": "1.1",
                    "meson": "1.2",
                    "cmake": "1.3",
                    "news": "1.4",
                    "debian-release": "0.1.0-1",
                    "debian-upstream": "0.1.0",
                },
            )
            self.assertEqual(
                src.lint_find_versions(allow_exec=True),
                {
                    "autotools": "1.1",
                    "meson": "1.2",
                    "cmake": "1.3",
                    "news": "1.4",
                    "setup.py": "1.5",
                    "debian-release": "0.1.0-1",
                    "debian-upstream": "0.1.0",
                },
            )


class TestDebianGBPTestDebian(GitFixture):
    git_name = "moncic-ci"
    source_info: SourceInfo
    gbp_info: GBPInfo

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()

        # Initial upstream
        cls.git.add("testfile")
        create_lint_version_fixture_git(cls.git, rpm=False, debian=False)
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

    @contextlib.contextmanager
    def source(self) -> Generator[DebianGBPTestDebian, None, None]:
        with Source.create_local(source=self.path) as parent:
            assert isinstance(parent, Git)
            src = DebianGBPTestDebian.prepare_from_git(
                parent, distro=SID, source_info=self.source_info, gbp_info=self.gbp_info
            )
            assert isinstance(src, DebianGBPTestDebian)
            assert isinstance(src.parent, Git)
            assert isinstance(src.parent.parent, Git)
            self.assertIsNot(src.parent, parent)
            self.assertIs(src.parent.parent, parent)
            yield src

    def test_prepare_from_git(self) -> None:
        with self.source() as src:
            assert isinstance(src.parent, Git)
            assert isinstance(src.parent.parent, Git)
            self.assertNotEqual(src.path, self.path)
            self.assertIs(src.repo, src.parent.repo)
            self.assertIsNot(src.repo, src.parent.parent.repo)
            self.assertFalse(src.readonly)
            self.assertEqual(src.source_info, self.source_info)
            self.assertEqual(src.gbp_info, self.gbp_info)
            self.assertEqual(src.gbp_args, ["--git-upstream-tree=branch"])

    def test_collect_build_artifacts(self) -> None:
        with self.source() as src:
            with tempfile.TemporaryDirectory() as destdir_str:
                destdir = Path(destdir_str)
                src.collect_build_artifacts(destdir)

                self.assertEqual(
                    sorted(p.name for p in destdir.iterdir()),
                    [],
                )

    def test_build_source_package(self) -> None:
        with self.source() as src:
            mock_result = Path("result.dsc")

            with mock.patch("subprocess.run") as subprocess_run:
                with mock.patch("moncic.source.debian.DebianSource._find_built_dsc", return_value=mock_result):
                    dsc_path = src.build_source_package()

            self.assertEqual(dsc_path, mock_result)
            subprocess_run.assert_called_once_with(
                [
                    "gbp",
                    "buildpackage",
                    "--git-ignore-new",
                    "-d",
                    "-S",
                    "--no-sign",
                    "--no-pre-clean",
                    "--git-upstream-tree=branch",
                ],
                check=True,
                cwd=src.path,
            )

    def test_lint_find_versions(self):
        with self.source() as src:
            self.assertEqual(
                src.lint_find_versions(),
                {
                    "autotools": "1.1",
                    "meson": "1.2",
                    "cmake": "1.3",
                    "news": "1.4",
                    "debian-release": "0.1.0-1",
                    "debian-upstream": "0.1.0",
                },
            )
            self.assertEqual(
                src.lint_find_versions(allow_exec=True),
                {
                    "autotools": "1.1",
                    "meson": "1.2",
                    "cmake": "1.3",
                    "news": "1.4",
                    "setup.py": "1.5",
                    "debian-release": "0.1.0-1",
                    "debian-upstream": "0.1.0",
                },
            )
