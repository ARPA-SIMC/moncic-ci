# Version NEXT

* Implemented simple deduplication of files with the same name and size across
  OS images. (#19)
* Deduplication is triggered automatically at the end of `monci update` (#19)
* Allow to configure a compression property to use when creating BTRFS
  subvolumes
* Exit with an appropriate error message instead of a traceback when asking for
  `shell` or `run` on an image that has not yet been bootstrapped

# Version 0.1

* First release
