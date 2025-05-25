from __future__ import annotations

import logging
import os
from typing import Never, override

from moncic import context
from moncic.image import BootstrappableImage, RunnableImage
from moncic.unittest import MoncicTestCase

# Use this image, if it exists, as a base for maintenance tests
# It can be any Linux distribution, and it will be snapshotted for the tests
base_image_name = "rocky8"
test_image_name = "moncic-ci-tests"


class TestMaintenance(MoncicTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.session = self.enterContext(self.moncic().session())
        self.images = self.session.images
        assert self.session.moncic.config.imagedir is not None
        self.test_image_config_file = self.session.moncic.config.imagedir / (test_image_name + ".yaml")
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
            bootstrappable_image = self.images.image(test_image_name)
            assert isinstance(bootstrappable_image, BootstrappableImage)
            self.session.bootstrapper.bootstrap(bootstrappable_image)

    @override
    def tearDown(self) -> None:
        with context.privs.root():
            image = self.images.image(test_image_name)
            assert isinstance(image, RunnableImage)
            image.remove()
            try:
                os.unlink(self.test_image_config_file)
            except FileNotFoundError:
                pass
        del self.images
        super().tearDownClass()

    def test_transactional_update_succeeded(self) -> None:
        with context.privs.root():
            self.assertFalse(os.path.exists(os.path.join(self.images.imagedir, test_image_name, "root", "token")))

            image = self.images.image(test_image_name)
            with image.maintenance_system() as system:
                # Check that we are working on a temporary snapshot
                self.assertEqual(os.path.basename(system.path), f"{test_image_name}.new")

                with system.create_container() as container:

                    def test_function() -> tuple[str, int]:
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

    def test_transactional_update_failed(self) -> None:
        with context.privs.root():
            self.assertFalse(os.path.exists(os.path.join(self.images.imagedir, test_image_name, "root", "token")))

            with self.assertRaises(RuntimeError):
                with self.assertLogs(level=logging.WARNING):
                    image = self.images.image(test_image_name)
                    with image.maintenance_system() as system:
                        # Check that we are working on a temporary snapshot
                        self.assertEqual(os.path.basename(system.path), f"{test_image_name}.new")

                        with system.create_container() as container:

                            def test_function() -> Never:
                                with open("/root/token", "w") as out:
                                    out.write("test_transactional_updates")
                                raise RuntimeError("expected error")

                            container.run_callable(test_function)

            # Exiting the maintenance transaction rolls back the changes
            self.assertFalse(os.path.exists(os.path.join(self.images.imagedir, test_image_name, "root", "token")))

            # The temporary snapshot has been deleted
            self.assertFalse(os.path.exists(os.path.join(self.images.imagedir, test_image_name) + ".new"))
            self.assertFalse(os.path.exists(os.path.join(self.images.imagedir, test_image_name) + ".tmp"))
