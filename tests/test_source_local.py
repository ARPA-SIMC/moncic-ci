import tempfile
from pathlib import Path
from typing import override

from moncic.exceptions import Fail
from moncic.source import Source
from moncic.source.local import Dir, File, Git

from .source import (
    GitFixture,
    GitRepo,
    WorkdirFixture,
    create_lint_version_fixture_git,
    create_lint_version_fixture_path,
)


class TestFile(WorkdirFixture):
    file: Path
    dsc: Path

    @override
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.file = cls.workdir / "testfile"
        cls.file.touch()
        cls.dsc = cls.workdir / "testfile.dsc"
        cls.dsc.touch()

    def test_plain_file_from_path(self) -> None:
        with Source.create_local(source=self.file) as src:
            assert isinstance(src, File)
            self.assertEqual(src.name, self.file.as_posix())
            self.assertEqual(src.path, self.file)

    def test_plain_file_from_url(self) -> None:
        with Source.create_local(source=f"file:{self.file}") as src:
            assert isinstance(src, File)
            self.assertEqual(src.name, f"file:{self.file}")
            self.assertEqual(src.path, self.file)

    def test_fail_if_branch_used(self) -> None:
        with self.assertRaisesRegex(Fail, "Cannot specify a branch when working on a file"):
            Source.create_local(source=self.file, branch="test")

    def test_derivation(self) -> None:
        with Source.create_local(source=self.file) as src:
            assert isinstance(src, File)
            kwargs = src.derive_kwargs()
            self.assertEqual(kwargs, {"parent": src, "name": self.file.as_posix(), "path": self.file})

    def test_lint_find_versions(self) -> None:
        with Source.create_local(source=self.file) as src:
            assert isinstance(src, File)
            self.assertEqual(src.lint_find_versions(), {})


class TestDir(WorkdirFixture):
    path: Path

    @override
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.path = cls.workdir / "source"
        cls.path.mkdir()

    def test_from_path(self) -> None:
        with Source.create_local(source=self.path) as src:
            assert isinstance(src, Dir)
            self.assertEqual(src.name, self.path.as_posix())
            self.assertEqual(src.path, self.path)

    def test_from_url(self) -> None:
        with Source.create_local(source=f"file:{self.path}") as src:
            assert isinstance(src, Dir)
            self.assertEqual(src.name, f"file:{self.path}")
            self.assertEqual(src.path, self.path)

    def test_fail_if_branch_used(self) -> None:
        with self.assertRaisesRegex(Fail, "Cannot specify a branch when working on a non-git directory"):
            Source.create_local(source=self.path, branch="test")

    def test_derivation(self) -> None:
        with Source.create_local(source=self.path) as src:
            assert isinstance(src, Dir)
            kwargs = src.derive_kwargs()
            self.assertEqual(kwargs, {"parent": src, "name": self.path.as_posix(), "path": self.path})

    def test_lint_find_versions(self) -> None:
        path = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        create_lint_version_fixture_path(path)

        with Source.create_local(source=path) as src:
            assert isinstance(src, Dir)
            self.assertEqual(
                src.lint_find_versions(),
                {
                    "autotools": "1.1",
                    "meson": "1.2",
                    "cmake": "1.3",
                    "news": "1.4",
                    # "setup.py": "1.5",
                    # "debian-upstream": "1.6",
                    # "debian-release": "1.6-1",
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
                    # "debian-upstream": "1.6",
                    # "debian-release": "1.6-1",
                },
            )


class TestGit(GitFixture):
    @override
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
        with Source.create_local(source=self.git.root) as src:
            assert isinstance(src, Git)
            self.assertEqual(src.name, self.git.root.as_posix())
            self.assertEqual(src.path, self.git.root)
            self.assertTrue(src.readonly)
            self.assertEqual(src.repo.active_branch.name, "main")

    def test_from_url(self) -> None:
        with Source.create_local(source=f"file:{self.git.root}") as src:
            assert isinstance(src, Git)
            self.assertEqual(src.name, f"file:{self.git.root}")
            self.assertEqual(src.path, self.git.root)
            self.assertTrue(src.readonly)
            self.assertEqual(src.repo.active_branch.name, "main")

    def test_with_branch(self) -> None:
        with Source.create_local(source=self.git.root, branch="devel") as src:
            assert isinstance(src, Git)
            self.assertEqual(src.name, self.git.root.as_posix())
            self.assertNotEqual(src.path, self.git.root)
            self.assertFalse(src.readonly)
            self.assertEqual(src.repo.active_branch.name, "devel")

    def test_with_tag(self) -> None:
        with Source.create_local(source=self.git.root, branch="1.0") as src:
            assert isinstance(src, Git)
            self.assertEqual(src.name, self.git.root.as_posix())
            self.assertNotEqual(src.path, self.git.root)
            self.assertFalse(src.readonly)
            self.assertEqual(src.repo.active_branch.name, "moncic-ci")

    def test_get_writable(self) -> None:
        with Source.create_local(source=self.git.root) as src:
            assert isinstance(src, Git)
            self.assertTrue(src.readonly)
            newsrc = src.get_writable()
            assert isinstance(newsrc, Git)
            self.assertFalse(newsrc.readonly)
            self.assertNotEqual(src.path, newsrc.path)
            self.assertIsNot(src.repo, newsrc.repo)

    def test_find_branch(self) -> None:
        with Source.create_local(source=self.git.root) as src:
            assert isinstance(src, Git)
            self.assertIsNotNone(src.find_branch("main"))
            self.assertIsNotNone(src.find_branch("devel"))
            self.assertIsNone(src.find_branch("1.0"))
            self.assertIsNone(src.find_branch("does-not-exist"))

    def test_derivation(self) -> None:
        with Source.create_local(source=self.path) as src:
            assert isinstance(src, Git)
            kwargs = src.derive_kwargs()
            self.assertEqual(
                kwargs,
                {
                    "parent": src,
                    "name": self.path.as_posix(),
                    "path": self.path,
                    "repo": src.repo,
                    "readonly": src.readonly,
                },
            )

    def test_lint_find_versions(self) -> None:
        path = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        git = self.stack.enter_context(GitRepo(path))
        create_lint_version_fixture_git(git)

        with Source.create_local(source=path) as src:
            assert isinstance(src, Git)
            self.assertEqual(
                src.lint_find_versions(),
                {
                    "autotools": "1.1",
                    "meson": "1.2",
                    "cmake": "1.3",
                    "news": "1.4",
                    # "setup.py": "1.5",
                    # "debian-upstream": "1.6",
                    # "debian-release": "1.6-1",
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
                    # "debian-upstream": "1.6",
                    # "debian-release": "1.6-1",
                },
            )
