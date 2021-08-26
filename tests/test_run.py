from __future__ import annotations
import unittest
import sys
import os
from moncic.unittest import privs, TEST_CHROOTS
from moncic.distro import Distro


class RunTestCase:
    def test_true(self):
        image = os.path.join("images", self.distro_name)
        distro = Distro.from_ostree(image)
        with privs.root():
            with distro.machine(image) as machine:
                machine.run(["/usr/bin/true"])

    def test_sleep(self):
        image = os.path.join("images", self.distro_name)
        distro = Distro.from_ostree(image)
        with privs.root():
            with distro.machine(image) as machine:
                machine.run(["/usr/bin/sleep", "0.1"])

    def test_stdout(self):
        image = os.path.join("images", self.distro_name)
        distro = Distro.from_ostree(image)
        with privs.root():
            with distro.machine(image) as machine:
                res = machine.run(["/usr/bin/echo", "test"])
                self.assertEqual(res["stdout"], b"test\n")
                self.assertEqual(res["stderr"], b"")

    def test_env(self):
        image = os.path.join("images", self.distro_name)
        distro = Distro.from_ostree(image)
        with privs.root():
            with distro.machine(image) as machine:
                res = machine.run(["/bin/sh", "-c", "echo $HOME"])
                self.assertEqual(res["stdout"], b"/root\n")
                self.assertEqual(res["stderr"], b"")


# Create an instance of RunTestCase for each distribution in TEST_CHROOTS.
# The test cases will be named Test$DISTRO. For example:
#   TestCentos7, TestCentos8, TestFedora32, TestFedora34
this_module = sys.modules[__name__]
for distro_name in TEST_CHROOTS:
    cls_name = "Test" + distro_name.capitalize()
    test_case = type(cls_name, (RunTestCase, unittest.TestCase), {"distro_name": distro_name})
    test_case.__module__ = __name__
    setattr(this_module, cls_name, test_case)
