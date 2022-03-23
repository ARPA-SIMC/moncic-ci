# Version NEXT

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

# Version 0.1

* First release
