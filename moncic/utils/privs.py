import contextlib
import os
import pwd
import sys
from typing import Generator

from ..exceptions import Fail


class ProcessPrivs:
    """
    Drop root privileges and regain them only when needed
    """

    def __init__(self) -> None:
        self.orig_uid, self.orig_euid, self.orig_suid = os.getresuid()
        self.orig_gid, self.orig_egid, self.orig_sgid = os.getresgid()

        self.have_sudo = "SUDO_UID" in os.environ

        if self.have_sudo:
            self.user_uid = int(os.environ["SUDO_UID"])
            self.user_gid = int(os.environ["SUDO_GID"])
        else:
            self.user_uid = self.orig_uid
            self.user_gid = self.orig_gid

        self.dropped = not self.have_sudo
        self.auto_sudo = False

    def can_regain(self) -> bool:
        return self.have_sudo or self.auto_sudo

    def update_env(self) -> None:
        uid = os.getuid()
        if uid == 0:
            os.environ["HOME"] = "/root"
            os.environ["USER"] = "root"
        else:
            pw = pwd.getpwuid(uid)
            os.environ["HOME"] = pw.pw_dir
            os.environ["USER"] = pw.pw_name

    def needs_sudo(self) -> None:
        if not self.have_sudo:
            if self.auto_sudo:
                os.execvp("sudo", ["sudo"] + sys.argv)
            else:
                raise Fail("This command needs sudo to run")

    def drop(self) -> None:
        """
        Drop root privileges
        """
        if self.dropped:
            return
        os.setresgid(self.user_gid, self.user_gid, 0)
        os.setresuid(self.user_uid, self.user_uid, 0)
        self.dropped = True
        self.update_env()

    def regain(self) -> None:
        """
        Regain root privileges
        """
        if not self.dropped:
            return
        self.needs_sudo()
        os.setresuid(self.orig_suid, self.orig_suid, self.user_uid)
        os.setresgid(self.orig_sgid, self.orig_sgid, self.user_gid)
        self.dropped = False
        self.update_env()

    @contextlib.contextmanager
    def root(self) -> Generator[None, None, None]:
        """
        Regain root privileges for the duration of this context manager
        """
        if not self.dropped:
            yield
        else:
            self.regain()
            try:
                yield
            finally:
                self.drop()

    @contextlib.contextmanager
    def user(self) -> Generator[None, None, None]:
        """
        Drop root privileges for the duration of this context manager
        """
        if self.dropped:
            yield
        else:
            self.drop()
            try:
                yield
            finally:
                self.regain()
