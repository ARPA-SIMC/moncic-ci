# Moncic CI

Continuous integration tool, and development helper.

Moncic CI manages lightweight containers for use with Continuous Integration or
to help developers target platforms different from the development machine.


## Container requirements

The current implementation of containers uses systemd-nspawn as a backend, and
a btrfs filesystem for storage and fast snapshotting.

Note that only the directory used by Moncic CI to store OS images needs to be
on a btrfs filesystem.
