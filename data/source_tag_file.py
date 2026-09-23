"""Readable tag list with an optional lossless booru metadata record."""

import json

MARKER = "# VLTagger metadata: "


def read_source_tags(content):
    """Return the visible comma-separated tags, excluding structured metadata."""
    return content.split("\n" + MARKER, 1)[0].strip()


def read_source_metadata(content):
    for line in content.splitlines()[1:]:
        if line.startswith(MARKER):
            value = json.loads(line[len(MARKER):])
            if not isinstance(value, dict):
                raise ValueError("Invalid source tag metadata")
            return value
    return None
