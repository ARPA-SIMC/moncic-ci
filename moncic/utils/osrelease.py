from __future__ import annotations

import shlex
from collections.abc import Iterator


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


def parse_osrelase(fname: str) -> dict[str, str]:
    """
    Parse an os-release file into a dict
    """
    with open(fname) as fd:
        lexer = shlex.shlex(fd, fname, posix=True)
        # Python 3.9 needs this, python 3.7 did not need it, release note don't
        # seem to mention a relevant change
        lexer.wordchars += "-"
        return dict(iter_assigns(lexer))
