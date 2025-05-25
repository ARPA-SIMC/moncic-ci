from __future__ import annotations

import abc
import contextlib
import dataclasses
import logging
import os
import shutil
import tempfile
from collections.abc import Generator
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

from moncic import context
from moncic.container import ContainerConfig, BindType
from moncic.build.utils import link_or_copy
from moncic.utils.script import Script
from moncic.runner import UserConfig
from moncic.source.distro import DistroSource
from moncic.utils.guest import guest_only, host_only

if TYPE_CHECKING:
    from moncic.image import RunnableImage
    from moncic.container import Container

log = logging.getLogger(__name__)


class ContainerSourceOperation(abc.ABC):
    """
    Base class for operations on sources performed inside a container
    """

    def __init__(self, image: RunnableImage, source: DistroSource, *, artifacts_dir: Path | None = None) -> None:
        #: Image used for container operations
        self.image = image
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
    def get_container_config(self) -> ContainerConfig:
        """Build the container configuration for this operation."""
        config = ContainerConfig()
        # Mount the source directory as /srv/moncic-ci/source/<name>
        # Set it as the default current directory in the container
        # Mounted volatile to prevent changes to it
        mountpoint = Path("/srv/moncic-ci/source")
        self.guest_source_path = mountpoint / self.source.path.name
        config.configure_workdir(self.source.path, bind_type=BindType.VOLATILE, mountpoint=mountpoint)
        return config

    @host_only
    def log_capture_start(self, log_file: Path) -> None:
        self.log_file = log_file.open("wt")
        self.log_handler = logging.StreamHandler(self.log_file)
        self.log_handler.setLevel(logging.DEBUG)
        self.log_handler.setFormatter(logging.Formatter("%(asctime)-19.19s %(levelname)s %(message)s"))
        logging.getLogger().addHandler(self.log_handler)
        logging.getLogger().setLevel(logging.DEBUG)

    @host_only
    def log_capture_end(self) -> None:
        if self.log_handler is not None:
            logging.getLogger().removeHandler(self.log_handler)
            self.log_handler = None
            self.log_file.close()
            self.log_file = None

    @host_only
    def setup_container_host(self, container: Container) -> None:
        """
        Set up the container before starting the build.

        This is run on the host system before starting the build
        """
        container_root = container.get_root()
        with context.privs.root():
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
            with tempfile.TemporaryDirectory() as tmpdir_str:
                tmpdir = Path(tmpdir_str)
                self.source.collect_build_artifacts(tmpdir, self.artifacts_dir)
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
        moncic_config = self.image.session.moncic.config
        for key, value in moncic_config.dict().items():
            log.debug("moncic:%s = %r", key, value)
        # Log container config
        container.config.log_debug(log)

    @host_only
    @contextlib.contextmanager
    def _container_collect_artifacts(self, config: ContainerConfig) -> Generator[None]:
        """Wrap operation execution to handle collecting artifacts produced inside the container."""
        assert self.artifacts_dir is not None
        with tempfile.TemporaryDirectory(dir=self.artifacts_dir) as artifacts_transfer_path_str:
            artifacts_transfer_path = Path(artifacts_transfer_path_str)
            config.add_bind(artifacts_transfer_path, Path("/srv/moncic-ci/artifacts"), BindType.ARTIFACTS)
            try:
                yield None
            finally:
                self.harvest_artifacts(artifacts_transfer_path)

    @host_only
    def harvest_artifacts(self, transfer_dir: Path) -> None:
        """Move artifacts from the transfer directory to their final destination."""
        assert self.artifacts_dir is not None
        for path in transfer_dir.iterdir():
            if not path.is_file():
                continue
            # TODO: this can be a move instead
            link_or_copy(path, self.artifacts_dir, filename=path.name)

    @host_only
    @contextlib.contextmanager
    def container(self) -> Generator[Container]:
        """
        Start a container to run CI operations
        """
        config = self.get_container_config()
        with contextlib.ExitStack() as stack:
            if self.artifacts_dir:
                stack.enter_context(self._container_collect_artifacts(config))

            with self.image.container(config=config) as container:
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

    @host_only
    def collect_artifacts_script(self) -> Script:
        """
        Collect artifacts from the guest filesystem before it is shut down
        """
        return Script(title="Collect artifacts produced inside the container")

    @host_only
    def _after_build(self, container: Container) -> None:
        """
        Hook to run commands on the container after the main operation ended
        """
        # Do nothing by default

    @guest_only
    def get_guest_source(self) -> DistroSource:
        """
        Return self.source pointing to its location inside the guest system
        """
        assert self.guest_source_path
        return self.source.in_path(self.guest_source_path)

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
                    script = self.collect_artifacts_script()
                container.run_script(script)
            finally:
                self._after_build(container)

        return result

    @abc.abstractmethod
    def guest_main(self) -> Any:
        """
        Function run on the guest system to perform the operation
        """
        ...
