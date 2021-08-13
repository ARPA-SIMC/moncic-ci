from __future__ import annotations
from typing import Optional
import logging
import time
import sys
import os


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
        SIMCOP_NAME = f"{os.path.basename(sys.argv[0])}[{os.getpid()}.{int(time.time())}]"
        FORMAT = f"[%(levelname)-9s] %(asctime)-19.19s {SIMCOP_NAME} %(message)s"
        log_handler = logging.StreamHandler(sys.stderr)
        log_handler.setFormatter(logging.Formatter(FORMAT))
        if self.args.debug:
            log_handler.setLevel(logging.DEBUG)
        elif self.args.verbose:
            log_handler.setLevel(logging.INFO)
        else:
            log_handler.setLevel(logging.WARN)
        root_logger = logging.getLogger()
        root_logger.addHandler(log_handler)
        root_logger.setLevel(logging.DEBUG)

# TODO: colors if on terminal:
#    local logtime="$(date -u +'%Y-%m-%d %H:%M:%S')"
#        if [[ -t 1 ]]
#        then
#                tput setab 0 || tput setb 0
#                case "$1" in
#                        debug) tput bold ; tput setaf 7 || tput setf 7 ;;
#                        info)  tput bold ; tput setaf 2 || tput setf 2 ;;
#                        warning)  tput bold ; tput setaf 3 || tput setf 6 ;;
#                        err) tput bold ; tput setaf 1 || tput setf 4 ;;
#                        crit) tput bold ; tput setaf 5 || tput setf 5;;
#                        *) return;;
#                esac
#        printf "[%-9s] %s %s: %s\n" "$1" "$logtime" "$SIMCOP_NAME" "$2"
#                tput sgr0;
#        else
#        printf "[%-9s] %s %s: %s\n" "$1" "$logtime" "$SIMCOP_NAME" "$2"
#        fi
#        return

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
