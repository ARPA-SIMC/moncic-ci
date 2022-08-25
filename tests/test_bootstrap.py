from __future__ import annotations
import os
import re
import unittest

from moncic.unittest import DistroTestMixin, make_moncic
from moncic.container import UserConfig


class BootstrapTestMixin(DistroTestMixin):
    def test_tarball(self):
        with self.config() as mconfig:
            # Create a mock tarball for fedora34
            tar_path = os.path.join(mconfig.imagedir, "fedora34.tar.gz")
            with open(tar_path, "wb"):
                pass

            with open(os.path.join(mconfig.imagedir, "test.yaml"), "wt") as fd:
                print("distro: fedora34", file=fd)

            with self.mock() as run_log:
                moncic = make_moncic(mconfig)
                with moncic.session() as session:
                    images = session.images()
                    images.bootstrap_system("test")
                    with images.system("test") as system:
                        path = system.path

        if self.DEFAULT_FILESYSTEM_TYPE == "btrfs":
            run_log.assertPopFirst(f'btrfs -q subvolume create {path}.new')
        run_log.assertPopFirst(f"tar -C {path}.new -axf {tar_path}")
        run_log.assertLogEmpty()

    def test_forward_user(self):
        user = UserConfig.from_sudoer()

        with self.config() as mconfig:
            with open(os.path.join(mconfig.imagedir, "test.yaml"), "wt") as fd:
                print("distro: fedora34", file=fd)
                print(f"forward_user: {user.user_name}", file=fd)

            with self.mock() as run_log:
                moncic = make_moncic(mconfig)
                with moncic.session() as session:
                    images = session.images()
                    with images.maintenance_system("test") as system:
                        system.update()

        run_log.assertPopFirst(f"forward_user:{user.user_name},{user.user_id},{user.group_name},{user.group_id}")
        run_log.assertPopFirst("/usr/bin/dnf upgrade -q -y")
        run_log.assertPopFirst('/usr/bin/dnf install -q -y iproute2 bash rootfiles dbus dnf')
        run_log.assertPopFirst("cachedir_tag:")
        run_log.assertLogEmpty()

    def test_snapshot_bootstrap(self):
        with self.config() as mconfig:
            parent_dir = os.path.join(mconfig.imagedir, "rocky8")
            # Pretend that rocky8 has already been bootstrapped
            os.mkdir(parent_dir)

            with open(os.path.join(mconfig.imagedir, "test.yaml"), "wt") as fd:
                print("extends: rocky8", file=fd)

            with self.mock() as run_log:
                moncic = make_moncic(mconfig)
                with moncic.session() as session:
                    images = session.images()
                    images.bootstrap_system("test")
                    with images.system("test") as system:
                        path = system.path

        if self.DEFAULT_FILESYSTEM_TYPE == "btrfs":
            run_log.assertPopFirst(f'btrfs -q subvolume snapshot {parent_dir} {path}.new')
        else:
            run_log.assertPopFirst(f'cp --reflink=auto -a {parent_dir} {path}.new')
        run_log.assertLogEmpty()

    def test_snapshot_update(self):
        with self.config() as mconfig:
            base_dir = os.path.join(mconfig.imagedir, "base")
            # Pretend that rocky8 has already been bootstrapped
            with open(os.path.join(mconfig.imagedir, "base.yaml"), "wt") as fd:
                print("extends: rocky8", file=fd)
                print("maintscript: echo base", file=fd)
            os.mkdir(base_dir)

            test_dir = os.path.join(mconfig.imagedir, "test")
            with open(os.path.join(mconfig.imagedir, "test.yaml"), "wt") as fd:
                print("extends: base", file=fd)
                print("maintscript: echo test", file=fd)
            os.mkdir(test_dir)

            with self.mock() as run_log:
                moncic = make_moncic(mconfig)
                with moncic.session() as session:
                    images = session.images()
                    with images.maintenance_system("test") as system:
                        system.update()
                        path = system.path[:-4]

        if self.DEFAULT_FILESYSTEM_TYPE == "btrfs":
            run_log.assertPopFirst(f'btrfs -q subvolume snapshot {path} {path}.new')
        run_log.assertPopFirst("/usr/bin/dnf upgrade -q -y")
        run_log.assertPopFirst('/usr/bin/dnf install -q -y iproute2 bash rootfiles dbus dnf')
        run_log.assertPopFirst("script:#!/bin/sh\necho base")
        run_log.assertPopFirst("script:#!/bin/sh\necho test")
        run_log.assertPopFirst("cachedir_tag:")
        if self.DEFAULT_FILESYSTEM_TYPE == "btrfs":
            run_log.assertPopFirst(f"'<replace>' {path}.new {path}")
        run_log.assertLogEmpty()

    def test_compression(self):
        with self.config() as mconfig:
            with open(os.path.join(mconfig.imagedir, "test.yaml"), "wt") as fd:
                print("distro: fedora34", file=fd)
                print("compression: zstd:9", file=fd)

            with self.mock() as run_log:
                moncic = make_moncic(mconfig)
                with moncic.session() as session:
                    images = session.images()
                    images.bootstrap_system("test")
                    with images.system("test") as system:
                        path = system.path

        if self.DEFAULT_FILESYSTEM_TYPE == "btrfs":
            run_log.assertPopFirst(f'btrfs -q subvolume create {path}.new')
            run_log.assertPopFirst(f'btrfs -q property set {path}.new compression zstd:9')
        run_log.assertPopFirst(re.compile('/usr/bin/dnf -c .+'))
        run_log.assertPopFirst("/usr/bin/rpmdb --rebuilddb")
        run_log.assertLogEmpty()


class BtrfsBootstrapTest(BootstrapTestMixin, unittest.TestCase):
    DEFAULT_FILESYSTEM_TYPE = "btrfs"


class PlainBootstrapTest(BootstrapTestMixin, unittest.TestCase):
    DEFAULT_FILESYSTEM_TYPE = "tmpfs"
