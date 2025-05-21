from typing import IO
import shlex


class Script:
    """Incrementally build a shellscript."""

    def __init__(self, title: str) -> None:
        self.title = title
        self.shell = "/bin/sh"
        self.lines: list[str] = []

    def __bool__(self) -> bool:
        """Check if the script contains any command."""
        return bool(self.lines)

    def add(self, command: list[str], *, description: str | None = None) -> None:
        """Append a command to the script."""
        if description:
            self.lines.append("echo " + shlex.quote(description))
        self.lines.append(shlex.join(command))

    def print(self, file: IO[str] | None = None) -> None:
        """Write the script to a file."""
        print(f"#!{self.shell}", file=file)
        print(file=file)
        print(f"# {self.title}", file=file)
        print(file=file)
        for line in self.lines:
            print(line, file=file)
