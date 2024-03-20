from importlib.metadata import entry_points


def load():
    return {
        c.name: c.load() for c in entry_points(group="lockfile_resolver.content_origins")
    }
