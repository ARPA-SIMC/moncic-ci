from __future__ import annotations

import logging
import os
import tempfile
import urllib.parse
from dataclasses import dataclass
from typing import TYPE_CHECKING, Generator, Optional, Type

import git

from ..exceptions import Fail
from ..utils.guest import guest_only, host_only
from ..utils.run import run

if TYPE_CHECKING:
    from ..build import Build, Builder
    from ..container import Container

log = logging.getLogger(__name__)


# Registry of known builders
source_types: dict[str, Type["Source"]] = {}


def register(builder_cls: Type["Build"]) -> Type["Build"]:
    """
    Add a Build object to the Build registry
    """
    name = getattr(builder_cls, "NAME", None)
    if name is None:
        name = builder_cls.__name__.lower()
    source_types[name] = builder_cls

    # Register extra_args callbacks.
    #
    # Only register callbacks that are in the class __dict__ to avoid
    # inheritance, which would register command line options from base
    # classes multiple times
    # if "add_arguments" in builder_cls.__dict__:
    #     cls.extra_args_callbacks.append(builder_cls.add_arguments)

    return builder_cls


def registry() -> dict[str, Type["Source"]]:
    from . import (  # noqa: import them so they are registered as builders
        debian, rpm)
    return source_types


def get_source_class(name: str) -> Type["Source"]:
    """
    Create a Build object by its name
    """
    return registry()[name.lower()]


def _git_clone(builder: Builder, repository: str, branch: Optional[str] = None) -> str:
    """
    Clone a git repository into a temporary working directory.

    Return the path of the new cloned working directory
    """
    # Git checkout in a temporary directory
    workdir = builder.enter_context(tempfile.TemporaryDirectory())
    cmd = ["git", "-c", "advice.detachedHead=false", "clone", "--quiet", repository]
    if branch is not None:
        cmd += ["--branch", branch]
    run(cmd, cwd=workdir)

    # Look for the directory that git created
    names = os.listdir(workdir)
    if len(names) != 1:
        raise RuntimeError("git clone create more than one entry in its current directory: {names!r}")
    return os.path.join(workdir, names[0])


class InputSource:
    """
    Input source as specified by the user
    """
    def __init__(self, source: str):
        self.source = source

    @classmethod
    def create(self, source: str) -> "InputSource":
        """
        Create an InputSource from a user argument
        """
        parsed = urllib.parse.urlparse(source)
        if parsed.scheme in ("", "file"):
            if os.path.isdir(parsed.path):
                if os.path.isdir(os.path.join(parsed.path, ".git")):
                    return LocalGit(source, parsed.path, copy=False)
                else:
                    return LocalDir(source, parsed.path)
            else:
                return LocalFile(source, parsed.path)
        else:
            return URL(source, parsed)

    def branch(self, builder: Builder, branch: Optional[str]) -> "InputSource":
        """
        Return an InputSource for the given branch
        """
        raise NotImplementedError(f"{self.__class__.__name__}.branch is not implemented")

    def detect_source(self, builder: Builder) -> "Source":
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

    def branch(self, builder: Builder, branch: Optional[str]) -> "InputSource":
        raise Fail("--branch does not make sense for local files")

    def detect_source(self, builder: Builder) -> "Source":
        from .debian import DebianSourcePackage
        from ..distro.debian import DebianDistro
        distro = builder.system.distro
        if isinstance(distro, DebianDistro):
            if self.source.endswith(".dsc"):
                return DebianSourcePackage(self.source)
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

    def branch(self, builder: Builder, branch: Optional[str]) -> "InputSource":
        raise Fail("--branch does not make sense for non-git directories")

    def detect_source(self, builder: Builder) -> "Source":
        from .debian import DebianSourceDir
        from ..distro.debian import DebianDistro

        distro = builder.system.distro
        if isinstance(distro, DebianDistro):
            if os.path.isdir(os.path.join(self.path, "debian")):
                return DebianSourceDir(self.path)
            else:
                raise Fail(f"{self.source!r}: cannot detect source type")
        else:
            if os.path.isdir(os.path.join(self.path, "debian")):
                raise Fail(f"{self.source!r}: cannot build Debian source on {distro}")
            else:
                # TODO: find specfiles?
                raise Fail(f"{self.source!r}: cannot detect source type")


class LocalGit(InputSource):
    """
    Source specified as a local git working directory
    """
    def __init__(self, source: str, path: str, copy: bool):
        super().__init__(source)
        self.repo = git.Repo(path)
        self.copy = copy

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

    def clone(self, builder: Builder, branch: Optional[str] = None) -> LocalGit:
        """
        Clone this URL into a local git repository
        """
        workdir = _git_clone(builder, self.repo.working_dir, branch)
        return LocalGit(self.source, workdir, copy=True)

    def branch(self, builder: Builder, branch: Optional[str]) -> "InputSource":
        if self.repo.active_branch == branch:
            return self
        return self.clone(builder, branch)

    def detect_source(self, builder: Builder) -> "Source":
        from ..distro.debian import DebianDistro
        from ..distro.rpm import RpmDistro
        distro = builder.system.distro
        if isinstance(distro, DebianDistro):
            from .debian import DebianGBPTestUpstream, DebianPlainGit, DebianGBPRelease, DebianGBPTestDebian
            if not os.path.isdir(os.path.join(self.repo.working_dir, "debian")):
                # There is no debian/directory, the current branch is upstream
                return DebianGBPTestUpstream._create_from_repo(builder, self)

            if not os.path.exists(os.path.join(self.repo.working_dir, "debian", "gbp.conf")):
                return DebianPlainGit._create_from_repo(builder, self)

            if self.repo.head.commit.hexsha in [t.commit.hexsha for t in self.repo.tags]:
                # If branch to build is a tag, build a release from it
                return DebianGBPRelease._create_from_repo(builder, self)
            else:
                # There is a debian/ directory, find upstream from gbp.conf
                return DebianGBPTestDebian._create_from_repo(builder, self)
        elif isinstance(distro, RpmDistro):
            from .rpm import ARPAGit
            travis_yml = os.path.join(self.repo.working_dir, ".travis.yml")
            try:
                with open(travis_yml, "rt") as fd:
                    if 'simc/stable' in fd.read():
                        return ARPAGit._create_from_repo(builder, self)
            except FileNotFoundError:
                pass
            raise Fail("but simc/stable not found in .travis.yml for ARPA builds")
        else:
            raise NotImplementedError(f"No suitable builder found for distribution {distro!r}")


class URL(InputSource):
    """
    Source specified as a URL
    """
    def __init__(self, source: str, parsed: urllib.parse.ParseResult):
        super().__init__(source)
        self.parsed = parsed

    def clone(self, builder: Builder, branch: Optional[str] = None) -> LocalGit:
        """
        Clone this URL into a local git repository
        """
        workdir = _git_clone(builder, self.source, branch)
        return LocalGit(self.source, workdir, copy=True)

    def branch(self, builder: Builder, branch: Optional[str]) -> "InputSource":
        return self.clone(builder, branch).branch(builder, branch)

    def detect_source(self, builder: Builder) -> "Source":
        return self.clone(builder).detect_source(builder)


@dataclass
class Source:
    """
    Sources to be built
    """
    # Original source as specified by the user
    source: str
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
        raise NotImplementedError(f"{self.__class__.__name__}.get_build_class is not implemented")

    @host_only
    def gather_sources_from_host(self, container: Container) -> None:
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
