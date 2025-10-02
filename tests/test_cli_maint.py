from moncic.exceptions import Fail
from moncic.runner import UserConfig
from moncic.unittest import CLITestCase


class CliMaintTests(CLITestCase):
    def test_bootstrap(self) -> None:
        self.session.test_write_config("test", {"distro": "rocky8"})
        res = self.call("monci", "bootstrap", "test")
        self.assertNoStderr(res)
        with self.match_run_log(self.session.run_log) as m:
            m.assertPopFirst("test: bootstrap")
            container_log = m.assertPopFirst("test: run container")
            with self.match_run_log(container_log) as cm:
                cm.assertPopScript("Upgrade container")
                cm.assertEmpty()

    def test_update(self) -> None:
        self.session.test_simulate_bootstrap("test", {"distro": "rocky8"})
        res = self.call("monci", "update", "test")
        self.assertNoStderr(res)
        with self.match_run_log(self.session.run_log) as m:
            container_log = m.assertPopFirst("test: run container")
            with self.match_run_log(container_log) as cm:
                cm.assertPopScript("Upgrade container")
                cm.assertEmpty()

    def test_remove(self) -> None:
        self.session.test_simulate_bootstrap("test", {"distro": "rocky8"})
        res = self.call("monci", "remove", "test")
        self.assertNoStderr(res)
        with self.match_run_log(self.session.run_log) as m:
            m.assertPopFirst("test: remove")
            m.assertEmpty()
