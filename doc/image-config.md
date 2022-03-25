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

One, and only one, of `distro` or `extends` must be present.

Note that `.yaml` files in image directories have precedence over plain
distribution names: one can for example define a `centos8.yaml` image that
contains `distro: rocky8`.
