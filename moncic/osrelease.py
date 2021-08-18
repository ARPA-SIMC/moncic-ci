from __future__ import annotations
from typing import Sequence, Dict
import shlex


def iter_assigns(tokens: Sequence[str]):
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


def parse_osrelase(fname: str) -> Dict[str, str]:
    """
    Parse an os-release file into a dict
    """
    with open(fname, "rt") as fd:
        lexer = shlex.shlex(fd, fname, posix=True)
        return dict(iter_assigns(lexer))
