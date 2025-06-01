from __future__ import annotations

import abc
import contextlib
import dataclasses
import logging
import tempfile
from collections.abc import Callable, Generator
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, ContextManager

from moncic.utils.link_or_copy import link_or_copy
from moncic.container import BindType, ContainerConfig
from moncic.runner import UserConfig
from moncic.source.distro import DistroSource
from moncic.utils.guest import guest_only, host_only
from moncic.utils.script import Script

if TYPE_CHECKING:
    from moncic.container import Container
    from moncic.image import RunnableImage

log = logging.getLogger(__name__)


class ContainerSourceOperation(abc.ABC):
    """
    Base class for operations on sources performed inside a container.

    This introduces well-known elements in the container filesystem layout:

    * /srv/moncic-ci/source-artifacts (readonly): source artifacts are made
      available here, and copied to /srv/moncic-ci/source/ at startup
    * /srv/moncic-ci/artifacts (writable): files found here at the end of the
      build will be consider artifacts to be preserved
    * /srv/moncic-ci/source/{name} (volatile): sources are mounted here
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
        #: Path in the container used as the root of our operation-specific filesystem tree
        self.operation_root = Path("/srv/moncic-ci/")
        #: Path inside the guest system where sources are mounted
        self.guest_source_path = self.operation_root / "source" / self.source.path.name
        #: Modular units of functionality activated on this operation
        self.plugins: list[Callable[[ContainerConfig], ContextManager[None]]] = [
            self.plugin_container_filesystem,
            self.plugin_mount_source,
            self.plugin_source_artifacts,
        ]
        if self.artifacts_dir:
            self.plugins.append(self.plugin_build_artifacts)

    @contextlib.contextmanager
    def plugin_container_filesystem(self, config: ContainerConfig) -> Generator[None]:
        """Set up the container before starting the build."""
        script = Script("Set up the container filesystem", cwd=Path("/"), root=True)
        script.run(["mkdir", "-p", "/srv/moncic-ci/source"])
        script.run(["chown", str(self.user.user_id), "/srv/moncic-ci/source"])
        script.run(["mkdir", "-p", "/srv/moncic-ci/build"])
        script.run(["chown", str(self.user.user_id), "/srv/moncic-ci/build"])
        config.add_guest_scripts(setup=script)
        yield

        # Capture build log
        # TODO
        # log_file = container_root / "srv" / "moncic-ci" / "buildlog"
        # self.log_capture_start(log_file)

    @contextlib.contextmanager
    def plugin_mount_source(self, config: ContainerConfig) -> Generator[None]:
        """Mount the source path inside the container."""
        # Mount the source path as /srv/moncic-ci/source/<name>
        # Set it as the default current directory in the container
        # Mounted volatile to prevent changes to it
        config.configure_workdir(
            self.source.path, bind_type=BindType.VOLATILE, mountpoint=self.guest_source_path.parent
        )
        yield

    @contextlib.contextmanager
    def plugin_source_artifacts(self, config: ContainerConfig) -> Generator[None]:
        """Collect source artifacts and make them available in the container."""
        with tempfile.TemporaryDirectory(dir=self.artifacts_dir) as source_artifacts_dir_str:
            source_artifacts_dir = Path(source_artifacts_dir_str)
            self.source.collect_build_artifacts(source_artifacts_dir, self.artifacts_dir)

            has_source_artifacts = False
            for path in source_artifacts_dir.iterdir():
                log.info("Found source artifact: %s", path)
                has_source_artifacts = True

            if not has_source_artifacts:
                yield None
                return

            guest_source_artifacts_dir = Path("/srv/moncic-ci/source-artifacts")
            config.add_bind(source_artifacts_dir, guest_source_artifacts_dir, BindType.READONLY)

            log.debug("Sources in: %s:", guest_source_artifacts_dir)
            for path in source_artifacts_dir.iterdir():
                log.debug("* %s", path.name)

            # In build, run a script to acquire source artifacts into /srv/moncic-ci/source
            copy_script = Script("Copy artifacts to build location")
            copy_script.run_unquoted("cp -r --reflink=auto /srv/moncic-ci/source-artifacts/* /srv/moncic-ci/source/")
            config.add_guest_scripts(setup=copy_script)

            yield None

    @contextlib.contextmanager
    def plugin_build_artifacts(self, config: ContainerConfig) -> Generator[None]:
        """Collect artifacts produced by the build."""
        assert self.artifacts_dir is not None
        with tempfile.TemporaryDirectory(dir=self.artifacts_dir) as artifacts_transfer_path_str:
            artifacts_transfer_path = Path(artifacts_transfer_path_str)
            config.add_bind(artifacts_transfer_path, Path("/srv/moncic-ci/artifacts"), BindType.ARTIFACTS)
            config.add_guest_scripts(teardown=self.collect_artifacts_script())
            try:
                yield None
            finally:
                self.harvest_artifacts(artifacts_transfer_path)

    @host_only
    @contextlib.contextmanager
    def container_config(self) -> Generator[ContainerConfig]:
        """Build the container configuration for this operation."""
        config = ContainerConfig()
        with contextlib.ExitStack() as stack:
            for plugin in self.plugins:
                log.info("Build plugin: %s", (plugin.__doc__ or plugin.__name__).strip())
                stack.enter_context(plugin(config))
            yield config

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
    def log_execution_info(self, container_config: ContainerConfig) -> None:
        """
        Log all available information about how the task is executed in the container
        """
        # Log moncic config
        moncic_config = self.image.session.moncic.config
        for key, value in moncic_config.dict().items():
            log.debug("moncic:%s = %r", key, value)
        # Log container config
        container_config.log_debug(log)

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
        with contextlib.ExitStack() as stack:
            config = stack.enter_context(self.container_config())
            self.log_execution_info(config)
            with self.image.container(config=config) as container:
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
            # Build run config
            run_config = container.config.run_config()
            run_config.user = UserConfig.root()
            # Log run config
            for fld in dataclasses.fields(run_config):
                log.debug("run:%s = %r", fld.name, getattr(run_config, fld.name))

            try:
                result = container.run_callable(self.guest_main, run_config)
                self.process_guest_result(result)
            finally:
                self._after_build(container)

        return result

    @abc.abstractmethod
    def guest_main(self) -> Any:
        """
        Function run on the guest system to perform the operation
        """
        ...
