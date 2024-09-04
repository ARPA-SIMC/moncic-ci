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

        if isinstance(distro, DebianDistro):
            if self.path.suffix == ".dsc":
                from .debian import DebianDsc

                # TODO: validate source_type
                return DebianDsc(parent=self, path=self.path)
            else:
                raise Fail(f"{self.path}: cannot detect source type")
        else:
            if self.path.suffix == ".dsc":
                raise Fail(f"{self.path}: cannot build Debian source package on {distro}")
            else:
                raise Fail(f"{self.path}: cannot detect source type")


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

        if isinstance(distro, DebianDistro):
            # TODO: validate source_type
            if (self.path / "debian").is_dir():
                from .debian import DebianSourceDir

                return DebianSourceDir(parent=self, path=self.path)
            else:
                raise Fail(f"{self.path}: cannot detect source type")
        else:
            from .rpm import ARPASourceDir

            specfile_paths = ARPASourceDir.locate_specfiles(self.path)
            if not specfile_paths:
                raise Fail(f"specfile not found in known locations inside {self.path}")
            if len(specfile_paths) > 1:
                raise Fail(f"{len(specfile_paths)} specfiles found inside {self.path}")

            return ARPASourceDir(parent=self, path=self.path, specfile_path=specfile_paths[0])


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
        # Switch to the right branch first, if needed
        if (new_source := self.get_branch()) != self:
            return new_source.make_buildable(distro=distro, source_type=source_type)

        from ..distro.debian import DebianDistro
        from ..distro.rpm import RpmDistro

        if isinstance(distro, DebianDistro):
            from .debian import DebianGit

            debian_src = DebianGit(parent=self, branch=self.branch, repo=self.repo, readonly=self.readonly)
            return debian_src.make_buildable(distro=distro, source_type=source_type)
        elif isinstance(distro, RpmDistro):
            from .rpm import ARPASourceGit

            specfile_paths = ARPASourceGit.locate_specfiles(self.path)
            if not specfile_paths:
                raise Fail(f"specfile not found in known locations inside {self.path}")
            if len(specfile_paths) > 1:
                raise Fail(f"{len(specfile_paths)} specfiles found inside {self.path}")

            return ARPASourceGit(
                parent=self, branch=self.branch, repo=self.repo, readonly=self.readonly, specfile_path=specfile_paths[0]
            )
        else:
            raise NotImplementedError(f"No suitable git builder found for distribution {distro!r}")


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
