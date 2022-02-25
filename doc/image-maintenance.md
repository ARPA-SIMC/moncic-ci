# Image boostrapping and maintenance

## Bootstrapping an image from scratch

Moncic-CI knows how to bootstrap an image from scratch for a number of
operating systems: run `monci distros` to get a list.

If a file called `$imagename.tar.gz` exists in the images directory, Moncic-CI
will use its contents instead of running the usual bootstrap operations. This
can be useful as a cache, or for bootstrapping images that the host system is
not able to recreate from scratch.

`debootstrap` is used to boostrap Debian and Ubuntu.

`dnf` or `yum` are used to boostrap RPM based distributions, like Fedora, Rocky
Linux, or Centos.

`debootstrap` is available as a package in RPM based distributions, and `dnf`
is available in Debian-based distributions, although they might not be
installed by default.


## Snapshotting an existing image

You can also configure an image to snapshot another image instead of
bootstrapping from scratch, using the `extends` keyword instead of the `distro`
keyword in the image `.yaml` configuration file.

This kind of bootstrapping is extremely quick, and will, at least initially,
share disk space between the two images. It is a convenient way to have
something like multiple images based on the same distribution, but with
different sets of packages installed.


## Image maintenance

After bootstrapping, each image has a maintenance procedure that is run
regularly to keep it up to date.

Maintenance is run once automatically after bootstrap, and then each time you
run `monci update`.

A maintenance procedure runs a few steps in sequence:

1. For images configured with `distro:`: a distribution-specfic upgrade
   command, like `apt-get update; apt-get -y upgrade` or `dnf upgrade -q -y`
2. For images configured with `extends:`: the whole maintenance procedure of
   the parent image (which may in turn have a parent image, and so on)
3. The `maintscript:` script configured in the image, if present

This means that the distribution-specific upgrade command of the base
distribution is always executed first, followed by the sequence of maintscripts
of all parent images from the top down, and the image maintscript as the last
command.


## Image dependencies and monci bootstrap

When running `monci bootstrap` on multiple images, or on all available images,
the images will be sorted topologically so that a base image will always be
bootstrapped before the images that extend it.

If you are running `monci bootstrap myimage`, and `myimage` extends
`baseimage`, `baseimage` (and its parent images if any) will be added to the
list of images to bootstrap, so the command will be equivalent to running
`monci bootstrap baseimage myimage`.
