from __future__ import annotations

import contextlib
import logging
import os
import unittest

from moncic.unittest import make_moncic, privs

# Use this image, if it exists, as a base for maintenance tests
# It can be any Linux distribution, and it will be snapshotted for the tests
base_image_name = "rocky8"
test_image_name = "moncic-ci-tests"


class TestMaintenance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cls_exit_stack = contextlib.ExitStack()
        cls.moncic = make_moncic()
        cls.session = cls.cls_exit_stack.enter_context(cls.moncic.session())
        cls.images = cls.session.images
        cls.test_image_config_file = os.path.join(cls.images.imagedir, test_image_name) + ".yaml"
        # Bootstrap a snapshot of base_image_name to use as our playground
        with privs.root():
            if base_image_name not in cls.images.list_images():
                raise unittest.SkipTest(f"Image {base_image_name} not available")
            with open(cls.test_image_config_file, "wt") as fd:
                fd.write(f"""
extends: {base_image_name}
maintscript: |
    # Prevent the default system update
    /bin/true
""")
            cls.images.bootstrap_system(test_image_name)

    @classmethod
    def tearDownClass(cls):
        with privs.root():
            cls.images.remove_system(test_image_name)
            try:
                os.unlink(cls.test_image_config_file)
            except FileNotFoundError:
                pass
        cls.cls_exit_stack.close()
        cls.images = None
        cls.moncic = None
        cls.cls_exit_stack = None
        super().tearDownClass()

    def test_transactional_update_succeeded(self):
        with privs.root():
            self.assertFalse(os.path.exists(os.path.join(self.images.imagedir, test_image_name, "root", "token")))

            with self.images.maintenance_system(test_image_name) as system:
                # Check that we are working on a temporary snapshot
                self.assertEqual(os.path.basename(system.path), f"{test_image_name}.new")

                with system.create_container() as container:
                    def test_function():
                        with open("/root/token", "wt") as out:
                            out.write("test_transactional_updates")
                        return ("result", 123)

                    self.assertEqual(
                            container.run_callable(test_function),
                            ("result", 123))

                # The file has been written in a persistent way
                self.assertTrue(os.path.exists(os.path.join(system.path, "root", "token")))

                # But not in the original image
                self.assertFalse(os.path.exists(os.path.join(self.images.imagedir, test_image_name, "root", "token")))

            # Exiting the maintenance transaction commits the changes
            self.assertTrue(os.path.exists(os.path.join(self.images.imagedir, test_image_name, "root", "token")))

            # The temporary snapshot has been deleted
            self.assertFalse(os.path.exists(os.path.join(self.images.imagedir, test_image_name) + ".new"))
            self.assertFalse(os.path.exists(os.path.join(self.images.imagedir, test_image_name) + ".tmp"))

    def test_transactional_update_failed(self):
        with privs.root():
            self.assertFalse(os.path.exists(os.path.join(self.images.imagedir, test_image_name, "root", "token")))

            with self.assertRaises(RuntimeError):
                with self.assertLogs(level=logging.WARNING):
                    with self.images.maintenance_system(test_image_name) as system:
                        # Check that we are working on a temporary snapshot
                        self.assertEqual(os.path.basename(system.path), f"{test_image_name}.new")

                        with system.create_container() as container:
                            def test_function():
                                with open("/root/token", "wt") as out:
                                    out.write("test_transactional_updates")
                                raise RuntimeError("expected error")

                            container.run_callable(test_function)

            # Exiting the maintenance transaction rolls back the changes
            self.assertFalse(os.path.exists(os.path.join(self.images.imagedir, test_image_name, "root", "token")))

            # The temporary snapshot has been deleted
            self.assertFalse(os.path.exists(os.path.join(self.images.imagedir, test_image_name) + ".new"))
            self.assertFalse(os.path.exists(os.path.join(self.images.imagedir, test_image_name) + ".tmp"))
