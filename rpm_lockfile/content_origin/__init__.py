from importlib.metadata import entry_points
from dataclasses import dataclass, field



@dataclass(frozen=True, order=True)
class Repo:
    repoid: str
    baseurl: str
    kwargs: dict() = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data):
        repoid = data.pop("repoid")
        baseurl = data.pop("baseurl")
        return cls(repoid=repoid, baseurl=baseurl, kwargs=data)


def load():
    group = "rpm_lockfile.content_origins"
    try:
        # Python 3.10+
        eps = entry_points(group=group)
    except TypeError:
        # Python 3.9
        eps = entry_points()[group]
    return {c.name: c.load() for c in eps}
