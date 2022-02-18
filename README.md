# Moncic CI

Continuous integration tool, and development helper.

Moncic CI manages lightweight containers for use with Continuous Integration or
to help developers target platforms different from the development machine.


## Container requirements

The current implementation of containers uses systemd-nspawn as a backend, and
a btrfs filesystem for storage and fast snapshotting.

Note that only the directory used by Moncic CI to store OS images needs to be
on a btrfs filesystem.


## OS image configuration

A name can provide enough information for Moncic CI to identify a basic OS
image without customization: see the shortcut names in the output of TODO.

For anything more than a standard basic OS image, one can define images by
placing a `.yaml` configuration files in the image directory. For example:

```yaml
distro: fedora34
maintscript: |
    dnf install git
```

Keywords currently supported are:

* `distro`: what distribution should be bootstrapped to create the image
* `maintscript`: script run after bootstrap and during regular maintenance, to
  customize the OS image. If it does not start with a shebang (`#!`),
  `#!/bin/sh` is automatically prepended.

`.yaml` files in image directories have precedence over plain distribution
names: one can for example define a `centos8.yaml` image that contains `distro:
rocky8`.
