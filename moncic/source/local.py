from __future__ import annotations

import abc
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .source import Source
from .lint import Reporter
from ..utils.run import run

import git


class LocalSource(Source, abc.ABC):
    """
    Locally-accessible source
    """

    #: Path to the source in the filesystem
    path: Path

    def __init__(self, *, path: Path, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.path = path

    def add_init_args_for_derivation(self, kwargs: dict[str, Any]) -> None:
        super().add_init_args_for_derivation(kwargs)
        kwargs["path"] = self.path

    @abc.abstractmethod
    def in_path(self, path: Path) -> LocalSource:  # TODO: use Self in 3.11+
        """
        Return a new source, the same as this one but on a different path.

        This can be used to work with a version of the source that is mounted
        in a different path inside a guest system.
        """

    def lint_find_versions(self, *, allow_exec: bool = False) -> dict[str, str]:
        """
        Scan sources looking for all places that define a version number.

        Distribution-specific subclasses can assume access to distro-specific tools.

        :param allow_exec: if True, allow running code from the repository to
                           find versions
        :return: a dict mapping place names to versions found
        """
        return {}


class File(LocalSource):
    """
    A local file
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        assert self.path.is_file()

    def in_path(self, path: Path) -> File:
        return self.__class__(**self.derive_kwargs(path=path))


class Dir(LocalSource):
    """
    Local directory that is not a git working directory
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        assert self.path.is_dir()

    def in_path(self, path: Path) -> Dir:
        return self.__class__(**self.derive_kwargs(path=path))

    def lint_find_versions(self, *, allow_exec: bool = False) -> dict[str, str]:
        versions = super().lint_find_versions(allow_exec=allow_exec)

        if (autotools := self.path / "configure.ac").exists():
            re_autotools = re.compile(r"\s*AC_INIT\s*\(\s*[^,]+\s*,\s*\[?([^,\]]+)")
            with autotools.open("rt") as fd:
                for line in fd:
                    if mo := re_autotools.match(line):
                        versions["autotools"] = mo.group(1).strip()
                        break

        if (meson := self.path / "meson.build").exists():
            re_meson = re.compile(r"\s*project\s*\(.+version\s*:\s*'([^']+)'")
            with meson.open("rt") as fd:
                for line in fd:
                    if mo := re_meson.match(line):
                        versions["meson"] = mo.group(1).strip()
                        break

        if (cmake := self.path / "CMakeLists.txt").exists():
            re_cmake = re.compile(r"""\s*set\s*\(\s*PACKAGE_VERSION\s+["']([^"']+)""")
            with cmake.open("rt") as fd:
                for line in fd:
                    if mo := re_cmake.match(line):
                        versions["cmake"] = mo.group(1).strip()
                        break

        if (news := self.path / "NEWS.md").exists():
            re_news = re.compile(r"# (?:New in version|Version) (.+)")
            with news.open("rt") as fd:
                for line in fd:
                    if mo := re_news.match(line):
                        versions["news"] = mo.group(1).strip()
                        break

        # Check setup.py by executing it with --version
        setup_py = self.path / "setup.py"
        if allow_exec and setup_py.exists():
            if python3 := shutil.which("python3"):
                res = run([python3, setup_py.as_posix(), "--version"], stdout=subprocess.PIPE, text=True, cwd=self.path)
                if res.returncode == 0:
                    lines = res.stdout.splitlines()
                    if lines:
                        versions["setup.py"] = lines[-1].strip()

        return versions


class Git(Dir):
    """
    Local git working directory
    """

    #: Git repository
    repo: git.Repo
    #: False if the git repo is ephemeral and can be modified at will
    readonly: bool

    def __init__(self, *, repo: git.Repo | None = None, readonly: bool = True, **kwargs) -> None:
        super().__init__(**kwargs)
        self.repo = repo or git.Repo(self.path)
        self.readonly = readonly

    def add_init_args_for_derivation(self, kwargs: dict[str, Any]) -> None:
        super().add_init_args_for_derivation(kwargs)
        kwargs["repo"] = self.repo
        kwargs["readonly"] = self.readonly

    def in_path(self, path: Path) -> Git:
        return self.__class__(**self.derive_kwargs(path=path, repo=None))

    def get_branch(self, branch: str) -> Git:
        """
        Return a Git repo with self.branch as the current branch
        """
        if not self.repo.head.is_detached and self.repo.active_branch == branch:
            return self

        if not self.readonly:
            raise NotImplementedError("Checkout branch in place not yet implemented")

        return self._git_clone(self.path.as_posix(), branch)

    def get_writable(self) -> Git:
        """
        Return a Git repo that is not readonly.

        If this repo is not readonly, return it. Else, return a clone
        """
        if not self.readonly:
            return self

        return self._git_clone(self.path.as_posix())

    def get_clean(self) -> Git:
        """
        Return a Git repo that is not dirty.

        If the repo is dirty, return a clone.
        """
        if not self.repo.is_dirty():
            return self
        return self._git_clone(self.path.as_posix())

    def find_branch(self, name: str) -> git.refs.symbolic.SymbolicReference | None:
        """
        Look for the named branch locally or in the origin repository.

        Return the branch object, or None if not found.

        If the result is not None, `git checkout <name>` is expected to work
        """
        for branch in self.repo.branches:
            if branch.name == name:
                return branch

        for remote in self.repo.remotes:
            if remote.name == "origin":
                break
        else:
            return None

        ref_name = remote.name + "/" + name
        for ref in remote.refs:
            if ref.name == ref_name:
                return ref
        return None

    def find_tags(self, hexsha: str | None = None) -> dict[str, git.objects.Commit]:
        """
        Return the tags corresponding to the given commit hash (if any)
        """
        if hexsha is None:
            hexsha = self.repo.head.commit.hexsha

        res: dict[str, git.objects.Commit] = {}
        for tag in self.repo.tags:
            if tag.commit.hexsha == hexsha:
                res[tag.name] = tag

        return res

    def lint_local_remote_sync(self, name: str, reporter: Reporter) -> str:
        """
        Check if branch {name} is in sync between local and remote.

        Return the name of the most up to date branch
        """
        if name not in self.repo.references:
            reporter.error(self, f"branch {name!r} does not exist locally")

        remote_name = f"origin/{name}"
        if remote_name not in self.repo.references:
            reporter.error(self, f"branch {remote_name!r} does not exist locally")

        local = self.repo.references[name]
        remote = self.repo.references[remote_name]
        if local.commit != remote.commit:
            if self.repo.is_ancestor(local.commit, remote.commit):
                reporter.warning(self, f"branch {remote_name} is ahead of local branch {name}")
                return remote_name
            elif self.repo.is_ancestor(remote.commit, local.commit):
                reporter.warning(self, f"branch {name} is ahead of remote branch {remote_name}")
                return name
            else:
                reporter.warning(self, f"branch {name} diverged from branch {remote_name}")
                return name
        else:
            return name
