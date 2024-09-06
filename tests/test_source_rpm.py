from __future__ import annotations

import os
import unittest
from pathlib import Path

from moncic.distro import DistroFamily
from moncic.exceptions import Fail
from moncic.source import Source
from moncic.source.local import File, Dir, Git
from moncic.source.rpm import RPMSource, ARPASourceDir, ARPASourceGit
from moncic.unittest import make_moncic

from .source import WorkdirFixture, GitFixture, MockBuilder, GitRepo

ROCKY9 = DistroFamily.lookup_distro("rocky9")
SID = DistroFamily.lookup_distro("sid")


class TestRPMSource(WorkdirFixture):
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
        with Source.create(source=path) as src:
            assert isinstance(src, File)
            with self.assertRaisesRegexp(Fail, f"{path}: cannot detect source type"):
                RPMSource.create_from_file(src)

    def test_from_file_dsc(self) -> None:
        path = self.workdir / "file.dsc"
        path.touch()
        with Source.create(source=path) as src:
            assert isinstance(src, File)
            with self.assertRaisesRegexp(Fail, f"{path}: cannot build Debian source package on a RPM distribution"):
                RPMSource.create_from_file(src)

    def test_from_dir_empty(self) -> None:
        path = self.workdir / "dir"
        path.mkdir()
        with Source.create(source=path) as src:
            assert isinstance(src, Dir)
            with self.assertRaisesRegexp(Fail, f"{path}: no specfiles found in well-known locations"):
                RPMSource.create_from_dir(src)

    def test_from_dir_one_specfile_root(self) -> None:
        path = self.workdir / "onespecroot"
        path.mkdir()
        (path / "specfile.spec").touch()
        with Source.create(source=path) as src:
            assert isinstance(src, Dir)
            newsrc = RPMSource.create_from_dir(src)
            assert isinstance(newsrc, ARPASourceDir)
            self.assertEqual(newsrc.specfile_path, Path("specfile.spec"))

    def test_from_dir_one_specfile_sub(self) -> None:
        path = self.workdir / "onespecsub"
        path.mkdir()
        specdir = path / "fedora" / "SPECS"
        specdir.mkdir(parents=True)
        (specdir / "specfile.spec").touch()

        with Source.create(source=path) as src:
            assert isinstance(src, Dir)
            newsrc = RPMSource.create_from_dir(src)
            assert isinstance(newsrc, ARPASourceDir)
            self.assertEqual(newsrc.specfile_path, Path("fedora/SPECS/specfile.spec"))

    def test_from_dir_twospecs(self) -> None:
        path = self.workdir / "twospecs"
        path.mkdir()
        (path / "specfile.spec").touch()
        specdir = path / "fedora" / "SPECS"
        specdir.mkdir(parents=True)
        (specdir / "specfile.spec").touch()

        with Source.create(source=path) as src:
            assert isinstance(src, Dir)
            with self.assertRaisesRegexp(Fail, f"{path}: 2 specfiles found"):
                RPMSource.create_from_dir(src)

    def test_from_git_empty(self) -> None:
        git = self.make_git_repo("git")
        with Source.create(source=git.root) as src:
            assert isinstance(src, Git)
            with self.assertRaisesRegexp(Fail, f"{git.root}: no specfiles found in well-known locations"):
                RPMSource.create_from_git(src)

    def test_from_git_one_specfile_root(self) -> None:
        git = self.make_git_repo("git_onespecroot")
        git.add("specfile.spec")
        git.commit("initial")
        with Source.create(source=git.root) as src:
            assert isinstance(src, Git)
            newsrc = RPMSource.create_from_git(src)
            assert isinstance(newsrc, ARPASourceGit)
            self.assertEqual(newsrc.specfile_path, Path("specfile.spec"))

    def test_from_git_one_specfile_sub(self) -> None:
        git = self.make_git_repo("git_onespecsub")
        git.add("fedora/SPECS/specfile.spec")
        git.commit("initial")
        with Source.create(source=git.root) as src:
            assert isinstance(src, Git)
            newsrc = RPMSource.create_from_git(src)
            assert isinstance(newsrc, ARPASourceGit)
            self.assertEqual(newsrc.specfile_path, Path("fedora/SPECS/specfile.spec"))

    def test_from_git_twospecs(self) -> None:
        git = self.make_git_repo("git_twospecs")
        git.add("specfile.spec")
        git.add("fedora/SPECS/specfile.spec")
        git.commit("initial")
        with Source.create(source=git.root) as src:
            assert isinstance(src, Git)
            with self.assertRaisesRegexp(Fail, f"{git.root}: 2 specfiles found"):
                RPMSource.create_from_git(src)


# class TestARPA(GitFixtureMixin, unittest.TestCase):
#     @classmethod
#     def setUpClass(cls):
#         super().setUpClass()
#         travis_yml = os.path.join(cls.workdir, ".travis.yml")
#         with open(travis_yml, "wt") as out:
#             print("foo foo simc/stable bar bar", file=out)
#
#         # Initial upstream
#         cls.git.add(
#             ".travis.yml",
#             """
# foo foo simc/stable bar bar
# """,
#         )
#         cls.git.add("fedora/SPECS/test.spec")
#         cls.git.commit()
#
#     def test_detect_local(self):
#         with InputSource.create(self.git.root) as isrc:
#             self.assertIsInstance(isrc, inputsource.LocalGit)
#
#             with self.assertRaises(Fail):
#                 isrc.detect_source(SID)
#
#             src = isrc.detect_source(ROCKY9)
#             self.assertIsInstance(src, rpm.ARPAGitSource)
#
#     def test_detect_url(self):
#         with self.git.serve() as url:
#             with InputSource.create(url) as isrc:
#                 self.assertIsInstance(isrc, inputsource.URL)
#
#                 with self.assertRaises(Fail):
#                     isrc.detect_source(SID)
#
#                 src = isrc.detect_source(ROCKY9)
#                 self.assertIsInstance(src, rpm.ARPAGitSource)
#
#     def _test_build_source(self, path):
#         with InputSource.create(path) as isrc:
#             src = isrc.detect_source(ROCKY9)
#             self.assertEqual(src.get_build_class().__name__, "ARPA")
#             build = src.make_build(distro=ROCKY9)
#             self.assertTrue(build.source.host_path.is_dir())
#             with (
#                 make_moncic() as moncic,
#                 moncic.session(),
#                 MockBuilder("rocky9", build) as builder,
#                 builder.container() as container,
#             ):
#                 src.gather_sources_from_host(builder.build, container)
#                 self.assertCountEqual(os.listdir(container.source_dir), [])
#                 # TODO: @guest_only
#                 # TODO: def build_source_package(self) -> str:
#
#     def test_build_source_git(self):
#         self._test_build_source(self.git.root)
#
#     def test_build_source_url(self):
#         with self.git.serve() as url:
#             self._test_build_source(url)
