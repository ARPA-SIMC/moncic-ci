from __future__ import annotations


def escape_bind_ro(s: str):
    r"""
    Escape a path for use in systemd-nspawn --bind-ro.

    Man systemd-nspawn says:

      Backslash escapes are interpreted, so "\:" may be used to embed
      colons in either path.
    """
    return s.replace(":", r"\:")
