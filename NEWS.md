# New in version ?

* Renamed `ci-chroot-tool` to `monci`
* Added `--checkout` option to `monci shell`, to quickly check out a
  local or remote repository inside the test machine
* Added `--workdir` option to `monci shell`, to allow to work in a directory in
  the host system and run tests in the chroot
* Added `--bind` and `--bind-ro` options to `monci shell`, passed through to
  `systemd-nspawn`
