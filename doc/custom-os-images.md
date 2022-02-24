# Creating OS images with a custom setup

Moncic-CI requires no configuration to boostrap minimal OS images, and often one needs more than that.

For example, one might want images with a set of build tools preinstalled, or with extra package repositories.

A convenient thing to have is an image which preinstalls all the build
dependencies a project under development, so one can run its test suite on
another OS at will.

To custommize how an OS image is created, place a `.yaml` file describing it in
the images directory. The name of the `.yaml` file will become the name of a
bootstrappable image, and you have control over its setup.

Examples:

## A build machine

```yaml
distro: fedora34
maintscript: |
    /usr/bin/sed -i '/^tsflags=/d' /etc/dnf/dnf.conf
    /usr/bin/dnf install -y --allowerasing @buildsys-build
    /usr/bin/dnf install -q -y 'dnf-command(builddep)'
    /usr/bin/dnf install -q -y git
    /usr/bin/dnf install -q -y rpmdevtools
    /usr/bin/dnf copr enable -y simc/stable
    /usr/bin/dnf upgrade -q -y
```


## An OS image for quick unit testing

```yaml
distro: rocky8

# Pre-create my local user to the image: this makes startup faster when
testing, as it won't needed to be created every time
forward_user: enrico

maintscript: |
    /usr/bin/dnf install -q -y epel-release
    /usr/bin/dnf install -q -y dnf-command(config-manager)
    /usr/bin/dnf config-manager --set-enabled powertools
    /usr/bin/dnf groupinstall -q -y Development Tools
    /usr/bin/dnf install -q -y dnf-command(builddep)
    /usr/bin/dnf install -q -y git
    /usr/bin/dnf install -q -y rpmdevtools
    # Add handy development tools
    /usr/bin/dnf install -q -y git vim-enhanced gdb
    # Add the build dependencies of my project
    /usr/bin/dnf install -q -y python3-pyyaml python3-coloredlogs python3-texttable
    /usr/bin/dnf upgrade -q -y
```


## Reference

The full reference of YAML image configuration is in [YAML configuration for custom OS images](image-config.md).

