from __future__ import annotations

import logging
import os
import tempfile
import urllib.parse
from dataclasses import dataclass
from typing import TYPE_CHECKING, Generator, Optional, Type

import git

from ..container import RunConfig
from ..exceptions import Fail
from ..utils.guest import guest_only, host_only

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


def get(name: str) -> Type["Source"]:
    """
    Create a Build object by its name
    """
    return registry()[name.lower()]


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
    def _create_local(cls, builder: Builder, path: str, branch: Optional[str]) -> "Source":
        """
        Create a Source class given a local path
        """
        from .debian import DebianSourceDir, DebianSourcePackage
        if os.path.isdir(path):
            if os.path.isdir(os.path.join(path, ".git")):
                return Git._create_local(builder, path, branch)
            elif os.path.isdir(os.path.join(path, "debian")):
                return DebianSourceDir(path)
            else:
                # TODO: find specfiles?
                raise Fail(f"{path!r}: cannot detect source type")
        elif path.endswith(".dsc"):
            return DebianSourcePackage(path)
        else:
            raise Fail(f"{path!r}: cannot detect source type")

    @classmethod
    def create(cls, builder: Builder, source: str, branch: Optional[str] = None) -> "Source":
        """
        Create a Source class for the given source path/url
        """
        parsed = urllib.parse.urlparse(source)
        if parsed.scheme == '':
            return cls._create_local(builder, source, branch)

        if parsed.scheme == "file":
            return cls._create_local(builder, parsed.path, branch)

        return Git._create_clone(builder, source, branch)

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


@dataclass
class Git(Source):
    """
    Local git working directory
    """
    @classmethod
    def _clone(cls, builder: Builder, repository: str, branch: Optional[str] = None) -> str:
        """
        Clone a git repository into a temporary working directory.

        Return the path of the new cloned working directory
        """
        # Git checkout in a temporary directory
        workdir = builder.enter_context(tempfile.TemporaryDirectory())
        cmd = ["git", "-c", "advice.detachedHead=false", "clone", repository]
        if branch is not None:
            cmd += ["--branch", branch]
        builder.system.local_run(cmd, config=RunConfig(cwd=workdir))

        # Look for the directory that git created
        names = os.listdir(workdir)
        if len(names) != 1:
            raise RuntimeError("git clone create more than one entry in its current directory: {names!r}")
        return os.path.join(workdir, names[0])

    @classmethod
    def _create_local(cls, builder: Builder, path: str, branch: Optional[str]) -> "Git":
        """
        Create a Git Source given a path to a local git workdir
        """
        repo = git.Repo(os.path.abspath(path))
        if branch is None or repo.active_branch == branch:
            return cls._create_from_repo(builder, path, repo, cloned=False)

        # We need to checkout the right branch
        return cls._create_clone(builder, path, branch)

    @classmethod
    def _create_clone(cls, builder: Builder, url: str, branch: Optional[str]) -> "Git":
        """
        Create a Git Source given a remote URL
        """
        new_workdir = cls._clone(builder, url, branch)
        return cls._create_from_repo(builder, url, git.Repo(new_workdir), cloned=True)

    @classmethod
    def _create_from_repo(cls, builder: Builder, source: str, repo: git.Repo, cloned: bool) -> "Git":
        """
        Create a Git Source from a prepared host path
        """
        from ..distro.debian import DebianDistro
        from ..distro.rpm import RpmDistro
        distro = builder.system.distro
        if isinstance(distro, DebianDistro):
            from .debian import DebianGit
            return DebianGit._create_from_repo(builder, source, repo, cloned=cloned)
        elif isinstance(distro, RpmDistro):
            from .rpm import RPMGit
            return RPMGit._create_from_repo(builder, source, repo, cloned=cloned)
        else:
            raise NotImplementedError(f"No suitable builder found for distribution {distro!r}")
