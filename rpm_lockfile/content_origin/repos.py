import os

from . import Repo
from .. import utils


class RepoOrigin:
    schema = {
        "type": "object",
        "properties": {
            "repoid": {"type": "string"},
            "baseurl": {"type": "string"},
            "varsFromImage": {"type": "string"},
            "varsFromContainerfile": {"type": "string"},
        },
        "required": ["repoid", "baseurl"],
    }

    def __init__(self, config_dir):
        self.config_dir = config_dir

    def collect(self, sources):
        for source in sources:
            image = source.pop("varsFromImage", None)
            containerfile = source.pop("varsFromContainerfile", None)
            vars = utils.get_labels(image, self._get_container_file(containerfile))
            source["baseurl"] = utils.subst_vars(source["baseurl"], vars)
            yield Repo.from_dict(source)

    def _get_container_file(self, containerfile):
        if containerfile:
            return os.path.join(self.config_dir, containerfile)
        return None
