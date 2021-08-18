from __future__ import annotations
import unittest
import sys
import os
from moncic.unittest import privs, TEST_CHROOTS
from moncic.machine import Machine


class RunTestCase:
    def test_run_command(self):
        image = os.path.join("images", self.distro_name)
        with privs.root():
            with Machine(image) as machine:
                machine.run(["/usr/bin/true"])


# Create an instance of RunTestCase for each distribution in TEST_CHROOTS.
# The test cases will be named Test$DISTRO. For example:
#   TestCentos7, TestCentos8, TestFedora32, TestFedora34
this_module = sys.modules[__name__]
for distro_name in TEST_CHROOTS:
    cls_name = "Test" + distro_name.capitalize()
    test_case = type(cls_name, (RunTestCase, unittest.TestCase), {"distro_name": distro_name})
    test_case.__module__ = __name__
    setattr(this_module, cls_name, test_case)
