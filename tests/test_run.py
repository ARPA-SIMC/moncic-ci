from __future__ import annotations
import unittest
import uuid
import os
from moncic.unittest import privs, TEST_CHROOTS
from moncic.machine import Machine


class TestRun(unittest.TestCase):
    def test_run_command(self):
        for name in TEST_CHROOTS:
            with self.subTest(name=name):
                run_id = str(uuid.uuid4())
                image = os.path.join("images", name)
                with privs.root():
                    with Machine(run_id, image) as machine:
                        machine.run(["/usr/bin/true"])
