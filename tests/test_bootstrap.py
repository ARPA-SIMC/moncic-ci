import os
import re

from moncic.runner import UserConfig
from moncic.unittest import MoncicTestCase


class BootstrapTests(MoncicTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.mconfig = self.config()

    def test_tarball(self):
        # Create a mock tarball for fedora34
        tar_path = os.path.join(self.mconfig.imagedir, "fedora34.tar.gz")
        with open(tar_path, "wb"):
            pass

        (self.mconfig.imagedir / "test.yaml").write_text("distro: fedora34\n")

        with self.mock_session(self.moncic(self.mconfig)) as session:
            images = session.images
            images.bootstrap_system("test")
            with images.system("test") as system:
                path = system.path

            run_log = session.run_log
        if self.DEFAULT_FILESYSTEM_TYPE == "btrfs":
            run_log.assertPopFirst(f"btrfs -q subvolume create {path}.new")
        run_log.assertPopFirst(f"tar -C {path}.new -axf {tar_path}")
        run_log.assertLogEmpty()

    def test_forward_user(self):
        user = UserConfig.from_sudoer()

        (self.mconfig.imagedir / "test.yaml").write_text(
            f"""
distro: fedora34
forward_user: {user.user_name}
"""
        )

        with self.mock_session(self.moncic(self.mconfig)) as session:
            images = session.images
            image = images.image("test")
            with image.maintenance_system() as system:
                system.update()
            run_log = session.run_log

        run_log.assertPopFirst(f"forward_user:{user.user_name},{user.user_id},{user.group_name},{user.group_id}")
        run_log.assertPopFirst("/usr/bin/systemctl mask --now systemd-resolved")
        run_log.assertPopFirst("/usr/bin/dnf updateinfo -q -y")
        run_log.assertPopFirst("/usr/bin/dnf upgrade -q -y")
        run_log.assertPopFirst("/usr/bin/dnf install -q -y bash dbus rootfiles iproute dnf")
        run_log.assertPopFirst("cachedir_tag:")
        run_log.assertLogEmpty()

    def test_snapshot_bootstrap(self):
        parent_dir = self.mconfig.imagedir / "rocky8"
        # Pretend that rocky8 has already been bootstrapped
        parent_dir.mkdir()

        (self.mconfig.imagedir / "test.yaml").write_text("extends: rocky8\n")

        with self.mock_session(self.moncic(self.mconfig)) as session:
            images = session.images
            images.bootstrap_system("test")
            with images.system("test") as system:
                path = system.path
            run_log = session.run_log

        if self.DEFAULT_FILESYSTEM_TYPE == "btrfs":
            run_log.assertPopFirst(f"btrfs -q subvolume snapshot {parent_dir} {path}.new")
        else:
            run_log.assertPopFirst(f"cp --reflink=auto -a {parent_dir} {path}.new")
        run_log.assertLogEmpty()

    def test_snapshot_update(self):
        base_dir = self.mconfig.imagedir / "base"
        # Pretend that rocky8 has already been bootstrapped
        base_dir.mkdir()
        (self.mconfig.imagedir / "base.yaml").write_text(
            """
extends: rocky8
maintscript: echo base
"""
        )

        test_dir = self.mconfig.imagedir / "test"
        (self.mconfig.imagedir / "test.yaml").write_text(
            """
extends: base
maintscript: echo test
"""
        )
        test_dir.mkdir()

        with self.mock_session(self.moncic(self.mconfig)) as session:
            images = session.images
            image = images.image("test")
            with image.maintenance_system() as system:
                system.update()
                path = system.path[:-4]
        run_log = session.run_log

        if self.DEFAULT_FILESYSTEM_TYPE == "btrfs":
            run_log.assertPopFirst(f"btrfs -q subvolume snapshot {path} {path}.new")
        run_log.assertPopFirst("/usr/bin/systemctl mask --now systemd-resolved")
        run_log.assertPopFirst("/usr/bin/dnf updateinfo -q -y")
        run_log.assertPopFirst("/usr/bin/dnf upgrade -q -y")
        run_log.assertPopFirst("/usr/bin/dnf install -q -y bash dbus rootfiles iproute dnf")
        run_log.assertPopFirst("script:#!/bin/sh\necho base")
        run_log.assertPopFirst("script:#!/bin/sh\necho test")
        run_log.assertPopFirst("cachedir_tag:")
        if self.DEFAULT_FILESYSTEM_TYPE == "btrfs":
            run_log.assertPopFirst(f"'<replace>' {path}.new {path}")
        run_log.assertLogEmpty()

    def test_packages_rpm(self):
        base_dir = self.mconfig.imagedir / "test"
        # Pretend that the distro has already been bootstrapped
        base_dir.mkdir()
        (self.mconfig.imagedir / "test.yaml").write_text(
            """
distro: rocky8
packages: [vim, mc]
maintscript: echo base
"""
        )

        with self.mock_session(self.moncic(self.mconfig)) as session:
            images = session.images
            image = images.image("test")
            with image.maintenance_system() as system:
                system.update()
                path = system.path[:-4]
            run_log = session.run_log

        if self.DEFAULT_FILESYSTEM_TYPE == "btrfs":
            run_log.assertPopFirst(f"btrfs -q subvolume snapshot {path} {path}.new")
        run_log.assertPopFirst("/usr/bin/systemctl mask --now systemd-resolved")
        run_log.assertPopFirst("/usr/bin/dnf updateinfo -q -y")
        run_log.assertPopFirst("/usr/bin/dnf upgrade -q -y")
        run_log.assertPopFirst("/usr/bin/dnf install -q -y bash dbus rootfiles iproute dnf vim mc")
        run_log.assertPopFirst("script:#!/bin/sh\necho base")
        run_log.assertPopFirst("cachedir_tag:")
        if self.DEFAULT_FILESYSTEM_TYPE == "btrfs":
            run_log.assertPopFirst(f"'<replace>' {path}.new {path}")
        run_log.assertLogEmpty()

    def test_packages_deb(self):
        base_dir = self.mconfig.imagedir / "test"
        # Pretend that the distro has already been bootstrapped
        base_dir.mkdir()
        (self.mconfig.imagedir / "test.yaml").write_text(
            """
distro: bookworm
packages: [vim, mc]
maintscript: echo base
"""
        )
        with self.mock_session(self.moncic(self.mconfig)) as session:
            images = session.images
            image = images.image("test")
            with image.maintenance_system() as system:
                system.update()
                path = system.path[:-4]
            run_log = session.run_log

        if self.DEFAULT_FILESYSTEM_TYPE == "btrfs":
            run_log.assertPopFirst(f"btrfs -q subvolume snapshot {path} {path}.new")
        apt_prefix = "/usr/bin/apt-get --assume-yes --quiet --show-upgraded '-o Dpkg::Options::=\"--force-confnew\"' "
        run_log.assertPopFirst("/usr/bin/apt-get update")
        run_log.assertPopFirst(apt_prefix + "full-upgrade")
        run_log.assertPopFirst(apt_prefix + "satisfy bash dbus systemd apt-utils eatmydata iproute2 vim mc")
        run_log.assertPopFirst("script:#!/bin/sh\necho base")
        run_log.assertPopFirst("cachedir_tag:")
        if self.DEFAULT_FILESYSTEM_TYPE == "btrfs":
            run_log.assertPopFirst(f"'<replace>' {path}.new {path}")
        run_log.assertLogEmpty()

    def test_compression(self):
        (self.mconfig.imagedir / "test.yaml").write_text(
            """
distro: fedora34
compression: zstd:9
"""
        )

        with self.mock_session(self.moncic(self.mconfig)) as session:
            images = session.images
            images.bootstrap_system("test")
            with images.system("test") as system:
                path = system.path
            run_log = session.run_log

        if self.DEFAULT_FILESYSTEM_TYPE == "btrfs":
            run_log.assertPopFirst(f"btrfs -q subvolume create {path}.new")
            run_log.assertPopFirst(f"btrfs -q property set {path}.new compression zstd:9")
        run_log.assertPopFirst(re.compile("/usr/bin/dnf -c .+"))
        run_log.assertPopFirst("/usr/bin/rpmdb --rebuilddb")
        run_log.assertLogEmpty()


class BtrfsBootstrapTest(BootstrapTests):
    DEFAULT_FILESYSTEM_TYPE = "btrfs"


class PlainBootstrapTest(BootstrapTests):
    DEFAULT_FILESYSTEM_TYPE = "tmpfs"


del BootstrapTests
