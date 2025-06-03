import argparse
import logging
from typing import Any, override

from moncic.container import RunConfig
from moncic.runner import UserConfig

from .moncic import ImageActionCommand, main_command

log = logging.getLogger(__name__)


@main_command
class Shell(ImageActionCommand):
    """
    Run a shell in the given container
    """

    def run(self) -> int:
        run_config = RunConfig()
        if self.args.root:
            run_config.user = UserConfig.root()
        elif self.args.user:
            run_config.user = UserConfig.from_sudoer()
        with self.container() as container:
            res = container.run_shell(config=run_config)
        return res.returncode


@main_command
class Run(ImageActionCommand):
    """
    Run a shell in the given container
    """

    @override
    @classmethod
    def make_subparser(cls, subparsers: "argparse._SubParsersAction[Any]") -> argparse.ArgumentParser:
        parser = super().make_subparser(subparsers)
        parser.add_argument("command", nargs=argparse.REMAINDER, help="Command to run")
        return parser

    def run(self) -> int:
        run_config = RunConfig()
        if self.args.root:
            run_config.user = UserConfig.root()
        elif self.args.user:
            run_config.user = UserConfig.from_sudoer()
        run_config.check = False

        with self.container() as container:
            res = container.run(self.args.command, config=run_config)
        return res.returncode
