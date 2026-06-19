"""
Containerfile-level parsing for RPM package extraction.

Parses Containerfile/Dockerfile structure to collect ARG/ENV variables,
build COPY/ADD mappings, locate and extract packages from shell scripts
referenced in RUN commands, and analyze per-stage install and update
commands.
"""

from __future__ import annotations

import fnmatch
import logging
import re
import shlex
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from dockerfile_parse import DockerfileParser

from .shell_commands import (
    ARCH_SUBSHELL_KEYWORDS,
    analyze_run_commands,
    resolve_bash_expansion,
)

# Common Linux architectures for multi-arch resolution
DEFAULT_ARCHES = ["x86_64", "s390x", "ppc64le", "aarch64"]


@dataclass
class StagePackages:
    """
    Analysis results for a single Containerfile stage.
    """

    base_image: str = ""
    stage_name: str = ""
    packages: list[str] = field(default_factory=list)
    has_update: bool = False
    arch_packages: dict[str, list[str]] = field(default_factory=dict)
    update_targets: list[str] = field(default_factory=list)
    builddep_packages: list[str] = field(default_factory=list)
    module_specs: list[str] = field(default_factory=list)

    def merge(self, other: "StagePackages") -> "StagePackages":
        """
        Merge another StagePackages into this one, combining packages
        and update targets.
        """
        merged_arch = dict(self.arch_packages)
        for arch, pkgs in other.arch_packages.items():
            existing = set(merged_arch.get(arch, []))
            existing.update(pkgs)
            merged_arch[arch] = sorted(existing)
        return StagePackages(
            base_image=self.base_image or other.base_image,
            stage_name=self.stage_name or other.stage_name,
            packages=sorted(set(self.packages + other.packages)),
            has_update=self.has_update or other.has_update,
            arch_packages=merged_arch,
            update_targets=sorted(set(self.update_targets + other.update_targets)),
            builddep_packages=sorted(
                set(self.builddep_packages + other.builddep_packages)
            ),
            module_specs=sorted(set(self.module_specs + other.module_specs)),
        )


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]:
        return value[1:-1]
    return value


def collect_stage_vars(
    entries: list[dict], inherited_vars: dict[str, str] | None = None
) -> dict[str, str]:
    """
    Collect ARG and ENV variable definitions from DockerfileParser
    structure entries.

    ARG values with defaults and ENV values are collected. ENV values
    can reference previously defined variables via bash expansion.

    Arg(s):
        entries (list[dict]): DockerfileParser structure entries with
            "instruction" and "value" keys.
        inherited_vars (dict[str, str] | None): Variables inherited from
            global scope (ARGs before first FROM).
    Return Value(s):
        dict[str, str]: Collected variable name-to-value mapping.
    """
    variables: dict[str, str] = dict(inherited_vars or {})

    for entry in entries:
        instruction = entry["instruction"]
        value = entry["value"]

        if instruction == "ARG":
            arg_match = re.match(r"^(\w+)(?:=(.*))?$", value.strip())
            if arg_match:
                var_name = arg_match.group(1)
                default_value = arg_match.group(2)
                if default_value is not None:
                    variables[var_name] = resolve_bash_expansion(
                        _strip_quotes(default_value.strip()), variables
                    )

        elif instruction == "ENV":
            env_match = re.match(r"^(\w+)(?:=|\s+)(.*)", value.strip())
            if env_match:
                var_name = env_match.group(1)
                variables[var_name] = resolve_bash_expansion(
                    _strip_quotes(env_match.group(2).strip()), variables
                )

    return variables


def build_copy_map(
    stage_entries: list[dict],
    env_vars: dict[str, str] | None = None,
) -> dict[str, str]:
    """
    Build a mapping from container destination paths to source paths
    from COPY/ADD instructions in a Containerfile stage.

    Handles both file and directory destinations:
        COPY hack/foo.sh /tmp   -> /tmp/foo.sh -> hack/foo.sh
        COPY hack/foo.sh /opt/renamed.sh -> /opt/renamed.sh -> hack/foo.sh

    Skips COPY --from=... (inter-stage copies) since those don't come
    from the source tree.

    Arg(s):
        stage_entries (list[dict]): DockerfileParser structure entries.
        env_vars (dict[str, str] | None): Variables for resolving
            COPY paths that contain ARG/ENV references.
    Return Value(s):
        dict[str, str]: Container path to source-relative path mapping.
    """
    variables = dict(env_vars or {})
    copy_map: dict[str, str] = {}
    for entry in stage_entries:
        if entry["instruction"] not in ("COPY", "ADD"):
            continue
        value = entry["value"]
        if "--from=" in value:
            continue
        if variables:
            value = resolve_bash_expansion(value, variables)
        try:
            parts = shlex.split(value)
        except ValueError:
            continue
        non_flag_parts = [p for p in parts if not p.startswith("--")]
        if len(non_flag_parts) < 2:
            continue
        sources = non_flag_parts[:-1]
        dest = non_flag_parts[-1]
        for src in sources:
            # Map as directory destination (COPY foo.sh /tmp/ → /tmp/foo.sh)
            copy_map[dest.rstrip("/") + "/" + Path(src).name] = src
        if len(sources) == 1 and not dest.endswith("/"):
            # Also map as file rename (COPY foo.sh /opt/bar.sh → /opt/bar.sh).
            # Without container filesystem context we can't distinguish
            # file renames from directory copies, so we keep both mappings.
            copy_map[dest] = sources[0]
    return copy_map


def _read_packages_from_file(source_file: Path) -> list[str]:
    """
    Read package names from a file (one per line, comments skipped).
    Allows file paths (e.g. /usr/sbin/udevadm) since DNF can resolve
    them via provides.
    """
    packages: list[str] = []
    for line in source_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        pkg_name = line.split()[0]
        if not pkg_name.startswith("-"):
            packages.append(pkg_name)
    return packages


def _resolve_file_from_copy_map(
    resolved_path: str,
    copy_map: dict[str, str],
    source_dir: Path,
) -> Path | None:
    """
    Look up a container path in copy_map and return the source file.
    Falls back to looking for the basename directly in source_dir
    when copy_map uses globs (e.g. COPY ${PKGS_LIST}* /tmp/).
    """
    source_file = None
    if resolved_path in copy_map:
        source_file = source_dir / copy_map[resolved_path]
    else:
        basename = Path(resolved_path).name
        for container_path, src_path in copy_map.items():
            if container_path.endswith("/" + basename):
                source_file = source_dir / src_path
                break
        if not source_file:
            candidate = source_dir / basename
            if candidate.exists():
                source_file = candidate
    if not source_file:
        return None
    try:
        source_file = source_file.resolve()
        if not source_file.is_relative_to(source_dir.resolve()):
            return None
    except (OSError, ValueError):
        return None
    return source_file if source_file.exists() else None


def extract_packages_from_file_installs(
    run_values: list[str],
    copy_map: dict[str, str],
    source_dir: Path,
    env_vars: dict[str, str] | None = None,
    arches: list[str] | None = None,
) -> tuple[list[str], dict[str, list[str]]]:
    """
    Extract package names from install commands that read packages from
    a file via stdin redirect or pipe.

    Supported patterns:
        xargs dnf install < /tmp/pkgs.txt
        grep -vE '^(#|$)' /tmp/pkgs.txt | xargs dnf install -y
        cat /tmp/pkgs.txt | xargs dnf install -y

    When the file path contains an arch keyword like $(arch), resolves
    it per-architecture and returns arch-specific packages separately.

    Arg(s):
        run_values (list[str]): RUN command bodies or joined script lines.
        copy_map (dict[str, str]): Container path to source path mapping.
        source_dir (Path): Source tree root.
        env_vars (dict[str, str] | None): Variables for path resolution.
        arches (list[str] | None): Architectures to resolve for arch-specific
            file paths. Defaults to DEFAULT_ARCHES.
    Return Value(s):
        tuple[list[str], dict[str, list[str]]]:
            - Sorted unique package names (common to all arches).
            - Dict mapping arch to sorted unique package names for that arch only.
    """
    logger = logging.getLogger(__name__)
    variables = dict(env_vars or {})
    arch_list = arches or DEFAULT_ARCHES
    redirect_re = re.compile(
        r"""
        (?:xargs\s+(?:\S+\s+)*)?    # optional xargs with optional flags
        (?:dnf|yum)\s+              # package manager
        .*?\b(?:install)\b          # install subcommand
        .*?<\s*(\S+)               # stdin redirect: < /path/to/file
        """,
        re.VERBOSE,
    )
    pipe_re = re.compile(
        r"""
        (?:grep|cat)\s+             # grep or cat command
        .*?(\S+)                    # file path argument (captured)
        \s*\|\s*                    # pipe
        (?:xargs\s+(?:\S+\s+)*)?    # optional xargs with optional flags
        (?:dnf|yum)\s+              # package manager
        .*?\b(?:install)\b          # install subcommand
        """,
        re.VERBOSE,
    )
    packages: set[str] = set()
    arch_packages: dict[str, set[str]] = {}

    for run_body in run_values:
        for cmd in re.split(r"&&|;", run_body):
            cmd_clean = re.sub(
                r"^(if\s+!\s*|if\s+|then|else|elif|do)\s*", "", cmd.strip()
            )
            match = redirect_re.search(cmd_clean)
            if not match:
                match = pipe_re.search(cmd_clean)
            if not match:
                continue
            raw_path = match.group(1)
            resolved_path = resolve_bash_expansion(raw_path, variables)
            if not resolved_path:
                continue

            has_arch_keyword = any(kw in resolved_path for kw in ARCH_SUBSHELL_KEYWORDS)
            if not has_arch_keyword and "$" in resolved_path:
                continue

            if has_arch_keyword:
                for arch in arch_list:
                    arch_path = resolved_path
                    for kw in ARCH_SUBSHELL_KEYWORDS:
                        arch_path = arch_path.replace(kw, arch)
                    source_file = _resolve_file_from_copy_map(
                        arch_path, copy_map, source_dir
                    )
                    if not source_file:
                        continue
                    logger.info(
                        f"Extracting arch-specific packages from {source_file} for {arch}"
                    )
                    try:
                        arch_packages.setdefault(arch, set()).update(
                            _read_packages_from_file(source_file)
                        )
                    except OSError:
                        continue
            else:
                source_file = _resolve_file_from_copy_map(
                    resolved_path, copy_map, source_dir
                )
                if not source_file:
                    logger.debug(
                        f"File install path {resolved_path} not resolved from COPY map"
                    )
                    continue
                logger.info(f"Extracting packages from file install: {source_file}")
                try:
                    packages.update(_read_packages_from_file(source_file))
                except OSError:
                    continue

    arch_result = {arch: sorted(pkgs) for arch, pkgs in sorted(arch_packages.items())}
    return sorted(packages), arch_result


def extract_packages_from_scripts(
    run_values: list[str],
    source_dir: Path | None = None,
    copy_map: dict[str, str] | None = None,
    env_vars: dict[str, str] | None = None,
) -> StagePackages:
    """
    Find shell scripts invoked in RUN commands and extract yum/dnf
    install/update packages from them.

    Detects patterns like:
        RUN /src/install-deps.sh
        RUN ./scripts/setup.sh
        RUN . /cachi2/cachi2.env && /src/install-deps.sh
        RUN /bin/bash /tmp/dockerfile_install_support.sh
        RUN bash /opt/scripts/setup.sh

    Uses copy_map to trace container paths back to source files when
    the script was COPY'd into the image (e.g. COPY hack/foo.sh /tmp).

    Arg(s):
        run_values (list[str]): RUN command bodies.
        source_dir (Path | None): Source tree root to locate script files.
        copy_map (dict[str, str] | None): Container path to source path
            mapping from COPY/ADD instructions.
        env_vars (dict[str, str] | None): Variables for path resolution.
    Return Value(s):
        StagePackages: Extracted packages, update targets, arch-specific
            packages, and update flag from scripts.
    """
    if not source_dir:
        return StagePackages()

    logger = logging.getLogger(__name__)
    script_pattern = re.compile(
        r"(?:^|&&\s*|;\s*)"
        r"(?:(?:(?:/usr)?/bin/)?(?:ba)?sh\s+)?"
        r"(/\S+\.sh|\.\/\S+\.sh|(?<!\S)[\w.-]+\.sh)"
    )
    copy_map = copy_map or {}
    all_packages: set[str] = set()
    all_updates: set[str] = set()
    all_arch_packages: dict[str, set[str]] = {}
    all_builddep: set[str] = set()
    all_modules: set[str] = set()
    scripts_have_bare_update: bool = False

    for run_body in run_values:
        for match in script_pattern.finditer(run_body):
            script_path = match.group(1)
            if script_path.startswith("./"):
                candidate = source_dir / script_path[2:]
            elif script_path.startswith("/src/"):
                candidate = source_dir / script_path[5:]
            elif script_path in copy_map:
                candidate = source_dir / copy_map[script_path]
            elif "/" not in script_path:
                found = None
                for container_path, src_path in copy_map.items():
                    if Path(container_path).name == script_path:
                        found = source_dir / src_path
                        break
                if found:
                    candidate = found
                else:
                    logger.debug(
                        f"Skipping bare script {script_path}: not found in COPY map"
                    )
                    continue
            else:
                logger.debug(
                    f"Skipping script {script_path}: unsupported path prefix and not in COPY map"
                )
                continue

            try:
                candidate = candidate.resolve()
                if not candidate.is_relative_to(source_dir.resolve()):
                    logger.debug(
                        f"Skipping script {script_path}: path traversal outside source_dir"
                    )
                    continue
            except (OSError, ValueError):
                continue

            if not candidate.exists():
                logger.warning(
                    f"Script {script_path} referenced in Containerfile but not found at {candidate}"
                )
                continue

            logger.info(f"Extracting packages from script: {candidate}")

            try:
                script_content = candidate.read_text()
            except OSError:
                continue

            raw_lines = [
                line
                for line in script_content.splitlines()
                if not line.strip().startswith("#")
            ]
            joined_lines: list[str] = []
            for line in raw_lines:
                stripped = line.rstrip()
                if joined_lines and joined_lines[-1].endswith("\\"):
                    joined_lines[-1] = joined_lines[-1][:-1] + " " + stripped.lstrip()
                else:
                    joined_lines.append(stripped)
            script_body = "\n".join(line for line in joined_lines if line.strip())

            result = analyze_run_commands([script_body])
            all_packages.update(result.packages)
            for arch, arch_pkgs in result.arch_packages.items():
                all_arch_packages.setdefault(arch, set()).update(arch_pkgs)
            all_updates.update(result.update_targets)
            all_builddep.update(result.builddep_packages)
            all_modules.update(result.module_specs)
            if result.has_update:
                scripts_have_bare_update = True

            file_pkgs, file_arch_pkgs = extract_packages_from_file_installs(
                [script_body], copy_map, source_dir, env_vars=env_vars
            )
            all_packages.update(file_pkgs)
            for arch, arch_pkgs in file_arch_pkgs.items():
                all_arch_packages.setdefault(arch, set()).update(arch_pkgs)

    return StagePackages(
        packages=sorted(all_packages),
        has_update=scripts_have_bare_update,
        arch_packages={
            arch: sorted(pkgs) for arch, pkgs in sorted(all_arch_packages.items())
        },
        update_targets=sorted(all_updates),
        builddep_packages=sorted(all_builddep),
        module_specs=sorted(all_modules),
    )


def analyze_containerfile_stages(
    containerfile_path: Path,
    source_dir: Path | None = None,
) -> list[StagePackages]:
    """
    Parse a Containerfile and return per-stage package analysis.

    Uses DockerfileParser for instruction parsing, backslash-continuation
    joining, and stage boundary detection. Collects ARG definitions before
    the first FROM as global variables, then per-stage ARG/ENV definitions.
    All variables are used to resolve package names in install commands.

    Also detects shell scripts invoked in RUN commands and extracts
    packages from them if the script file exists in source_dir.

    Packages inside arch-conditional blocks (if [ $(arch) = X ]) are
    returned separately so they can be resolved only for the matching
    architecture.

    Arg(s):
        containerfile_path (Path): Path to the Containerfile/Dockerfile.
        source_dir (Path | None): Source tree root to locate script files
            referenced in RUN commands.
    Return Value(s):
        list[StagePackages]: Per-stage package analysis.
    """
    dfp = DockerfileParser(str(containerfile_path))
    entries = dfp.structure

    pre_from_entries: list[dict] = []
    stage_entry_lists: list[list[dict]] = []
    current_stage: list[dict] = []
    seen_from = False

    for entry in entries:
        if entry["instruction"] == "FROM":
            if seen_from and current_stage:
                stage_entry_lists.append(current_stage)
            seen_from = True
            current_stage = [entry]
        elif seen_from:
            current_stage.append(entry)
        else:
            pre_from_entries.append(entry)

    if current_stage:
        stage_entry_lists.append(current_stage)

    global_args = collect_stage_vars(pre_from_entries)
    stages: list[StagePackages] = []

    from_re = re.compile(
        r"^(--platform=\S+\s+)?(?P<img>\S+)(\s+[Aa][Ss]\s+(?P<name>\S+))?\s*$"
    )

    for stage_entries in stage_entry_lists:
        stage_vars = collect_stage_vars(stage_entries, inherited_vars=global_args)
        run_values = [e["value"] for e in stage_entries if e["instruction"] == "RUN"]

        base_image = ""
        stage_name = ""
        from_entries = [e for e in stage_entries if e["instruction"] == "FROM"]
        if from_entries:
            m = from_re.match(from_entries[0]["value"])
            if m:
                base_image = resolve_bash_expansion(m.group("img"), stage_vars)
                stage_name = m.group("name") or ""

        result = analyze_run_commands(run_values, env_vars=stage_vars)
        stage = StagePackages(
            base_image=base_image,
            stage_name=stage_name,
            **asdict(result),
        )

        copy_map = build_copy_map(stage_entries, env_vars=stage_vars)
        stage = stage.merge(
            extract_packages_from_scripts(
                run_values,
                source_dir=source_dir,
                copy_map=copy_map,
                env_vars=stage_vars,
            )
        )

        if source_dir:
            file_pkgs, file_arch_pkgs = extract_packages_from_file_installs(
                run_values, copy_map, source_dir, env_vars=stage_vars
            )
            stage = stage.merge(
                StagePackages(packages=file_pkgs, arch_packages=file_arch_pkgs)
            )

        stages.append(stage)

    return stages


def resolve_builddep_packages(
    builddep_patterns: list[str],
    source_dir: Path,
) -> set[str]:
    """
    Resolve dnf builddep glob patterns to concrete BuildRequires by
    finding matching SRPMs in source_dir and extracting their
    dependencies via rpm -qpR.

    Spec files are intentionally not supported: rpmspec resolves macros
    using host system definitions, which may not match the build
    environment and would produce incorrect results.

    Arg(s):
        builddep_patterns (list[str]): Glob patterns from dnf builddep
            commands (e.g., ["pkcs11-helper*", "openvpn*"]).
        source_dir (Path): Directory containing SRPMs.
    Return Value(s):
        set[str]: Deduplicated package names from BuildRequires.
    """
    resolved: set[str] = set()

    for pattern in builddep_patterns:
        srpm_pattern = pattern if pattern.endswith(".src.rpm") else f"{pattern}.src.rpm"
        matching = [
            f
            for f in source_dir.iterdir()
            if f.is_file()
            and f.name.endswith(".src.rpm")
            and fnmatch.fnmatch(f.name, srpm_pattern)
        ]

        if not matching:
            logging.warning(
                "No SRPM matching '%s' found in %s, "
                "builddep packages will not be included in lockfile",
                pattern,
                source_dir,
            )
            continue

        for path in matching:
            logging.info("Extracting BuildRequires from %s", path.name)
            try:
                cmd = ["rpm", "-qpR", str(path)]
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode != 0:
                    logging.warning("%s failed: %s", " ".join(cmd), result.stderr)
                    continue
                for line in result.stdout.strip().splitlines():
                    req = line.strip().split()[0] if line.strip() else ""
                    # Skip rpmlib(...) — those are RPM-internal and not
                    # installable. Everything else (package names, file
                    # paths, virtual provides) is valid for resolution.
                    if req and not req.startswith("rpmlib("):
                        resolved.add(req)
            except Exception as exc:
                logging.warning(
                    "Failed to extract BuildRequires from %s: %s", path.name, exc
                )

    if resolved:
        logging.info(
            "Resolved %d builddep packages: %s", len(resolved), sorted(resolved)
        )
    return resolved


def select_stage(
    stages: list[StagePackages],
    stage_num: int | None = None,
    stage_name: str | None = None,
    image_pattern: str | None = None,
) -> StagePackages | None:
    """
    Select a single stage from analysis results, using the same matching
    logic as extract_image(): stage number (1-indexed), stage name (AS alias),
    image pattern (regex on base image), or default to last stage.

    Arg(s):
        stages (list[StagePackages]): Per-stage analysis results.
        stage_num (int | None): 1-indexed stage number to select.
        stage_name (str | None): Stage alias (FROM ... AS name) to match.
        image_pattern (str | None): Regex to match against base image.
    Return Value(s):
        StagePackages | None: Matching stage, or None if no match.
    """
    if not stages:
        return None

    if stage_num is not None:
        if 1 <= stage_num <= len(stages):
            return stages[stage_num - 1]
        return None

    if stage_name is not None:
        for stage in stages:
            if stage.stage_name == stage_name:
                return stage
        return None

    if image_pattern is not None:
        for stage in stages:
            if re.search(image_pattern, stage.base_image):
                return stage
        return None

    return stages[-1]
