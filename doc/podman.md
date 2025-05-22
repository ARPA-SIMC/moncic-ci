# Using podman images

Moncic-CI can use podman images and containers to run commands and builds.

Any image tagged in a container starting with `localhost/moncic-ci/` will be
available for use in Moncic-CI. If you build an image yourself, you can make it
available by tagging it:

    podman image tag {image} localhost/moncic-ci/{distro}:{variant}


## Bootstrapping podman images

**TODO: build commands are not yet supported**


