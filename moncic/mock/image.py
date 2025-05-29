import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, override

from moncic.container import Container, ContainerConfig, MaintenanceContainer
from moncic.distro import Distro
from moncic.image import BootstrappableImage, ImageType, RunnableImage

if TYPE_CHECKING:
    from .images import MockImages
    from .session import MockSession


class MockImage(RunnableImage):
    session: "MockSession"
    images: "MockImages"

    def __init__(self, *, images: "MockImages", name: str, distro: Distro) -> None:
        super().__init__(images=images, image_type=ImageType.MOCK, name=name, distro=distro)

    @override
    def get_backend_id(self) -> str:
        return "mock"

    @override
    def remove(self) -> BootstrappableImage | None:
        self.session.run_log.append_action(f"{self.name}: remove")
        return self.bootstrap_from

    @override
    def container(self, *, instance_name: str | None = None, config: ContainerConfig | None = None) -> Container:
        from .container import MockContainer

        config = config or ContainerConfig()

        # Allow distro-specific setup
        self.distro.container_config_hook(self, config)

        return MockContainer(self, config=config)

    @override
    def maintenance_container(
        self, *, instance_name: str | None = None, config: ContainerConfig | None = None
    ) -> MaintenanceContainer:
        from .container import MockMaintenanceContainer

        config = config or ContainerConfig()

        # Allow distro-specific setup
        self.distro.container_config_hook(self, config)

        return MockMaintenanceContainer(self, config=config)

    @override
    def host_run(
        self, cmd: list[str], check: bool = True, cwd: Path | None = None, interactive: bool = False
    ) -> subprocess.CompletedProcess:
        self.session.run_log.append(cmd, {})
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    # @override
    # def remove_config(self) -> None:
    #     raise NotImplementedError()
