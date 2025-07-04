import abc
import contextlib
import inspect
import logging
import os
import shlex
import subprocess
from collections.abc import Generator, Sequence
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import TYPE_CHECKING, Any, override

import yaml

from moncic.container import BindType, Container, ContainerConfig, RunConfig
from moncic.exceptions import Fail
from moncic.runner import UserConfig
from moncic.source.distro import DistroSource
from moncic.utils.link_or_copy import link_or_copy
from moncic.utils.run import run
from moncic.utils.script import Script

from .base import ContainerSourceOperation

if TYPE_CHECKING:
    from moncic.image import RunnableImage

log = logging.getLogger(__name__)


@dataclass
class BuildConfig:
    """Configuration for a build."""

    #: Set to True for faster builds, that assume that the container is already
    #: up to date
    quick: bool = False
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
    def get_name(cls) -> str:
        """
        Get the user-facing name for this Build class
        """
        return cls.__name__.lower().removesuffix("buildconfig")

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

    @classmethod
    def list_build_options(cls) -> Generator[tuple[str, str]]:
        """
        List available build option names and their documentation
        """
        for f in fields(cls):
            if doc := f.metadata.get("doc"):
                yield f.name, inspect.cleandoc(doc)


@dataclass
class BuildResults:
    #: Package name
    name: str | None = None
    #: True if the build was successful
    success: bool = False
    #: List of container paths for artifacts
    artifacts: list[str] = field(default_factory=list)
    #: Commands that can be used to recreate this build
    trace_log: list[str] = field(default_factory=list)
    #: Scripts run to build
    scripts: list[Script] = field(default_factory=list)


class Builder(ContainerSourceOperation, abc.ABC):
    """
    Build a Source using a container
    """

    build_config_class: type[BuildConfig] = BuildConfig

    @classmethod
    def get_builder_class(cls, source: DistroSource) -> type["Builder"]:
        from ..source.debian import DebianDir, DebianDsc, DebianGBP
        from ..source.rpm import ARPASource, RPMSource
        from .build_arpa import ARPABuilder, RPMBuilder
        from .build_debian import DebianBuilderDir, DebianBuilderDsc, DebianBuilderGBP

        match source:
            case DebianDsc():
                return DebianBuilderDsc
            case DebianDir():
                return DebianBuilderDir
            case DebianGBP():
                return DebianBuilderGBP
            case ARPASource():
                return ARPABuilder
            case RPMSource():
                return RPMBuilder
        raise Fail(f"Cannot detect builder class for {source.__class__.__name__} source")

    def __init__(self, source: DistroSource, image: "RunnableImage", config: BuildConfig) -> None:
        super().__init__(image=image, source=source, source_artifacts_dir=config.artifacts_dir)
        #: Build configuration
        self.config = config
        #: Build results
        self.results = BuildResults()
        #: Directory where extra artifacts can be found or stored
        if self.config.artifacts_dir:
            self.plugins.append(self.plugin_build_artifacts)

        self.plugins.append(self.operation_plugin)

    @contextlib.contextmanager
    def plugin_build_artifacts(self, config: ContainerConfig) -> Generator[None]:
        """Collect artifacts produced by the build."""
        assert self.config.artifacts_dir is not None
        artifacts_transfer_path = self.host_root / "artifacts"
        artifacts_transfer_path.mkdir()
        config.add_bind(artifacts_transfer_path, Path("/srv/moncic-ci/artifacts"), BindType.ARTIFACTS)
        config.add_guest_scripts(teardown=self.collect_artifacts_script())
        try:
            yield None
        finally:
            self.harvest_artifacts(artifacts_transfer_path)

    @contextlib.contextmanager
    def operation_plugin(self, config: ContainerConfig) -> Generator[None, None, None]:
        """Build-specific container setup."""
        if not self.config.quick:
            script = Script("Update container packages before build", cwd=Path("/"), user=UserConfig.root())
            self.image.distro.get_update_pkgdb_script(script)
            self.image.distro.get_upgrade_system_script(script)
            self.image.distro.get_prepare_build_script(script)
            config.add_guest_scripts(setup=script)
        yield None

    def add_trace_log(self, *args: str) -> None:
        """
        Add a command to the trace log
        """
        self.results.trace_log.append(shlex.join(args))

    def trace_run(self, cmd: Sequence[str], check: bool = True, **kwargs: Any) -> subprocess.CompletedProcess:
        """
        Run a command, adding it to trace_log
        """
        self.add_trace_log(*cmd)
        return run(cmd, check=check, **kwargs)

    @override
    def log_execution_info(self, container_config: "ContainerConfig") -> None:
        # General builder information
        log.info("Build strategy: %s", self.build.__class__.__name__)
        super().log_execution_info(container_config)

    def build(self, container: Container) -> None:
        """Run the build."""

    @override
    def _after_build(self, container: "Container") -> None:
        """
        Run configured commands after the build ended
        """
        super()._after_build(container)
        if self.results.success:
            for cmd in self.config.on_success:
                self._run_command(container, cmd)
        else:
            for cmd in self.config.on_fail:
                self._run_command(container, cmd)
        for cmd in self.config.on_end:
            self._run_command(container, cmd)

    def _run_command(self, container: "Container", cmd: str) -> None:
        """
        Run a command after a build
        """
        if cmd.startswith("@"):
            if cmd == "@shell":
                run_config = RunConfig()
                run_config.user = UserConfig.root()
                run_config.cwd = Path("/srv/moncic-ci/build")
                container.run_shell(config=run_config)
            elif cmd == "@linger":
                container.linger = True
            else:
                log.error("%r: unsupported post-build command", cmd)
        else:
            env = dict(os.environ)
            env["MONCIC_ARTIFACTS_DIR"] = self.config.artifacts_dir.as_posix() if self.config.artifacts_dir else ""
            env["MONCIC_CONTAINER_NAME"] = container.instance_name
            env["MONCIC_IMAGE"] = self.image.name
            env["MONCIC_CONTAINER_ROOT"] = container.get_root().as_posix()
            env["MONCIC_PACKAGE_NAME"] = self.results.name or ""
            env["MONCIC_RESULT"] = "success" if self.results.success else "fail"
            env["MONCIC_SOURCE"] = self.source.name
            run(["/bin/sh", "-c", cmd], env=env)

    @override
    def run(self, container: Container) -> None:
        self.build(container)

    @override
    def collect_artifacts_script(self) -> Script:
        script = super().collect_artifacts_script()

        # TODO: collect build log (if needed: we are streaming back the output after all)
        # user = UserConfig.from_sudoer()
        # if self.build.name is None:
        #     raise RuntimeError("build name not set")
        # build_log_name = self.build.name + ".buildlog"
        # if (logfile := container.get_root() / "srv" / "moncic-ci" / "buildlog").exists():
        #     self.log_capture_end()
        #     link_or_copy(logfile, destdir, user=user, filename=build_log_name)
        #     log.info("Saving build log to %s/%s", destdir, build_log_name)
        #     self.build.artifacts.append(build_log_name)

        return script

    def harvest_artifacts(self, transfer_dir: Path) -> None:
        """Move artifacts from the transfer directory to their final destination."""
        assert self.config.artifacts_dir is not None
        for path in transfer_dir.iterdir():
            if not path.is_file():
                continue
            # TODO: this can be a move instead
            link_or_copy(path, self.config.artifacts_dir, filename=path.name)
            self.results.artifacts.append(path.name)

    @classmethod
    def get_name(cls) -> str:
        """
        Get the user-facing name for this Build class
        """
        return cls.__name__.lower().removesuffix("builder")

    @classmethod
    def list_build_classes(cls) -> list[type["Builder"]]:
        """
        Return a list of all available build classes, including intermediate
        classes in class hierarchies
        """
        from .build_arpa import ARPABuilder, RPMBuilder
        from .build_debian import DebianBuilder

        return [
            DebianBuilder,
            RPMBuilder,
            ARPABuilder,
        ]
