import csv
import io

from moncic.unittest import CLITestCase


class CliQueryTests(CLITestCase):
    def test_images_csv(self) -> None:
        self.session.test_simulate_bootstrap("test", {"extends": "rocky8"})
        res = self.call("monci", "images", "--csv")
        self.assertNoStderr(res)
        self.assertRunLogEmpty(self.session.run_log)

        with io.StringIO(res.stdout) as csvin:
            rows = list(csv.reader(csvin))
        self.assertIn(["fedora:40", "40", "no", "-", "-"], rows)
        self.assertIn(["test", "8", "yes", "mock", "mock"], rows)

    def test_distros_csv(self) -> None:
        # Session is not used by the command, but the mock session needs a
        # chance to run cleanup
        with self.session:
            pass
        self.session.test_simulate_bootstrap("test", {"extends": "rocky8"})
        res = self.call("monci", "distros", "--csv")
        self.assertNoStderr(res)
        self.assertRunLogEmpty(self.session.run_log)

        with io.StringIO(res.stdout) as csvin:
            rows = list(csv.reader(csvin))
        self.assertIn(["debian:sid", "sid, unstable, debian:unstable"], rows)
