# Moncic-CI

Continuous integration tool, and development helper.

Moncic CI manages lightweight containers for use with Continuous Integration or
to help developers target platforms different from the development machine.


## Installing Moncic-CI

### Install dependencies

Choose one of:

```
apt install python3-yaml python3-coloredlogs python3-texttable dnf
dnf install python3-pyyaml python3-coloredlogs python3-texttable debootstrap
pip install yaml coloredlogs texttable
```

### Pick a directory for images

Decide on a directory that will contain container images: it will have to be on
a BTRFS filesystem. Many systems are on BTRFS by default, or make it convenient
to create and mount a BTRFS partition.

If none of that is convenient for you, you can use a filesystem on a file. For
example:

```
truncate --size=10G images.img
/sbin/mkfs.btrfs -f images.img
mkdir images
sudo mount images.img images
```

### Try bootstrapping an image

Run `monci distros` to get a list of supported OS images, and `monci bootstrap`
to create one. For example:

```
sudo monci bootstrap --imagedir images rocky8
```

## HOWTOs for using Moncic-CI

For freely trying things out on a shell, see [Running a shell](doc/shell.md).

For creating OS images with a custom setup, see [Custom OS images](doc/custom-os-images.md)

## Container details

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
ubuntu:xenial      xenial, ubuntu:16.04
ubuntu:bionic      bionic, ubuntu:18.04
ubuntu:focal       focal, ubuntu:20.04
ubuntu:hirsute     hirsute, ubuntu:21.04
ubuntu:impish      impish, ubuntu:21.10
ubuntu:jammy       jammy, ubuntu:22.04
```

## Reference documentation

* [YAML configuration for custom OS images](doc/image-config.md)
