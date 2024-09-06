from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from .source import Source
from ..exceptions import Fail

import git

if TYPE_CHECKING:
    from ..distro import Distro


class File(Source):
    """
    A local file
    """

    #: Path to the file
    path: Path

    def __init__(self, *, path: Path, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.path = path

    def make_buildable(self, *, distro: Distro, source_type: str | None = None) -> Source:
        from ..distro.debian import DebianDistro
        from ..distro.rpm import RpmDistro

        new_source: Source

        if isinstance(distro, DebianDistro):
            from .debian import DebianSource

            new_source = DebianSource.create_from_file(self)
        elif isinstance(distro, RpmDistro):
            from .rpm import RPMSource

            new_source = RPMSource.create_from_file(self)
        else:
            raise NotImplementedError(f"No suitable file builder found for distribution {distro!r}")

        return new_source.make_buildable(distro=distro, source_type=source_type)


class Dir(Source):
    """
    Local directory that is not a git working directory
    """

    #: Path to the directory
    path: Path

    def __init__(self, *, path: Path, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.path = path

    def make_buildable(self, *, distro: Distro, source_type: str | None = None) -> Source:
        from ..distro.debian import DebianDistro
        from ..distro.rpm import RpmDistro

        new_source: Source

        if isinstance(distro, DebianDistro):
            from .debian import DebianSource

            new_source = DebianSource.create_from_dir(self)
        elif isinstance(distro, RpmDistro):
            from .rpm import RPMSource

            new_source = RPMSource.create_from_dir(self)
        else:
            raise NotImplementedError(f"No suitable directory builder found for distribution {distro!r}")

        return new_source.make_buildable(distro=distro, source_type=source_type)


class Git(Dir):
    """
    Local git working directory
    """

    #: Git repository
    repo: git.Repo
    #: Branch to use (default: the current one)
    branch: str | None
    #: False if the git repo is ephemeral and can be modified at will
    readonly: bool

    def __init__(
        self, *, branch: str | None = None, repo: git.Repo | None = None, readonly: bool = True, **kwargs
    ) -> None:
        super().__init__(**kwargs)
        self.repo = repo or git.Repo(self.path)
        self.branch = branch
        self.readonly = readonly

    @classmethod
    def derive_from_git(cls, parent: "Git", **kwargs) -> "Git":  # TODO: use Self from python 3.11
        kwargs.setdefault("parent", parent)
        kwargs.setdefault("path", parent.path)
        kwargs.setdefault("repo", parent.repo)
        kwargs.setdefault("branch", parent.branch)
        kwargs.setdefault("readonly", parent.readonly)
        return cls(**kwargs)

    def get_branch(self) -> Git:
        """
        Return a Git repo with self.branch as the current branch
        """
        if self.branch is None:
            return self

        if not self.repo.head.is_detached and self.repo.active_branch == self.branch:
            return self

        if not self.readonly:
            raise NotImplementedError("Checkout branch in place not yet implemented")

        return self._git_clone(self.path.as_posix(), self.branch)

    def make_buildable(self, *, distro: Distro, source_type: str | None = None) -> Source:
        from ..distro.debian import DebianDistro
        from ..distro.rpm import RpmDistro

        new_source: Source

        if isinstance(distro, DebianDistro):
            from .debian import DebianSource

            new_source = DebianSource.create_from_git(self)
        elif isinstance(distro, RpmDistro):
            from .rpm import RPMSource

            new_source = RPMSource.create_from_git(self)
        else:
            raise NotImplementedError(f"No suitable git builder found for distribution {distro!r}")

        return new_source.make_buildable(distro=distro, source_type=source_type)


#     def find_branch(self, name: str) -> git.refs.symbolic.SymbolicReference | None:
#         """
#         Look for the named branch locally or in the origin repository.
#
#         Return the branch object, or None if not found.
#
#         If the result is not None, `git checkout <name>` is expected to work
#         """
#         for branch in self.repo.branches:
#             if branch.name == name:
#                 return branch
#
#         for remote in self.repo.remotes:
#             if remote.name == "origin":
#                 break
#         else:
#             return None
#
#         ref_name = remote.name + "/" + name
#         for ref in remote.refs:
#             if ref.name == ref_name:
#                 return ref
#         return None


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
