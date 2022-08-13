from __future__ import annotations

import contextlib
import os
import tempfile
from typing import Dict, Generator, NamedTuple


class FileInfo(NamedTuple):
    size: int
    atime_ns: int


@contextlib.contextmanager
def dirfd(path: str) -> Generator[int, None, None]:
    """
    Open a directory as a file descriptor
    """
    fileno = os.open(path, os.O_RDONLY)
    try:
        yield fileno
    finally:
        os.close(fileno)


class DebCache:
    def __init__(self, cache_dir: str, cache_size: int = 512*1024*1024):
        self.cache_dir = cache_dir
        # Maximum cache size in bytes
        self.cache_size = cache_size

    @contextlib.contextmanager
    def apt_archives(self) -> Generator[str, None, None]:
        """
        Create a directory that can be bind mounted as /apt/cache/apt/archives
        """
        os.makedirs(self.cache_dir, exist_ok=True)
        with dirfd(self.cache_dir) as src_dir_fd:
            with tempfile.TemporaryDirectory(dir=self.cache_dir) as aptdir:
                with dirfd(aptdir) as dst_dir_fd:
                    debs: Dict[str, FileInfo] = {}

                    # Handlink debs to temp dir
                    with os.scandir(src_dir_fd) as it:
                        for de in it:
                            if de.name.endswith(".deb"):
                                st = de.stat()
                                debs[de.name] = FileInfo(st.st_size, st.st_atime_ns)
                                os.link(de.name, de.name, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

                    yield aptdir

                    # Hardlink new debs to cache dir
                    with os.scandir(dst_dir_fd) as it:
                        for de in it:
                            if de.name.endswith(".deb"):
                                st = de.stat()
                                if de.name not in debs:
                                    os.link(de.name, de.name, src_dir_fd=dst_dir_fd, dst_dir_fd=src_dir_fd)
                                debs[de.name] = FileInfo(st.st_size, st.st_atime_ns)

                    # Sort debs by atime and remove all those that go beyond
                    # self.cache_size
                    sdebs = sorted(debs.items(), key=lambda x: x[1].atime_ns, reverse=True)
                    size = 0
                    for name, info in sdebs:
                        if size > self.cache_size:
                            os.unlink(name, dir_fd=src_dir_fd)
                        else:
                            size += info.size
