from __future__ import annotations

import contextlib
import dataclasses
import logging
import os
from typing import IO, TYPE_CHECKING, Optional

from ..container import ContainerConfig
from ..runner import UserConfig
from ..utils.guest import guest_only, host_only
from ..utils.run import run
from . import build
from .utils import link_or_copy

if TYPE_CHECKING:
    from ..container import Container, System
    from .build import Build

log = logging.getLogger(__name__)


class Builder(contextlib.ExitStack):
    """
    Interface for classes providing the logic for CI builds
    """
    def __init__(self, system: System, build: Build):
        super().__init__()
        # System used for the build
        self.system = system
        # Build object that is being built
        self.build: Build = build
        # User to use for the build
        self.user = UserConfig.from_sudoer()
        # Build log file
        self.buildlog_file: Optional[IO[str]] = None
        # Log handler used to capture build output
        self.buildlog_handler: Optional[logging.Handler] = None

    @host_only
    def setup_container_host(self, container: Container):
        """
        Set up the container before starting the build.

        This is run on the host system before starting the build
        """
        container_root = container.get_root()

        # Set user permissions on source and build directories
        srcdir = os.path.join(container_root, "srv", "moncic-ci", "source")
        os.chown(srcdir, self.user.user_id, self.user.group_id)
        builddir = os.path.join(container_root, "srv", "moncic-ci", "build")
        os.makedirs(builddir, exist_ok=True)
        os.chown(builddir, self.user.user_id, self.user.group_id)

        # Capture build log
        log_file = os.path.join(container_root, "srv", "moncic-ci", "buildlog")
        self.log_capture_start(log_file)

        self.build.source.gather_sources_from_host(self.build, container)
        self.build.setup_container_host(container)

    @host_only
    def log_capture_start(self, log_file: str):
        self.buildlog_file = open(log_file, "wt")
        self.buildlog_handler = logging.StreamHandler(self.buildlog_file)
        self.buildlog_handler.setLevel(logging.DEBUG)
        self.buildlog_handler.setFormatter(
                logging.Formatter("%(asctime)-19.19s %(levelname)s %(message)s"))
        logging.getLogger().addHandler(self.buildlog_handler)
        logging.getLogger().setLevel(logging.DEBUG)

    @host_only
    def log_capture_end(self):
        if self.buildlog_handler is not None:
            logging.getLogger().removeHandler(self.buildlog_handler)
            self.buildlog_handler = None
            self.buildlog_file.close()
            self.buildlog_file = None

    @host_only
    def get_build_deps(self) -> list[str]:
        """
        Return a list of packages to be installed as build-depedencies to build
        this source
        """
        raise NotImplementedError(f"{self.__class__.__name__}.get_build_deps is not implemented")

    @host_only
    @contextlib.contextmanager
    def container(self):
        """
        Start a container to run CI operations
        """
        container_config = ContainerConfig()
        # Mount the source directory as /srv/moncic-ci/source/<name>
        # Set it as the default current directory in the container
        # Mounted volatile to prevent changes to it
        mountpoint = "/srv/moncic-ci/source"
        self.build.source.guest_path = os.path.join(mountpoint, self.build.source.host_path.name)
        container_config.configure_workdir(
                self.build.source.host_path.as_posix(), bind_type="volatile", mountpoint=mountpoint)
        container = self.system.create_container(config=container_config)
        with container:
            self.setup_container_host(container)
            try:
                yield container
            finally:
                self.log_capture_end()

    @host_only
    def run_build(self) -> None:
        """
        Run the build, store the artifacts in the given directory if requested,
        return the returncode of the build process
        """
        artifacts_dir = self.build.artifacts_dir
        with self.container() as container:
            # General builder information
            log.info("Build strategy: %s", self.build.__class__.__name__)
            # Log moncic config
            moncic_config = self.system.images.session.moncic.config
            for fld in dataclasses.fields(moncic_config):
                log.debug("moncic:%s = %r", fld.name, getattr(moncic_config, fld.name))
            # Log container config
            for fld in dataclasses.fields(container.config):
                log.debug("container:%s = %r", fld.name, getattr(container.config, fld.name))

            # Build run config
            run_config = container.config.run_config()
            run_config.user = UserConfig.root()
            # Log run config
            for fld in dataclasses.fields(run_config):
                log.debug("run:%s = %r", fld.name, getattr(run_config, fld.name))

            try:
                self.build = container.run_callable(
                        self.build_in_container,
                        run_config)
                if artifacts_dir:
                    self.collect_artifacts(container, artifacts_dir)
            finally:
                self._after_build(container)

    @host_only
    def _after_build(self, container: Container):
        """
        Run configured commands after the build ended
        """
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
            env["MONCIC_ARTIFACTS_DIR"] = self.build.artifacts_dir or ""
            env["MONCIC_CONTAINER_NAME"] = container.instance_name
            env["MONCIC_IMAGE"] = self.system.config.name
            env["MONCIC_CONTAINER_ROOT"] = container.get_root()
            env["MONCIC_PACKAGE_NAME"] = self.build.name or ""
            env["MONCIC_RESULT"] = "success" if self.build.success else "fail"
            env["MONCIC_SOURCE"] = self.build.source.source.source
            run(["/bin/sh", "-c", cmd], env=env)

    @guest_only
    def build_in_container(self) -> build.Build:
        """
        Run the build
        """
        self.build.setup_container_guest(self.system)
        self.build.build()
        return self.build

    @host_only
    def collect_artifacts(self, container: Container, destdir: str):
        """
        Copy build artifacts to the given directory
        """
        # Do nothing by default
        self.build.collect_artifacts(container, destdir)

        user = UserConfig.from_sudoer()
        if self.build.name is None:
            raise RuntimeError("build name not set")
        build_log_name = self.build.name + ".buildlog"
        if os.path.exists(logfile := os.path.join(container.get_root(), "srv", "moncic-ci", "buildlog")):
            self.log_capture_end()
            link_or_copy(
                    logfile, destdir, user=user,
                    filename=build_log_name)
            log.info("Saving build log to %s/%s", destdir, build_log_name)
            self.build.artifacts.append(build_log_name)
