from __future__ import annotations

import logging
import shlex
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional, Type

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
