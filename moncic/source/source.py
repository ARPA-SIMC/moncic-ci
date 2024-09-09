from __future__ import annotations

import abc
import contextlib
import inspect
import logging
import re
import shlex
import subprocess
import tempfile
import urllib.parse
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Sequence

import git

from moncic.container import ContainerConfig
from moncic.exceptions import Fail
from moncic.utils.guest import guest_only, host_only

from ..utils.run import run

if TYPE_CHECKING:
    from ..build import Build
    from ..container import Container, System
    from ..distro import Distro
    from ..lint import Linter
    from .local import LocalSource, Git
    from .distro import DistroSource

log = logging.getLogger(__name__)


# Registry of known builders
source_types: dict[str, type[Source]] = {}


class CommandLog(list[str]):
    """
    Log of commands used to create a source
    """

    def add_command(self, *args: str) -> None:
        """
        Add a command to the command log
        """
        self.append(shlex.join(args))

    def run(self, cmd: Sequence[str], **kwargs) -> subprocess.CompletedProcess:
        """
        Run a command and append it to the command log
        """
        self.append(shlex.join(cmd))
        return run(cmd, **kwargs)


class SourceStack(contextlib.ExitStack):
    """
    ExitStack that raises an error if entered multiple times.
    """

    def __init__(self) -> None:
        super().__init__()
        self.entered: bool = False

    def __enter__(self) -> "SourceStack":  # TODO: use Self from 3.11+
        if self.entered:
            raise RuntimeError("__enter__ called in multiple Sources of the same chain")
        super().__enter__()
        self.entered = True
        return self


class Source(abc.ABC):
    """
    Source code to build.

    Not all sources can be built directly: remote URLs need to be cloned
    locally, or local sources need to be prepared for build.

    An initial Source can create a transformed version of itself that can be
    built, tracking the sequence of transformations.

    The initial source needs to be used as a context manager, and serves as
    storage of temporary resources for the sources derived from it.
    """

    #: User-provided name for this resource
    name: str
    #: Source from which this one was generated. None if this is the original source
    parent: Optional["Source"]
    #: ExitStack to use for temporary state
    stack: contextlib.ExitStack
    #: Commands that can be used to recreate this source
    command_log: CommandLog

    @classmethod
    def get_source_type(cls) -> str:
        """
        Return the user-facing name for this class
        """
        if name := cls.__dict__.get("NAME"):
            return name
        return cls.__name__.lower()

    def __init_subclass__(cls, **kwargs) -> None:
        """Register subclasses."""
        super().__init_subclass__(**kwargs)
        if inspect.isabstract(cls):
            return
        source_types[cls.get_source_type()] = cls

    def __init__(self, *, name: str | None = None, parent: Source | None = None, command_log: CommandLog | None = None):
        self.parent = parent
        if parent is None:
            self.stack = SourceStack()
        else:
            self.stack = parent.stack
            if name is None:
                name = parent.name
        if name is None:
            raise AttributeError("name not provided, and no parent to use as a fallback")
        self.name = name
        self.command_log = command_log or CommandLog()

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.name})"

    def __enter__(self) -> "Source":
        self.stack.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> Any:
        return self.stack.__exit__(exc_type, exc_val, exc_tb)

    @classmethod
    def create_local(cls, *, source: str | Path, branch: str | None = None) -> "LocalSource":
        """
        Create a distro-agnostic source from a user-defined string
        """
        base: Source

        # Handle string arguments
        if isinstance(source, str):
            url = urllib.parse.urlparse(source)
            if url.scheme not in ("", "file"):
                from .remote import URL

                base = URL(name=source, url=url)
                return base.clone(branch)
            name = source
            source = Path(url.path)
        else:
            name = source.as_posix()

        # Handle paths
        if source.is_dir():
            if (source / ".git").is_dir():
                from .local import Git

                base = Git(name=name, path=source.absolute())
                if branch:
                    return base.get_branch(branch)
                else:
                    return base
            else:
                from .local import Dir

                if branch is not None:
                    raise Fail("Cannot specify a branch when working on a non-git directory")
                return Dir(name=name, path=source.absolute())
        else:
            from .local import File

            if branch is not None:
                raise Fail("Cannot specify a branch when working on a file")
            return File(name=name, path=source.absolute())

    @classmethod
    def get_distro_source_class(cls, *, distro: Distro) -> type["DistroSource"]:
        from ..distro.debian import DebianDistro
        from ..distro.rpm import RpmDistro

        if isinstance(distro, DebianDistro):
            from .debian import DebianSource

            return DebianSource
        elif isinstance(distro, RpmDistro):
            from .rpm import RPMSource

            return RPMSource
        else:
            raise NotImplementedError(f"No suitable git builder found for distribution {distro!r}")

    def _git_clone(self, repository: str, branch: str | None = None) -> "Git":
        """
        Derive a Git source from this one, by cloning a git repository
        Clone this git repository into a temporary working directory.

        Return the path of the new cloned working directory
        """
        from .local import Git

        # Git checkout in a temporary directory
        workdir = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        command_log = CommandLog()

        clone_cmd = ["git", "-c", "advice.detachedHead=false", "clone", "--quiet", repository]
        if branch is not None:
            clone_cmd += ["--branch", branch]
        command_log.run(clone_cmd, cwd=workdir)

        # Look for the directory that git created
        paths = list(workdir.iterdir())
        if len(paths) != 1:
            raise RuntimeError("git clone created more than one entry in its current directory")

        new_path = paths[0]

        # Recreate remote branches
        repo = git.Repo(new_path)
        for ref in repo.remote().refs:
            name = ref.remote_head
            if name == "HEAD":
                continue
            if name in repo.refs:
                continue
            repo.create_head(name, ref)

        # If we cloned a detached head, create a local branch for it
        if repo.head.is_detached:
            branch = "moncic-ci"
            local_branch = repo.create_head(branch)
            local_branch.checkout()
            command_log.add_command("git", "checkout", "-b", branch)

        return Git(parent=self, path=new_path, repo=repo, readonly=False, command_log=command_log)


#    @abstractmethod
#    def get_linter_class(self) -> type[Linter]:
#        """
#        Return the Linter subclass used to check this source
#        """
#
#    def make_build(self, **kwargs: Any) -> Build:
#        """
#        Create a Build to build this Source
#        """
#        return self.get_build_class()(source=self, **kwargs)
#
#    @host_only
#    def gather_sources_from_host(self, build: Build, container: Container) -> None:
#        """
#        Gather needed source files from the host system and copy them to the
#        guest
#        """
#        # Do nothing by default
#
#    @guest_only
#    def build_source_package(self) -> str:
#        """
#        Build a source package in /src/moncic-ci/source returning the name of
#        the main file of the source package fileset
#        """
#        raise NotImplementedError(f"{self.__class__.__name__}.build_source_package is not implemented")
#
#    def find_versions(self, system: System) -> dict[str, str]:
#        """
#        Get the program version from sources.
#
#        Return a dict mapping version type to version strings
#        """
#        versions: dict[str, str] = {}
#
#        path = self.host_path
#        if (autotools := path / "configure.ac").exists():
#            re_autotools = re.compile(r"\s*AC_INIT\s*\(\s*[^,]+\s*,\s*\[?([^,\]]+)")
#            with autotools.open("rt") as fd:
#                for line in fd:
#                    if mo := re_autotools.match(line):
#                        versions["autotools"] = mo.group(1).strip()
#                        break
#
#        if (meson := path / "meson.build").exists():
#            re_meson = re.compile(r"\s*project\s*\(.+version\s*:\s*'([^']+)'")
#            with meson.open("rt") as fd:
#                for line in fd:
#                    if mo := re_meson.match(line):
#                        versions["meson"] = mo.group(1).strip()
#                        break
#
#        if (cmake := path / "CMakeLists.txt").exists():
#            re_cmake = re.compile(r"""\s*set\s*\(\s*PACKAGE_VERSION\s+["']([^"']+)""")
#            with cmake.open("rt") as fd:
#                for line in fd:
#                    if mo := re_cmake.match(line):
#                        versions["cmake"] = mo.group(1).strip()
#                        break
#
#        if (news := path / "NEWS.md").exists():
#            re_news = re.compile(r"# New in version (.+)")
#            with news.open("rt") as fd:
#                for line in fd:
#                    if mo := re_news.match(line):
#                        versions["news"] = mo.group(1).strip()
#                        break
#
#        # Check setup.py by executing it with --version inside the container
#        if (path / "setup.py").exists():
#            cconfig = ContainerConfig()
#            cconfig.configure_workdir(path, bind_type="ro")
#            with system.create_container(config=cconfig) as container:
#                res = container.run(["/usr/bin/python3", "setup.py", "--version"])
#            if res.returncode == 0:
#                lines = res.stdout.splitlines()
#                if lines:
#                    versions["setup.py"] = lines[-1].strip().decode()
#
#        return versions
#
#    def lint(self, linter: Linter):
#        # Check for version mismatches
#        versions = self.find_versions(linter.system)
#
#        by_version: dict[str, list[str]] = defaultdict(list)
#        for name, version in versions.items():
#            if name.endswith("-release"):
#                by_version[version.split("-", 1)[0]].append(name)
#            else:
#                by_version[version].append(name)
#        if len(by_version) > 1:
#            descs = [f"{v} in {', '.join(names)}" for v, names in by_version.items()]
#            linter.warning(f"Versions mismatch: {'; '.join(descs)}")
