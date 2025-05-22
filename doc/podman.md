# Using podman images

Moncic-CI can use podman images and containers to run commands and builds.

Any image tagged in a container starting with `localhost/moncic-ci/` will be
available for use in Moncic-CI. If you build an image yourself, you can make it
available by tagging it:

    podman image tag {image} localhost/moncic-ci/{distro}:{variant}


## Bootstrapping podman images

All the various `bootstrap`, `update`, `remove` commands work with images
configured by [Moncic-CI YAML files](image-config.md).

If an image file name contains a colon, then it is used as the tag in podman.
Otherwise, the file name is used as the repository, and `latest` is used as
tag.

Running `monci` without sudo will disable nspawn as a container technology, and
images will be bootstrapped using podman.
