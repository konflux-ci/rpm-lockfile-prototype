import os

import productmd
import requests

from .. import schema


class ComposeOrigin:
    schema = {
        "anyOf": [
            {
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "latest": {
                        "type": "object",
                        "properties": {
                            "release_short": {"type": "string"},
                            "release_version": {"type": "string"},
                            "release_type": {"type": "string"},
                            "tag": schema.STRINGS,
                        },
                    }
                },
                "required": ["latest"],
                "additionalProperties": False,
            },
        ]
    }

    def __init__(self):
        self.session = requests.Session()
        try:
            self.cts_url = os.environ["CTS_URL"].rstrip("/")
        except KeyError:
            raise RuntimeError("Env var 'CTS_URL' is not defined.")

    def collect(self, sources):
        for spec in sources:
            yield from self.collect_from_spec(spec)

    def collect_from_spec(self, spec):
        key = list(spec.keys())[0]
        collector = getattr(self, f"collect_by_{key}")
        yield from collector(spec[key])

    def collect_from_url(self, compose_url):
        compose = productmd.Compose(compose_url)
        for variant in compose.info.variants.variants.values():
            paths = set()
            for arch, path in variant.paths.repository.items():
                paths.add(path.replace(arch, "$basearch"))
            if len(paths) != 1:
                raise RuntimeError("Unexpected compose metadata")
            yield {
                "repoid": f"{compose.info.compose.id}-{variant.uid}-rpms",
                "baseurl": f"{compose.compose_path}/{paths.pop()}",
            }

    def collect_by_id(self, compose_id):
        resp = self.session.get(f"{self.cts_url}/api/1/composes/{compose_id}")
        resp.raise_for_status()
        data = resp.json()
        yield from self.collect_from_url(data["compose_url"])

    def collect_by_latest(self, filters):
        resp = self.session.get(f"{self.cts_url}/api/1/composes/", params=filters)
        resp.raise_for_status()
        data = resp.json()["items"][0]
        yield from self.collect_from_url(data["compose_url"])
