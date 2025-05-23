from __future__ import annotations

import contextlib
import tempfile
from collections.abc import Generator
from pathlib import Path
from typing import cast
from unittest import mock

from moncic.distro import DistroFamily
from moncic.distro.rpm import RpmDistro
from moncic.exceptions import Fail
from moncic.source import Source
from moncic.source.local import Dir, File, Git
from moncic.source.rpm import ARPASource, ARPASourceDir, ARPASourceGit, RPMSource

from .source import (
    GitFixture,
    GitRepo,
    WorkdirFixture,
    create_lint_version_fixture_git,
    create_lint_version_fixture_path,
)

ROCKY9 = cast(RpmDistro, DistroFamily.lookup_distro("rocky9"))


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
        with Source.create_local(source=path) as src:
            assert isinstance(src, File)
            with self.assertRaisesRegex(Fail, f"{path}: cannot detect source type"):
                RPMSource.create_from_file(src, distro=ROCKY9)

    def test_from_file_dsc(self) -> None:
        path = self.workdir / "file.dsc"
        path.touch()
        with Source.create_local(source=path) as src:
            assert isinstance(src, File)
            with self.assertRaisesRegex(Fail, f"{path}: cannot build Debian source package on a RPM distribution"):
                RPMSource.create_from_file(src, distro=ROCKY9)

    def test_from_dir_empty(self) -> None:
        path = self.workdir / "dir"
        path.mkdir()
        with Source.create_local(source=path) as src:
            assert isinstance(src, Dir)
            with self.assertRaisesRegex(Fail, f"{path}: no specfiles found in well-known locations"):
                RPMSource.create_from_dir(src, distro=ROCKY9)

    def test_from_dir_one_specfile_root(self) -> None:
        path = self.workdir / "onespecroot"
        path.mkdir()
        (path / "specfile.spec").touch()
        with Source.create_local(source=path) as src:
            assert isinstance(src, Dir)
            with mock.patch("moncic.source.rpm.ARPASource.prepare_from_dir") as patched:
                RPMSource.create_from_dir(src, distro=ROCKY9)
            patched.assert_called_once()

    def test_from_dir_one_specfile_sub(self) -> None:
        path = self.workdir / "onespecsub"
        path.mkdir()
        specdir = path / "fedora" / "SPECS"
        specdir.mkdir(parents=True)
        (specdir / "specfile.spec").touch()

        with Source.create_local(source=path) as src:
            assert isinstance(src, Dir)
            with mock.patch("moncic.source.rpm.ARPASource.prepare_from_dir") as patched:
                RPMSource.create_from_dir(src, distro=ROCKY9)
            patched.assert_called_once()

    def test_from_dir_twospecs(self) -> None:
        path = self.workdir / "twospecs"
        path.mkdir()
        (path / "specfile.spec").touch()
        specdir = path / "fedora" / "SPECS"
        specdir.mkdir(parents=True)
        (specdir / "specfile.spec").touch()

        with Source.create_local(source=path) as src:
            assert isinstance(src, Dir)
            with self.assertRaisesRegex(Fail, f"{path}: 2 specfiles found"):
                RPMSource.create_from_dir(src, distro=ROCKY9)

    def test_from_git_empty(self) -> None:
        git = self.make_git_repo("git")
        with Source.create_local(source=git.root) as src:
            assert isinstance(src, Git)
            with self.assertRaisesRegex(Fail, f"{git.root}: no specfiles found in well-known locations"):
                RPMSource.create_from_git(src, distro=ROCKY9)

    def test_from_git_one_specfile_root(self) -> None:
        git = self.make_git_repo("git_onespecroot")
        git.add("specfile.spec")
        git.commit("initial")
        with Source.create_local(source=git.root) as src:
            assert isinstance(src, Git)
            with mock.patch("moncic.source.rpm.ARPASource.prepare_from_git") as patched:
                RPMSource.create_from_git(src, distro=ROCKY9)
            patched.assert_called_once()

    def test_from_git_one_specfile_sub(self) -> None:
        git = self.make_git_repo("git_onespecsub")
        git.add("fedora/SPECS/specfile.spec")
        git.commit("initial")
        with Source.create_local(source=git.root) as src:
            assert isinstance(src, Git)
            with mock.patch("moncic.source.rpm.ARPASource.prepare_from_git") as patched:
                RPMSource.create_from_git(src, distro=ROCKY9)
            patched.assert_called_once()

    def test_from_git_twospecs(self) -> None:
        git = self.make_git_repo("git_twospecs")
        git.add("specfile.spec")
        git.add("fedora/SPECS/specfile.spec")
        git.commit("initial")
        with Source.create_local(source=git.root) as src:
            assert isinstance(src, Git)
            with self.assertRaisesRegex(Fail, f"{git.root}: 2 specfiles found"):
                RPMSource.create_from_git(src, distro=ROCKY9)

    def test_lint_path_is_packaging(self) -> None:
        path = self.workdir / "path_is_packaging"
        path.mkdir()
        (path / "specfile.spec").touch()
        with Source.create_local(source=path) as parent:
            assert isinstance(parent, Dir)
            src = RPMSource.create_from_dir(parent, distro=ROCKY9)
            assert isinstance(src, ARPASourceDir)
            self.assertFalse(src.lint_path_is_packaging(Path("test")))
            self.assertTrue(src.lint_path_is_packaging(Path("test.spec")))
            self.assertFalse(src.lint_path_is_packaging(Path("spec")))
            self.assertTrue(src.lint_path_is_packaging(Path("spec.spec")))
            self.assertFalse(src.lint_path_is_packaging(Path("debian/control")))
            self.assertTrue(src.lint_path_is_packaging(Path("test/test.spec")))


class TestARPA(WorkdirFixture):
    pass


class TestARPASourceDir(WorkdirFixture):
    path: Path

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.path = cls.workdir / "source"
        cls.path.mkdir()

    def make_specfile(self, path: Path, name: str = "specfile.spec") -> Path:
        path.mkdir(parents=True, exist_ok=True)
        specfile = path / name
        specfile.touch()
        self.addCleanup(specfile.unlink)
        return specfile

    @contextlib.contextmanager
    def source(self, specfile: Path, root: Path | None = None) -> Generator[ARPASourceDir]:
        if root is None:
            root = self.path
        with Source.create_local(source=root) as parent:
            assert isinstance(parent, Dir)
            src = ARPASource.prepare_from_dir(parent, distro=ROCKY9, specfiles=[specfile])
            assert isinstance(src, ARPASourceDir)
            self.assertIs(src.parent, parent)
            yield src

    def test_from_dir_one_specfile_root(self) -> None:
        specfile = self.make_specfile(self.path)
        with self.source(specfile=specfile) as src:
            self.assertEqual(src.distro, ROCKY9)
            self.assertEqual(src.specfile_path, specfile)

    def test_from_dir_one_specfile_sub(self) -> None:
        specfile = self.make_specfile(self.path / "fedora" / "SPECS")
        with self.source(specfile=specfile) as src:
            self.assertEqual(src.distro, ROCKY9)
            self.assertEqual(src.specfile_path, specfile)

    def test_derivation(self) -> None:
        specfile = self.make_specfile(self.path)
        with self.source(specfile=specfile) as src:
            kwargs = src.derive_kwargs()
            self.assertEqual(
                kwargs,
                {
                    "parent": src,
                    "name": self.path.as_posix(),
                    "path": self.path,
                    "distro": ROCKY9,
                    "specfile_path": specfile,
                },
            )

    def test_lint_find_versions(self):
        path = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        create_lint_version_fixture_path(path)
        with self.source(specfile=Path("fedora/SPECS/test.spec"), root=path) as src:
            self.assertEqual(
                src.lint_find_versions(),
                {
                    "autotools": "1.1",
                    "meson": "1.2",
                    "cmake": "1.3",
                    "news": "1.4",
                    "spec-upstream": "1.6",
                    "spec-release": "1.6-1",
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
                    "spec-upstream": "1.6",
                    "spec-release": "1.6-1",
                },
            )


class TestARPASourceGit(GitFixture):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.git.add("testfile")
        cls.git.commit()

        cls.git.git("checkout", "-b", "specfile_root")
        cls.git.add("specfile.spec")
        cls.git.commit()

        cls.git.git("checkout", "main", "-b", "specfile_subdir")
        cls.git.add("fedora/SPECS/specfile.spec")
        cls.git.commit()

        cls.git.git("checkout", "main")

    @contextlib.contextmanager
    def source(self, branch: str, specfile: Path, root: Path | None = None) -> Generator[ARPASourceGit]:
        if root is None:
            root = self.path
        with Source.create_local(source=root, branch=branch) as parent:
            assert isinstance(parent, Git)
            src = ARPASource.prepare_from_git(parent, distro=ROCKY9, specfiles=[specfile])
            assert isinstance(src, ARPASourceGit)
            self.assertIs(src.parent, parent)
            yield src

    def test_from_dir_one_specfile_root(self) -> None:
        specfile = Path("specfile.spec")
        with self.source(branch="specfile_root", specfile=specfile) as src:
            self.assertEqual(src.distro, ROCKY9)
            self.assertEqual(src.specfile_path, specfile)

    def test_from_dir_one_specfile_sub(self) -> None:
        specfile = Path("fedora/SPECS/specfile.spec")
        with self.source(branch="specfile_subdir", specfile=specfile) as src:
            self.assertEqual(src.distro, ROCKY9)
            self.assertEqual(src.specfile_path, specfile)

    def test_derivation(self) -> None:
        specfile = Path("specfile.spec")
        with self.source(branch="specfile_root", specfile=specfile) as src:
            kwargs = src.derive_kwargs()
            self.assertEqual(
                kwargs,
                {
                    "parent": src,
                    "name": self.path.as_posix(),
                    "path": src.path,
                    "readonly": False,
                    "repo": src.repo,
                    "distro": ROCKY9,
                    "specfile_path": specfile,
                },
            )

    def test_lint_find_versions(self):
        path = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        git = self.stack.enter_context(GitRepo(path))
        create_lint_version_fixture_git(git)
        git.commit()

        with self.source(branch="main", specfile=Path("fedora/SPECS/test.spec"), root=path) as src:
            assert isinstance(src, Dir)
            self.assertEqual(
                src.lint_find_versions(),
                {
                    "autotools": "1.1",
                    "meson": "1.2",
                    "cmake": "1.3",
                    "news": "1.4",
                    "spec-upstream": "1.6",
                    "spec-release": "1.6-1",
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
                    "spec-upstream": "1.6",
                    "spec-release": "1.6-1",
                },
            )

    def test_lint_find_tags(self):
        path = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        git = self.stack.enter_context(GitRepo(path))
        git.add("testfile")
        git.commit()
        git.git("tag", "v1.0")
        git.add(
            "testfile.spec",
            """
Name:           test
Version:        1.0
Release:        1
Summary:        test repo
License:        CC-0
%description
test
""",
        )
        git.commit()
        git.git("tag", "v1.0-1")
        with self.source(branch="main", specfile=Path("testfile.spec"), root=path) as src:
            tag = src.lint_find_upstream_tag()
            self.assertEqual(tag.name, "v1.0")
            tag = src.lint_find_packaging_tag()
            self.assertEqual(tag.name, "v1.0-1")

    def test_lint_find_tags_upstream_dash(self):
        path = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        git = self.stack.enter_context(GitRepo(path))
        git.add("testfile")
        git.commit()
        git.git("tag", "v1.0-1")
        git.add(
            "testfile.spec",
            """
Name:           test
Version:        1.0
Release:        2
Summary:        test repo
License:        CC-0
%description
test
""",
        )
        git.commit()
        git.git("tag", "v1.0-2")
        with self.source(branch="main", specfile=Path("testfile.spec"), root=path) as src:
            tag = src.lint_find_upstream_tag()
            self.assertEqual(tag.name, "v1.0-1")
            tag = src.lint_find_packaging_tag()
            self.assertEqual(tag.name, "v1.0-2")

    def test_lint_find_tags_release_not_found(self):
        path = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        git = self.stack.enter_context(GitRepo(path))
        git.add("testfile")
        git.commit()
        git.git("tag", "v1.0-1")
        git.add(
            "testfile.spec",
            """
Name:           test
Version:        1.0
Release:        3
Summary:        test repo
License:        CC-0
%description
test
""",
        )
        git.commit()
        git.git("tag", "v1.0-2")
        with self.source(branch="main", specfile=Path("testfile.spec"), root=path) as src:
            tag = src.lint_find_upstream_tag()
            self.assertEqual(tag.name, "v1.0-1")
            tag = src.lint_find_packaging_tag()
            self.assertIsNone(tag)

    def test_lint_find_tags_not_found(self):
        path = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        git = self.stack.enter_context(GitRepo(path))
        git.add("testfile")
        git.commit()
        git.add(
            "testfile.spec",
            """
Name:           test
Version:        1.0
Release:        3
Summary:        test repo
License:        CC-0
%description
test
""",
        )
        git.commit()
        with self.source(branch="main", specfile=Path("testfile.spec"), root=path) as src:
            tag = src.lint_find_upstream_tag()
            self.assertIsNone(tag)
            tag = src.lint_find_packaging_tag()
            self.assertIsNone(tag)


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
