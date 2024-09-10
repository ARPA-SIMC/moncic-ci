from __future__ import annotations

import abc
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .source import Source
from ..exceptions import Fail

import git

if TYPE_CHECKING:
    from ..distro import Distro


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


# class GitSource(Source):
#     """
#     Source backed by a Git repo
#     """
#
#     # Redefine source specialized as LocalGit
#     source: LocalGit
#
#     def _get_tags_by_hexsha(self) -> dict[str, git.objects.Commit]:
#         res: dict[str, list[git.objects.Commit]] = defaultdict(list)
#         for tag in self.source.repo.tags:
#             res[tag.object.hexsha].append(tag)
#         return res
#
#     def find_versions(self, system: System) -> dict[str, str]:
#         versions = super().find_versions(system)
#
#         re_versioned_tag = re.compile(r"^v?([0-9].+)")
#
#         repo = self.source.repo
#
#         _tags_by_hexsha = self._get_tags_by_hexsha()
#
#         # List tags for the current commit
#         for tag in _tags_by_hexsha.get(repo.head.commit.hexsha, ()):
#             if tag.name.startswith("debian/"):
#                 version = tag.name[7:]
#                 if "-" in version:
#                     versions["tag-debian"] = version.split("-", 1)[0]
#                     versions["tag-debian-release"] = version
#                 else:
#                     versions["tag-debian"] = version
#             elif mo := re_versioned_tag.match(tag.name):
#                 version = mo.group(1)
#                 if "-" in version:
#                     versions["tag-arpa"] = version.split("-", 1)[0]
#                     versions["tag-arpa-release"] = version
#                 else:
#                     versions["tag"] = version
#
#         return versions
