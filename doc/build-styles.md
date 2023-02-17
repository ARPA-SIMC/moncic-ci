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

## debian

Build Debian packages

### Options

#### artifacts_dir

Directory where artifacts are copied after the build. Artifacts are lost when not set

#### source_only

Set to True to only build source packages, and skip compiling/building
binary packages

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

## arpa

ARPA/SIMC builder, building RPM packages using the logic previously
configured for travis

### Options

#### artifacts_dir

Directory where artifacts are copied after the build. Artifacts are lost when not set

#### source_only

Set to True to only build source packages, and skip compiling/building
binary packages

