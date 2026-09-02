"""
Merge per-arch lockfile fragments produced by separate resolution passes.
"""


def merge_arch_results(results: list[dict]) -> dict:
    """
    Merge multiple process_arch() results for the same architecture.

    Packages, sources, and module metadata are deduplicated by URL (falling
    back to name when URL is absent).

    Arg(s):
        results (list[dict]): Non-empty list of arch result dicts.
    Return Value(s):
        dict: Single merged arch result.
    """
    if not results:
        raise ValueError("merge_arch_results requires at least one result")
    if len(results) == 1:
        return results[0]

    arch = results[0]["arch"]
    packages: dict[str, dict] = {}
    sources: dict[str, dict] = {}
    module_metadata: dict[str, dict] = {}

    for result in results:
        if result["arch"] != arch:
            raise ValueError(
                f"Cannot merge arch results for different arches: "
                f"{arch!r} and {result['arch']!r}"
            )
        for pkg in result.get("packages", []):
            key = pkg.get("url") or pkg.get("name") or ""
            packages.setdefault(key, pkg)
        for src in result.get("source", []):
            key = src.get("url") or src.get("name") or ""
            sources.setdefault(key, src)
        for mod in result.get("module_metadata", []):
            key = mod.get("url") or ""
            module_metadata.setdefault(key, mod)

    return {
        "arch": arch,
        "packages": sorted(
            packages.values(), key=lambda item: item.get("url") or item.get("name") or ""
        ),
        "source": sorted(
            sources.values(), key=lambda item: item.get("url") or item.get("name") or ""
        ),
        "module_metadata": sorted(
            module_metadata.values(), key=lambda item: item.get("url") or ""
        ),
    }
