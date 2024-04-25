from importlib.metadata import entry_points


def load():
    group = "rpm_lockfile.content_origins"
    try:
        # Python 3.10+
        eps = entry_points(group=group)
    except TypeError:
        # Python 3.9
        eps = entry_points()[group]
    return {c.name: c.load() for c in eps}
