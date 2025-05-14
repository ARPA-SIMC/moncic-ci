import subprocess
from typing import override

from moncic.nspawn.image import NspawnImage


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
                    with self.images.system(self.extends) as parent:
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
