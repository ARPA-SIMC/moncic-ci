# YAML configuration for CI builds

You can store common build options into a YAML file, and load it with the
`monci ci --build-config=file.yaml` command line option.

The file can contain any of the options documented in 
[Build styles](doc/build-styles.md).

This is an example content for the file:

```yaml
build:
  artifacts_dir: /tmp

debian:
  build_profile: nodoc
```

Each section of the file corresponds to a build style, with `build` being
common to all build styles. The key/value contents of the section are options
passed to the build.

Options in a section that does not apply to the current build are ignored: this
way you can use a single file, for example, to configure default options for
Debian and RPM builds.
