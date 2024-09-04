from __future__ import annotations

import logging
import re
import shlex
from abc import ABC, abstractclassmethod, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from moncic.container import ContainerConfig
from moncic.utils.guest import guest_only, host_only

from .inputsource import LocalGit

if TYPE_CHECKING:
    import git

    from ..build import Build
    from ..container import Container, System
    from ..distro import Distro
    from ..lint import Linter
    from .inputsource import InputSource

log = logging.getLogger(__name__)


# Registry of known builders
source_types: dict[str, type[Source]] = {}


def register(source_cls: type[Source]) -> type[Source]:
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


def registry() -> dict[str, type[Source]]:
    """
    Return the registry of available source types
    """
    from . import debian, rpm  # noqa: import them so they are registered as builders

    return source_types


def get_source_class(name: str) -> type[Source]:
    """
    Create a Build object by its name
    """
    return registry()[name.lower()]


@dataclass
class Source(ABC):
    """
    Sources to be built
    """

    # Original source as specified by the user
    source: InputSource
    # Path to the unpacked sources in the host system
    host_path: Path
    # Path to the unpacked sources in the guest system
    guest_path: str | None = None
    # Commands that can be used to recreate this source
    trace_log: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.trace_log.extend(self.source.trace_log)

    @classmethod
    def get_name(cls) -> str:
        """
        Return the user-facing name for this class
        """
        if name := cls.__dict__.get("NAME"):
            return name
        return cls.__name__.lower()

    def add_trace_log(self, *args: str) -> None:
        """
        Add a command to the trace log
        """
        self.trace_log.append(" ".join(shlex.quote(c) for c in args))

    @abstractmethod
    def get_build_class(self) -> type[Build]:
        """
        Return the Build subclass used to build this source
        """

    @abstractmethod
    def get_linter_class(self) -> type[Linter]:
        """
        Return the Linter subclass used to check this source
        """

    @abstractclassmethod
    def create(cls, distro: Distro, source: InputSource) -> Source:
        """
        Create an instance of this source.

        This is used to instantiate a source from a well known class, instead
        of having it autodetected by InputSource
        """

    def make_build(self, **kwargs: Any) -> Build:
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

    @guest_only
    def build_source_package(self) -> str:
        """
        Build a source package in /src/moncic-ci/source returning the name of
        the main file of the source package fileset
        """
        raise NotImplementedError(f"{self.__class__.__name__}.build_source_package is not implemented")

    def find_versions(self, system: System) -> dict[str, str]:
        """
        Get the program version from sources.

        Return a dict mapping version type to version strings
        """
        versions: dict[str, str] = {}

        path = self.host_path
        if (autotools := path / "configure.ac").exists():
            re_autotools = re.compile(r"\s*AC_INIT\s*\(\s*[^,]+\s*,\s*\[?([^,\]]+)")
            with autotools.open("rt") as fd:
                for line in fd:
                    if mo := re_autotools.match(line):
                        versions["autotools"] = mo.group(1).strip()
                        break

        if (meson := path / "meson.build").exists():
            re_meson = re.compile(r"\s*project\s*\(.+version\s*:\s*'([^']+)'")
            with meson.open("rt") as fd:
                for line in fd:
                    if mo := re_meson.match(line):
                        versions["meson"] = mo.group(1).strip()
                        break

        if (cmake := path / "CMakeLists.txt").exists():
            re_cmake = re.compile(r"""\s*set\s*\(\s*PACKAGE_VERSION\s+["']([^"']+)""")
            with cmake.open("rt") as fd:
                for line in fd:
                    if mo := re_cmake.match(line):
                        versions["cmake"] = mo.group(1).strip()
                        break

        if (news := path / "NEWS.md").exists():
            re_news = re.compile(r"# New in version (.+)")
            with news.open("rt") as fd:
                for line in fd:
                    if mo := re_news.match(line):
                        versions["news"] = mo.group(1).strip()
                        break

        # Check setup.py by executing it with --version inside the container
        if (path / "setup.py").exists():
            cconfig = ContainerConfig()
            cconfig.configure_workdir(path, bind_type="ro")
            with system.create_container(config=cconfig) as container:
                res = container.run(["/usr/bin/python3", "setup.py", "--version"])
            if res.returncode == 0:
                lines = res.stdout.splitlines()
                if lines:
                    versions["setup.py"] = lines[-1].strip().decode()

        return versions

    def lint(self, linter: Linter):
        # Check for version mismatches
        versions = self.find_versions(linter.system)

        by_version: dict[str, list[str]] = defaultdict(list)
        for name, version in versions.items():
            if name.endswith("-release"):
                by_version[version.split("-", 1)[0]].append(name)
            else:
                by_version[version].append(name)
        if len(by_version) > 1:
            descs = [f"{v} in {', '.join(names)}" for v, names in by_version.items()]
            linter.warning(f"Versions mismatch: {'; '.join(descs)}")


class GitSource(Source):
    """
    Source backed by a Git repo
    """

    # Redefine source specialized as LocalGit
    source: LocalGit

    def _get_tags_by_hexsha(self) -> dict[str, git.objects.Commit]:
        res: dict[str, list[git.objects.Commit]] = defaultdict(list)
        for tag in self.source.repo.tags:
            res[tag.object.hexsha].append(tag)
        return res

    def find_versions(self, system: System) -> dict[str, str]:
        versions = super().find_versions(system)

        re_versioned_tag = re.compile(r"^v?([0-9].+)")

        repo = self.source.repo

        _tags_by_hexsha = self._get_tags_by_hexsha()

        # List tags for the current commit
        for tag in _tags_by_hexsha.get(repo.head.commit.hexsha, ()):
            if tag.name.startswith("debian/"):
                version = tag.name[7:]
                if "-" in version:
                    versions["tag-debian"] = version.split("-", 1)[0]
                    versions["tag-debian-release"] = version
                else:
                    versions["tag-debian"] = version
            elif mo := re_versioned_tag.match(tag.name):
                version = mo.group(1)
                if "-" in version:
                    versions["tag-arpa"] = version.split("-", 1)[0]
                    versions["tag-arpa-release"] = version
                else:
                    versions["tag"] = version

        return versions
