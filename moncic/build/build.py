from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field, fields
from typing import TYPE_CHECKING, Generator, Optional, Type

import yaml

from ..distro import Distro
from ..exceptions import Fail
from ..source import Source
from ..utils.guest import guest_only, host_only

if TYPE_CHECKING:
    from ..container import Container

log = logging.getLogger(__name__)


@dataclass
class Build:
    """
    Build source packages
    """
    # Path to source to be built
    source: Source
    # Distribution on which to build
    distro: Distro
    # Package name (optional when not yet set)
    name: Optional[str] = None
    # True if the build was successful
    success: bool = False
    # List of container paths for artifacts
    artifacts: list[str] = field(default_factory=list)

    artifacts_dir: Optional[str] = field(
            default=None,
            metadata={
                "doc": """
                    Directory where artifacts are copied after the build. Artifacts are lost when not set
                    """})

    source_only: bool = field(
            default=False,
            metadata={
                "doc": """
                    Set to True to only build source packages, and skip compiling/building
                    binary packages
                """})

    on_success: list[str] = field(
            default_factory=list,
            metadata={
                "doc": """
                    Zero or more scripts or actions to execute after a
                    successful build.

                    See [Post-build actions](post-build.actions.md) for documentation of possible values.
                """})

    on_fail: list[str] = field(
            default_factory=list,
            metadata={
                "doc": """
                    Zero or more scripts or actions to execute after a
                    failed build.

                    See [Post-build actions](post-build.actions.md) for documentation of possible values.
                """})

    on_end: list[str] = field(
            default_factory=list,
            metadata={
                "doc": """
                    Zero or more scripts or actions to execute after a
                    build, regardless of its result.

                    See [Post-build actions](post-build.actions.md) for documentation of possible values.
                """})

    def load_yaml(self, pathname: str) -> None:
        """
        Load build configuration from the given YAML file.

        Keys the YAML contains a dict whose keys are the lowercased names of
        Build subclasses (including `build`) and the values are dicts with
        key/value pairs to populate fields.

        Fields will be set from key/value pairs in classes that are part of
        this object's inheritance tree. Notably, `build` key/value pairs are
        always set.
        """
        with open(pathname, "rt") as fd:
            conf = yaml.load(fd, Loader=yaml.CLoader)

        if not isinstance(conf, dict):
            raise Fail(f"{pathname!r}: YAML file should contain a dict")

        sections = set(cls.get_name() for cls in self.__class__.__mro__ if cls != object)

        valid_fields = set(f.name for f in fields(self))

        for section, values in conf.items():
            if section not in sections:
                continue
            for key, val in values.items():
                if key not in valid_fields:
                    log.warning("%r: unknown field {%r} in section {%r}", pathname, key, section)
                else:
                    setattr(self, key, val)

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
    def get_name(cls) -> str:
        """
        Get the user-facing name for this Build class
        """
        if (name := cls.__dict__.get("NAME")):
            return name
        return cls.__name__.lower()

    @classmethod
    def list_build_classes(cls) -> list[Type["Build"]]:
        """
        Return a list of all available build classes, including intermediate
        classes in class hierarchies
        """
        from .arpa import ARPA, RPM
        from .debian import Debian
        return [
            Build,
            Debian,
            RPM,
            ARPA,
        ]

    @classmethod
    def list_build_options(cls) -> Generator[tuple[str, str], None, None]:
        """
        List available build option names and their documentation
        """
        for f in fields(cls):
            if (doc := f.metadata.get("doc")):
                yield f.name, inspect.cleandoc(doc)
