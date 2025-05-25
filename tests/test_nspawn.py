# import re
from pathlib import Path
from typing import override

# from moncic.runner import UserConfig
# from moncic.image import BootstrappableImage, RunnableImage
from moncic.nspawn.images import BtrfsImages, NspawnImages, PlainImages
from moncic.unittest import MoncicTestCase


class NspawnTests(MoncicTestCase):
    images_class: type[NspawnImages]

    @override
    def setUp(self) -> None:
        super().setUp()
        self.imageconfdir = self.workdir()
        self.mconfig = self.config()
        self.mconfig.imageconfdirs.append(self.imageconfdir)
        assert self.mconfig.imagedir is not None
        self.imagedir: Path = self.mconfig.imagedir
        self.image_yaml = self.imageconfdir / "test.yaml"
        self.image_yaml.write_text("distro: fedora34\n")
        self.session = self.enterContext(self.mock_session(self.moncic(self.mconfig), images_class=self.images_class))
        self.images = self.images_class(self.session, self.imagedir)

    def _mock_bootstrap(self) -> None:
        root = self.imagedir / "test"
        root.mkdir()
        os_release = root / "etc" / "os-release"
        os_release.parent.mkdir(parents=True)
        os_release.write_text(
            """
ID=fedora
VERSION_ID=34
"""
        )


# TODO: refactor these test
#    def test_snapshot_bootstrap(self):
#        parent_dir = self.mconfig.imagedir / "rocky8"
#        # Pretend that rocky8 has already been bootstrapped
#        parent_dir.mkdir()
#
#        (self.mconfig.imagedir / "test.yaml").write_text("extends: rocky8\n")
#
#        with self.mock_session(self.moncic(self.mconfig)) as session:
#            images = session.images
#            images.bootstrap_system("test")
#            with images.system("test") as system:
#                path = system.path
#            run_log = session.run_log
#
#        if self.DEFAULT_FILESYSTEM_TYPE == "btrfs":
#            run_log.assertPopFirst(f"btrfs -q subvolume snapshot {parent_dir} {path}.new")
#        else:
#            run_log.assertPopFirst(f"cp --reflink=auto -a {parent_dir} {path}.new")
#        run_log.assertLogEmpty()
#
#    def test_snapshot_update(self):
#        base_dir = self.mconfig.imagedir / "base"
#        # Pretend that rocky8 has already been bootstrapped
#        base_dir.mkdir()
#        (self.mconfig.imagedir / "base.yaml").write_text(
#            """
# extends: rocky8
# maintscript: echo base
# """
#        )
#
#        test_dir = self.mconfig.imagedir / "test"
#        (self.mconfig.imagedir / "test.yaml").write_text(
#            """
# extends: base
# maintscript: echo test
# """
#        )
#        test_dir.mkdir()
#
#        with self.mock_session(self.moncic(self.mconfig)) as session:
#            images = session.images
#            image = images.image("test")
#            with image.maintenance_system() as system:
#                system.update()
#                path = system.path[:-4]
#        run_log = session.run_log
#
#        if self.DEFAULT_FILESYSTEM_TYPE == "btrfs":
#            run_log.assertPopFirst(f"btrfs -q subvolume snapshot {path} {path}.new")
#        run_log.assertPopFirst("/usr/bin/systemctl mask --now systemd-resolved")
#        run_log.assertPopFirst("/usr/bin/dnf updateinfo -q -y")
#        run_log.assertPopFirst("/usr/bin/dnf upgrade -q -y")
#        run_log.assertPopFirst("/usr/bin/dnf install -q -y bash dbus rootfiles iproute dnf")
#        run_log.assertPopFirst("script:#!/bin/sh\necho base")
#        run_log.assertPopFirst("script:#!/bin/sh\necho test")
#        run_log.assertPopFirst("cachedir_tag:")
#        if self.DEFAULT_FILESYSTEM_TYPE == "btrfs":
#            run_log.assertPopFirst(f"'<replace>' {path}.new {path}")
#        run_log.assertLogEmpty()
#
#    def test_compression(self):
#        (self.mconfig.imagedir / "test.yaml").write_text(
#            """
# distro: fedora34
# compression: zstd:9
# """
#        )
#
#        with self.mock_session(self.moncic(self.mconfig)) as session:
#            images = session.images
#            images.bootstrap_system("test")
#            with images.system("test") as system:
#                path = system.path
#            run_log = session.run_log
#
#        if self.DEFAULT_FILESYSTEM_TYPE == "btrfs":
#            run_log.assertPopFirst(f"btrfs -q subvolume create {path}.new")
#            run_log.assertPopFirst(f"btrfs -q property set {path}.new compression zstd:9")
#        run_log.assertPopFirst(re.compile("/usr/bin/dnf -c .+"))
#        run_log.assertPopFirst("/usr/bin/rpmdb --rebuilddb")
#        run_log.assertLogEmpty()


class BtrfsNspawnTests(NspawnTests):
    images_class: type[NspawnImages] = BtrfsImages
    DEFAULT_FILESYSTEM_TYPE = "btrfs"


class PlainNspawnTests(NspawnTests):
    images_class: type[NspawnImages] = PlainImages
    DEFAULT_FILESYSTEM_TYPE = "tmpfs"


del NspawnTests
