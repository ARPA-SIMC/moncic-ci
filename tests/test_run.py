from __future__ import annotations
import unittest
import os
from moncic.unittest import privs, TEST_CHROOTS
from moncic.machine import Machine


class TestRun(unittest.TestCase):
    def test_run_command(self):
        for name in TEST_CHROOTS:
            with self.subTest(name=name):
                image = os.path.join("images", name)
                with privs.root():
                    with Machine(image) as machine:
                        machine.run(["/usr/bin/true"])
