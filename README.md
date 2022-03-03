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

Decide on a directory that will contain container images: it will have to be on
a BTRFS filesystem. Many systems are on BTRFS by default, or make it convenient
to create and mount a BTRFS partition.

If none of that is convenient for you, you can use a filesystem on a file. For
example:

```
truncate --size=10G images
/sbin/mkfs.btrfs images
```

### Try bootstrapping an image

Run `monci distros` to get a list of supported OS images, and `monci bootstrap`
to create one. For example:

```
sudo monci bootstrap --imagedir images rocky8
```

### Set image location as default

You can configure the image location as a default so you do not need to type
`--imagedir` every time you run `monci`.

Create `~/.config/moncic-ci/moncic-ci.yaml` and add:

```yaml
imagedir: <your image file or directory location>
```

Then `monci` will just work with no extra arguments: try it out with `sudo
monci images` to get a list of available OS images, and you should see `rocky8`
listed.


## Using Moncic-CI

For freely trying things out on a shell, see [Running a shell](doc/shell.md).

For creating OS images with a custom setup, see [Custom OS images](doc/custom-os-images.md).

For running the test suite of local code on a different OS, see [Testing on another distro](doc/testing-on-another-distro.md).

## Technology

Moncic-CI uses [systemd-nspawn](https://www.freedesktop.org/software/systemd/man/systemd-nspawn.html)
as a backend, and a [btrfs](https://btrfs.wiki.kernel.org/index.php/Main_Page)
filesystem for storage and fast snapshotting.

The btrfs storage can be just a normal directory for systems that already use
btrfs or can easily setup a btrfs partition. Otherwise, with a small
performance penalty, Moncic-CI can [store OS images in a
file](doc/btrfs-on-file.md) by managing a btrfs filesystem inside it.


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

## In depth

* [Security considerations](doc/security.md)
* [Image bootstrapping and maintenance](doc/image-maintenance.md)
* [OS images in a file](doc/btrfs-on-file.md)

## Reference documentation

* [Moncic-CI configuration](doc/moncic-ci-config.md)
* [YAML configuration for custom OS images](doc/image-config.md)
