import contextlib
import subprocess
from typing import TYPE_CHECKING, override
from collections.abc import Generator

from moncic.nspawn.image import NspawnImage

if TYPE_CHECKING:
    from .system import MockMaintenanceSystem, MockSystem


class MockImage(NspawnImage):
    def local_run(self, cmd: list[str]) -> subprocess.CompletedProcess:
        self.images.session.mock_log(system=self.name, cmd=cmd)
        return self.images.session.get_process_result(args=cmd)

    @override
    def bootstrap(self) -> None:
        if self.path.exists():
            return

        log.info("%s: bootstrapping directory", self.name)

        orig_path = self.path
        work_path = self.path.parent / f"{self.path.name}.new"
        try:
            self.path = work_path
            try:
                if self.extends is not None:
                    image = self.images.image(self.extends)
                    with image.system() as parent:
                        self.local_run(["cp", "--reflink=auto", "-a", parent.path.as_posix(), work_path.as_posix()])
                else:
                    tarball_path = self.images.get_distro_tarball(self.distro)
                    if tarball_path is not None:
                        # Shortcut in case we have a chroot in a tarball
                        self.images.session.mock_log(system=self.name, action="mkdir", arg=tarball_path)
                        self.local_run(["tar", "-C", work_path.as_posix(), "-axf", tarball_path])
                    else:
                        system = MaintenanceSystem(self, image)
                        distro = DistroFamily.lookup_distro(image.distro)
                        distro.bootstrap(system)
            except BaseException:
                self.images.session.mock_log(system=self.name, action="rmtree", arg=work_path)
                raise
            else:
                self.images.session.mock_log(system=self.name, action="mv", src=work_path, dst=self.path)
        finally:
            self.path = orig_path

    @override
    def remove(self) -> None:
        self.session.mock_log(system=self.name, action="rmtree", arg=self.path)

    @override
    def remove_config(self) -> None:
        raise NotImplementedError()

    @contextlib.contextmanager
    def container(self) -> Generator["MockSystem", None, None]:
        from .system import MockSystem

        yield MockSystem(self.images, self)

    @contextlib.contextmanager
    def maintenance_container(self) -> Generator["MaintenanceSystem", None, None]:
        from .system import MockMaintenanceSystem

        image = self.image(name)
        yield MockMaintenanceSystem(self.images, self)

    def create_container(self, instance_name: str | None = None, config: ContainerConfig | None = None) -> Container:
        """
        Boot a container with this system
        """
        from moncic.container import MockContainer

        config = self.container_config(config)
        return MockContainer(self, config, instance_name)
