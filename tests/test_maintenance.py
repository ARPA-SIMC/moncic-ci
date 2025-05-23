from __future__ import annotations

import logging
import os

from moncic import context
from moncic.unittest import MoncicTestCase

# Use this image, if it exists, as a base for maintenance tests
# It can be any Linux distribution, and it will be snapshotted for the tests
base_image_name = "rocky8"
test_image_name = "moncic-ci-tests"


class TestMaintenance(MoncicTestCase):
    def setUp(self):
        super().setUp()
        self.moncic = self.moncic()
        self.session = self.enterContext(self.moncic.session())
        self.images = self.session.images
        self.test_image_config_file = self.moncic.config.imagedir / (test_image_name + ".yaml")
        # Bootstrap a snapshot of base_image_name to use as our playground
        with context.privs.root():
            if base_image_name not in self.images.list_images():
                self.skipTest(f"Image {base_image_name} not available")
            self.test_image_config_file.write_text(
                f"""
extends: {base_image_name}
maintscript: |
    # Prevent the default system update
    /bin/true
"""
            )
            self.images.bootstrap_system(test_image_name)

    @classmethod
    def tearDownClass(cls):
        with context.privs.root():
            image = cls.images.image(test_image_name)
            image.remove()
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
        with context.privs.root():
            self.assertFalse(os.path.exists(os.path.join(self.images.imagedir, test_image_name, "root", "token")))

            image = self.images.image(test_image_name)
            with image.maintenance_system() as system:
                # Check that we are working on a temporary snapshot
                self.assertEqual(os.path.basename(system.path), f"{test_image_name}.new")

                with system.create_container() as container:

                    def test_function():
                        with open("/root/token", "w") as out:
                            out.write("test_transactional_updates")
                        return ("result", 123)

                    self.assertEqual(container.run_callable(test_function), ("result", 123))

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
        with context.privs.root():
            self.assertFalse(os.path.exists(os.path.join(self.images.imagedir, test_image_name, "root", "token")))

            with self.assertRaises(RuntimeError):
                with self.assertLogs(level=logging.WARNING):
                    image = self.images.image(test_image_name)
                    with image.maintenance_system() as system:
                        # Check that we are working on a temporary snapshot
                        self.assertEqual(os.path.basename(system.path), f"{test_image_name}.new")

                        with system.create_container() as container:

                            def test_function():
                                with open("/root/token", "w") as out:
                                    out.write("test_transactional_updates")
                                raise RuntimeError("expected error")

                            container.run_callable(test_function)

            # Exiting the maintenance transaction rolls back the changes
            self.assertFalse(os.path.exists(os.path.join(self.images.imagedir, test_image_name, "root", "token")))

            # The temporary snapshot has been deleted
            self.assertFalse(os.path.exists(os.path.join(self.images.imagedir, test_image_name) + ".new"))
            self.assertFalse(os.path.exists(os.path.join(self.images.imagedir, test_image_name) + ".tmp"))
