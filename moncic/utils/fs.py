from __future__ import annotations

import contextlib
import logging
import os
import tempfile
from collections.abc import Generator

log = logging.getLogger(__name__)


@contextlib.contextmanager
def atomic_writer(
    fname: str, mode: str = "w+b", chmod: int | None = 0o664, sync: bool = True, use_umask: bool = False, **kw
):
    """
    open/tempfile wrapper to atomically write to a file, by writing its
    contents to a temporary file in the same directory, and renaming it at the
    end of the block if no exception has been raised.

    :arg fname: name of the file to create
    :arg mode: passed to mkstemp/open
    :arg chmod: permissions of the resulting file
    :arg sync: if True, call fdatasync before renaming
    :arg use_umask: if True, apply umask to chmod

    All the other arguments are passed to open
    """

    if chmod is not None and use_umask:
        cur_umask = os.umask(0)
        os.umask(cur_umask)
        chmod &= ~cur_umask

    dirname = os.path.dirname(fname)
    if not os.path.isdir(dirname):
        os.makedirs(dirname)

    fd, abspath = tempfile.mkstemp(dir=dirname, text="b" not in mode, prefix=fname)
    outfd = open(fd, mode, closefd=True, **kw)
    try:
        yield outfd
        outfd.flush()
        if sync:
            os.fdatasync(fd)
        if chmod is not None:
            os.fchmod(fd, chmod)
        os.rename(abspath, fname)
    except Exception:
        os.unlink(abspath)
        raise
    finally:
        outfd.close()


def is_on_rotational(pathname: str) -> bool | None:
    """
    Check if the given file is stored on rotational storage.

    Returns None if detection failed.
    """
    st = os.stat(pathname)
    dev = f"/sys/dev/block/{os.major(st.st_dev)}:{os.minor(st.st_dev)}"
    try:
        dest = os.readlink(dev)
    except FileNotFoundError:
        return None
    # Resolve the relative symlink to the partition device
    fulldev = os.path.join(os.path.dirname(dev), dest)
    # Look for queue/rotational in the parent directory (which should be the disk device)
    rotfile = os.path.join(os.path.dirname(fulldev), "queue", "rotational")
    try:
        with open(rotfile) as fd:
            return fd.read().strip() == "1"
    except FileNotFoundError:
        return None


@contextlib.contextmanager
def cd(path: str):
    """
    chdir to path for the duration of this context manager
    """
    cwd = os.getcwd()
    try:
        os.chdir(path)
        yield
    finally:
        os.chdir(cwd)


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


@contextlib.contextmanager
def extra_packages_dir(path: str) -> Generator[str, None, None]:
    """
    Create a temporary directory where all packages found in path are
    hardlinked
    """
    with tempfile.TemporaryDirectory(dir=path) as mirrordir:
        # Hard link all .deb files into the temporary mirror directory
        with dirfd(path) as src_dir_fd:
            with dirfd(mirrordir) as dst_dir_fd:
                os.chmod(dst_dir_fd, 0o755)
                with os.scandir(src_dir_fd) as it:
                    for de in it:
                        if de.name.endswith(".deb") or de.name.endswith(".rpm"):
                            os.link(de.name, de.name, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)
        # We cannot create a mirror now, since apt-ftparchive may not be
        # present outside the container
        yield mirrordir
