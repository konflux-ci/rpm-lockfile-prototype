"""
Shell command parsing for RPM package extraction.

Provides bashlex-based AST walking to extract package names from
yum/dnf install and update commands in RUN bodies and shell scripts.
Handles variable assignments, arch-conditional blocks, subshell
expansions, and bash-to-POSIX preprocessing.
"""

import logging
import re
from dataclasses import dataclass, field

try:
    import bashlex
except ImportError:
    bashlex = None


# Shell subshell expressions that evaluate to the current architecture
ARCH_SUBSHELL_KEYWORDS = ("$(arch)", "$(uname -m)", "$(uname -p)")

# Shell variable names that hold the current architecture
ARCH_VAR_NAMES = ("HOSTTYPE", "ARCH")

# All arch keywords: subshells + variables in both $VAR and ${VAR} forms
ARCH_KEYWORDS = ARCH_SUBSHELL_KEYWORDS + tuple(
    form for name in ARCH_VAR_NAMES for form in (f"${name}", f"${{{name}}}")
)


_ARCH_PATTERN = "|".join(re.escape(kw).replace(r"\ ", r"\s+") for kw in ARCH_KEYWORDS)
ARCH_VALUE_RE = re.compile(
    rf"(?:{_ARCH_PATTERN})"
    r"[\"']?\s*==?\s*[\"']?(\w+)[\"']?"
)


_MAX_EXPANSION_DEPTH = 10

_RE_CONDITIONAL_SET = re.compile(r"\$\{(\w+):\+([^}]*)\}")  # ${VAR:+value}
_RE_CONDITIONAL_DEFAULT = re.compile(r"\$\{(\w+):-([^}]*)\}")  # ${VAR:-default}
_RE_BRACED_VAR = re.compile(r"\$\{(\w+)\}")  # ${VAR}
_RE_PLAIN_VAR = re.compile(r"\$(\w+)")  # $VAR


@dataclass
class _WalkContext:
    """
    Mutable state accumulated during bashlex AST walking.
    """

    variables: dict[str, str] = field(default_factory=dict)
    shell_vars: dict[str, str] = field(default_factory=dict)
    arch_shell_vars: dict[str, dict[str, str]] = field(default_factory=dict)
    packages: set[str] = field(default_factory=set)
    arch_packages: dict[str, set[str]] = field(default_factory=dict)
    update_targets: set[str] = field(default_factory=set)
    has_update: bool = False
    builddep_packages: set[str] = field(default_factory=set)
    module_specs: set[str] = field(default_factory=set)


def resolve_bash_expansion(text: str, variables: dict[str, str]) -> str:
    """
    Resolve bash-style variable expansions in text.

    Supports ${VAR:+value}, ${VAR:-default}, ${VAR}, and $VAR.
    Unresolved variables are replaced with empty string.
    Iterates up to _MAX_EXPANSION_DEPTH times to handle nested references.

    Arg(s):
        text (str): Text containing variable references.
        variables (dict[str, str]): Variable name to value mapping.
    Return Value(s):
        str: Text with variables resolved.
    """
    for _ in range(_MAX_EXPANSION_DEPTH):
        prev = text
        text = _RE_CONDITIONAL_SET.sub(lambda m: m.group(2) if variables.get(m.group(1)) else "", text)
        text = _RE_CONDITIONAL_DEFAULT.sub(lambda m: variables.get(m.group(1)) or m.group(2), text)
        text = _RE_BRACED_VAR.sub(lambda m: variables.get(m.group(1), ""), text)
        text = _RE_PLAIN_VAR.sub(lambda m: variables.get(m.group(1), ""), text)
        if text == prev:
            break
    return text


def _has_arch_test(text: str) -> bool:
    """
    Return True if text contains a known architecture-testing expression.
    """
    return any(kw in text for kw in ARCH_KEYWORDS)


def _extract_arch_values(text: str) -> list[str]:
    """
    Extract architecture names from conditional expressions.
    """
    return ARCH_VALUE_RE.findall(text)


def _preprocess_for_bashlex(text: str) -> str:
    """
    Convert bash-specific syntax to POSIX equivalents for bashlex.

    bashlex doesn't support ``[[ ]]`` (bash test) or ``==`` inside
    test brackets. Convert to ``[ ]`` and ``=`` respectively.
    """
    text = text.replace("[[", "[").replace("]]", "]")
    text = re.sub(r"(\[\s+\S+\s+)==(\s+\S+\s+\])", r"\1=\2", text)
    return text


def _is_valid_package_token(token: str) -> bool:
    """
    Return True if token looks like a package name or file path provide.
    """
    if not token or token.startswith("-") or token.endswith("-"):
        return False
    if "$" in token or "*" in token:
        return False
    if token in (">=", "<=", "==", ">", "<", "!="):
        return False
    return True


def _is_valid_builddep_token(token: str) -> bool:
    """
    Return True if token looks like a builddep argument (package name or glob).
    Allows glob wildcards (*) unlike _is_valid_package_token.
    """
    if not token or token.startswith("-") or token.endswith("-"):
        return False
    if "$" in token:
        return False
    if token in (">=", "<=", "==", ">", "<", "!="):
        return False
    return True


def _extract_condition_arch(node) -> list[str]:
    """
    Extract architecture names from an if/elif condition node.

    Looks for patterns like ``[ $(arch) = x86_64 ]`` or
    ``[ $ARCH = aarch64 ]`` in the condition's command parts.
    Handles ``||`` conditions by collecting arches from all branches.
    """
    if not hasattr(node, "parts"):
        return []
    arches: list[str] = []
    for part in node.parts:
        if hasattr(part, "parts"):
            words = [p.word for p in part.parts if hasattr(p, "word")]
            text = " ".join(words)
            if _has_arch_test(text):
                arches.extend(_extract_arch_values(text))
    return arches


def _walk_nodes(
    nodes: list,
    ctx: _WalkContext,
    arch_context: list[str] | None = None,
    in_conditional: bool = False,
):
    """
    Recursively walk bashlex AST nodes, extracting package names,
    variable assignments, and arch-conditional context.
    """
    for node in nodes:
        kind = node.kind

        if kind == "list":
            _walk_nodes(node.parts, ctx, arch_context, in_conditional)

        elif kind == "compound":
            for child in node.list:
                if child.kind == "if":
                    _walk_if_node(child, ctx)
                elif child.kind == "for":
                    body_nodes = [p for p in child.parts if hasattr(p, "kind") and p.kind in ("list", "command")]
                    _walk_nodes(body_nodes, ctx, arch_context, in_conditional)
                elif child.kind == "function":
                    body_nodes = [p for p in child.parts if hasattr(p, "kind") and p.kind == "compound"]
                    for body in body_nodes:
                        _walk_nodes([body], ctx, arch_context, in_conditional)
                else:
                    _walk_nodes([child], ctx, arch_context, in_conditional)

        elif kind == "command":
            _process_command_node(node, ctx, arch_context, in_conditional)

        elif kind == "pipeline":
            cmd_nodes = [p for p in node.parts if hasattr(p, "kind") and p.kind == "command"]
            for cmd_node in cmd_nodes:
                _process_command_node(cmd_node, ctx, arch_context, in_conditional)

        elif kind == "if":
            _walk_if_node(node, ctx)

        elif kind == "function":
            body_nodes = [p for p in node.parts if hasattr(p, "kind") and p.kind == "compound"]
            for body in body_nodes:
                _walk_nodes([body], ctx, arch_context, in_conditional)


def _walk_if_node(node, ctx: _WalkContext):
    """
    Walk an IfNode, detecting arch conditionals in if/elif branches.
    Also walks condition nodes for commands (e.g. ``if ! yum install``).
    """
    parts = list(node.parts)
    i = 0
    while i < len(parts):
        part = parts[i]
        if not hasattr(part, "word"):
            i += 1
            continue
        word = part.word

        if word in ("if", "elif"):
            condition = parts[i + 1] if i + 1 < len(parts) else None
            body = None
            for j in range(i + 2, len(parts)):
                if hasattr(parts[j], "word") and parts[j].word in ("then",):
                    if j + 1 < len(parts):
                        body = parts[j + 1]
                    break

            arch_ctx = None
            if condition:
                arch_ctx = _extract_condition_arch(condition) or None
                _walk_nodes([condition], ctx, arch_ctx, in_conditional=not bool(arch_ctx))

            if body:
                _walk_nodes([body], ctx, arch_ctx, in_conditional=not bool(arch_ctx))

        elif word == "else":
            if i + 1 < len(parts) and hasattr(parts[i + 1], "kind"):
                _walk_nodes([parts[i + 1]], ctx, None, in_conditional=True)

        i += 1


def _process_assignments(
    assignments: list,
    ctx: _WalkContext,
    arch_context: list[str] | None,
    in_conditional: bool,
):
    """
    Process shell variable assignments from a CommandNode.
    """
    for assign in assignments:
        raw = assign.word
        eq_idx = raw.index("=")
        var_name = raw[:eq_idx]
        var_value = raw[eq_idx + 1:]

        has_cmdsub = any(
            hasattr(p, "kind") and p.kind == "commandsubstitution"
            for p in (assign.parts if hasattr(assign, "parts") and assign.parts else [])
        )

        if has_cmdsub:
            extracted = _extract_subshell_packages(var_value)
            if extracted:
                ctx.shell_vars[var_name] = extracted
        elif arch_context:
            per_arch = ctx.arch_shell_vars.setdefault(var_name, {})
            for arch in arch_context:
                if arch in per_arch:
                    per_arch[arch] = f"{per_arch[arch]} {var_value}"
                else:
                    per_arch[arch] = var_value
        else:
            all_vars = {**ctx.variables, **ctx.shell_vars}
            resolved = resolve_bash_expansion(var_value, all_vars)
            if var_name in ctx.shell_vars and in_conditional:
                ctx.shell_vars[var_name] = f"{ctx.shell_vars[var_name]} {resolved}"
            else:
                ctx.shell_vars[var_name] = resolved


def _detect_pkg_action(word_values: list[str], ctx: _WalkContext) -> tuple[str | None, int]:
    """
    Detect install/update/upgrade action in a dnf/yum command.

    Return Value(s):
        tuple[str | None, int]: (action, action_index) or (None, -1).
    """
    first_word = word_values[0].lower() if word_values else ""
    if first_word not in ("dnf", "yum", "microdnf"):
        return None, -1

    for idx, w in enumerate(word_values[1:], 1):
        wl = w.lower()
        if wl == "install":
            return "install", idx
        if wl in ("update", "upgrade"):
            ctx.has_update = True
            return "update", idx
        if wl == "builddep":
            return "builddep", idx
        if wl == "module":
            for sub_idx, sub_w in enumerate(word_values[idx + 1:], idx + 1):
                sub_wl = sub_w.lower()
                if sub_wl in ("install", "enable"):
                    return "module", sub_idx
                if not sub_wl.startswith("-"):
                    break
            return None, -1

    return None, -1


def _resolve_arch_specific_tokens(
    pkg_words: list[str],
    raw_args: str,
    all_vars: dict[str, str],
    ctx: _WalkContext,
) -> set[str]:
    """
    Resolve arch-specific variable expansions and add per-arch packages.

    Return Value(s):
        set[str]: Tokens already classified as arch-specific (to exclude
            from the common package list).
    """
    arch_resolved: set[str] = set()
    if not ctx.arch_shell_vars:
        return arch_resolved

    used_arch_vars: dict[str, dict[str, str]] = {}
    for var_name, per_arch in ctx.arch_shell_vars.items():
        if f"${var_name}" in raw_args or f"${{{var_name}}}" in raw_args:
            used_arch_vars[var_name] = per_arch

    if not used_arch_vars:
        return arch_resolved

    no_arch_vars = dict.fromkeys(used_arch_vars, "")
    common_resolved_tokens = set()
    for pw in pkg_words:
        r = resolve_bash_expansion(pw, {**all_vars, **no_arch_vars})
        common_resolved_tokens.update(r.split())

    all_arches: set[str] = set()
    for per_arch in used_arch_vars.values():
        all_arches.update(per_arch.keys())
    for arch in all_arches:
        arch_var_vals = {vn: pa.get(arch, "") for vn, pa in used_arch_vars.items()}
        for pw in pkg_words:
            r = resolve_bash_expansion(pw, {**all_vars, **arch_var_vals})
            for token in r.split():
                if _is_valid_package_token(token) and token not in common_resolved_tokens:
                    ctx.arch_packages.setdefault(arch, set()).add(token)
                    arch_resolved.add(token)

    return arch_resolved


def _classify_package_tokens(
    resolved_tokens: list[str],
    arch_resolved_tokens: set[str],
    action: str,
    ctx: _WalkContext,
    arch_context: list[str] | None,
):
    """
    Classify resolved tokens into packages, update targets, or
    arch-specific packages.
    """
    skip_next = False
    for token in resolved_tokens:
        if skip_next:
            skip_next = False
            continue
        if token in (">=", "<=", "==", ">", "<", "!="):
            skip_next = True
            continue
        token = re.split(r"\s+(?:>=|<=|==|!=|>|<)\s+", token)[0].strip()

        if action == "builddep":
            if _is_valid_builddep_token(token):
                ctx.builddep_packages.add(token)
            continue

        if action == "module":
            if _is_valid_builddep_token(token) and ":" in token:
                ctx.module_specs.add(token)
            continue

        if not _is_valid_package_token(token):
            continue
        if token in arch_resolved_tokens:
            continue

        if action == "update":
            ctx.update_targets.add(token)
        elif arch_context:
            for arch in arch_context:
                ctx.arch_packages.setdefault(arch, set()).add(token)
        else:
            ctx.packages.add(token)


def _process_command_node(
    node,
    ctx: _WalkContext,
    arch_context: list[str] | None = None,
    in_conditional: bool = False,
):
    """
    Process a single CommandNode — handle assignments and dnf/yum commands.
    """
    assignments = []
    words = []

    for part in node.parts:
        if part.kind == "assignment":
            assignments.append(part)
        elif part.kind == "word":
            words.append(part)

    _process_assignments(assignments, ctx, arch_context, in_conditional)

    if not words:
        return

    word_values = [w.word for w in words]
    action, action_idx = _detect_pkg_action(word_values, ctx)
    if not action:
        return

    pkg_words = word_values[action_idx + 1:]
    all_vars = {**ctx.variables, **ctx.shell_vars}
    combined_arch_vals = {k: " ".join(per_arch.values()) for k, per_arch in ctx.arch_shell_vars.items()}
    all_vars_with_arch = {
        **all_vars,
        **{k: resolve_bash_expansion(v, all_vars) for k, v in combined_arch_vals.items()},
    }

    resolved_tokens: list[str] = []
    raw_args = " ".join(pkg_words)
    for pw in pkg_words:
        resolved = resolve_bash_expansion(pw, all_vars_with_arch)
        for sub in resolved.split():
            resolved_tokens.append(sub)

    arch_resolved_tokens = set[str]()
    if not arch_context:
        arch_resolved_tokens = _resolve_arch_specific_tokens(pkg_words, raw_args, all_vars, ctx)

    _classify_package_tokens(resolved_tokens, arch_resolved_tokens, action, ctx, arch_context)


def _extract_subshell_packages(subshell_body: str) -> str:
    """
    Extract package names from echo commands inside a $(...) subshell.

    Handles patterns like:
        $(if [ "$(uname -m)" != "s390x" ]; then echo -n mstflint; fi)
    """
    packages: list[str] = []
    for match in re.finditer(r"\becho\s+(?:-\w+\s+)*([\w\s-]+)", subshell_body):
        tokens = match.group(1).strip().split()
        for token in tokens:
            if token and not token.startswith("-") and re.match(r"^[\w][\w.\-]*$", token):
                packages.append(token)
    return " ".join(packages)


def _parse_and_walk(
    run_values: list[str],
    env_vars: dict[str, str] | None = None,
) -> tuple[set[str], dict[str, set[str]], set[str], bool, set[str], set[str]]:
    """
    Single pass: preprocess, parse with bashlex, and walk all RUN bodies.

    Arg(s):
        run_values (list[str]): RUN command bodies.
        env_vars (dict[str, str] | None): Variables from ARG/ENV directives.
    Return Value(s):
        tuple[set[str], dict[str, set[str]], set[str], bool, set[str], set[str]]:
            - Install packages (common to all arches).
            - Arch-specific install packages.
            - Update target packages.
            - Whether any update command was found.
            - Builddep package patterns.
            - Module specs (e.g., nodejs:18, nodejs:18/development).
    """
    if bashlex is None:
        raise ImportError(
            "bashlex is required for shell command parsing. "
            "Install it with: pip install bashlex"
        )
    logger = logging.getLogger(__name__)
    ctx = _WalkContext(variables=dict(env_vars or {}))

    for run_body in run_values:
        preprocessed = _preprocess_for_bashlex(run_body)
        try:
            ast_nodes = bashlex.parse(preprocessed)
        except (bashlex.errors.ParsingError, NotImplementedError, ValueError) as exc:
            logger.warning(f"bashlex failed to parse RUN body, skipping: {exc}")
            continue

        ctx.shell_vars = {}
        ctx.arch_shell_vars = {}
        _walk_nodes(ast_nodes, ctx)

    return ctx.packages, ctx.arch_packages, ctx.update_targets, ctx.has_update, ctx.builddep_packages, ctx.module_specs


def analyze_run_commands(
    run_values: list[str],
    env_vars: dict[str, str] | None = None,
) -> tuple[list[str], dict[str, list[str]], list[str], bool, list[str], list[str]]:
    """
    Single-pass analysis of RUN command bodies. Returns all install
    packages, arch-specific packages, update targets, update flag,
    builddep package patterns, and module specs.

    Arg(s):
        run_values (list[str]): RUN command bodies.
        env_vars (dict[str, str] | None): Variables from ARG/ENV directives.
    Return Value(s):
        tuple[list[str], dict[str, list[str]], list[str], bool, list[str], list[str]]:
            - Sorted common package names.
            - Dict mapping arch to sorted package names.
            - Sorted update target package names.
            - Whether any update command was found.
            - Sorted builddep package patterns.
            - Sorted module specs.
    """
    packages, arch_packages, update_targets, found_update, builddep_packages, module_specs = _parse_and_walk(
        run_values, env_vars
    )
    arch_result = {arch: sorted(pkgs) for arch, pkgs in sorted(arch_packages.items())}
    return (
        sorted(packages),
        arch_result,
        sorted(update_targets),
        found_update,
        sorted(builddep_packages),
        sorted(module_specs),
    )
