import argparse
import json
import sys

import jsonschema

from . import content_origin, utils


STRINGS = {
    "anyOf": [
        {"type": "string"},
        {"type": "array", "items": {"type": "string"}},
    ]
}


def get_schema():
    return {
        "$schema": "http://json-schema.org/draft-04/schema#",
        "type": "object",
        "properties": {
            # TODO Packages should not be required. If possible, they should be
            # extracted from other input files.
            "packages": {
                "type": "array",
                "items": {"type": "string"},
            },
            "arches": {
                "type": "array",
                "items": {"type": "string"},
            },
            "reinstallPackages": {
                "type": "array",
                "items": {"type": "string"},
            },
            "contentOrigin": {
                "type": "object",
                "properties": {
                    source_type: {"type": "array", "items": collector.schema}
                    for source_type, collector in content_origin.load().items()
                },
            },
            "context": {
                "type": "object",
                "anyOf": [
                    {
                        "additionalProperties": False,
                        "properties": {
                            "image": {"type": "string"},
                            "flatpak": {"type": "boolean"},
                        },
                    },
                    {
                        "additionalProperties": False,
                        "properties": {
                            "containerfile": utils.CONTAINERFILE_SCHEMA,
                            "flatpak": {"type": "boolean"},
                        },
                    },
                    {
                        "additionalProperties": False,
                        "properties": {"rpmOstreeTreefile": {"type": "string"}}
                    },
                    {
                        "additionalProperties": False,
                        "properties": {"localSystem": {"type": "boolean"}}
                    },
                    {
                        "additionalProperties": False,
                        "properties": {
                            "bare": {"type": "boolean"},
                            "flatpak": {"type": "boolean"},
                        }
                    },
                ],
            },
        },
        "required": ["contentOrigin"],
        "additionalProperties": False,
    }


def validate(config):
    try:
        jsonschema.validate(config, get_schema())
    except jsonschema.ValidationError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)


class HelpAction(argparse.Action):
    def __init__(self, option_strings, **kwargs):
        kwargs["nargs"] = 0
        super().__init__(option_strings, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        print(json.dumps(get_schema(), indent=2))
        parser.exit()
