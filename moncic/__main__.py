import logging
import sys
from typing import Protocol

from moncic import cli, exceptions

log = logging.getLogger(__name__)

cli.FAIL_EXCEPTIONS.append(exceptions.Fail)
cli.SUCCESS_EXCEPTIONS.append(exceptions.Success)


class Handler(Protocol):
    def run(self) -> int | None: ...


def main() -> int | None:
    parser = cli.make_argparser()

    try:
        args = parser.parse_args()
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 1

    handler: Handler = args.handler(args)
    return handler.run()


if __name__ == "__main__":
    cli.run_main(main)
