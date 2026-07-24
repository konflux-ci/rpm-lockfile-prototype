# Integration Tests

Black-box tests that invoke `rpm-lockfile-prototype` as a subprocess, feed
it input files, and validate the output lockfile. The tool is never
imported directly -- it is always run as a child process.

## Prerequisites

- **rpm-lockfile-prototype** must be installed and on PATH. Use
  `pip install -e .` in a virtual environment to install from the repo.
- **skopeo** must be installed and on PATH.
- **dnf** Python bindings must be available (system package).

## Running

```bash
# Run only integration tests
pytest tests/integration/

# Run a single test case
pytest tests/integration/ -k single-package

# Run with parallel workers (requires pytest-xdist)
pytest tests/integration/ -n 10

# Run with coverage
pytest tests/integration/ --cov rpm_lockfile --cov-report html

# Speed up repeated runs by caching skopeo downloads
SKOPEO_CACHE_DIR=/tmp/skopeo-cache pytest tests/integration/
```

## How it works

Each test case is a directory under `data/success/` or `data/failure/`.
Test cases are autodiscovered -- no manual registration needed.

For each test case, the harness:

1. Scans `repos/<repo-name>.yaml` and generates DNF-compatible repodata.
2. Starts a per-test HTTP server to serve the generated repos.
3. Copies `rpms.in.yaml` and any extra files to a temp directory, replacing
   the `PORT` placeholder with the actual server port.
4. Runs `rpm-lockfile-prototype rpms.in.yaml --outfile rpms.lock.yaml`.
5. Stops the server.

**Success tests** (`data/success/`): the tool must exit 0 and the output
lockfile must match `expected.lock.yaml`. The `PORT` placeholder in the
expected file is replaced with the actual server port before comparison.

**Failure tests** (`data/failure/`): the tool must exit with a non-zero code.

Both types optionally support `expected.stdout` and `expected.stderr` files
for validating log messages (see below).

## Adding a new test case

### 1. Create the directory

```
tests/integration/data/success/my-new-test/
```

Or under `data/failure/` if the tool is expected to fail.

### 2. Create the input file (`rpms.in.yaml`)

Use `http://localhost:PORT/<repo-name>` for repo URLs. The `PORT`
placeholder is replaced at runtime with the actual HTTP server port.

```yaml
contentOrigin:
  repos:
    - repoid: test-repo
      baseurl: http://localhost:PORT/test-repo
packages:
  - my-package
arches:
  - x86_64
context:
  bare: true
```

### 3. Define the repo packages (`repos/<repo-name>.yaml`)

Each YAML file under `repos/` defines the packages in one repository.
The filename (minus `.yaml`) becomes the repo directory name on the
HTTP server.

```yaml
packages:
  - nvr: my-package-1.0-1

  - nvr: my-dep-2.0-1
    arch: x86_64
    requires:
      - something
    provides:
      - something-else
    recommends:
      - optional-thing
    files:
      - /usr/share/my-dep/data.txt
```

Supported fields per package:

| Field | Default | Description |
|---|---|---|
| `nvr` | (required) | Name-Version-Release. Supports epoch: `name-1:2.0-1` |
| `arch` | `noarch` | Package architecture |
| `requires` | `[]` | Hard dependencies |
| `provides` | `[]` | Extra capabilities (self-provide is always added) |
| `recommends` | `[]` | Weak dependencies |
| `files` | `[]` | File paths (for filelists.xml, used for file-based deps) |
| `sourcerpm` | auto | Defaults to `<name>-<ver>-<rel>.src.rpm` |

### 4. Generate the expected lockfile

For success tests, use the `generate-expected` helper:

```bash
tests/integration/generate-expected tests/integration/data/success/my-new-test
```

This reads all `repos/*.yaml` files, computes the checksums and sizes for
every package, and writes `expected.lock.yaml` with all packages included.
**Edit the file to remove packages that should not appear in the output**
(e.g., packages in the repo that the tool shouldn't resolve). The helper
gives you a starting point with correct checksums -- you decide which
packages belong in the expected result.

### 5. (Optional) Add output expectations

Create `expected.stdout` and/or `expected.stderr` to validate log messages.
Each non-empty, non-comment line is checked as a substring that must appear
in the corresponding output stream.

```
# Lines starting with # are comments
Expected log message substring
```

### 6. Run the test

```bash
pytest tests/integration/ -k my-new-test -v
```

## Repo directory layout

The path of each `.yaml` file under `repos/` (relative to `repos/`, minus
the extension) becomes the directory path for the generated repodata:

```
repos/test-repo.yaml            -> <tmpdir>/test-repo/repodata/
repos/test-repo/x86_64.yaml     -> <tmpdir>/test-repo/x86_64/repodata/
repos/test-repo/9.yaml          -> <tmpdir>/test-repo/9/repodata/
```

This is used for `$basearch` and `$releasever` substitution tests. For
example, with `baseurl: http://localhost:PORT/test-repo/$basearch`, place
the repo definition at `repos/test-repo/x86_64.yaml`.

## Extra files

Any file in the test case directory that is not `rpms.in.yaml`,
`expected.lock.yaml`, `expected.stdout`, `expected.stderr`,
`Containerfile`, or `Dockerfile` is copied to the working directory.
Files with the `.repo` extension also get `PORT` placeholder replacement.

Containerfiles and Dockerfiles are copied automatically without
placeholder replacement.

## Skopeo caching

The skopeo caching wrapper (`skopeo-cache`) is always enabled. It
transparently intercepts skopeo calls and caches inspect/copy results.
It is thread-safe for use with pytest-xdist.

By default, the cache is stored in a per-user directory
(`/tmp/rpm-lockfile-test-skopeo-cache-<user>`) that is shared across all
xdist workers and persists across runs until the system reboots or the
directory is manually removed. To use a different location, set
`SKOPEO_CACHE_DIR`:

```bash
SKOPEO_CACHE_DIR=/tmp/skopeo-cache pytest tests/integration/
```

## Tool cache isolation

Each test run uses a fresh `XDG_CACHE_HOME` (session-scoped temp directory)
so the tool's internal cache does not interfere with test isolation. Within
a single test session, the cache is shared across tests -- the first test
using a given image exercises the cold (download) path, and subsequent
tests hit the warm (cached) path.
