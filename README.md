# Moncic CI

Continuous integration tool, and development helper.

Moncic CI manages lightweight containers for use with Continuous Integration or
to help developers target platforms different from the development machine.


## Container requirements

The current implementation of containers uses systemd-nspawn as a backend, and
a btrfs filesystem for storage and fast snapshotting.

Note that only the directory used by Moncic CI to store OS images needs to be
on a btrfs filesystem.


## Distributions supported

The up to date list of supported distribution can be queried with `monci
distros`. It currently is:

```
$ monci distros
Name               Shortcuts
centos:7           centos7
centos:8           centos8
debian:buster      buster, debian:10
debian:bullseye    bullseye, debian:11
debian:bookworm    bookworm, debian:12
debian:sid         sid
debian:oldstable
debian:stable
debian:testing
debian:unstable
fedora:32          fedora32
fedora:33          fedora33
fedora:34          fedora34
fedora:35          fedora35
rocky:8            rocky8
ubuntu:trusty      trusty, ubuntu:14.04
ubuntu:xenial      xenial, ubuntu:16.04
ubuntu:bionic      bionic, ubuntu:18.04
ubuntu:focal       focal, ubuntu:20.04
ubuntu:hirsute     hirsute, ubuntu:21.04
ubuntu:impish      impish, ubuntu:21.10
ubuntu:jammy       jammy, ubuntu:22.04
```


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
