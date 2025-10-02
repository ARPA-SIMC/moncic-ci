import contextlib
import os
import shutil
import tempfile
import types
from collections.abc import Generator
from pathlib import Path
from typing import NamedTuple, Self

from moncic.runner import UserConfig

from .fs import dirfd


class FileInfo(NamedTuple):
    size: int
    atime_ns: int


class DebCache:
    def __init__(self, cache_dir: Path, cache_size: int = 512 * 1024 * 1024) -> None:
        self.cache_dir = cache_dir
        # Maximum cache size in bytes
        self.cache_size = cache_size
        # Information about .deb files present in cache
        self.debs: dict[str, FileInfo] = {}
        self.src_dir_fd: int | None = None
        self.cache_user = UserConfig.from_sudoer()

    def __enter__(self) -> Self:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.src_dir_fd = os.open(self.cache_dir, os.O_RDONLY)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        # Do cache cleanup
        self.trim_cache()
        assert self.src_dir_fd is not None
        os.close(self.src_dir_fd)

    def trim_cache(self) -> None:
        """
        Trim cache to fit self.cache_size, removing the files least recently
        accessed
        """
        # Sort debs by atime and remove all those that go beyond
        # self.cache_size
        sdebs = sorted(self.debs.items(), key=lambda x: x[1].atime_ns, reverse=True)
        size = 0
        for name, info in sdebs:
            if size + info.size > self.cache_size:
                os.unlink(name, dir_fd=self.src_dir_fd)
                del self.debs[name]
            else:
                size += info.size

    def _debs_to_aptdir(self, dst_dir_fd: int) -> None:
        """Handlink existing debs to the destination directory."""
        with os.scandir(self.src_dir_fd) as it:
            for de in it:
                if not de.name.endswith(".deb"):
                    continue
                st = de.stat()
                self.debs[de.name] = FileInfo(st.st_size, st.st_atime_ns)
                os.link(de.name, de.name, src_dir_fd=self.src_dir_fd, dst_dir_fd=dst_dir_fd)

    def _debs_from_aptdir(self, dst_dir_fd: int) -> None:
        """Hardlink new debs to cache dir."""
        os.lseek(dst_dir_fd, 0, os.SEEK_SET)
        with os.scandir(dst_dir_fd) as it:
            for de in it:
                if de.name.endswith(".deb"):
                    st = de.stat()
                    if de.name not in self.debs:
                        os.link(de.name, de.name, src_dir_fd=dst_dir_fd, dst_dir_fd=self.src_dir_fd)
                    self.debs[de.name] = FileInfo(st.st_size, st.st_atime_ns)

    @contextlib.contextmanager
    def apt_archives(self) -> Generator[Path]:
        """
        Create a directory that can be bind mounted as /apt/cache/apt/archives
        """
        with tempfile.TemporaryDirectory(dir=self.cache_dir, suffix="aptdir") as aptdir_str:
            aptdir = Path(aptdir_str)
            with dirfd(aptdir) as dst_dir_fd:
                self._debs_to_aptdir(dst_dir_fd)
                try:
                    yield aptdir
                finally:
                    self._debs_from_aptdir(dst_dir_fd)


def apt_get_cmd(*args: str) -> list[str]:
    """
    Build an apt-get command
    """
    res = []

    eatmydata = shutil.which("eatmydata")
    if eatmydata:
        res.append(eatmydata)

    res += [
        "apt-get",
        "--assume-yes",
        "--quiet",
        "--show-upgraded",
        # The space after -o is odd but required, and I could
        # not find a better working syntax
        '-o Dpkg::Options::="--force-confnew"',
    ]

    res.extend(args)

    return res
