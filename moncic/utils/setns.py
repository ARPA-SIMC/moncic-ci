import ctypes
import os

libc = None

# From /usr/include/linux/sched.h
CLONE_VM = 0x00000100  # set if VM shared between processes
CLONE_FS = 0x00000200  # set if fs info shared between processes
CLONE_FILES = 0x00000400  # set if open files shared between processes
CLONE_SIGHAND = 0x00000800  # set if signal handlers and blocked signals shared
CLONE_PIDFD = 0x00001000  # set if a pidfd should be placed in parent
CLONE_PTRACE = 0x00002000  # set if we want to let tracing continue on the child too
CLONE_VFORK = 0x00004000  # set if the parent wants the child to wake it up on mm_release
CLONE_PARENT = 0x00008000  # set if we want to have the same parent as the cloner
CLONE_THREAD = 0x00010000  # Same thread group?
CLONE_NEWNS = 0x00020000  # New mount namespace group
CLONE_SYSVSEM = 0x00040000  # share system V SEM_UNDO semantics
CLONE_SETTLS = 0x00080000  # create a new TLS for the child
CLONE_PARENT_SETTID = 0x00100000  # set the TID in the parent
CLONE_CHILD_CLEARTID = 0x00200000  # clear the TID in the child
CLONE_DETACHED = 0x00400000  # Unused, ignored
CLONE_UNTRACED = 0x00800000  # set if the tracing process can't force CLONE_PTRACE on this clone
CLONE_CHILD_SETTID = 0x01000000  # set the TID in the child
CLONE_NEWCGROUP = 0x02000000  # New cgroup namespace
CLONE_NEWUTS = 0x04000000  # New utsname namespace
CLONE_NEWIPC = 0x08000000  # New ipc namespace
CLONE_NEWUSER = 0x10000000  # New user namespace
CLONE_NEWPID = 0x20000000  # New pid namespace
CLONE_NEWNET = 0x40000000  # New network namespace
CLONE_IO = 0x80000000  # Clone io context
CLONE_CLEAR_SIGHAND = 0x100000000  # Clear any signal handler and reset to SIG_DFL.
CLONE_INTO_CGROUP = 0x200000000  # Clone into a specific cgroup given the right permissions.
CLONE_NEWTIME = 0x00000080  # New time namespace


def setns(fd: int, flags: int = 0) -> None:
    """
    Wrapper for the libc setns syscall
    """
    global libc
    if libc is None:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
    if libc.setns(fd, flags) == -1:
        errno = ctypes.get_errno()
        raise OSError(ctypes.get_errno(), os.strerror(errno))


def unshare(flags: int = 0) -> None:
    """
    Wrapper for the libc unshare syscall
    """
    global libc
    if libc is None:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
    if libc.unshare(flags) == -1:
        errno = ctypes.get_errno()
        raise OSError(ctypes.get_errno(), os.strerror(errno))


def nsenter(
    leader_pid: int,
    cgroup: bool = True,
    ipc: bool = True,
    net: bool = True,
    mnt: bool = True,
    pid: bool = True,
    time: bool = True,
    user: bool = True,
    uts: bool = True,
) -> None:
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
