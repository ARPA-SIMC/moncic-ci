from moncic.unittest import CLITestCase


class CliQueryTests(CLITestCase):
    def test_images(self) -> None:
        self.session.test_simulate_bootstrap("test", {"extends": "rocky8"})
        res = self.call("monci", "images")
        self.assertNoStderr(res)
        self.assertRunLogEmpty(self.session.run_log)

        self.assertRegex(res.stdout, r"fedora:40\s+40\s+no")
        self.assertRegex(res.stdout, r"test\s+8\s+yes\s+mock\s+mock")

    def test_images_csv(self) -> None:
        self.session.test_simulate_bootstrap("test", {"extends": "rocky8"})
        res = self.call("monci", "images", "--csv")
        self.assertNoStderr(res)
        self.assertRunLogEmpty(self.session.run_log)

        self.assertRegex(res.stdout, r"fedora:40,40,no")
        self.assertRegex(res.stdout, r"test,8,yes,mock,mock")

    def test_distros(self) -> None:
        # Session is not used by the command, but the mock session needs a chance to run cleanup
        with self.session:
            pass
        self.session.test_simulate_bootstrap("test", {"extends": "rocky8"})
        res = self.call("monci", "distros")
        self.assertNoStderr(res)
        self.assertRunLogEmpty(self.session.run_log)

        self.assertRegex(res.stdout, r"debian:sid\s+sid, unstable, debian:unstable")

    def test_distros_csv(self) -> None:
        # Session is not used by the command, but the mock session needs a chance to run cleanup
        with self.session:
            pass
        self.session.test_simulate_bootstrap("test", {"extends": "rocky8"})
        res = self.call("monci", "distros", "--csv")
        self.assertNoStderr(res)
        self.assertRunLogEmpty(self.session.run_log)
        self.assertRegex(res.stdout, r'debian:sid,"sid, unstable, debian:unstable"')
