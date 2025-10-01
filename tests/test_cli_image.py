import sys
import tempfile
from pathlib import Path
from typing import override
from unittest import mock

from moncic.__main__ import main
from moncic.exceptions import Fail
from moncic.moncic import MoncicConfig
from moncic.unittest import MockMoncicTestCase


class CliImageTests(MockMoncicTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.enterContext(mock.patch("moncic.cli.moncic.Moncic", return_value=self.moncic))

    @override
    def get_imageconfdir(self) -> Path | None:
        return Path(self.enterContext(tempfile.TemporaryDirectory()))

    def call(self, *args: str) -> None:
        orig_argv = sys.argv
        sys.argv = list(args)
        try:
            main()
        finally:
            sys.argv = orig_argv

    def test_image_distro(self) -> None:
        self.call("monci", "image", "test", "distro", "rocky8")
        run_log = self.moncic.last_run_log
        assert run_log
        self.assertRunLogPopFirst(run_log, "test: bootstrap")

    def test_image_distro_config_exists(self) -> None:
        (self.config.imageconfdirs[0] / "test.yaml").write_text("")
        with self.assertRaisesRegex(Fail, "^test: configuration already exists in "):
            self.call("monci", "image", "test", "distro", "rocky8")
        run_log = self.moncic.last_run_log
        assert run_log
        self.assertRunLogEmpty(run_log)

    def test_image_extends(self) -> None:
        self.call("monci", "image", "test", "extends", "rocky8")
        run_log = self.moncic.last_run_log
        assert run_log
        self.assertRunLogPopFirst(run_log, "rocky8: bootstrap")
        self.assertRunLogPopUntil(run_log, "test: extend rocky8")
        self.assertRunLogEmpty(run_log)

    def test_image_extends_config_exists(self) -> None:
        (self.config.imageconfdirs[0] / "test.yaml").write_text("")
        with self.assertRaisesRegex(Fail, "^test: configuration already exists in "):
            self.call("monci", "image", "test", "distro", "rocky8")
        run_log = self.moncic.last_run_log
        assert run_log
        self.assertRunLogEmpty(run_log)
