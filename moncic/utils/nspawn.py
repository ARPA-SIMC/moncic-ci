from pathlib import Path


def escape_bind_ro(s: str | Path) -> str:
    r"""
    Escape a path for use in systemd-nspawn --bind-ro.

    Man systemd-nspawn says:

      Backslash escapes are interpreted, so "\:" may be used to embed
      colons in either path.
    """
    return str(s).replace(":", r"\:")
