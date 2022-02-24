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
* `maintscript`: script run after bootstrap and during regular maintenance, to
  customize the OS image. If it does not start with a shebang (`#!`),
  `#!/bin/sh` is automatically prepended.
* `forward_user`: username, or list of usernames, to propagate from the host
  system to the image. Users will be propagated with their primary groups, but
  not with their additional groups.

Note that `.yaml` files in image directories have precedence over plain
distribution names: one can for example define a `centos8.yaml` image that
contains `distro: rocky8`.

