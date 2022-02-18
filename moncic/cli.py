from __future__ import annotations
from typing import Optional
import logging
import sys

try:
    import coloredlogs
except ModuleNotFoundError:
    coloredlogs = None


class Fail(RuntimeError):
    """
    Exception raised when the program should exit with an error but without a
    backtrace
    """
    pass


class Command:
    # Command name (as used in command line)
    # Defaults to the lowercased class name
    NAME: Optional[str] = None

    # Command description (as used in command line help)
    # Defaults to the strip()ped class docstring.
    DESC: Optional[str] = None

    def __init__(self, args):
        self.args = args
        self.setup_logging()

    def setup_logging(self):
        # Setup logging
        FORMAT = "%(asctime)-19.19s %(levelname)s %(name)s %(message)s"
        if self.args.debug:
            log_level = logging.DEBUG
        elif self.args.verbose:
            log_level = logging.INFO
        else:
            log_level = logging.WARN

        if coloredlogs is not None:
            coloredlogs.install(level=log_level, fmt=FORMAT)
        else:
            logging.basicConfig(level=log_level, stream=sys.stderr, format=FORMAT)

        # log_handler = logging.StreamHandler(sys.stderr)
        # log_handler.setFormatter(logging.Formatter(FORMAT))
        # log_handler.setLevel(log_level)
        # root_logger = logging.getLogger()
        # root_logger.addHandler(log_handler)
        # root_logger.setLevel(logging.DEBUG)

    @classmethod
    def get_name(cls):
        if cls.NAME is not None:
            return cls.NAME
        return cls.__name__.lower()

    @classmethod
    def make_subparser(cls, subparsers):
        desc = cls.DESC
        if desc is None:
            desc = cls.__doc__.strip()

        parser = subparsers.add_parser(cls.get_name(), help=desc)
        parser.set_defaults(handler=cls)
        parser.add_argument("-v", "--verbose", action="store_true", help="verbose output")
        parser.add_argument("--debug", action="store_true", help="verbose output")
        return parser


def run(func):
    try:
        func()
    except Fail as e:
        print(e, file=sys.stderr)
        sys.exit(1)
