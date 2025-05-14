import contextlib
import logging
import os
import subprocess
from typing import Generator

from moncic.nspawn.image import NspawnImage
from moncic.nspawn.images import NspawnImages
from moncic.nspawn.system import MaintenanceSystem

from .system import MockMaintenanceSystem, MockSystem

log = logging.getLogger("images")


class MockImages(NspawnImages):
    """
    Mock image storage, used for testing
    """

    def system_config(self, name: str) -> NspawnImage:
        return NspawnImage(name=name, path="/tmp/mock-moncic-ci", distro=name)

    @contextlib.contextmanager
    def system(self, name: str) -> Generator[NspawnSystem, None, None]:
        system_config = self.system_config(name)
        yield MockSystem(self, system_config)

    @contextlib.contextmanager
    def maintenance_system(self, name: str) -> Generator[MaintenanceSystem, None, None]:
        system_config = self.system_config(name)
        yield MockMaintenanceSystem(self, system_config)

    def bootstrap_system(self, name: str):
        image = self.system_config(name)
        if image.path.exists():
            return

        log.info("%s: bootstrapping directory", name)

        path = self.imagedir / name
        work_path = self.imagedir / f"{name}.new"
        image.path = work_path

        try:
            if image.extends is not None:
                with self.system(image.extends) as parent:
                    image.local_run(["cp", "--reflink=auto", "-a", parent.path, work_path.as_posix()])
            else:
                tarball_path = self.get_distro_tarball(image.distro)
                if tarball_path is not None:
                    # Shortcut in case we have a chroot in a tarball
                    self.session.mock_log(system=name, action="mkdir", arg=tarball_path)
                    image.local_run(["tar", "-C", work_path.as_posix(), "-axf", tarball_path])
                else:
                    system = MaintenanceSystem(self, image)
                    distro = DistroFamily.lookup_distro(image.distro)
                    distro.bootstrap(system)
        except BaseException:
            self.session.mock_log(system=name, action="rmtree", arg=work_path)
            raise
        else:
            self.session.mock_log(system=name, action="mv", src=work_path, dst=path)

    def remove_system(self, name: str):
        path = os.path.join(self.imagedir, name)
        self.session.mock_log(system=name, action="rmtree", arg=path)
