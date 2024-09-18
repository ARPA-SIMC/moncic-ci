from __future__ import annotations

import argparse
import logging

from .moncic import ImageActionCommand, main_command

log = logging.getLogger(__name__)


@main_command
class Shell(ImageActionCommand):
    """
    Run a shell in the given container
    """

    def run(self):
        run_config = self.get_run_config()
        run_config.check = False

        with self.container() as container:
            res = container.run_shell(config=run_config)
        return res.returncode


@main_command
class Run(ImageActionCommand):
    """
    Run a shell in the given container
    """

    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("command", nargs=argparse.REMAINDER, help="Command to run")
        return parser

    def run(self):
        run_config = self.get_run_config()
        run_config.use_path = True
        run_config.check = False

        with self.container() as container:
            res = container.run(self.args.command, config=run_config)
        return res.returncode
