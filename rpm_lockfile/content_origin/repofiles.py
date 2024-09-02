import configparser
import os

import requests

from . import Repo
from .. import utils

"""
The user specifies URL pointing to a .repo file in the input file. This module
will download the file and extract baseurls and repoids from it. Disabled
repositories are ignored.

The repos must have exactly one base url. Mirror lists are not supported. Any
repo level options are passed over to DNF.
"""


class RepofileOrigin:
    schema = {
        "oneOf": [
            {"type": "string"},
            {
                "type": "object",
                "properties": {
                    "location": {"type": "string"},
                    "varsFromContainerfile": utils.CONTAINERFILE_SCHEMA,
                    "varsFromImage": {"type": "string"},
                },
                "required": ["location"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "giturl": {"type": "string"},
                    "file": {"type": "string"},
                    "gitref": {"type": "string"},
                    "varsFromContainerfile": utils.CONTAINERFILE_SCHEMA,
                    "varsFromImage": {"type": "string"},
                },
                "required": ["giturl", "file", "gitref"],
                "additionalProperties": False,
            },
        ],
    }

    def __init__(self, config_dir):
        self.session = requests.Session()
        self.config_dir = config_dir

    def collect(self, sources):
        for source in sources:
            repofile = self._get_repofile_path(source)
            yield from self.collect_repofile(repofile)

    def _get_repofile_path(self, source):
        if isinstance(source, str):
            return source
        vars = utils.get_labels(source, self.config_dir)
        if "location" in source:
            return utils.subst_vars(source["location"], vars)
        return utils.get_file_from_git(
            utils.subst_vars(source["giturl"], vars),
            utils.subst_vars(source["gitref"], vars),
            utils.subst_vars(source["file"], vars),
        )

    def collect_repofile(self, url):
        if url.startswith("http"):
            yield from self.collect_http(url)
        else:
            yield from self.collect_local(url)

    def collect_http(self, url):
        resp = self.session.get(url, timeout=(2, 5))
        resp.raise_for_status()

        yield from self.parse_repofile(resp.text)

    def collect_local(self, url):
        with open(os.path.join(self.config_dir, url)) as f:
            yield from self.parse_repofile(f.read())

    def parse_repofile(self, contents):
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_string(contents)

        for section in parser.sections():
            options = {"repoid": section} | dict(parser.items(section))
            yield Repo.from_dict(options)
