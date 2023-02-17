# Post-build actions

It is possible to configure actions to be run after a build, using the
`on_success`, `on_fail`, and `on_end` [build options](build-styles.md).

An action can be a path to a command to run, or a special action prefixed with
a `@` character.

## Special actions

### `@shell`

Open a shell inside the container


### `@linger`

Leave container running on exit. It will need to be shut down manually using
a command like `machinectl stop`.


## Command execution

An action not prefixed with an `@` is a command to be run, in the host system,
after the build.

The command is run using `sh -c`, so it can use shell syntax.

Moncic-CI exports various environment variables with information about the run:

* `MONCIC_ARTIFACTS_DIR`: directory where artifacts are stored, or empty string
  if none was set
* `MONCIC_CONTAINER_NAME`: name of the container used for the build. The
  container is still active while the script is run, so `machinectl` commands
  will work on it
* `MONCIC_IMAGE`: name of the OS image used for the container
* `MONCIC_CONTAINER_ROOT`: host path of the root directory for the container
  file system
* `MONCIC_PACKAGE_NAME`: name of the package that has been build, or empty
  string if the build failed before being able to detect a package name
* `MONCIC_RESULT`: "success" if the build succeeded, or "fail" otherwise
* `MONCIC_SOURCE`: path or URL of the source that has been built
