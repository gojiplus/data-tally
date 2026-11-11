"""Find new datasets by author on each source; additions only, never removals."""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlencode

from data_tally.core import (
    DATAVERSE,
    HUGGINGFACE,
    ID_PATTERNS,
    ZENODO,
    Dataset,
    sort_datasets,
)

KEYS = ("dataverse_authors", "zenodo_creators", "huggingface_authors")
# Harvard's own DOI prefix. The search also returns records harvested from other
# repositories, whose files (and downloads) live elsewhere.
DATAVERSE_PREFIX = "doi:10.7910/DVN/"
DATAVERSE_PAGE = 100
# Zenodo rejects larger pages from unauthenticated clients.
ZENODO_PAGE = 25
HUGGINGFACE_LIMIT = 1000


def read_config(path: Path) -> dict:
    with path.open("rb") as stream:
        config = tomllib.load(stream)
    if set(config) - set(KEYS):
        raise ValueError(f"Unknown discovery fields: {sorted(set(config) - set(KEYS))}")
    for key in KEYS:
        values = config.setdefault(key, [])
        if not isinstance(values, list) or any(
            not isinstance(v, str) or not v for v in values
        ):
            raise ValueError(f"{key} must be a list of non-empty strings")
    return config


def candidate(source: str, identifier: str, title: str = "") -> Dataset | None:
    """Build an entry from remote text, which the depositor controls.

    An id the manifest loader would refuse, or a control character in a title,
    would otherwise be committed and break every later run.
    """
    if not isinstance(identifier, str) or not re.fullmatch(
        ID_PATTERNS[source], identifier
    ):
        return None
    title = title if isinstance(title, str) else ""
    clean = "".join(ch if ch.isprintable() else " " for ch in title)
    return Dataset(source, identifier, " ".join(clean.split()))


def sift(found: list, rejected: list, source: str, identifier, title="") -> None:
    dataset = candidate(source, identifier, title)
    if dataset:
        found.append(dataset)
    else:
        rejected.append({"source": source, "id": str(identifier), "why": "invalid id"})


def dataverse_candidates(client, author: str) -> tuple[list[Dataset], list[dict]]:
    found, rejected, start = [], [], 0
    while True:
        query = urlencode(
            {
                "q": f'authorName:"{author}"',
                "type": "dataset",
                "per_page": DATAVERSE_PAGE,
                "start": start,
            }
        )
        data = json.loads(client.fetch(f"{DATAVERSE}/api/search?{query}")["body"])
        if data.get("status") != "OK":
            raise ValueError("Dataverse search failed")
        items = data["data"]["items"]
        for item in items:
            pid = item["global_id"]
            if author not in item.get("authors", []):
                rejected.append({"source": "dataverse", "id": pid, "why": "author"})
            elif not pid.startswith(DATAVERSE_PREFIX):
                rejected.append({"source": "dataverse", "id": pid, "why": "harvested"})
            else:
                sift(found, rejected, "dataverse", pid, item.get("name", ""))
        start += DATAVERSE_PAGE
        if not items or start >= data["data"]["total_count"]:
            return found, rejected


def zenodo_candidates(client, creator: str) -> tuple[list[Dataset], list[dict]]:
    found, rejected, page = [], [], 1
    while True:
        query = urlencode(
            {"q": f'creators.name:"{creator}"', "size": ZENODO_PAGE, "page": page}
        )
        data = json.loads(client.fetch(f"{ZENODO}/api/records?{query}")["body"])
        hits = data["hits"]["hits"]
        for hit in hits:
            concept = str(hit["conceptrecid"])
            metadata = hit["metadata"]
            names = [c.get("name") for c in metadata.get("creators", [])]
            if creator not in names:
                rejected.append({"source": "zenodo", "id": concept, "why": "creator"})
            elif metadata.get("resource_type", {}).get("type") != "dataset":
                rejected.append(
                    {"source": "zenodo", "id": concept, "why": "not a dataset"}
                )
            else:
                sift(found, rejected, "zenodo", concept, metadata.get("title", ""))
        if not hits or page * ZENODO_PAGE >= data["hits"]["total"]:
            return found, rejected
        page += 1


def huggingface_candidates(client, author: str) -> tuple[list[Dataset], list[dict]]:
    query = urlencode({"author": author, "limit": HUGGINGFACE_LIMIT})
    items = json.loads(client.fetch(f"{HUGGINGFACE}/api/datasets?{query}")["body"])
    if not isinstance(items, list):
        raise ValueError("Hugging Face listing failed")
    if len(items) >= HUGGINGFACE_LIMIT:
        raise ValueError("Hugging Face listing needs pagination")
    found, rejected = [], []
    for item in items:
        if item["id"].split("/")[0] != author:
            rejected.append({"source": "huggingface", "id": item["id"], "why": "owner"})
        else:
            sift(found, rejected, "huggingface", item["id"])
    return found, rejected


def discover(datasets: list[Dataset], config: dict, client) -> tuple[list, dict]:
    searches = (
        ("dataverse_authors", dataverse_candidates),
        ("zenodo_creators", zenodo_candidates),
        ("huggingface_authors", huggingface_candidates),
    )
    known = {(d.source, d.id.lower()) for d in datasets}
    added, rejected = [], []
    for key, search in searches:
        for name in config[key]:
            found, skipped = search(client, name)
            rejected += skipped
            for dataset in found:
                if (dataset.source, dataset.id.lower()) not in known:
                    known.add((dataset.source, dataset.id.lower()))
                    added.append(dataset)
    report = {
        "complete": True,
        "added": [asdict(d) for d in sort_datasets(added)],
        "rejected": rejected,
    }
    return sort_datasets(datasets + added), report


def toml_string(value: str) -> str:
    # A JSON string is a valid TOML basic string, except that JSON leaves
    # U+007F raw and TOML forbids it. Python 3.11 has no TOML writer.
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


def manifest_text(datasets: list[Dataset]) -> str:
    lines = [
        "# Curated entries plus datasets found by author search; see README.md.",
    ]
    for dataset in sort_datasets(datasets):
        lines += ["", "[[datasets]]"]
        for key, value in asdict(dataset).items():
            if value:
                lines.append(f"{key} = {toml_string(value)}")
    return "\n".join(lines) + "\n"
