# Running unit tests

Many of Moncic-CI tests need a containerized system to run.

Running test cases using sudo is based on [work done on transilience](https://github.com/spanezz/transilience/blob/main/TESTING.md).


## Prerequisites

The directory `images/` needs to contain one btrfs snapshot for each
distribution used by tests.

TODO: automatically bootstrap the images if missing.


## Running tests

To start and stop the nspawn containers, the unit tests need to be run as root
with `sudo`. The test suite drops root as soon as possible (see
`moncic.unittest.ProcessPrivs`) and changes to `$SUDO_UID` and `$SUDO_GID`.

They will temporarily regain root for as short as possible to start the
container, run commands on it, and stop it. Look for `privs.root` in the code
to see where this happens.

To run the test, once `images/` are set up, use `sudo`
[`nose2`](https://docs.nose2.io/):

```
sudo nose2-3
```
