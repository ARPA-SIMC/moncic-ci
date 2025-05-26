[![Build Status](https://simc.arpae.it/moncic-ci/moncic-ci/fedora40.png)](https://simc.arpae.it/moncic-ci/moncic-ci/)
[![Build Status](https://simc.arpae.it/moncic-ci/moncic-ci/fedora42.png)](https://simc.arpae.it/moncic-ci/moncic-ci/)
[![Build Status](https://copr.fedorainfracloud.org/coprs/simc/stable/package/moncic-ci/status_image/last_build.png)](https://copr.fedorainfracloud.org/coprs/simc/stable/package/moncic-ci/)

# Moncic-CI

Moncic CI manages lightweight containers for use with Continuous Integration or
to help developers target platforms different from the development machine.

It manages a pool of Operating System images that can be used to run shells,
commands, and builds.

It deduplicates common files across OS images, so one can have one image per
project on a developer machine with limited disk usage.

It has low startup times on containers, making it convenient for quick
development iterations: run tests on your code on another OS about as easily as
you would run it on your normal system, keeping iteration lags low.

Does your test suite complete on the production OS? Does your software install
where it should? Does your playbook do what it should? With Moncic-CI it
becomes second nature to check it out.


## Installing Moncic-CI

### Install dependencies

Choose one of:

```
# On apt-based systems:
apt install python3-yaml python3-coloredlogs python3-texttable dnf btrfs-progs systemd-container
# On dnf-based systems:
dnf install python3-pyyaml python3-coloredlogs python3-texttable debootstrap btrfs-progs systemd-container
# On all systems:
pip install .
```

### Pick a directory for images

Decide on a directory that will contain container images. Using a BTRFS
filesystem will give you more features, but a non-BTRFS filesystem can work,
too.

Moncic-CI uses a default of `/var/lib/machines/` to share containers with
`machinectl`, and it allows to set other paths, to let users manage different
sets of SO images if needed

### Try bootstrapping an image

Run `monci distros` to get a list of supported OS images, and `monci bootstrap`
to create one. For example:

```
sudo monci bootstrap rocky8
```

## Using Moncic-CI

For freely trying things out on a shell, see [Running a shell](doc/shell.md).

For creating OS images with a custom setup, see [Custom OS images](doc/custom-os-images.md).

For running the test suite of local code on a different OS, see [Testing on another distro](doc/testing-on-another-distro.md).

For building Debian packages, see [Building Debian packages](doc/build-debian.md).

For a helping hand in testing, see [Reproducible test sessions](doc/reproducible-testing.md).

## Technology

Moncic-CI uses [systemd-nspawn](https://www.freedesktop.org/software/systemd/man/systemd-nspawn.html)
as a backend. When using a [btrfs](https://btrfs.wiki.kernel.org/index.php/Main_Page)
filesystem it can optionally use its features to reduce disk usage.


## Distributions supported

The up to date list of supported distribution can be queried with `monci
distros`. It currently is:

```
$ monci distros
Name               Shortcuts
centos:7           centos7
centos:8           centos8
debian:jessie      jessie, debian:8
debian:stretch     stretch, debian:9
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
fedora:36          fedora36
fedora:37          fedora37
fedora:38          fedora38
rocky:8            rocky8
rocky:9            rocky9
ubuntu:xenial      xenial, ubuntu:16.04
ubuntu:bionic      bionic, ubuntu:18.04
ubuntu:focal       focal, ubuntu:20.04
ubuntu:hirsute     hirsute, ubuntu:21.04
ubuntu:impish      impish, ubuntu:21.10
ubuntu:jammy       jammy, ubuntu:22.04
```

## In depth

* [Security considerations](doc/security.md)
* [Image bootstrapping and maintenance](doc/image-maintenance.md)

## Reference documentation

* [Moncic-CI configuration](doc/moncic-ci-config.md)
* [YAML configuration for custom OS images](doc/image-config.md)
* [YAML configuration for CI builds](doc/build-config.md)
* [Source styles](doc/source-styles.md) that Moncic-CI knows how to build
* [Build styles](doc/build-styles.md) and their options
* [Post-build actionss](doc/post-build-actions.md)
