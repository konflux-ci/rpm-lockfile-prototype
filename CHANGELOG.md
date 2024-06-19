# Changelog

## Unreleased

### Added

- New command line option `--allowerasing` makes it possible to remove packages
  from the base image to replace them with a conflicting one.


## [0.1.0-alpha.5] - 2024-06-10

### Changed

- There is no explicit dependency on DNF anymore. It was causing problems in
  certain installation scenarios. Instead, if you run the tool with no
  python3-dnf package available, an error message is printed with instructions
  on what to do.

## [0.1.0-alpha.4] - 2024-06-10

### Fixed

- Correctly process aarch64 images.

## [0.1.0-alpha.3] - 2024-06-10

### Added

- Any repository option can be specified in `contentOrigins.repos`. There is no
  schema validation on them though. The resolver only requires `repoid` and
  `baseurl`. Anything else will be forwarded to DNF. Handling unrecognized
  options depends on what DNF does. On Fedora 39 it silently ignores unknown
  options.

### Changed

- All options from repofiles are now honored and passed over to DNF. This means
  that DNF will now see even disabled repos, but will not include any packages
  from there.
- Extracting rpmdb from an image is now done with skopeo. This makes it
  possible to run inside non-privileged containers. The `--pull` option is now
  deprecated and doesn't do anything, the image is always pulled fresh.

## [0.1.0-alpha.2] - 2024-04-25

### Added

- `arch-include` directive in treefile is now handled

### Changed

- Missing sources are reported as a warning only.
- List of `packages` in input file can be empty.

### Fixed

- Compatibility with Python 3.9

## [0.1.0-alpha.1] - 2024-04-11

Initial release
