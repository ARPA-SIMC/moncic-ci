import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, override

from moncic.build.build import Build
from moncic.container import Container, ContainerConfig
from moncic.runner import UserConfig
from moncic.utils.guest import guest_only, host_only
from moncic.utils.run import run
from moncic.utils.script import Script

from .base import ContainerSourceOperation

if TYPE_CHECKING:
    from moncic.image import RunnableImage

log = logging.getLogger(__name__)


class Builder(ContainerSourceOperation):
    """
    Build a Source using a container
    """

    def __init__(self, image: "RunnableImage", build: Build) -> None:
        super().__init__(image=image, source=build.source, artifacts_dir=build.artifacts_dir)
        # Build object that is being built
        self.build: Build = build
        self.plugins.append(self.build.operation_plugin)

    @override
    @host_only
    def log_execution_info(self, container_config: "ContainerConfig") -> None:
        # General builder information
        log.info("Build strategy: %s", self.build.__class__.__name__)
        super().log_execution_info(container_config)

    @override
    @host_only
    def process_guest_result(self, result: Any) -> None:
        assert isinstance(result, Build)
        self.build = result

    @override
    @host_only
    def _after_build(self, container: "Container") -> None:
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
    def _run_command(self, container: "Container", cmd: str) -> None:
        """
        Run a command after a build
        """
        if cmd.startswith("@"):
            if cmd == "@shell":
                run_config = container.config.run_config()
                run_config.interactive = True
                run_config.check = False
                run_config.user = UserConfig.root()
                run_config.cwd = Path("/srv/moncic-ci/build")
                container.run_shell(config=run_config)
            elif cmd == "@linger":
                container.linger = True
            else:
                log.error("%r: unsupported post-build command", cmd)
        else:
            env = dict(os.environ)
            env["MONCIC_ARTIFACTS_DIR"] = self.build.artifacts_dir.as_posix() if self.build.artifacts_dir else ""
            env["MONCIC_CONTAINER_NAME"] = container.instance_name
            env["MONCIC_IMAGE"] = self.image.name
            env["MONCIC_CONTAINER_ROOT"] = container.get_root().as_posix()
            env["MONCIC_PACKAGE_NAME"] = self.build.name or ""
            env["MONCIC_RESULT"] = "success" if self.build.success else "fail"
            env["MONCIC_SOURCE"] = self.build.source.name
            run(["/bin/sh", "-c", cmd], env=env)

    @override
    @guest_only
    def guest_main(self) -> Build:
        """
        Run the build
        """
        self.build.source = self.get_guest_source()
        self.build.build()
        return self.build

    @override
    @host_only
    def collect_artifacts_script(self) -> Script:
        script = super().collect_artifacts_script()

        # Do nothing by default
        self.build.collect_artifacts(script)

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

    @override
    @host_only
    def harvest_artifacts(self, transfer_dir: Path) -> None:
        for path in transfer_dir.iterdir():
            self.build.artifacts.append(path.name)
        super().harvest_artifacts(transfer_dir)
