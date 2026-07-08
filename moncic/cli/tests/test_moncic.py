from pathlib import Path

from moncic.cli.moncic import MoncicCommand, make_argparser
from moncic.exceptions import Fail
from moncic.moncic import MoncicConfig
from moncic.provision.image import ConfiguredImage
from moncic.unittest import TestCase


class CliImageTests(TestCase):
    def test_imageconfs_from_imagedir(self) -> None:
        sample_imagedir = Path("/does-not-exist")
        parser = make_argparser()
        args = parser.parse_args(
            ["bootstrap", "--imagedir", sample_imagedir.as_posix(), "testimage"]
        )
        cmd = MoncicCommand(args)
        config = cmd.moncic.config
        self.assertEqual(config.imagedir, sample_imagedir)
        self.assertEqual(
            config.imageconfdirs,
            [MoncicConfig.xdg_local_config_dir(), sample_imagedir],
        )
