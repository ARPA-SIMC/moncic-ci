from __future__ import annotations

import abc
import contextlib
import logging
import tempfile
from collections.abc import Callable, Generator
from pathlib import Path
from typing import IO, TYPE_CHECKING, ContextManager

from moncic.container import BindType, ContainerConfig
from moncic.runner import UserConfig
from moncic.source.distro import DistroSource
from moncic.utils.script import Script

if TYPE_CHECKING:
    from moncic.container import Container
    from moncic.image import RunnableImage

log = logging.getLogger(__name__)


class ContainerSourceOperation(contextlib.ExitStack, abc.ABC):
    """
    Base class for operations on sources performed inside a container.

    This introduces well-known elements in the container filesystem layout:

    * /srv/moncic-ci/source-artifacts (readonly): source artifacts are made
      available here, and copied to /srv/moncic-ci/source/ at startup
    * /srv/moncic-ci/artifacts (writable): files found here at the end of the
      build will be consider artifacts to be preserved
    * /srv/moncic-ci/source/{name} (volatile): sources are mounted here
    """

    def __init__(self, image: RunnableImage, source: DistroSource, *, source_artifacts_dir: Path | None = None) -> None:
        super().__init__()
        #: Image used for container operations
        self.image = image
        #: Source to work on
        self.source = source
        #: User to use for the build
        self.user = UserConfig.from_sudoer()
        #: Optional extra directory used to look for source artifacts
        self.source_artifacts_dir = source_artifacts_dir
        #: Build log file
        self.log_file: IO[str] | None = None
        #: Log handler used to capture build output
        self.log_handler: logging.Handler | None = None
        #: Host path used as working area
        self.host_root = Path(self.enter_context(tempfile.TemporaryDirectory()))
        #: Path in the container used as the root of our operation-specific filesystem tree
        self.guest_root = Path("/srv/moncic-ci/")
        #: Host path of sources to build
        self.host_source_path = self.source.path
        #: Path inside the guest system where sources are mounted
        self.guest_source_path = self.guest_root / "source" / self.source.path.name
        #: Modular units of functionality activated on this operation
        self.plugins: list[Callable[[ContainerConfig], ContextManager[None]]] = [
            self.plugin_mount_source,
            self.plugin_container_filesystem,
            self.plugin_source_artifacts,
        ]

    @contextlib.contextmanager
    def plugin_mount_source(self, config: ContainerConfig) -> Generator[None]:
        """Mount the source path inside the container."""
        # Mount the source path as /srv/moncic-ci/source/<name>
        # Set it as the default current directory in the container
        config.configure_workdir(
            self.host_source_path, bind_type=BindType.VOLATILE, mountpoint=self.guest_source_path.parent
        )
        yield

    @contextlib.contextmanager
    def plugin_container_filesystem(self, config: ContainerConfig) -> Generator[None]:
        """Set up the container before starting the build."""
        script = Script("Set up the container filesystem", cwd=Path("/"), user=UserConfig.root())
        script.run(
            ["chown", str(self.user.user_id), "/srv/moncic-ci/source"],
            description="Make source directory user-writable",
        )
        script.run(
            ["/usr/bin/install", "-d", "-o", str(self.user.user_id), "/srv/moncic-ci/build"],
            description="Create directory where the build is run",
        )
        config.add_guest_scripts(setup=script)
        yield

        # Capture build log
        # TODO
        # log_file = container_root / "srv" / "moncic-ci" / "buildlog"
        # self.log_capture_start(log_file)

    @contextlib.contextmanager
    def plugin_source_artifacts(self, config: ContainerConfig) -> Generator[None]:
        """Collect source artifacts and make them available in the container."""
        source_artifacts_dir = self.host_root / "source-artifacts"
        source_artifacts_dir.mkdir()
        self.source.collect_build_artifacts(source_artifacts_dir, self.source_artifacts_dir)

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
    def container_config(self) -> Generator[ContainerConfig]:
        """Build the container configuration for this operation."""
        config = ContainerConfig()
        with contextlib.ExitStack() as stack:
            for plugin in self.plugins:
                log.info("Build plugin: %s", (plugin.__doc__ or plugin.__name__).strip())
                stack.enter_context(plugin(config))
            yield config

    def log_capture_start(self, log_file: Path) -> None:
        self.log_file = log_file.open("wt")
        self.log_handler = logging.StreamHandler(self.log_file)
        self.log_handler.setLevel(logging.DEBUG)
        self.log_handler.setFormatter(logging.Formatter("%(asctime)-19.19s %(levelname)s %(message)s"))
        logging.getLogger().addHandler(self.log_handler)
        logging.getLogger().setLevel(logging.DEBUG)

    def log_capture_end(self) -> None:
        if self.log_handler is not None:
            logging.getLogger().removeHandler(self.log_handler)
            self.log_handler = None
            self.log_file.close()
            self.log_file = None

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

    def collect_artifacts_script(self) -> Script:
        """
        Collect artifacts from the guest filesystem before it is shut down
        """
        return Script(title="Collect artifacts produced inside the container", user=UserConfig.root())

    def _after_build(self, container: Container) -> None:
        """
        Hook to run commands on the container after the main operation ended
        """
        # Do nothing by default

    def host_main(self) -> None:
        """
        Run the build, store the artifacts in the given directory if requested,
        return the returncode of the build process
        """
        with self.container() as container:
            try:
                self.run(container)
            finally:
                self._after_build(container)

    @abc.abstractmethod
    def run(self, container: Container) -> None:
        """Run the operation in the container."""
