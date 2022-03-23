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
run `monci update`. You may want to schedule running `monci update` regularly,
to keep your images up to date.

A maintenance procedure for an image configured as `distro:` (without
`extends:`):

1. The `maintscript:` script configured in the image, if present
2. If the image configuration does not specify a `maintascript:` script, a
   default distribution-specfic upgrade command, like `apt-get update; apt-get
   -y upgrade` or `dnf upgrade -q -y`

For images configured with `extends:`:

1. Moncic-CI recursively runs the whole maintenance procedure of
   the parent image (which may in turn have a parent image, and so on)
2. The `maintscript:` script of the image, if present.

This means that the sequence of maintscripts of all parent images from the top
down is executed first, followed by the the image maintscript. If no image
maintscript is configured for an image that does not use `extends:`, then a
distribution-specific default upgrade command is run instead.


## Image dependencies and monci bootstrap

When running `monci bootstrap` on multiple images, or on all available images,
the images will be sorted topologically so that a base image will always be
bootstrapped before the images that extend it.

If you are running `monci bootstrap myimage`, and `myimage` extends
`baseimage`, `baseimage` (and its parent images if any) will be added to the
list of images to bootstrap, so the command will be equivalent to running
`monci bootstrap baseimage myimage`.


## Deduplicating common files

BTRFS can share disk space when files are the same, with copy on write
semantics. Moncic-CI implements a simple deduplication strategy, offering the
kernel to attempt deduplication of every file which has the same pathname and
size across OS images. This is fast, and safe, since the kernel checks that
file contents really are the same before deduplicating them.

There is a `monci dedup` command to trigger this deduplication across all OS
images, and it is also performed automatically at the end of `monci update`
runs.
