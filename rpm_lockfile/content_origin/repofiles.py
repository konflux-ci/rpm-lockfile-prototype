import configparser
import json
import os
import subprocess

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
                    "varsFromContainerfile": {"type": "string"},
                    "varsFromImage": {"type": "string"},
                },
                "required": ["location"],
                "additionalProperties": False,
            },
        ],
    }

    def __init__(self, config_dir):
        self.session = requests.Session()
        self.config_dir = config_dir

    def collect(self, sources):
        for source in sources:
            repofile = self._get_repofile(source)
            yield from self.collect_repofile(repofile)

    def _get_repofile(self, source):
        if isinstance(source, str):
            return source
        return subst_vars(
            source["location"],
            self._get_image_labels(source.get("varsFromImage"))
            | self._get_containerfile_labels(source.get("varsFromContainerfile")),
        )

    def _get_image_labels(self, image_spec):
        if not image_spec:
            return {}
        cp = utils.logged_run(
            ["skopeo", "inspect", f"docker://{image_spec}"],
            stdout=subprocess.PIPE,
            check=True,
        )
        data = json.loads(cp.stdout)
        return data["Labels"]

    def _get_containerfile_labels(self, containerfile):
        if not containerfile:
            return {}
        return self._get_image_labels(
            utils.extract_image(os.path.join(self.config_dir, containerfile))
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


def subst_vars(template, vars):
    for key, value in vars.items():
        template = template.replace(f"{{{key}}}", value)
    return template
