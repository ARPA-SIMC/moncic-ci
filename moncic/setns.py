import ctypes
import os

libc = None


def setns(fd: int, flags: int = 0) -> int:
    """
    Wrapper for the libc setns syscall
    """
    global libc
    if libc is None:
        libc = ctypes.CDLL('libc.so.6')
    return libc.setns(fd, flags)


def nsenter(leader_pid: int, cgroup=True, ipc=True, net=True, mnt=True, pid=True, time=True, user=True, uts=True):
    """
    Move this process to the namespace(s) of the given process.

    This is similar to ``nsenter --target``
    """
    if cgroup:
        try:
            fd = os.open(f"/proc/{leader_pid}/ns/cgroup", os.O_RDONLY)
            setns(fd, 0)
        finally:
            os.close(fd)

    if ipc:
        try:
            fd = os.open(f"/proc/{leader_pid}/ns/ipc", os.O_RDONLY)
            setns(fd, 0)
        finally:
            os.close(fd)

    if net:
        try:
            fd = os.open(f"/proc/{leader_pid}/ns/net", os.O_RDONLY)
            setns(fd, 0)
        finally:
            os.close(fd)

    if pid:
        try:
            fd = os.open(f"/proc/{leader_pid}/ns/pid", os.O_RDONLY)
            setns(fd, 0)
        finally:
            os.close(fd)

    if time:
        try:
            fd = os.open(f"/proc/{leader_pid}/ns/time", os.O_RDONLY)
            setns(fd, 0)
        finally:
            os.close(fd)

    if user:
        try:
            fd = os.open(f"/proc/{leader_pid}/ns/user", os.O_RDONLY)
            setns(fd, 0)
        finally:
            os.close(fd)

    if uts:
        try:
            fd = os.open(f"/proc/{leader_pid}/ns/uts", os.O_RDONLY)
            setns(fd, 0)
        finally:
            os.close(fd)

    if mnt:
        try:
            fd = os.open(f"/proc/{leader_pid}/ns/mnt", os.O_RDONLY)
            setns(fd, 0)
        finally:
            os.close(fd)
