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

    def local_run(self, system_config: NspawnImage, cmd: list[str]) -> subprocess.CompletedProcess:
        self.session.mock_log(system=system_config.name, cmd=cmd)
        return self.session.get_process_result(args=cmd)

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
        system_config = self.system_config(name)
        if os.path.exists(system_config.path):
            return

        log.info("%s: bootstrapping directory", name)

        path = os.path.join(self.imagedir, name)
        work_path = path + ".new"
        system_config.path = work_path

        try:
            if system_config.extends is not None:
                with self.system(system_config.extends) as parent:
                    self.local_run(system_config, ["cp", "--reflink=auto", "-a", parent.path, work_path])
            else:
                tarball_path = self.get_distro_tarball(system_config.distro)
                if tarball_path is not None:
                    # Shortcut in case we have a chroot in a tarball
                    self.session.mock_log(system=name, action="mkdir", arg=tarball_path)
                    self.local_run(system_config, ["tar", "-C", work_path, "-axf", tarball_path])
                else:
                    system = MaintenanceSystem(self, system_config)
                    distro = DistroFamily.lookup_distro(system_config.distro)
                    distro.bootstrap(system)
        except BaseException:
            self.session.mock_log(system=name, action="rmtree", arg=work_path)
            raise
        else:
            self.session.mock_log(system=name, action="mv", src=work_path, dst=path)

    def remove_system(self, name: str):
        path = os.path.join(self.imagedir, name)
        self.session.mock_log(system=name, action="rmtree", arg=path)
