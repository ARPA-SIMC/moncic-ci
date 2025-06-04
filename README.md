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

The up to date list of supported operating systems can be queried with `monci
distros`. Currently supported, on nspawn and podman containers:

* Almalinux: 8 and 9
* Centos: 7
* Debian: stretch to trixie and testing/sid
* Fedora: 39 to 42
* Rocky: 8 and 9
* Ubuntu: xenial, bionic, focal, jammy, noble, oracular, plucky

Caveats from the last integration test run:

* Ubuntu Xenial on nspawn: systemd in container needs cgroups v1 support

All other systems appear to work on both nspawn and podman containers.

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
