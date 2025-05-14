import subprocess

from moncic.nspawn.image import NspawnImage


class MockImage(NspawnImage):
    def local_run(self, cmd: list[str]) -> subprocess.CompletedProcess:
        self.images.session.mock_log(system=self.name, cmd=cmd)
        return self.images.session.get_process_result(args=cmd)
