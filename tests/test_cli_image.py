from unittest import mock

from moncic.exceptions import Fail
from moncic.unittest import CLITestCase
from moncic.provision.image import ConfiguredImage


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

    def test_image_extends_config_exists(self) -> None:
        (self.config.imageconfdirs[0] / "test.yaml").write_text("")
        with self.assertRaisesRegex(Fail, "^test: configuration already exists in "):
            self.call("monci", "image", "test", "distro", "rocky8")
        self.assertRunLogEmpty(self.session.run_log)

    def test_image_setup(self) -> None:
        self.session.test_simulate_bootstrap("test", {"extends": "rocky8"})
        res = self.call("monci", "image", "test", "setup", "true")
        self.assertNoStderr(res)
        self.assertEqual(res.stdout, "")
        image = self.session.images.configured_images.image("test")
        self.assertIsInstance(image, ConfiguredImage)
        assert isinstance(image, ConfiguredImage)
        self.assertEqual(image.config.bootstrap_info.maintscript, "#!/bin/sh\ntrue\n")

        with self.match_run_log(self.session.run_log) as m:
            container_log = m.assertPopFirst("test: run container")
            with self.match_run_log(container_log) as cm:
                cm.assertPopScript("Upgrade container")

    def test_image_install(self) -> None:
        self.session.test_simulate_bootstrap("test", {"extends": "rocky8"})
        res = self.call("monci", "image", "test", "install", "package")
        self.assertNoStderr(res)
        self.assertEqual(res.stdout, "")
        image = self.session.images.configured_images.image("test")
        self.assertIsInstance(image, ConfiguredImage)
        assert isinstance(image, ConfiguredImage)
        self.assertEqual(image.config.bootstrap_info.packages, ["package"])

        with self.match_run_log(self.session.run_log) as m:
            container_log = m.assertPopFirst("test: run container")
            with self.match_run_log(container_log) as cm:
                cm.assertPopScript("Upgrade container")

    def test_image_edit(self) -> None:
        self.session.test_simulate_bootstrap("test", {"extends": "rocky8"})
        with mock.patch("moncic.cli.image.edit_yaml", return_value="distro: rocky8"):
            res = self.call("monci", "image", "test", "edit")
        self.assertNoStderr(res)
        self.assertEqual(res.stdout, "")

        with self.match_run_log(self.session.run_log) as m:
            container_log = m.assertPopFirst("test: run container")
            with self.match_run_log(container_log) as cm:
                cm.assertPopScript("Upgrade container")

    def test_image_cat(self) -> None:
        path = self.config.imageconfdirs[0] / "test.yaml"
        self.session.test_simulate_bootstrap("test", {"extends": "rocky8"})
        res = self.call("monci", "image", "test", "cat")
        self.assertNoStderr(res)
        self.assertEqual(res.stdout.splitlines(), [f"# {path}", "---", "extends: rocky8"])
        self.assertRunLogEmpty(self.session.run_log)
