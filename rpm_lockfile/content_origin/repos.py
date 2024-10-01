from . import Repo
from .. import utils


class RepoOrigin:
    schema = {
        "type": "object",
        "properties": {
            "repoid": {"type": "string"},
            "baseurl": {"type": "string"},
            "varsFromImage": {"type": "string"},
            "varsFromContainerfile": utils.CONTAINERFILE_SCHEMA,
        },
        "required": ["repoid"],
        "anyOf": [
            {"required": ["baseurl"]},
            {"required": ["metalink"]},
            {"required": ["mirrorlist"]},
        ],
    }

    def __init__(self, config_dir):
        self.config_dir = config_dir

    def collect(self, sources):
        for source in sources:
            vars = utils.get_labels(source, self.config_dir)
            if "baseurl" in source:
                source["baseurl"] = utils.subst_vars(source["baseurl"], vars)
            yield Repo.from_dict(source)
