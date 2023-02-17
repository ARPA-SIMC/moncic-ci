# Build styles

This the documentation of the various ways Moncic-CI can build sources, and the
options that can be used to control them.

You can use these options with the `monci ci --option` command line arguments,
or in the [YAML configuration for CI builds](build-config.md).

## build

Build source packages

### Options

#### artifacts_dir

Directory where artifacts are copied after the build. Artifacts are lost when not set

#### source_only

Set to True to only build source packages, and skip compiling/building
binary packages

#### on_success

Zero or more scripts or actions to execute after a
successful build.

See [Post-build actions](post-build.actions.md) for documentation of possible values.

#### on_fail

Zero or more scripts or actions to execute after a
failed build.

See [Post-build actions](post-build.actions.md) for documentation of possible values.

#### on_end

Zero or more scripts or actions to execute after a
build, regardless of its result.

See [Post-build actions](post-build.actions.md) for documentation of possible values.

## debian

Build Debian packages

### Options

#### artifacts_dir

Directory where artifacts are copied after the build. Artifacts are lost when not set

#### source_only

Set to True to only build source packages, and skip compiling/building
binary packages

#### on_success

Zero or more scripts or actions to execute after a
successful build.

See [Post-build actions](post-build.actions.md) for documentation of possible values.

#### on_fail

Zero or more scripts or actions to execute after a
failed build.

See [Post-build actions](post-build.actions.md) for documentation of possible values.

#### on_end

Zero or more scripts or actions to execute after a
build, regardless of its result.

See [Post-build actions](post-build.actions.md) for documentation of possible values.

#### build_profile

space-separate list of Debian build profile to pass as DEB_BUILD_PROFILE

## rpm

Build RPM packages

### Options

#### artifacts_dir

Directory where artifacts are copied after the build. Artifacts are lost when not set

#### source_only

Set to True to only build source packages, and skip compiling/building
binary packages

#### on_success

Zero or more scripts or actions to execute after a
successful build.

See [Post-build actions](post-build.actions.md) for documentation of possible values.

#### on_fail

Zero or more scripts or actions to execute after a
failed build.

See [Post-build actions](post-build.actions.md) for documentation of possible values.

#### on_end

Zero or more scripts or actions to execute after a
build, regardless of its result.

See [Post-build actions](post-build.actions.md) for documentation of possible values.

## arpa

ARPA/SIMC builder, building RPM packages using the logic previously
configured for travis

### Options

#### artifacts_dir

Directory where artifacts are copied after the build. Artifacts are lost when not set

#### source_only

Set to True to only build source packages, and skip compiling/building
binary packages

#### on_success

Zero or more scripts or actions to execute after a
successful build.

See [Post-build actions](post-build.actions.md) for documentation of possible values.

#### on_fail

Zero or more scripts or actions to execute after a
failed build.

See [Post-build actions](post-build.actions.md) for documentation of possible values.

#### on_end

Zero or more scripts or actions to execute after a
build, regardless of its result.

See [Post-build actions](post-build.actions.md) for documentation of possible values.

