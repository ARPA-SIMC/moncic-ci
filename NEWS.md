# Version UNRELEASED

* Removed support for a btrfs filesystem in a file (#41)

# Version 0.6

* Fixed network issues with systemd-resolved based images (Fedora 36)

# Version 0.5

* Fixed bootstrapping on non-btrfs filesystems (#44)

# Version 0.4

* Allow to store image configuration separately from images (#33)
* Get systemd version from systemcl instead of systemd (#40)
* Added Fedora 36 distro
* Arpa builder search SPECS both in `*/SPECS` and `./` (#42)

# Version 0.3

* Default for imagedir changed from `./images` to `/var/lib/machines` (#25)
* Added a `tmpfs` configuration, both global and per-image, to use `tmpfs`
  backing for ephemeral images instead of btrfs snapshots. If the machine is
  configured with enough ram and swap, it makes for faster CI runs (#27)
* Support non-btrfs image storage, by forcing ephemeral images to use `tmpfs`
  backing instead of btrfs snapshots
* Add `-C`/`--config` option to specify a config file from command line (#34)
* Made non-ephemeral containers transactional on BTRFS: updates are run on a
  snapshot of the OS image, which is swapped with the original if the operation
  succeeds, or removed without changing the original if it fails (#29)
* Run containers with `--suppress-sync=yes` on systemd >= 250 (#28)
* Run commands in container with /dev/null redirectd to stdin, instead of stdin
  being a closed file descriptor (#37)
* Fixed selection of build-dep command in ARPA-style builds (#38)

# Version 0.2

* Implemented simple deduplication of files with the same name and size across
  OS images. (#19)
* Deduplication is triggered automatically at the end of `monci update` (#19)
* Allow to configure a compression property to use when creating BTRFS
  subvolumes
* Exit with an appropriate error message instead of a traceback when asking for
  `shell` or `run` on an image that has not yet been bootstrapped
* If bootstrap is interrupted by keyboard interrupt, remove the subvolume
  instead of leaving a partially built OS image
* If imagedir points to a btrfs filesystem on a file, automatically mount it an
  unmount it as needed (#21)
* `fstrim(8)` disk usage on an images file if configured, or if it can be
  detected to be on an SSD (#21)
* Automatically reexec with sudo if permissions are needed (#23)
* Do not run the default upgrade command if a maintscript is provided
* Allow using `.tar.xz` and `.tar` as cached distribution images
* Reduce yum/dnf verbosity on bootstrap

# Version 0.1

* First release
