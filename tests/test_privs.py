import os
import unittest
from typing import ClassVar

from moncic.unittest import SudoTestSuite


class TestPrivs(unittest.TestCase):
    privs: ClassVar[SudoTestSuite]

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.privs = SudoTestSuite()

    def assertUnprivileged(self):
        uid, euid, suid = os.getresuid()
        self.assertEqual(uid, self.privs.user_uid)
        self.assertEqual(euid, self.privs.user_uid)
        self.assertEqual(suid, 0)

        gid, egid, sgid = os.getresgid()
        self.assertEqual(gid, self.privs.user_gid)
        self.assertEqual(egid, self.privs.user_gid)
        self.assertEqual(sgid, 0)

    def assertPrivileged(self):
        uid, euid, suid = os.getresuid()
        self.assertEqual(uid, 0)
        self.assertEqual(euid, 0)
        self.assertEqual(suid, self.privs.user_uid)

        gid, egid, sgid = os.getresgid()
        self.assertEqual(gid, 0)
        self.assertEqual(egid, 0)
        self.assertEqual(sgid, self.privs.user_gid)

    def test_default(self):
        self.privs.needs_sudo()

        self.assertTrue(self.privs.dropped)
        self.assertUnprivileged()

    def test_root(self):
        self.privs.needs_sudo()

        self.assertTrue(self.privs.dropped)
        self.assertUnprivileged()
        with self.privs.root():
            self.assertFalse(self.privs.dropped)
            self.assertPrivileged()
        self.assertTrue(self.privs.dropped)
        self.assertUnprivileged()
