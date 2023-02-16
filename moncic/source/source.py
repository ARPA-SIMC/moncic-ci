from __future__ import annotations

import contextlib
import logging
import os
import tempfile
import urllib.parse
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generator, Optional, Type

import git

from ..exceptions import Fail
from ..utils.guest import guest_only, host_only
from ..utils.run import run

if TYPE_CHECKING:
    from ..build import Build
    from ..container import Container
    from ..distro import Distro

log = logging.getLogger(__name__)


# Registry of known builders
source_types: dict[str, Type["Source"]] = {}


def register(source_cls: Type["Source"]) -> Type["Source"]:
    """
    Add a Build object to the Build registry
    """
    name = getattr(source_cls, "NAME", None)
    if name is None:
        name = source_cls.__name__.lower()
    source_types[name] = source_cls

    # Register extra_args callbacks.
    #
    # Only register callbacks that are in the class __dict__ to avoid
    # inheritance, which would register command line options from base
    # classes multiple times
    # if "add_arguments" in builder_cls.__dict__:
    #     cls.extra_args_callbacks.append(builder_cls.add_arguments)

    return source_cls


def registry() -> dict[str, Type["Source"]]:
    from . import (  # noqa: import them so they are registered as builders
        debian, rpm)
    return source_types


def get_source_class(name: str) -> Type["Source"]:
    """
    Create a Build object by its name
    """
    return registry()[name.lower()]


def _git_clone(stack: contextlib.ExitStack, repository: str, branch: Optional[str] = None) -> str:
    """
    Clone a git repository into a temporary working directory.

    Return the path of the new cloned working directory
    """
    # Git checkout in a temporary directory
    workdir = stack.enter_context(tempfile.TemporaryDirectory())
    cmd = ["git", "-c", "advice.detachedHead=false", "clone", "--quiet", repository]
    if branch is not None:
        cmd += ["--branch", branch]
    run(cmd, cwd=workdir)

    # Look for the directory that git created
    names = os.listdir(workdir)
    if len(names) != 1:
        raise RuntimeError("git clone create more than one entry in its current directory: {names!r}")
    return os.path.join(workdir, names[0])


class InputSource(contextlib.ExitStack):
    """
    Input source as specified by the user
    """
    def __init__(self, source: str):
        super().__init__()
        self.source = source

    def __str__(self) -> str:
        return self.source

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.source})"

    @classmethod
    def create(self, source: str) -> "InputSource":
        """
        Create an InputSource from a user argument
        """
        parsed = urllib.parse.urlparse(source)
        if parsed.scheme in ("", "file"):
            if os.path.isdir(parsed.path):
                if os.path.isdir(os.path.join(parsed.path, ".git")):
                    return LocalGit(source, parsed.path, copy=False, orig_path=os.path.abspath(parsed.path))
                else:
                    return LocalDir(source, parsed.path)
            else:
                return LocalFile(source, parsed.path)
        else:
            return URL(source, parsed)

    def branch(self, branch: Optional[str]) -> "InputSource":
        """
        Return an InputSource for the given branch
        """
        raise NotImplementedError(f"{self.__class__.__name__}.branch is not implemented")

    def detect_source(self, distro: Distro) -> "Source":
        """
        Autodetect the Source for this input
        """
        raise NotImplementedError(f"{self.__class__.__name__}.detect_source is not implemented")


class LocalFile(InputSource):
    """
    Source specified as a local file
    """
    def __init__(self, source: str, path: str):
        super().__init__(source)
        self.path = path

    def branch(self, branch: Optional[str]) -> "InputSource":
        raise Fail("--branch does not make sense for local files")

    def detect_source(self, distro: Distro) -> "Source":
        from ..distro.debian import DebianDistro
        if isinstance(distro, DebianDistro):
            if self.source.endswith(".dsc"):
                from .debian import DebianDsc
                return DebianDsc._create_from_file(self)
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

    def branch(self, branch: Optional[str]) -> "InputSource":
        raise Fail("--branch does not make sense for non-git directories")

    def detect_source(self, distro: Distro) -> "Source":
        from ..distro.debian import DebianDistro
        from .debian import DebianSourceDir

        if isinstance(distro, DebianDistro):
            if os.path.isdir(os.path.join(self.path, "debian")):
                return DebianSourceDir._create_from_dir(self)
            else:
                raise Fail(f"{self.source!r}: cannot detect source type")
        else:
            from .rpm import RPMSource
            return RPMSource.detect(distro, self)


class LocalGit(InputSource):
    """
    Source specified as a local git working directory
    """
    def __init__(self, source: str, path: str, copy: bool, orig_path: Optional[str] = None):
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

    def find_branch(self, name: str) -> Optional[git.refs.symbolic.SymbolicReference]:
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

    def clone(self, branch: Optional[str] = None) -> LocalGit:
        """
        Clone this URL into a local git repository
        """
        workdir = _git_clone(self, self.repo.working_dir, branch)
        return LocalGit(self.source, workdir, copy=True, orig_path=self.orig_path)

    def branch(self, branch: Optional[str]) -> "InputSource":
        if self.repo.active_branch == branch:
            return self
        return self.clone(branch)

    def detect_source(self, distro: Distro) -> "Source":
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

    def clone(self, branch: Optional[str] = None) -> LocalGit:
        """
        Clone this URL into a local git repository
        """
        workdir = _git_clone(self, self.source, branch)
        return LocalGit(self.source, workdir, copy=True, orig_path=None)

    def branch(self, branch: Optional[str]) -> "InputSource":
        return self.clone(branch).branch(branch)

    def detect_source(self, distro: Distro) -> "Source":
        return self.clone().detect_source(distro)


@dataclass
class Source:
    """
    Sources to be built
    """
    # Original source as specified by the user
    source: InputSource
    # Path to the unpacked sources in the host system
    host_path: str
    # Path to the unpacked sources in the guest system
    guest_path: Optional[str] = None

    @classmethod
    def list_build_options(cls) -> Generator[tuple[str, str], None, None]:
        """
        List available build option names and their documentation
        """
        return
        yield

    def get_build_class(self) -> Type["Build"]:
        """
        Return the Build subclass used to build this source
        """
        raise NotImplementedError(f"{self.__class__.__name__}.get_build_class is not implemented")

    def make_build(self, **kwargs: Any) -> "Build":
        """
        Create a Build to build this Source
        """
        return self.get_build_class()(source=self, **kwargs)

    @host_only
    def gather_sources_from_host(self, build: Build, container: Container) -> None:
        """
        Gather needed source files from the host system and copy them to the
        guest
        """
        # Do nothing by default
        pass

    @guest_only
    def build_source_package(self) -> str:
        """
        Build a source package in /src/moncic-ci/source returning the name of
        the main file of the source package fileset
        """
        raise NotImplementedError(f"{self.__class__.__name__}.build_source_package is not implemented")
