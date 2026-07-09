## YAML configuration for custom OS images

A name can provide enough information for Moncic CI to identify a basic OS
image without customization.

For anything more than a standard basic OS image, one can define images by
placing a `.yaml` configuration files in the image directory.

For example:

```yaml
distro: fedora34
maintscript: |
    dnf install git
```

Keywords currently supported are:

* `distro`: what distribution should be bootstrapped to create the image
* `extends`: instead of bootstrapping, snapshot an existing OS image
* `packages: List[str]`: list of extra packages to install during maintenance
* `maintscript`: script run after bootstrap and during regular maintenance, to
  customize the OS image. If it does not start with a shebang (`#!`),
  `#!/bin/sh` is automatically prepended.
* `forward_user`: username, or list of usernames, to propagate from the host
  system to the image. Users will be propagated with their primary groups, but
  not with their additional groups.
* `backup`: if false (the default), a
  [CACHEDIR.TAG](https://bford.info/cachedir/) file is created inside the
  image, to hint backup software that it does not need to be backed up.
  Set it to `true` and Moncic-CI will make sure there is no `CACHEDIR.TAG` file
  at the top of the OS image.
* `compression`: [btrfs compression attribute](https://btrfs.wiki.kernel.org/index.php/Compression)
  to set on the OS image subvolume when they are created. The value is the same
  as can be set by `btrfs property set compression`. Default: the global
  'compression' setting. You can use 'no' or 'none' to ask for no compression
  when one is globally set.
* `tmpfs`: Use a tmpfs overlay for ephemeral containers instead of btrfs
  snapshots. Default: as set in the global configuration, overridden to `true`
  if the OS image is not on btrfs
* `extra_sources`: Extra package sources to configure in the image. Default:
  none. It can be set to a mapping of names to distribution-specific extra
  source definitions, see below for examples.

One, and only one, of `distro` or `extends` must be present.

Note that `.yaml` files in image directories have precedence over plain
distribution names: one can for example define a `centos8.yaml` image that
contains `distro: rocky8`.

### Debian extra sources

Extra sources for debian distribution can be configured as
[deb822](https://manpages.debian.org/bookworm/apt/sources.list.5.en.html#DEB822-STYLE_FORMAT)
entries or as [extrepo](https://manpages.debian.org/trixie/extrepo/extrepo.1p.en.html)
repository names.

This is an example `extra_sources` entry with both in use:

```yaml
extra_sources:
  backports: debian_backports
  sid-arpae-simc:
    deb822: |
     Types: deb
     URIs: https://deb.debusine.debian.net/debian/r-arpae-simc
     Suites: sid-arpae-simc
     Components: main
     Signed-By:
      -----BEGIN PGP PUBLIC KEY BLOCK-----
      .
      mDMEak5/BhYJKwYBBAHaRw8BAQdAw2lfoNohpnwCl3Vj4RwM1PcAWd/C79IR3pN8
      8jn9bKy0K0FyY2hpdmUgc2lnbmluZyBrZXkgZm9yIGRlYmlhbi9yLWFycGFlLXNp
      bWOIkAQTFgoAOBYhBN2g5f2utuWJmzlWHEOxSSgRAU3VBQJqTn8GAhsDBQsJCAcC
      BhUKCQgLAgQWAgMBAh4BAheAAAoJEEOxSSgRAU3VfpoA/jvbQHm/gEuDAhzdn+73
      JFOGVdWUVwAtsKtH6wP3EPRtAP9rMcPItf+eUytelNRX2GVJimr8IeI0n6slPVBj
      J+u8Cg==
      =COh3
      -----END PGP PUBLIC KEY BLOCK-----

```

Each `extra_sources` entry will correspond to a generated
`/etc/apt/sources.list.d/{name}.sources` source configuration file.
