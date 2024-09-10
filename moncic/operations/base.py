from __future__ import annotations

import abc
import contextlib
import dataclasses
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import IO, TYPE_CHECKING, cast, Any

from ..container import ContainerConfig
from ..runner import UserConfig
from ..utils.guest import guest_only, host_only
from . import build
from ..source.distro import DistroSource

if TYPE_CHECKING:
    from ..container import Container, System

log = logging.getLogger(__name__)


class ContainerSourceOperation(abc.ABC):
    """
    Base class for operations on sources performed inside a container
    """

    def __init__(self, system: System, source: DistroSource, artifacts_dir: Path | None = None):
        #: System used for container operations
        self.system = system
        #: Source to work on
        self.source = source
        #: Directory where extra artifacts can be found or stored
        self.artifacts_dir = artifacts_dir
        # User to use for the build
        self.user = UserConfig.from_sudoer()
        # Build log file
        self.log_file: IO[str] | None = None
        # Log handler used to capture build output
        self.log_handler: logging.Handler | None = None
        #: Path inside the guest system where sources can be found
        self.guest_source_path: Path | None = None

    @host_only
    def log_capture_start(self, log_file: Path):
        self.log_file = log_file.open("wt")
        self.log_handler = logging.StreamHandler(self.log_file)
        self.log_handler.setLevel(logging.DEBUG)
        self.log_handler.setFormatter(logging.Formatter("%(asctime)-19.19s %(levelname)s %(message)s"))
        logging.getLogger().addHandler(self.log_handler)
        logging.getLogger().setLevel(logging.DEBUG)

    @host_only
    def log_capture_end(self):
        if self.log_handler is not None:
            logging.getLogger().removeHandler(self.log_handler)
            self.log_handler = None
            self.log_file.close()
            self.log_file = None

    @host_only
    def setup_container_host(self, container: Container):
        """
        Set up the container before starting the build.

        This is run on the host system before starting the build
        """
        container_root = container.get_root()

        # Set user permissions on source and build directories
        srcdir = container_root / "srv" / "moncic-ci" / "source"
        os.chown(srcdir, self.user.user_id, self.user.group_id)
        builddir = container_root / "srv" / "moncic-ci" / "build"
        builddir.mkdir(parents=True, exist_ok=True)
        os.chown(builddir, self.user.user_id, self.user.group_id)

        # Capture build log
        log_file = container_root / "srv" / "moncic-ci" / "buildlog"
        self.log_capture_start(log_file)

        # Collect artifacts in a temporary directory, to be able to do it as non-root
        privs = self.system.images.session.moncic.privs
        with privs.user():
            with tempfile.TemporaryDirectory() as tmpdir_str:
                tmpdir = Path(tmpdir_str)
                self.source.collect_build_artifacts(tmpdir, self.artifacts_dir)
                # Regain privileges and move results to the container
                privs.regain()
                for path in tmpdir.iterdir():
                    shutil.move(path, srcdir)

        log.debug("Sources in: %s:", srcdir)
        for path in srcdir.iterdir():
            log.debug("* %s", path.name)

    @host_only
    def log_execution_info(self, container: Container) -> None:
        """
        Log all available information about how the task is executed in the container
        """
        # Log moncic config
        moncic_config = self.system.images.session.moncic.config
        for fld in dataclasses.fields(moncic_config):
            log.debug("moncic:%s = %r", fld.name, getattr(moncic_config, fld.name))
        # Log container config
        for fld in dataclasses.fields(container.config):
            log.debug("container:%s = %r", fld.name, getattr(container.config, fld.name))

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
        mountpoint = Path("/srv/moncic-ci/source")
        self.guest_source_path = mountpoint / self.source.path.name
        container_config.configure_workdir(self.source.path.as_posix(), bind_type="volatile", mountpoint=mountpoint)
        container = self.system.create_container(config=container_config)
        with container:
            self.setup_container_host(container)
            try:
                yield container
            finally:
                self.log_capture_end()

    @host_only
    def process_guest_result(self, result: Any) -> None:
        """
        Handle the result value of the main callable run on the guest system
        """
        # Do nothing by default
        pass

    @host_only
    def collect_artifacts(self, container: Container, destdir: Path):
        """
        Collect artifacts from the guest filesystem before it is shut down
        """
        # Do nothing by default
        pass

    @host_only
    def _after_build(self, container: Container):
        """
        Hook to run commands on the container after the main operation ended
        """
        # Do nothing by default
        pass

    @guest_only
    def get_guest_source(self) -> DistroSource:
        """
        Return self.source pointing to its location inside the guest system
        """
        assert self.guest_source_path
        # TODO: remove cast from python 3.11
        return cast(DistroSource, self.source.in_path(self.guest_source_path))

    @host_only
    def host_main(self) -> Any:
        """
        Run the build, store the artifacts in the given directory if requested,
        return the returncode of the build process
        """
        with self.container() as container:
            self.log_execution_info(container)

            # Build run config
            run_config = container.config.run_config()
            run_config.user = UserConfig.root()
            # Log run config
            for fld in dataclasses.fields(run_config):
                log.debug("run:%s = %r", fld.name, getattr(run_config, fld.name))

            try:
                result = container.run_callable(self.guest_main, run_config)
                self.process_guest_result(result)
                if self.artifacts_dir:
                    self.collect_artifacts(container, self.artifacts_dir)
            finally:
                self._after_build(container)

        return result

    @abc.abstractmethod
    def guest_main(self):
        """
        Function run on the guest system to perform the operation
        """
        ...
