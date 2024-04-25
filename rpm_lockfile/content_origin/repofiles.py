import configparser
import os

import requests

from . import Repo

"""
The user specifies URL pointing to a .repo file in the input file. This module
will download the file and extract baseurls and repoids from it. Disabled
repositories are ignored.

The repos must have exactly one base url. Mirror lists are not supported. Any
repo level options are passed over to DNF.
"""


class RepofileOrigin:
    schema = {"type": "string"}

    def __init__(self, config_dir):
        self.session = requests.Session()
        self.config_dir = config_dir

    def collect(self, sources):
        for repofile in sources:
            yield from self.collect_repofile(repofile)

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
