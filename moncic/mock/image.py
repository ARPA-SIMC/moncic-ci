import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, override

from moncic.container import Container, ContainerConfig
from moncic.distro import Distro
from moncic.image import BootstrappableImage, ImageType, RunnableImage

if TYPE_CHECKING:
    from .session import MockSession


class MockImage(RunnableImage):
    session: "MockSession"

    def __init__(self, *, session: "MockSession", name: str, distro: Distro) -> None:
        super().__init__(session=session, image_type=ImageType.NSPAWN, name=name, distro=distro)

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

        return MockContainer(self, config=config)

    @override
    def maintenance_container(
        self, *, instance_name: str | None = None, config: ContainerConfig | None = None
    ) -> Container:
        from .container import MockContainer

        return MockContainer(self, config=config)

    def host_run(
        self, cmd: list[str], check: bool = True, cwd: Path | None = None, interactive: bool = False
    ) -> subprocess.CompletedProcess:
        self.session.run_log.append(cmd, {})
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    # @override
    # def remove_config(self) -> None:
    #     raise NotImplementedError()
