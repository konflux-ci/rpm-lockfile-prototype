"""
Shell command parsing for RPM package extraction.

Provides bashlex-based AST walking to extract package names from
yum/dnf install, update, and reinstall commands in RUN bodies and
shell scripts.
Handles variable assignments, subshell expansions, and bash-to-POSIX
preprocessing. Architecture-conditional blocks are evaluated against
a single target architecture passed as a parameter.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import NamedTuple

from rpm_lockfile.vendor import bashlex

# Shell subshell expressions that evaluate to the current architecture
ARCH_SUBSHELL_KEYWORDS = ("$(arch)", "$(uname -m)", "$(uname -p)", "$(go env GOARCH)")

# Shell variable names that hold the current architecture
ARCH_VAR_NAMES = ("HOSTTYPE", "ARCH", "GOARCH")

# All arch keywords: subshells + variables in both $VAR and ${VAR} forms
ARCH_KEYWORDS = ARCH_SUBSHELL_KEYWORDS + tuple(
    form for name in ARCH_VAR_NAMES for form in (f"${name}", f"${{{name}}}")
)


_ARCH_PATTERN = "|".join(re.escape(kw).replace(r"\ ", r"\s+") for kw in ARCH_KEYWORDS)
ARCH_VALUE_RE = re.compile(
    rf"(?:{_ARCH_PATTERN})"
    r"[\"']?\s*==?\s*[\"']?(\w+)[\"']?"
)


ARCH_NEQ_VALUE_RE = re.compile(
    rf"(?:{_ARCH_PATTERN})"
    r"""[\"']?\s*!=\s*[\"']?(\w+)[\"']?"""
)

_GO_TO_RPM_ARCH = {
    "amd64": "x86_64",
    "arm64": "aarch64",
}


_MAX_EXPANSION_DEPTH = 10

_RE_CONDITIONAL_SET = re.compile(r"\$\{(\w+):\+([^}]*)\}")  # ${VAR:+value}
_RE_CONDITIONAL_DEFAULT = re.compile(r"\$\{(\w+):-([^}]*)\}")  # ${VAR:-default}
_RE_BRACED_VAR = re.compile(r"\$\{(\w+)\}")  # ${VAR}
_RE_PLAIN_VAR = re.compile(r"\$(\w+)")  # $VAR


@dataclass
class RunCommandResult:
    """
    Aggregated result from analyzing RUN command bodies.
    """

    packages: list[str] = field(default_factory=list)
    update_targets: list[str] = field(default_factory=list)
    has_update: bool = False
    reinstall_targets: list[str] = field(default_factory=list)
    builddep_packages: list[str] = field(default_factory=list)
    module_specs: list[str] = field(default_factory=list)


@dataclass
class _WalkContext:
    """
    Mutable state accumulated during bashlex AST walking.
    """

    variables: dict[str, str] = field(default_factory=dict)
    arch: str | None = None
    shell_vars: dict[str, str] = field(default_factory=dict)
    packages: set[str] = field(default_factory=set)
    update_targets: set[str] = field(default_factory=set)
    has_update: bool = False
    reinstall_targets: set[str] = field(default_factory=set)
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
        text = _RE_CONDITIONAL_SET.sub(
            lambda m: m.group(2) if variables.get(m.group(1)) else "", text
        )
        text = _RE_CONDITIONAL_DEFAULT.sub(
            lambda m: variables.get(m.group(1)) or m.group(2), text
        )
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
    Return True if token looks like a package name, file path provide,
    or glob pattern (e.g. golang-*1.23*).
    """
    if not token or token.startswith("-") or token.endswith("-"):
        return False
    if "$" in token:
        return False
    return token not in (">=", "<=", "==", ">", "<", "!=")


def _normalize_arch_names(arches: list[str]) -> list[str]:
    """
    Normalize Go-style arch names (amd64, arm64) to RPM names
    (x86_64, aarch64). Names already in RPM form pass through unchanged.
    """
    return [_GO_TO_RPM_ARCH.get(a, a) for a in arches]


class _ConditionArches(NamedTuple):
    """Arches extracted from an if/elif condition.

    ``eq`` lists arches matched with ``=`` / ``==`` (the body runs on
    these arches).  ``neq`` lists arches matched with ``!=`` (the body
    runs on every arch *except* these).
    """

    eq: list[str]
    neq: list[str]


def _extract_condition_arch(node) -> _ConditionArches:
    """
    Extract architecture names from an if/elif condition node.

    Looks for patterns like ``[ $(arch) = x86_64 ]`` or
    ``[ $ARCH = aarch64 ]`` in the condition's command parts.
    Handles ``||`` conditions by collecting arches from all branches.

    Also detects ``!=`` (not-equal) patterns and returns them separately
    so the caller can compute the complement against the known arch set.
    """
    if not hasattr(node, "parts"):
        return _ConditionArches([], [])
    eq_arches: list[str] = []
    neq_arches: list[str] = []
    for part in node.parts:
        if hasattr(part, "parts"):
            words = [p.word for p in part.parts if hasattr(p, "word")]
            text = " ".join(words)
            if _has_arch_test(text):
                eq_arches.extend(
                    _normalize_arch_names(ARCH_VALUE_RE.findall(text))
                )
                neq_arches.extend(
                    _normalize_arch_names(ARCH_NEQ_VALUE_RE.findall(text))
                )
    return _ConditionArches(eq_arches, neq_arches)


def _eval_list_arch_test(test_node, operator: str, current_arch: str | None) -> bool | None:
    """
    Evaluate whether a ``[ test ] || cmd`` or ``[ test ] && cmd``
    pattern means the command should run on the current architecture.

    Return Value(s):
        True if the command should run, False if it should be skipped,
        None if the test is not an architecture test (treat as unconditional).
    """
    if not hasattr(test_node, "parts"):
        return None
    words = [p.word for p in test_node.parts if hasattr(p, "word")]
    if not words or words[0] != "[":
        return None
    text = " ".join(words)
    if not _has_arch_test(text):
        return None

    if current_arch is None:
        return None

    neq_arches = _normalize_arch_names(ARCH_NEQ_VALUE_RE.findall(text))
    eq_arches = _normalize_arch_names(ARCH_VALUE_RE.findall(text))

    if operator == "||":
        # [ test ] || cmd  — cmd runs when test fails
        # [ $(arch) != X ] || cmd  — test fails when arch IS X → run on X
        # [ $(arch) = X ] || cmd   — test fails when arch is NOT X → run on all except X
        if neq_arches:
            return current_arch in neq_arches
        if eq_arches:
            return current_arch not in eq_arches
    elif operator == "&&":
        # [ test ] && cmd  — cmd runs when test succeeds
        # [ $(arch) = X ] && cmd   — test succeeds when arch IS X → run on X
        # [ $(arch) != X ] && cmd  — test succeeds when arch is NOT X → run on all except X
        if eq_arches:
            return current_arch in eq_arches
        if neq_arches:
            return current_arch not in neq_arches

    return None


def _walk_list_node(
    node,
    ctx: _WalkContext,
    in_conditional: bool = False,
):
    """
    Walk a list node, detecting ``[ test ] || cmd`` and
    ``[ test ] && cmd`` arch-conditional patterns.
    """
    parts = node.parts
    consumed: set[int] = set()

    for i, part in enumerate(parts):
        if part.kind != "command" or i in consumed:
            continue
        words = [p.word for p in part.parts if hasattr(p, "word")]
        if not words or words[0] != "[":
            continue
        if i + 2 >= len(parts):
            continue
        op_node = parts[i + 1]
        if not hasattr(op_node, "op") or op_node.op not in ("||", "&&"):
            continue
        should_run = _eval_list_arch_test(part, op_node.op, ctx.arch)
        if should_run is None:
            continue
        consumed.add(i)
        consumed.add(i + 1)
        consumed.add(i + 2)
        if should_run:
            _walk_nodes([parts[i + 2]], ctx, in_conditional=False)

    remaining = [p for idx, p in enumerate(parts) if idx not in consumed]
    if remaining:
        _walk_nodes(remaining, ctx, in_conditional)


def _walk_nodes(
    nodes: list,
    ctx: _WalkContext,
    in_conditional: bool = False,
):
    """
    Recursively walk bashlex AST nodes, extracting package names
    and variable assignments.
    """
    for node in nodes:
        kind = node.kind

        if kind == "list":
            _walk_list_node(node, ctx, in_conditional)

        elif kind == "compound":
            for child in node.list:
                if child.kind == "if":
                    _walk_if_node(child, ctx)
                elif child.kind == "for":
                    body_nodes = [
                        p
                        for p in child.parts
                        if hasattr(p, "kind") and p.kind in ("list", "command")
                    ]
                    _walk_nodes(body_nodes, ctx, in_conditional)
                elif child.kind == "function":
                    body_nodes = [
                        p
                        for p in child.parts
                        if hasattr(p, "kind") and p.kind == "compound"
                    ]
                    for body in body_nodes:
                        _walk_nodes([body], ctx, in_conditional)
                else:
                    _walk_nodes([child], ctx, in_conditional)

        elif kind == "command":
            _process_command_node(node, ctx, in_conditional)

        elif kind == "pipeline":
            cmd_nodes = [
                p for p in node.parts if hasattr(p, "kind") and p.kind == "command"
            ]
            for cmd_node in cmd_nodes:
                _process_command_node(cmd_node, ctx, in_conditional)

        elif kind == "if":
            _walk_if_node(node, ctx)

        elif kind == "function":
            body_nodes = [
                p for p in node.parts if hasattr(p, "kind") and p.kind == "compound"
            ]
            for body in body_nodes:
                _walk_nodes([body], ctx, in_conditional)


def _walk_if_node(node, ctx: _WalkContext):
    """
    Walk an IfNode, evaluating arch conditionals against the current
    architecture. Non-arch conditionals are walked unconditionally
    (both branches) since we can't evaluate them statically.
    Also walks condition nodes for commands (e.g. ``if ! yum install``).
    """
    parts = list(node.parts)
    i = 0
    # Track whether any if/elif branch matched the current arch.
    # If so, the else branch should be skipped. None means no arch
    # condition was seen (non-arch if).
    arch_branch_matched: bool | None = None
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

            cond = _extract_condition_arch(condition) if condition else _ConditionArches([], [])
            if cond.eq or cond.neq:
                # This is an arch-conditional branch.
                if cond.eq:
                    matches = ctx.arch in _normalize_arch_names(cond.eq) if ctx.arch else False
                else:
                    # != condition: matches everything except the listed arches
                    matches = ctx.arch not in _normalize_arch_names(cond.neq) if ctx.arch else False
                if arch_branch_matched is None:
                    arch_branch_matched = False
                if matches:
                    arch_branch_matched = True
                    if condition:
                        _walk_nodes([condition], ctx, in_conditional=False)
                    if body:
                        _walk_nodes([body], ctx, in_conditional=False)
                # else: skip this branch entirely
            else:
                # Not an arch condition — walk both condition and body
                # as we can't evaluate statically.
                if condition:
                    _walk_nodes([condition], ctx, in_conditional=True)
                if body:
                    _walk_nodes([body], ctx, in_conditional=True)

        elif word == "else":
            if i + 1 < len(parts) and hasattr(parts[i + 1], "kind"):
                if arch_branch_matched is True:
                    # An arch branch matched; skip the else.
                    pass
                elif arch_branch_matched is False:
                    # Arch conditions were present but none matched;
                    # the else branch applies to this arch.
                    _walk_nodes([parts[i + 1]], ctx, in_conditional=False)
                else:
                    # No arch conditions at all — walk else as conditional.
                    _walk_nodes([parts[i + 1]], ctx, in_conditional=True)

        i += 1


def _process_assignments(
    assignments: list,
    ctx: _WalkContext,
    in_conditional: bool,
):
    """
    Process shell variable assignments from a CommandNode.
    """
    for assign in assignments:
        raw = assign.word
        eq_idx = raw.index("=")
        var_name = raw[:eq_idx]
        var_value = raw[eq_idx + 1 :]

        has_cmdsub = any(
            hasattr(p, "kind") and p.kind == "commandsubstitution"
            for p in (assign.parts if hasattr(assign, "parts") and assign.parts else [])
        )

        if has_cmdsub:
            extracted = _extract_subshell_packages(var_value, ctx.arch)
            if extracted:
                ctx.shell_vars[var_name] = extracted
            else:
                ctx.shell_vars[var_name] = ""
        else:
            all_vars = {**ctx.variables, **ctx.shell_vars}
            resolved = resolve_bash_expansion(var_value, all_vars)
            if var_name in ctx.shell_vars and in_conditional:
                ctx.shell_vars[var_name] = f"{ctx.shell_vars[var_name]} {resolved}"
            else:
                ctx.shell_vars[var_name] = resolved


def _detect_pkg_action(
    word_values: list[str], ctx: _WalkContext
) -> tuple[str | None, int]:
    """
    Detect install/update/upgrade/reinstall action in a dnf/yum command.

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
        if wl == "reinstall":
            return "reinstall", idx
        if wl in ("builddep", "build-dep"):
            return "builddep", idx
        if wl == "module":
            for sub_idx, sub_w in enumerate(word_values[idx + 1 :], idx + 1):
                sub_wl = sub_w.lower()
                if sub_wl in ("install", "enable"):
                    return "module", sub_idx
                if not sub_wl.startswith("-"):
                    break
            return None, -1

    return None, -1


def _classify_package_tokens(
    resolved_tokens: list[str],
    action: str,
    ctx: _WalkContext,
):
    """
    Classify resolved tokens into packages, update targets, reinstall
    targets, builddep packages, or module specs.
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
            if _is_valid_package_token(token):
                ctx.builddep_packages.add(token)
            continue

        if action == "module":
            if _is_valid_package_token(token) and ":" in token:
                ctx.module_specs.add(token)
            continue

        if not _is_valid_package_token(token):
            continue

        if action == "update":
            ctx.update_targets.add(token)
        elif action == "reinstall":
            ctx.reinstall_targets.add(token)
        else:
            ctx.packages.add(token)


def _process_command_node(
    node,
    ctx: _WalkContext,
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

    _process_assignments(assignments, ctx, in_conditional)

    if not words:
        return

    word_values = [w.word for w in words]
    all_vars = {**ctx.variables, **ctx.shell_vars}
    resolved_first = (
        resolve_bash_expansion(word_values[0], all_vars) if word_values else ""
    )
    resolved_word_values = (
        [resolved_first] + word_values[1:] if word_values else word_values
    )
    action, action_idx = _detect_pkg_action(resolved_word_values, ctx)
    if not action:
        return

    pkg_words = word_values[action_idx + 1 :]

    resolved_tokens: list[str] = []
    for pw in pkg_words:
        resolved = resolve_bash_expansion(pw, all_vars)
        resolved_tokens.extend(resolved.split())

    _classify_package_tokens(resolved_tokens, action, ctx)


def _eval_subshell_arch_condition(
    subshell_body: str, current_arch: str | None
) -> bool:
    """
    Evaluate whether a subshell with an architecture condition produces
    output for the current architecture.

    Arg(s):
        subshell_body (str): Text inside a $(...) subshell, e.g.
            'if [ "$(uname -m)" != "s390x" ]; then echo -n mstflint; fi'
        current_arch (str | None): Architecture being resolved.
    Return Value(s):
        bool: True if the subshell produces output for this arch.
    """
    if not _has_arch_test(subshell_body):
        return True

    if not current_arch:
        return True

    neq_arches = _normalize_arch_names(ARCH_NEQ_VALUE_RE.findall(subshell_body))
    eq_arches = _normalize_arch_names(ARCH_VALUE_RE.findall(subshell_body))

    if neq_arches and eq_arches:
        return True

    if neq_arches:
        return current_arch not in neq_arches

    if eq_arches:
        return current_arch in eq_arches

    return True


def _extract_subshell_packages(subshell_body: str, current_arch: str | None = None) -> str:
    """
    Extract package names from echo commands inside a $(...) subshell,
    evaluating any architecture condition against the current arch.

    Handles patterns like:
        $(if [ "$(uname -m)" != "s390x" ]; then echo -n mstflint; fi)
    """
    if not _eval_subshell_arch_condition(subshell_body, current_arch):
        return ""

    packages: list[str] = []
    for match in re.finditer(r"\becho\s+(?:-\w+\s+)*([\w\s-]+)", subshell_body):
        tokens = match.group(1).strip().split()
        for token in tokens:
            if (
                token
                and not token.startswith("-")
                and re.match(r"^[\w][\w.\-]*$", token)
            ):
                packages.append(token)
    return " ".join(packages)


def _parse_and_walk(
    run_values: list[str],
    env_vars: dict[str, str] | None = None,
    arch: str | None = None,
) -> _WalkContext:
    """
    Single pass: preprocess, parse with bashlex, and walk all RUN bodies.

    Arg(s):
        run_values (list[str]): RUN command bodies.
        env_vars (dict[str, str] | None): Variables from ARG/ENV directives.
        arch (str | None): Architecture being resolved, used for
            evaluating arch-conditional blocks.
    Return Value(s):
        _WalkContext: Walk context with accumulated packages and state.
    """
    logger = logging.getLogger(__name__)
    ctx = _WalkContext(
        variables=dict(env_vars or {}),
        arch=arch,
    )

    for run_body in run_values:
        preprocessed = _preprocess_for_bashlex(run_body)
        try:
            ast_nodes = bashlex.parse(preprocessed)
        except (bashlex.errors.ParsingError, NotImplementedError, ValueError) as exc:
            logger.warning(f"bashlex failed to parse RUN body, skipping: {exc}")
            continue

        ctx.shell_vars = {}
        _walk_nodes(ast_nodes, ctx)

    return ctx


def analyze_run_commands(
    run_values: list[str],
    env_vars: dict[str, str] | None = None,
    arch: str | None = None,
) -> RunCommandResult:
    """
    Single-pass analysis of RUN command bodies.

    Arg(s):
        run_values (list[str]): RUN command bodies.
        env_vars (dict[str, str] | None): Variables from ARG/ENV directives.
        arch (str | None): Architecture being resolved, used for
            evaluating arch-conditional blocks.
    Return Value(s):
        RunCommandResult: Sorted packages, update targets, builddep
            patterns, and module specs.
    """
    ctx = _parse_and_walk(run_values, env_vars, arch)
    return RunCommandResult(
        packages=sorted(ctx.packages),
        update_targets=sorted(ctx.update_targets),
        has_update=ctx.has_update,
        reinstall_targets=sorted(ctx.reinstall_targets),
        builddep_packages=sorted(ctx.builddep_packages),
        module_specs=sorted(ctx.module_specs),
    )
