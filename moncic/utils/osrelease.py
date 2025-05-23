import shlex
from collections.abc import Iterator
from pathlib import Path
from typing import IO


def iter_assigns(tokens: Iterator[str]):
    while True:
        try:
            name = next(tokens)
        except StopIteration:
            break
        equals = next(tokens)
        if equals != "=":
            raise RuntimeError("syntax error, found a triplet that is not an assignment")
        value = next(tokens)
        yield (name, value)


def parse_osrelase(fname: Path) -> dict[str, str]:
    """
    Parse an os-release file into a dict
    """
    with fname.open() as fd:
        return parse_osrelase_contents(fd, fname.as_posix())


def parse_osrelase_contents(fd: IO[str], filename: str) -> dict[str, str]:
    """
    Parse an os-release file into a dict
    """
    lexer = shlex.shlex(fd, filename, posix=True)
    # Python 3.9 needs this, python 3.7 did not need it, release note don't
    # seem to mention a relevant change. Python 3.12 also needs "."
    lexer.wordchars += "-."
    return dict(iter_assigns(lexer))
