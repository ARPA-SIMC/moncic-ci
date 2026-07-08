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

    def test_bootstrap_multi(self) -> None:
        self.session.test_write_config("test1", {"distro": "rocky8"})
        self.session.test_write_config("test2", {"distro": "rocky9"})
        self.session.test_simulate_bootstrap("test1")
        res = self.call("monci", "bootstrap", "test1", "test2")
        self.assertNoStderr(res)
        with self.match_run_log(self.session.run_log) as m:
            container_log = m.assertPopFirst("test1: run container")
            with self.match_run_log(container_log) as cm:
                cm.assertPopScript("Upgrade container")
                cm.assertEmpty()
            m.assertPopFirst("test2: bootstrap")
            container_log = m.assertPopFirst("test2: run container")
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
