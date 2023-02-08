from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Generator, Optional, Type

from ..utils.guest import guest_only, host_only

if TYPE_CHECKING:
    from ..container import Container, System

# Registry of known builders
build_types: dict[str, Type["Build"]] = {}


def register(builder_cls: Type["Build"]) -> Type["Build"]:
    """
    Add a Build object to the Build registry
    """
    name = getattr(builder_cls, "NAME", None)
    if name is None:
        name = builder_cls.__name__.lower()
    build_types[name] = builder_cls

    # Register extra_args callbacks.
    #
    # Only register callbacks that are in the class __dict__ to avoid
    # inheritance, which would register command line options from base
    # classes multiple times
    # if "add_arguments" in builder_cls.__dict__:
    #     cls.extra_args_callbacks.append(builder_cls.add_arguments)

    return builder_cls


def get(name: str) -> Type["Build"]:
    """
    Create a Build object by its name
    """
    return build_types[name.lower()]


def detect(*, system: System, source: str, **kw) -> Type["Build"]:
    """
    Autodetect and instantiate a build object
    """
    from ..distro.debian import DebianDistro
    from ..distro.rpm import RpmDistro
    if isinstance(system.distro, DebianDistro):
        from . import debian
        return debian.detect(system=system, source=source, **kw)
        # return build_types["debian"].create(system=system, source=source, **kw)
    elif isinstance(system.distro, RpmDistro):
        from . import arpa
        return arpa.detect(system=system, source=source, **kw)
        # return build_types["rpm"].create(system=system, source=source, **kw)
    else:
        raise NotImplementedError(f"No suitable builder found for distribution {system.distro!r}")


@dataclass
class Build:
    """
    Information gathered during a build
    """
    # Path to source to be built
    source: str
    # Package name (optional when not yet set)
    name: Optional[str] = None
    # True if the build was successful
    success: bool = False
    # List of container paths for artifacts
    artifacts: list[str] = field(default_factory=list)

    @guest_only
    def build(self):
        """
        Run the build.

        The function will be called inside the running system.

        The current directory will be set to the source directory in /srv/moncic-ci/source/<name>.

        Standard output and standard error are logged.
        """
        raise NotImplementedError(f"{self.__class__.__name__}.build is not implemented")

    @host_only
    def setup_container_host(self, container: Container):
        """
        Hook to run setup functions in the host container
        """
        # TODO: remove in favour of something more specific
        pass

    @guest_only
    def setup_container_guest(self):
        """
        Set up the build environment in the container
        """
        pass

    @classmethod
    def list_build_options(cls) -> Generator[tuple[str, str], None, None]:
        """
        List available build option names and their documentation
        """
        return
        yield
