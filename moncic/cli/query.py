from __future__ import annotations

import csv
import logging
import shutil
import subprocess
import sys
from collections.abc import Sequence
from typing import Any, NamedTuple, TextIO

try:
    from texttable import Texttable

    HAVE_TEXTTABLE = True
except ModuleNotFoundError:
    HAVE_TEXTTABLE = False

from ..distro import DistroFamily
from .moncic import MoncicCommand, main_command

log = logging.getLogger(__name__)


class RowOutput:
    def add_row(self, row: Sequence[Any]):
        raise NotImplementedError(f"{self.__class__}.add_row() not implemented")

    def flush(self):
        pass


class CSVOutput(RowOutput):
    def __init__(self, out: TextIO):
        self.writer = csv.writer(out)

    def add_row(self, row: Sequence[Any]):
        self.writer.writerow(row)


class TextColumn(NamedTuple):
    title: str
    dtype: str = "t"
    align: str = "l"


class TableOutput(RowOutput):
    def __init__(self, out: TextIO, *args: TextColumn):
        self.out = out
        self.table = Texttable(max_width=shutil.get_terminal_size()[0])
        self.table.set_deco(Texttable.HEADER)
        self.table.set_cols_dtype([a.dtype for a in args])
        self.table.set_cols_align([a.align for a in args])
        self.table.add_row([a.title for a in args])

    def add_row(self, row: Sequence[Any]):
        self.table.add_row(row)

    def flush(self):
        print(self.table.draw())


@main_command
class Images(MoncicCommand):
    """
    List OS images
    """

    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("--csv", action="store_true", help="machine readable output in CSV format")
        return parser

    def run(self) -> None:
        if self.args.csv or not HAVE_TEXTTABLE:
            output = CSVOutput(sys.stdout)
        else:
            output = TableOutput(
                sys.stdout,
                TextColumn("Name"),
                TextColumn("Distro"),
                TextColumn("Boostrapped"),
                TextColumn("Backend"),
                TextColumn("Backend ID"),
            )

        # List images that have been bootstrapped
        res = subprocess.run(
            ["machinectl", "list-images", "--no-pager", "--no-legend"], check=True, stdout=subprocess.PIPE, text=True
        )
        bootstrapped: set[str] = set()
        for line in res.stdout.splitlines():
            bootstrapped.add(line.split()[0])

        # List configured images
        with self.moncic.session() as session:
            images = session.images
            for image in images.list_images():
                output.add_row(
                    (
                        image.name,
                        image.distro,
                        "yes" if image.bootstrapped else "no",
                        image.image_type,
                        image.get_backend_id(),
                    )
                )
        output.flush()


@main_command
class Distros(MoncicCommand):
    """
    List OS images
    """

    @classmethod
    def make_subparser(cls, subparsers):
        parser = super().make_subparser(subparsers)
        parser.add_argument("--csv", action="store_true", help="machine readable output in CSV format")
        return parser

    def run(self):
        if self.args.csv or not HAVE_TEXTTABLE:
            output = CSVOutput(sys.stdout)
        else:
            output = TableOutput(sys.stdout, TextColumn("Name"), TextColumn("Shortcuts"))

        for family in sorted(DistroFamily.list(), key=lambda x: x.name):
            for info in family.list_distros():
                output.add_row((info.name, ", ".join(info.shortcuts)))
        output.flush()
