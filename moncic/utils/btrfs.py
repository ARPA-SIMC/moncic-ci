import contextlib
import fcntl
import logging
import os
import re
import struct
import subprocess
import tempfile
from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..moncic import MoncicConfig

log = logging.getLogger(__name__)


class Subvolume:
    """
    Low-level functions to access and maintain a btrfs subvolume
    """

    def __init__(self, mconfig: "MoncicConfig", path: Path, compression: str | None):
        self.mconfig = mconfig
        self.path = path
        self.compression: str | None
        if compression is None:
            self.compression = mconfig.compression
        else:
            self.compression = compression

    def replace_subvolume(self, path: Path) -> None:
        """
        Replace the given subvolume with this one.

        This and the destination subvolumes need to be on the same filesystem.
        """
        # We can do this because we stay on the same directory, which should
        # only be writable by root
        stash_path = path.parent / f"{path.name}.tmp"
        path.rename(stash_path)
        self.path.rename(path)
        self.path = path
        old = Subvolume(self.mconfig, stash_path, self.compression)
        old.remove()

    def local_run(self, cmd: list[str]) -> None:
        """Run a command on the host system."""
        subprocess.run(cmd)

    @contextlib.contextmanager
    def create(self) -> Generator[None, None, None]:
        """
        Create a btrfs subvolume, and leave it on exit only if the context
        manager did not raise an exception
        """
        if os.path.exists(self.path):
            raise RuntimeError(f"{self.path!r} already exists")

        # See if there is a compression level configured that we should apply
        self.local_run(["btrfs", "-q", "subvolume", "create", self.path.as_posix()])
        try:
            if self.compression is not None:
                self.local_run(
                    ["btrfs", "-q", "property", "set", self.path.as_posix(), "compression", self.compression]
                )
            yield
        except BaseException:
            # Catch BaseException instead of Exception to also cleanup in case
            # of KeyboardInterrupt
            self.remove()
            raise

    def snapshot(self, source_path: Path) -> None:
        """
        Create a btrfs subvolume, and leave it on exit only if the context
        manager did not raise an exception
        """
        if not os.path.exists(source_path):
            raise RuntimeError(f"{source_path!r} does not exist")
        if os.path.exists(self.path):
            raise RuntimeError(f"{self.path!r} already exists")

        self.local_run(["btrfs", "-q", "subvolume", "snapshot", source_path.as_posix(), self.path.as_posix()])

    def remove(self) -> None:
        """
        Remove this subvolume and all subvolumes nested inside it
        """
        # Fetch IDs of nested subvolumes
        #
        # Use IDs rather than paths to avoid potential issues with exotic path
        # names
        re_btrfslist = re.compile(r"^ID (\d+) gen \d+ top level \d+ path (.+)$")
        res = subprocess.run(
            ["btrfs", "subvolume", "list", "-o", self.path.as_posix()], check=True, text=True, capture_output=True
        )
        to_delete = []
        for line in res.stdout.splitlines():
            if mo := re_btrfslist.match(line):
                to_delete.append((mo.group(1), mo.group(2)))
            else:
                raise RuntimeError(f"Unparsable line in btrfs output: {line!r}")

        # Delete in reverse order
        for subvolid, subvolpath in to_delete[::-1]:
            log.info("removing btrfs subvolume %r", subvolpath)
            self.local_run(["btrfs", "-q", "subvolume", "delete", "--subvolid", subvolid, self.path.as_posix()])

        # Delete the subvolume itself
        self.local_run(["btrfs", "-q", "subvolume", "delete", self.path.as_posix()])


FIDEDUPERANGE = 0xC0189436


def ioctl_fideduperange(src_fd: int, s: bytes) -> tuple[int, int]:
    """
    Wrapper for ioctl_fideduperange(2)
    """
    v = fcntl.ioctl(src_fd, FIDEDUPERANGE, s)
    _, _, _, _, _, _, _, bytes_dup, status, _ = struct.unpack("QQHHIqQQiH", v)
    return bytes_dup, status


def do_dedupe(src_file: str, dst_file: str, size: int) -> int:
    """
    Tell the kernel to deduplicate the two files if their contents are the
    same.

    The files are supposed to have the same size, which is already known and
    passed as the ``size`` argument.
    """
    # The code to interface with BTRFS is taken using dduper as a reference.
    # See https://github.com/Lakshmipathi/dduper/blob/master/dduper

    total_bytes_deduped = 0

    src_fd = os.open(src_file, os.O_RDONLY)
    try:
        dst_fd = os.open(dst_file, os.O_WRONLY)
        try:
            # todo: Clear dict/np/list if there are not used further
            # todo : handle same content within single file

            chunk_size = 1024 * 1024
            for offset in range(0, size, chunk_size):
                src_len = min(chunk_size, size - offset)

                s = struct.pack("QQHHIqQQiH", offset, src_len, 1, 0, 0, dst_fd, offset, 0, 0, 0)
                bytes_deduped, status = ioctl_fideduperange(src_fd, s)
                total_bytes_deduped += bytes_deduped
        finally:
            os.close(dst_fd)
    finally:
        os.close(src_fd)

    return total_bytes_deduped


def is_btrfs(path: Path) -> bool:
    """
    Check if a path is on a btrfs filesystem
    """
    # FIXME: One could use os.statvfs, but its Python version does not (yet?)
    #        expose the f_type field in its output
    res = subprocess.run(
        ["stat", "--file-system", "--format=%T", path.as_posix()], capture_output=True, text=True, check=True
    )
    return res.stdout.strip() == "btrfs"


@contextlib.contextmanager
def pause_automounting(pathname: str) -> Generator[None, None, None]:
    """
    Pause automounting on the file image for the duration of this context manager
    """
    # Get the partition UUID
    res = subprocess.run(["btrfs", "filesystem", "show", pathname, "--raw"], check=True, capture_output=True, text=True)
    if mo := re.search(r"uuid: (\S+)", res.stdout):
        uuid = mo.group(1)
    else:
        raise RuntimeError(f"btrfs filesystem uuid not found in {pathname}")

    # See /usr/lib/udisks2/udisks2-inhibit
    rules_dir = "/run/udev/rules.d"
    os.makedirs(rules_dir, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="wt", dir=rules_dir, prefix="90-udisks-inhibit-", suffix=".rules") as fd:
        print(f'SUBSYSTEM=="block", ENV{{ID_FS_UUID}}=="{uuid}", ENV{{UDISKS_IGNORE}}="1"', file=fd)
        fd.flush()
        os.fsync(fd.fileno())
        subprocess.run(["udevadm", "control", "--reload"], check=True)
        subprocess.run(["udevadm", "trigger", "--settle", "--subsystem-match=block"], check=True)
        try:
            yield
        finally:
            fd.close()
            subprocess.run(["udevadm", "control", "--reload"], check=True)
            subprocess.run(["udevadm", "trigger", "--settle", "--subsystem-match=block"], check=True)
