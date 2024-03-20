class RepoOrigin:
    schema = {
        "type": "object",
        "properties": {
            "repoid": {"type": "string"},
            "baseurl": {"type": "string"},
        },
        "required": ["repoid", "baseurl"],
    }

    def __init__(self, *args, **kwargs):
        pass

    def collect(self, sources):
        return sources
