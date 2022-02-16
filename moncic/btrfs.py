from __future__ import annotations
import contextlib
import logging
import os
import re
import subprocess
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .run import MaintenanceMixin

log = logging.getLogger(__name__)


class Subvolume:
    """
    Low-level functions to access and maintain a btrfs subvolume
    """
    def __init__(self, run: MaintenanceMixin):
        self.run = run
        self.path = self.run.system.path

    @contextlib.contextmanager
    def create(self):
        """
        Create a btrfs subvolume, and leave it on exit only if the context
        manager did not raise an exception
        """
        if os.path.exists(self.path):
            raise RuntimeError(f"{self.path!r} already exists")

        self.run.local_run(["btrfs", "-q", "subvolume", "create", self.path])
        try:
            yield
        except Exception:
            self.run.local_run(["btrfs", "-q", "subvolume", "delete", self.path])
            raise

    def remove(self):
        """
        Remove this subvolume and all subvolumes nested inside it
        """
        # Fetch IDs of nested subvolumes
        #
        # Use IDs rather than paths to avoid potential issues with exotic path
        # names
        re_btrfslist = re.compile(r"^ID (\d+) gen \d+ top level \d+ path (.+)$")
        res = subprocess.run(
                ["btrfs", "subvolume", "list", "-o", self.path],
                check=True, text=True, capture_output=True)
        to_delete = []
        for line in res.stdout.splitlines():
            if mo := re_btrfslist.match(line):
                to_delete.append((mo.group(1), mo.group(2)))
            else:
                raise RuntimeError(f"Unparsable line in btrfs output: {line!r}")

        # Delete in reverse order
        for subvolid, subvolpath in to_delete[::-1]:
            log.info("removing btrfs subvolume %r", subvolpath)
            self.run.local_run(["btrfs", "-q", "subvolume", "delete", "--subvolid", subvolid, self.path])

        # Delete the subvolume itself
        self.run.local_run(["btrfs", "-q", "subvolume", "delete", self.path])
