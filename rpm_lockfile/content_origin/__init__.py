from importlib.metadata import entry_points
from dataclasses import dataclass, field


@dataclass(frozen=True, order=True)
class Repo:
    repoid: str
    kwargs: dict() = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data):
        repoid = data.pop("repoid")
        if not isinstance(data.get("baseurl", []), list):
            # If baseurl is not a list already, convert it to a list by
            # splitting on whitespace. If there's only one URL, this will
            # produce a list with a single item. But there may be multiple
            # baseurls.
            data["baseurl"] = data["baseurl"].split()

        if (
            "baseurl" not in data
            and "metalink" not in data
            and "mirrorlist" not in data
        ):
            raise RuntimeError(
                f"Repo {repoid} must specify one of baseurl/metalink/mirrorlist"
            )

        return cls(repoid=repoid, kwargs=data)


def load():
    group = "rpm_lockfile.content_origins"
    try:
        # Python 3.10+
        eps = entry_points(group=group)
    except TypeError:
        # Python 3.9
        eps = entry_points()[group]
    return {c.name: c.load() for c in eps}
