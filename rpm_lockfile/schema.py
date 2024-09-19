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
        "$defs": {
            # Either a single string or a list of strings
            "strings": {
                "oneOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "string"}},
                ],
            },
            # Either just a string with name, or an object with arch specification
            "pkg": {
                "oneOf": [
                    {"type": "string"},
                    {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "arches": {
                                "oneOf": [
                                    {
                                        "type": "object",
                                        "properties": {
                                            "only": {"$ref": "#/$defs/strings"},
                                        },
                                        "additionalProperties": False,
                                        "required": ["only"],
                                    },
                                    {
                                        "type": "object",
                                        "properties": {
                                            "not": {"$ref": "#/$defs/strings"},
                                        },
                                        "additionalProperties": False,
                                        "required": ["not"],
                                    },
                                ],
                            }
                        },
                        "required": ["name"],
                    }
                ],
            }
        },
        "type": "object",
        "properties": {

            "packages": {
                "type": "array",
                "items": {"$ref": "#/$defs/pkg"},
            },

            "arches": {
                "type": "array",
                "items": {"type": "string"},
            },
            "reinstallPackages": {
                "type": "array",
                "items": {"$ref": "#/$defs/pkg"},
            },
            "moduleEnable": {
                "type": "array",
                "items": {"$ref": "#/$defs/pkg"},
            },
            "moduleDisable": {
                "type": "array",
                "items": {"$ref": "#/$defs/pkg"},
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
            "allowerasing": {"type": "boolean"},
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
