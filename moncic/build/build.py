import abc
import contextlib
import inspect
import logging
import shlex
import subprocess
from dataclasses import dataclass, field, fields
from typing import Any
from collections.abc import Generator, Sequence
from pathlib import Path

import yaml

from moncic.distro import Distro
from moncic.container import ContainerConfig
from moncic.exceptions import Fail
from moncic.source.distro import DistroSource
from moncic.utils.guest import guest_only, host_only
from moncic.utils.run import run
from moncic.utils.script import Script
from moncic.image import RunnableImage


log = logging.getLogger(__name__)


@dataclass
class Build(abc.ABC):
    """
    Build source packages
    """

    #: Source to be built
    source: DistroSource
    #: Distribution on which to build
    distro: Distro
    #: Package name (optional when not yet set)
    name: str | None = None
    #: Set to True for faster builds, that assume that the container is already
    #: up to date
    quick: bool = False
    #: True if the build was successful
    success: bool = False
    #: List of container paths for artifacts
    artifacts: list[str] = field(default_factory=list)
    #: Commands that can be used to recreate this build
    trace_log: list[str] = field(default_factory=list)

    artifacts_dir: Path | None = field(
        default=None,
        metadata={
            "doc": """
                    Directory where artifacts are copied after the build. Artifacts are lost when not set
                    """
        },
    )

    source_only: bool = field(
        default=False,
        metadata={
            "doc": """
                    Set to True to only build source packages, and skip compiling/building
                    binary packages
                """
        },
    )

    on_success: list[str] = field(
        default_factory=list,
        metadata={
            "doc": """
                    Zero or more scripts or actions to execute after a
                    successful build.

                    See [Post-build actions](post-build.actions.md) for documentation of possible values.
                """
        },
    )

    on_fail: list[str] = field(
        default_factory=list,
        metadata={
            "doc": """
                    Zero or more scripts or actions to execute after a
                    failed build.

                    See [Post-build actions](post-build.actions.md) for documentation of possible values.
                """
        },
    )

    on_end: list[str] = field(
        default_factory=list,
        metadata={
            "doc": """
                    Zero or more scripts or actions to execute after a
                    build, regardless of its result.

                    See [Post-build actions](post-build.actions.md) for documentation of possible values.
                """
        },
    )

    @classmethod
    def get_build_class(cls, source: DistroSource) -> type["Build"]:
        from ..source.debian import DebianSource
        from ..source.rpm import RPMSource, ARPASource
        from .debian import Debian
        from .arpa import RPM, ARPA

        # FIXME: use match from python 3.10+
        if isinstance(source, DebianSource):
            return Debian
        elif isinstance(source, ARPASource):
            return ARPA
        elif isinstance(source, RPMSource):
            return RPM
        raise Fail(f"Cannot detect build class for {source.__class__.__name__} source")

    def add_trace_log(self, *args: str) -> None:
        """
        Add a command to the trace log
        """
        self.trace_log.append(shlex.join(args))

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
        with open(pathname) as fd:
            conf = yaml.load(fd, Loader=yaml.CLoader)

        if not isinstance(conf, dict):
            raise Fail(f"{pathname!r}: YAML file should contain a dict")

        sections = {cls.get_name() for cls in self.__class__.__mro__ if cls != object}

        valid_fields = {f.name for f in fields(self)}

        for section, values in conf.items():
            if section not in sections:
                continue
            for key, val in values.items():
                if key not in valid_fields:
                    log.warning("%r: unknown field {%r} in section {%r}", pathname, key, section)
                else:
                    setattr(self, key, val)

    def trace_run(self, cmd: Sequence[str], check: bool = True, **kwargs: Any) -> subprocess.CompletedProcess:
        """
        Run a command, adding it to trace_log
        """
        self.add_trace_log(*cmd)
        return run(cmd, check=check, **kwargs)

    @contextlib.contextmanager
    def operation_plugin(self, config: ContainerConfig) -> Generator[None, None, None]:
        """Build-specific container setup."""
        if not self.quick:
            script = Script("Update container packages before build", cwd=Path("/"), root=True)
            self.distro.get_update_pkgdb_script(script)
            self.distro.get_upgrade_system_script(script)
            config.add_guest_scripts(setup=script)
        yield None

    @guest_only
    def build(self) -> None:
        """
        Run the build.

        The function will be called inside the running system.

        The current directory will be set to the source directory in /srv/moncic-ci/source/<name>.

        Standard output and standard error are logged.
        """
        raise NotImplementedError(f"{self.__class__.__name__}.build is not implemented")

    @classmethod
    def get_name(cls) -> str:
        """
        Get the user-facing name for this Build class
        """
        return cls.__name__.lower()

    @classmethod
    def list_build_classes(cls) -> list[type["Build"]]:
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
    def list_build_options(cls) -> Generator[tuple[str, str]]:
        """
        List available build option names and their documentation
        """
        for f in fields(cls):
            if doc := f.metadata.get("doc"):
                yield f.name, inspect.cleandoc(doc)

    @host_only
    @abc.abstractmethod
    def collect_artifacts(self, script: Script) -> None:
        """
        Look for artifacts created by the build, copy them to ``destdir``, add
        their names to self.artifacts
        """
