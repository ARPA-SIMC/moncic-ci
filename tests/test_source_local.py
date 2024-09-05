from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from moncic.distro import DistroFamily
from moncic.exceptions import Fail
from moncic.source import Source
from moncic.source.local import File, Dir, Git
from moncic.source.debian import DebianDsc, DebianSourceDir
from moncic.source.rpm import ARPASourceDir

from .source import WorkdirFixture, GitFixture

ROCKY9 = DistroFamily.lookup_distro("rocky9")
SID = DistroFamily.lookup_distro("sid")


class TestFile(WorkdirFixture):
    file: Path
    dsc: Path

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.file = cls.workdir / "testfile"
        cls.file.touch()
        cls.dsc = cls.workdir / "testfile.dsc"
        cls.dsc.touch()

    def test_plain_file_from_path(self) -> None:
        with Source.create(source=self.file) as src:
            assert isinstance(src, File)
            self.assertEqual(src.name, self.file.as_posix())
            self.assertEqual(src.path, self.file)

    def test_plain_file_from_url(self) -> None:
        with Source.create(source=f"file:{self.file}") as src:
            assert isinstance(src, File)
            self.assertEqual(src.name, f"file:{self.file}")
            self.assertEqual(src.path, self.file)

    def test_fail_if_branch_used(self) -> None:
        with self.assertRaisesRegexp(Fail, "Cannot specify a branch when working on a file"):
            Source.create(source=self.file, branch="test")

    def test_plain_file_make_buildable(self) -> None:
        with Source.create(source=self.file) as src:
            with self.assertRaisesRegexp(Fail, f"{self.file}: cannot detect source type"):
                src.make_buildable(distro=ROCKY9)
            with self.assertRaisesRegexp(Fail, f"{self.file}: cannot detect source type"):
                src.make_buildable(distro=SID)

    def test_dsc_file_make_buildable(self) -> None:
        with Source.create(source=self.dsc) as src:
            with self.assertRaisesRegexp(Fail, f"{self.dsc}: cannot build Debian source package on rocky:9"):
                src.make_buildable(distro=ROCKY9)

            newsrc = src.make_buildable(distro=SID)
            self.assertIsInstance(newsrc, DebianDsc)


class TestDir(WorkdirFixture):
    path: Path

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.path = cls.workdir / "source"
        cls.path.mkdir()

    def test_from_path(self) -> None:
        with Source.create(source=self.path) as src:
            assert isinstance(src, Dir)
            self.assertEqual(src.name, self.path.as_posix())
            self.assertEqual(src.path, self.path)

    def test_from_url(self) -> None:
        with Source.create(source=f"file:{self.path}") as src:
            assert isinstance(src, Dir)
            self.assertEqual(src.name, f"file:{self.path}")
            self.assertEqual(src.path, self.path)

    def test_fail_if_branch_used(self) -> None:
        with self.assertRaisesRegexp(Fail, "Cannot specify a branch when working on a non-git directory"):
            Source.create(source=self.path, branch="test")

    def test_make_buildable(self) -> None:
        with Source.create(source=self.path) as src:
            with self.assertRaisesRegexp(Fail, f"{self.path}: no specfiles found in well-known locations"):
                src.make_buildable(distro=ROCKY9)
            with self.assertRaisesRegexp(Fail, f"{self.path}: cannot detect source type"):
                src.make_buildable(distro=SID)


class TestDirWithPackaging(WorkdirFixture):
    path: Path

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.path = cls.workdir / "source"
        cls.path.mkdir()
        (cls.path / "debian").mkdir()
        (cls.path / "source.spec").touch()

    def test_make_buildable_debian(self) -> None:
        with Source.create(source=self.path) as src:
            newsrc = src.make_buildable(distro=SID)
            assert isinstance(newsrc, DebianSourceDir)
            self.assertEqual(newsrc.name, self.path.as_posix())
            self.assertEqual(newsrc.path, self.path)

    def test_make_buildable_rpm(self) -> None:
        with Source.create(source=self.path) as src:
            newsrc = src.make_buildable(distro=ROCKY9)
            assert isinstance(newsrc, ARPASourceDir)
            self.assertEqual(newsrc.name, self.path.as_posix())
            self.assertEqual(newsrc.path, self.path)
            self.assertEqual(newsrc.specfile_path, Path("source.spec"))


class TestGit(GitFixture):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()

        cls.git.add("test")
        cls.git.commit("Initial")
        cls.git.git("tag", "1.0")

        # Side branch
        cls.git.git("checkout", "-b", "devel")
        cls.git.add("devel")
        cls.git.commit()

        # New changes to main branch
        cls.git.git("checkout", "main")
        cls.git.add("main")
        cls.git.commit()

    def test_from_path(self) -> None:
        with Source.create(source=self.git.root) as src:
            assert isinstance(src, Git)
            self.assertEqual(src.name, self.git.root.as_posix())
            self.assertEqual(src.path, self.git.root)
            self.assertIsNone(src.branch)
            self.assertTrue(src.readonly)
            self.assertEqual(src.repo.active_branch.name, "main")

    def test_from_url(self) -> None:
        with Source.create(source=f"file:{self.git.root}") as src:
            assert isinstance(src, Git)
            self.assertEqual(src.name, f"file:{self.git.root}")
            self.assertEqual(src.path, self.git.root)
            self.assertIsNone(src.branch)
            self.assertTrue(src.readonly)
            self.assertEqual(src.repo.active_branch.name, "main")

    def test_with_branch(self) -> None:
        with Source.create(source=self.git.root, branch="devel") as src:
            assert isinstance(src, Git)
            self.assertEqual(src.name, self.git.root.as_posix())
            self.assertEqual(src.path, self.git.root)
            self.assertEqual(src.branch, "devel")
            self.assertTrue(src.readonly)
            self.assertEqual(src.repo.active_branch.name, "main")

    def test_with_tag(self) -> None:
        with Source.create(source=self.git.root, branch="1.0") as src:
            assert isinstance(src, Git)
            self.assertEqual(src.name, self.git.root.as_posix())
            self.assertEqual(src.path, self.git.root)
            self.assertEqual(src.branch, "1.0")
            self.assertTrue(src.readonly)
            self.assertEqual(src.repo.active_branch.name, "main")

    def test_make_buildable(self) -> None:
        raise NotImplementedError("test_make_buildable")


#     def test_create_path(self):
#         with InputSource.create(self.git.root) as isrc:
#             self.assertIsInstance(isrc, inputsource.LocalGit)
#             self.assertEqual(isrc.source, self.git.root.as_posix())
#             self.assertEqual(isrc.repo.working_dir, self.git.root.as_posix())
#             self.assertFalse(isrc.copy)
#             self.assertEqual(isrc.orig_path, self.git.root)
#
#     def test_create_file_url(self):
#         with InputSource.create("file:" + self.git.root.as_posix()) as isrc:
#             self.assertIsInstance(isrc, inputsource.LocalGit)
#             self.assertEqual(isrc.source, "file:" + self.git.root.as_posix())
#             self.assertEqual(isrc.repo.working_dir, self.git.root.as_posix())
#             self.assertFalse(isrc.copy)
#             self.assertEqual(isrc.orig_path, self.git.root)
#
#     def test_clone(self):
#         with InputSource.create(self.git.root) as isrc:
#             clone = isrc.clone()
#             self.assertIsInstance(clone, inputsource.LocalGit)
#             self.assertNotEqual(clone.repo.working_dir, self.git.root)
#             self.assertTrue(clone.copy)
#             self.assertEqual(isrc.orig_path, self.git.root)
#             self.assertEqual(isrc.repo.active_branch.name, "main")
#             self.assertEqual(clone.repo.active_branch.name, "main")
#             self.assertTrue(os.path.exists(clone.repo.working_dir))
#
#         self.assertTrue(os.path.exists(isrc.repo.working_dir))
#         self.assertFalse(os.path.exists(clone.repo.working_dir))
#
#     def test_clone_branch(self):
#         with InputSource.create(self.git.root) as isrc:
#             clone = isrc.clone("branch1").branch("branch1")
#             self.assertIsInstance(clone, inputsource.LocalGit)
#             self.assertNotEqual(clone.repo.working_dir, self.git.root)
#             self.assertTrue(clone.copy)
#             self.assertEqual(isrc.orig_path, self.git.root)
#             self.assertEqual(isrc.repo.active_branch.name, "main")
#             self.assertEqual(clone.repo.active_branch.name, "branch1")
#             self.assertTrue(os.path.exists(clone.repo.working_dir))
#
#         self.assertTrue(os.path.exists(isrc.repo.working_dir))
#         self.assertFalse(os.path.exists(clone.repo.working_dir))
#
#     def test_branch(self):
#         with InputSource.create(self.git.root) as isrc:
#             clone = isrc.branch("branch1")
#             self.assertIsInstance(clone, inputsource.LocalGit)
#             self.assertNotEqual(clone.repo.working_dir, self.git.root)
#             self.assertTrue(clone.copy)
#             self.assertEqual(isrc.orig_path, self.git.root)
#             self.assertEqual(isrc.repo.active_branch.name, "main")
#             self.assertEqual(clone.repo.active_branch.name, "branch1")
#             self.assertTrue(os.path.exists(clone.repo.working_dir))
#
#         self.assertTrue(os.path.exists(isrc.repo.working_dir))
#         self.assertFalse(os.path.exists(clone.repo.working_dir))
