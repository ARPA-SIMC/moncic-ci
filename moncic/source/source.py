from __future__ import annotations

import logging
import re
import shlex
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Type

from ..analyze import Analyzer
from ..utils.guest import guest_only, host_only

if TYPE_CHECKING:
    from ..build import Build
    from ..container import Container
    from .inputsource import InputSource

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
    # Commands that can be used to recreate this source
    trace_log: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.trace_log.extend(self.source.trace_log)

    @classmethod
    def get_name(cls) -> str:
        """
        Return the user-facing name for this class
        """
        if (name := cls.__dict__.get("NAME")):
            return name
        return cls.__name__.lower()

    def add_trace_log(self, *args: str) -> None:
        """
        Add a command to the trace log
        """
        self.trace_log.append(" ".join(shlex.quote(c) for c in args))

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

    def find_versions(self) -> dict[str, str]:
        """
        Get the program version from sources.

        Return a dict mapping version type to version strings
        """
        versions: dict[str, str] = {}

        path = Path(self.source.path)
        if (autotools := path / "configure.ac").exists():
            re_autotools = re.compile(r"\s*AC_INIT\s*\(\s*[^,]+\s*,\s*\[?([^,\]]+)")
            with autotools.open("rt") as fd:
                for line in fd:
                    if (mo := re_autotools.match(line)):
                        versions["autotools"] = mo.group(1).strip()
                        break

        if (meson := path / "meson.build").exists():
            re_meson = re.compile(r"\s*project\s*\(.+version\s*:\s*'([^']+)'")
            with meson.open("rt") as fd:
                for line in fd:
                    if (mo := re_meson.match(line)):
                        versions["meson"] = mo.group(1).strip()
                        break

        if (cmake := path / "CMakeLists.txt").exists():
            re_cmake = re.compile(r"""\s*set\s*\(\s*PACKAGE_VERSION\s+["']([^"']+)""")
            with cmake.open("rt") as fd:
                for line in fd:
                    if (mo := re_cmake.match(line)):
                        versions["cmake"] = mo.group(1).strip()
                        break

        if (news := path / "NEWS.md").exists():
            re_news = re.compile(r"# New in version (.+)")
            with news.open("rt") as fd:
                for line in fd:
                    if (mo := re_news.match(line)):
                        versions["news"] = mo.group(1).strip()
                        break

        # TODO: check setup.py
        # TODO: can it be checked without checking out the branch and executing it?
        # TODO: check debian/changelog in a subclass
        # TODO: check specfile in a subclass

        return versions

    def analyze(self, analyzer: Analyzer):
        """
        lint-check the sources, using analyzer to output results
        """
        # Check for version mismatches
        versions = self.find_versions()

        by_version: dict[str, list[str]] = defaultdict(list)
        for name, version in versions.items():
            by_version[version].append(name)
        if len(by_version) > 1:
            descs = [f"{v} in {', '.join(names)}" for v, names in by_version.items()]
            analyzer.warning(f"Versions mismatch: {'; '.join(descs)}")
