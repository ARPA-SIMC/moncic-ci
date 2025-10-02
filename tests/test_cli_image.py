from moncic.exceptions import Fail
from moncic.unittest import CLITestCase


class CliImageTests(CLITestCase):
    def test_image_distro(self) -> None:
        self.call("monci", "image", "test", "distro", "rocky8")
        with self.match_run_log(self.session.run_log) as m:
            m.assertPopFirst("test: bootstrap")

    def test_image_distro_config_exists(self) -> None:
        (self.config.imageconfdirs[0] / "test.yaml").write_text("")
        with self.assertRaisesRegex(Fail, "^test: configuration already exists in "):
            self.call("monci", "image", "test", "distro", "rocky8")
        self.assertRunLogEmpty(self.session.run_log)

    def test_image_extends(self) -> None:
        self.call("monci", "image", "test", "extends", "rocky8")
        with self.match_run_log(self.session.run_log) as m:
            m.assertPopFirst("rocky8: bootstrap")
            m.assertPopUntil("test: extend rocky8")
            m.assertEmpty()

    def test_image_extends_config_exists(self) -> None:
        (self.config.imageconfdirs[0] / "test.yaml").write_text("")
        with self.assertRaisesRegex(Fail, "^test: configuration already exists in "):
            self.call("monci", "image", "test", "distro", "rocky8")
        self.assertRunLogEmpty(self.session.run_log)
