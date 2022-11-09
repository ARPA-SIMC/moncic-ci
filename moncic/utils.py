from __future__ import annotations

import contextlib
import functools
import io
import logging
import os
import re
import shlex
import subprocess
import tempfile
from typing import Generator, Optional, Sequence

import yaml

log = logging.getLogger(__name__)


# Set to True when running in the guest system
in_guest = False


def host_only(f):
    """
    Mark a function to be run only in host systems
    """
    @functools.wraps(f)
    def wrapper(*args, **kw):
        global in_guest
        if in_guest:
            raise RuntimeError(f"{f.__name__} called when in guest system")
        return f(*args, **kw)
    return wrapper


def guest_only(f):
    """
    Mark a function to be run only in guest systems
    """
    @functools.wraps(f)
    def wrapper(*args, **kw):
        global in_guest
        if not in_guest:
            raise RuntimeError(f"{f.__name__} called when in host system")
        return f(*args, **kw)
    return wrapper


def fix_logging_on_guest():
    """
    When running a fuction in the guest system, logging is reinitialized, but
    the `log` value remains a reference to the old logger. Recreate it here.
    """
    global log
    log = logging.getLogger(__name__)


def run(cmd: Sequence[str], check: bool = True, **kw) -> subprocess.CompletedProcess:
    """
    Logging wrapper to subprocess.run.

    Also, default check to True.
    """
    log.info("Run: %s", " ".join(shlex.quote(x) for x in cmd))
    return subprocess.run(cmd, check=check, **kw)


@contextlib.contextmanager
def atomic_writer(
        fname: str,
        mode: str = "w+b",
        chmod: Optional[int] = 0o664,
        sync: bool = True,
        use_umask: bool = False,
        **kw):
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


@contextlib.contextmanager
def pause_automounting(pathname: str):
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


def is_on_rotational(pathname: str) -> Optional[bool]:
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
        with open(rotfile, "rt") as fd:
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
    Create a temporarya directory where all packages found in path are
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


def edit_yaml(buf: str, path: str) -> Optional[str]:
    """
    Open an editor on buf and validate its result as YAML.

    path is only used for error messages.

    Return the edited text contents.

    Return None if editing did not change the contents.
    """
    ERROR_MARKER = "# ----- this line and everything below it will be ignored -----"
    editor = os.environ.get("EDITOR", "sensible-editor")

    current = buf
    error = None

    while True:
        with tempfile.NamedTemporaryFile(
                mode="wt",
                suffix=".yaml") as tf:
            # Write out the current buffer
            tf.write(current)
            # Write lines to communicate a parser error, if needed
            if error is not None:
                print(ERROR_MARKER, file=tf)
                print("#", file=tf)
                print(f"# Original file: {path}", file=tf)
                print("#", file=tf)
                print("# Quit with no modifications to restore the original.", file=tf)
                print("#", file=tf)
                print("# Error:", file=tf)
                for line in error.splitlines():
                    print(f"# {line}", file=tf)
                error = None
            tf.flush()

            # Run the editor on it
            subprocess.run([editor, tf.name], check=True)

            # Reopen by name in case the editor did not write on the same
            # inode
            with open(tf.name, "rt") as fd:
                lines = []
                for line in fd:
                    if line.startswith(ERROR_MARKER):
                        break
                    lines.append(line)
                edited = "".join(lines)

            if edited == current:
                return None

            try:
                with io.StringIO(edited) as buf:
                    yaml.load(buf, Loader=yaml.CLoader)
                return edited
            except Exception as e:
                error = str(e)
            current = edited
