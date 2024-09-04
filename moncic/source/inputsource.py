from __future__ import annotations

import contextlib
import logging
import os
import shlex
import tempfile
import urllib.parse
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

import git

from ..exceptions import Fail
from ..utils.run import run

if TYPE_CHECKING:
    from ..distro import Distro
    from .source import Source

log = logging.getLogger(__name__)


def _git_clone(inputsource: InputSource, repository: str, branch: str | None = None) -> str:
    """
    Clone a git repository into a temporary working directory.

    Return the path of the new cloned working directory
    """
    # Git checkout in a temporary directory
    workdir = inputsource.enter_context(tempfile.TemporaryDirectory())
    cmd = ["git", "-c", "advice.detachedHead=false", "clone", "--quiet", repository]
    if branch is not None:
        cmd += ["--branch", branch]
    inputsource.add_trace_log(*cmd)
    run(cmd, cwd=workdir)

    # Look for the directory that git created
    names = os.listdir(workdir)
    if len(names) != 1:
        raise RuntimeError("git clone create more than one entry in its current directory: {names!r}")

    repo_path = os.path.join(workdir, names[0])

    # Recreate remote branches
    repo = git.Repo(repo_path)
    for ref in repo.remote().refs:
        name = ref.remote_head
        if name == "HEAD":
            continue
        if name in repo.refs:
            continue
        repo.create_head(name, ref)

    # If we cloned a detached head, create a local branch for it
    if branch and repo.head.is_detached:
        local_branch = repo.create_head("moncic-ci")
        local_branch.checkout()
        inputsource.add_trace_log("git", "checkout", "-b", "moncic-ci")

    return repo_path


class InputSource(contextlib.ExitStack, ABC):
    """
    Input source as specified by the user
    """

    def __init__(self, source: str):
        super().__init__()
        self.source = source
        # Commands that can be used to recreate this InputSource
        self.trace_log: list[str] = []

    def __str__(self) -> str:
        return self.source

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.source})"

    def add_trace_log(self, *args: str) -> None:
        """
        Add a command to the trace log
        """
        self.trace_log.append(" ".join(shlex.quote(c) for c in args))

    @classmethod
    def create(self, source: str | Path) -> InputSource:
        """
        Create an InputSource from a user argument
        """
        if isinstance(source, Path):
            source = source.as_posix()
        parsed = urllib.parse.urlparse(source)
        if parsed.scheme in ("", "file"):
            if os.path.isdir(parsed.path):
                if os.path.isdir(os.path.join(parsed.path, ".git")):
                    return LocalGit(source, parsed.path, copy=False, orig_path=Path(parsed.path).absolute())
                else:
                    return LocalDir(source, parsed.path)
            else:
                return LocalFile(source, parsed.path)
        else:
            return URL(source, parsed)

    @abstractmethod
    def branch(self, branch: str | None) -> InputSource:
        """
        Return an InputSource for the given branch
        """

    @abstractmethod
    def detect_source(self, distro: Distro) -> Source:
        """
        Autodetect the Source for this input
        """


class LocalFile(InputSource):
    """
    Source specified as a local file
    """

    def __init__(self, source: str, path: str):
        super().__init__(source)
        self.path = path

    def branch(self, branch: str | None) -> InputSource:
        raise Fail("--branch does not make sense for local files")

    def detect_source(self, distro: Distro) -> Source:
        from ..distro.debian import DebianDistro

        if isinstance(distro, DebianDistro):
            if self.source.endswith(".dsc"):
                from .debian import DebianDsc

                return DebianDsc._create_from_file(distro, self)
            else:
                raise Fail(f"{self.source!r}: cannot detect source type")
        else:
            if self.source.endswith(".dsc"):
                raise Fail(f"{self.source!r}: cannot build Debian source package on {distro}")
            else:
                raise Fail(f"{self.source!r}: cannot detect source type")


class LocalDir(InputSource):
    """
    Source specified as a local directory, that is not a git working directory
    """

    def __init__(self, source: str, path: str):
        super().__init__(source)
        self.path = path

    def branch(self, branch: str | None) -> InputSource:
        raise Fail("--branch does not make sense for non-git directories")

    def detect_source(self, distro: Distro) -> Source:
        from ..distro.debian import DebianDistro
        from .debian import DebianSourceDir

        if isinstance(distro, DebianDistro):
            if os.path.isdir(os.path.join(self.path, "debian")):
                return DebianSourceDir._create_from_dir(distro, self)
            else:
                raise Fail(f"{self.source!r}: cannot detect source type")
        else:
            from .rpm import RPMSource

            return RPMSource.detect(distro, self)


class LocalGit(InputSource):
    """
    Source specified as a local git working directory
    """

    def __init__(self, source: str, path: str, copy: bool, orig_path: Path | None = None):
        super().__init__(source)
        self.repo = git.Repo(path)
        self.copy = copy
        self.orig_path = orig_path

    @property
    def path(self) -> str:
        """
        Return the filesystem path to the working directory
        """
        return self.repo.working_dir

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

    def clone(self, branch: str | None = None) -> LocalGit:
        """
        Clone this URL into a local git repository
        """
        workdir = _git_clone(self, self.repo.working_dir, branch)
        res = self.enter_context(LocalGit(self.source, workdir, copy=True, orig_path=self.orig_path))
        res.trace_log.extend(self.trace_log)
        return res

    def branch(self, branch: str | None) -> InputSource:
        if not self.repo.head.is_detached and self.repo.active_branch == branch:
            return self
        return self.clone(branch)

    def detect_source(self, distro: Distro) -> Source:
        from ..distro.debian import DebianDistro
        from ..distro.rpm import RpmDistro

        if isinstance(distro, DebianDistro):
            from .debian import DebianGitSource

            return DebianGitSource.detect(distro, self)
        elif isinstance(distro, RpmDistro):
            from .rpm import RPMSource

            return RPMSource.detect(distro, self)
        else:
            raise NotImplementedError(f"No suitable builder found for distribution {distro!r}")


class URL(InputSource):
    """
    Source specified as a URL
    """

    def __init__(self, source: str, parsed: urllib.parse.ParseResult):
        super().__init__(source)
        self.parsed = parsed

    def clone(self, branch: str | None = None) -> LocalGit:
        """
        Clone this URL into a local git repository
        """
        workdir = _git_clone(self, self.source, branch)
        res = self.enter_context(LocalGit(self.source, workdir, copy=True, orig_path=None))
        res.trace_log.extend(self.trace_log)
        return res

    def branch(self, branch: str | None) -> InputSource:
        return self.clone(branch)

    def detect_source(self, distro: Distro) -> Source:
        return self.clone().detect_source(distro)
