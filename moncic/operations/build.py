from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..runner import UserConfig
from ..utils.guest import guest_only, host_only
from ..utils.run import run
from . import build
from ..build.utils import link_or_copy
from ..build.build import Build
from .base import ContainerSourceOperation


if TYPE_CHECKING:
    from ..container import Container, System

log = logging.getLogger(__name__)


class Builder(ContainerSourceOperation):
    """
    Build a Source using a container
    """

    def __init__(self, system: System, build: Build):
        super().__init__(system=system, source=build.source, artifacts_dir=build.artifacts_dir)
        # Build object that is being built
        self.build: Build = build

    @host_only
    def setup_container_host(self, container: Container):
        super().setup_container_host(container)
        self.build.setup_container_host(container)

    @host_only
    def log_execution_info(self, container: Container) -> None:
        # General builder information
        log.info("Build strategy: %s", self.build.__class__.__name__)
        super().log_execution_info(container)

    @host_only
    def process_guest_result(self, result: Any) -> None:
        assert isinstance(result, Build)
        self.build = result

    @host_only
    def _after_build(self, container: Container):
        """
        Run configured commands after the build ended
        """
        super()._after_build(container)
        if self.build.success:
            for cmd in self.build.on_success:
                self._run_command(container, cmd)
        else:
            for cmd in self.build.on_fail:
                self._run_command(container, cmd)
        for cmd in self.build.on_end:
            self._run_command(container, cmd)

    @host_only
    def _run_command(self, container: Container, cmd: str):
        """
        Run a command after a build
        """
        if cmd.startswith("@"):
            if cmd == "@shell":
                run_config = container.config.run_config()
                run_config.interactive = True
                run_config.check = False
                run_config.user = UserConfig.root()
                run_config.cwd = "/srv/moncic-ci/build"
                container.run_shell(config=run_config)
            elif cmd == "@linger":
                container.linger = True
            else:
                log.error("%r: unsupported post-build command", cmd)
        else:
            env = dict(os.environ)
            env["MONCIC_ARTIFACTS_DIR"] = self.build.artifacts_dir.as_posix() if self.build.artifacts_dir else ""
            env["MONCIC_CONTAINER_NAME"] = container.instance_name
            env["MONCIC_IMAGE"] = self.system.config.name
            env["MONCIC_CONTAINER_ROOT"] = container.get_root().as_posix()
            env["MONCIC_PACKAGE_NAME"] = self.build.name or ""
            env["MONCIC_RESULT"] = "success" if self.build.success else "fail"
            env["MONCIC_SOURCE"] = self.build.source.name
            run(["/bin/sh", "-c", cmd], env=env)

    @guest_only
    def guest_main(self) -> build.Build:
        """
        Run the build
        """
        self.build.source = self.get_guest_source()
        self.build.setup_container_guest(self.system)
        self.build.build()
        return self.build

    @host_only
    def collect_artifacts(self, container: Container, destdir: Path):
        """
        Copy build artifacts to the given directory
        """
        super().collect_artifacts(container, destdir)

        # Do nothing by default
        self.build.collect_artifacts(container, destdir)

        user = UserConfig.from_sudoer()
        if self.build.name is None:
            raise RuntimeError("build name not set")
        build_log_name = self.build.name + ".buildlog"
        if (logfile := container.get_root() / "srv" / "moncic-ci" / "buildlog").exists():
            self.log_capture_end()
            link_or_copy(logfile, destdir, user=user, filename=build_log_name)
            log.info("Saving build log to %s/%s", destdir, build_log_name)
            self.build.artifacts.append(build_log_name)
