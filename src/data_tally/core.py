"""Manifest validation, HTTP snapshots, source parsers, and aggregation."""

from __future__ import annotations

import hashlib
import json
import re
import time
import tomllib
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urlencode

DATAVERSE = "https://dataverse.harvard.edu"
ZENODO = "https://zenodo.org"
HUGGINGFACE = "https://huggingface.co"

ID_PATTERNS = {
    "dataverse": r"doi:10\.[0-9]+/[^\s]+",
    # Concept record id: stable across versions, unlike the version record id.
    "zenodo": r"[0-9]+",
    "huggingface": r"[A-Za-z0-9][\w.-]*/[\w.-]+",
}
COVERAGE = {
    "dataverse": "File downloads, all versions, including before Make Data Count",
    "zenodo": "Downloads across all versions",
    "huggingface": "All-time downloads; one per IP per 5-minute window",
}


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class Dataset:
    source: str
    id: str
    title: str = ""
    note: str = ""


def load_datasets(path: Path) -> list[Dataset]:
    with path.open("rb") as stream:
        manifest = tomllib.load(stream)
    rows = manifest.get("datasets")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Manifest must contain at least one [[datasets]] entry")
    datasets = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) - set(Dataset.__dataclass_fields__):
            raise ValueError(f"Unknown dataset fields: {row}")
        if any(not isinstance(value, str) for value in row.values()):
            raise ValueError("Dataset fields must be strings")
        source = row.get("source")
        if source not in ID_PATTERNS:
            raise ValueError(f"source must be one of {sorted(ID_PATTERNS)}")
        identifier = row.get("id")
        if not identifier or not re.fullmatch(ID_PATTERNS[source], identifier):
            raise ValueError(f"Invalid {source} id: {identifier!r}")
        key = (source, identifier.lower())
        if key in seen:
            raise ValueError(f"Duplicate dataset: {source}/{identifier}")
        seen.add(key)
        datasets.append(Dataset(**row))
    return sort_datasets(datasets)


def sort_datasets(datasets: list[Dataset]) -> list[Dataset]:
    return sorted(datasets, key=lambda d: (d.source, d.id.lower()))


class FetchError(RuntimeError):
    """A source could not provide a usable response."""


class Client:
    def __init__(self, directory: Path, *, opener=None, sleep=time.sleep):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.opener = opener or urllib.request.urlopen
        self.sleep = sleep

    def fetch(self, url: str) -> dict:
        # Harvard Dataverse answers 403 to urllib's default User-Agent.
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "data-tally/0.1 (https://github.com/gojiplus/data-tally)"
            },
        )
        for attempt in range(3):
            try:
                with self.opener(request, timeout=60) as response:
                    body = response.read()
                    record = {
                        "url": url,
                        "fetched_at": timestamp(),
                        "sha256": hashlib.sha256(body).hexdigest(),
                        "content_type": response.headers.get("Content-Type"),
                        "server_date": response.headers.get("Date"),
                        "body": body.decode("utf-8"),
                    }
                filename = hashlib.sha256(url.encode()).hexdigest() + ".json"
                (self.directory / filename).write_text(
                    json.dumps(record, indent=2) + "\n", encoding="utf-8"
                )
                return record
            except urllib.error.HTTPError as exc:
                if exc.code not in {429, 500, 502, 503, 504} or attempt == 2:
                    raise FetchError(f"HTTP {exc.code}: {url}") from exc
                retry = exc.headers.get("Retry-After", "")
                delay = int(retry) if retry.isdecimal() else 2 ** (attempt + 1)
                if delay > 60:
                    raise FetchError(
                        f"Rate limited; retry after {delay} seconds: {url}"
                    ) from exc
                self.sleep(delay)
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                if attempt == 2:
                    raise FetchError(f"Connection failed: {url}: {exc}") from exc
                self.sleep(2 ** (attempt + 1))
        raise AssertionError("Unreachable")


def count_url(dataset: Dataset) -> str:
    if dataset.source == "dataverse":
        # Without includeMDC the count stops at the Make Data Count start date.
        query = urlencode({"includeMDC": "true", "persistentId": dataset.id})
        return f"{DATAVERSE}/api/datasets/:persistentId/download/count?{query}"
    if dataset.source == "zenodo":
        return f"{ZENODO}/api/records/{dataset.id}"
    query = urlencode({"expand[]": "downloadsAllTime"})
    return f"{HUGGINGFACE}/api/datasets/{quote(dataset.id)}?{query}"


def page_url(dataset: Dataset) -> str:
    if dataset.source == "dataverse":
        query = urlencode({"persistentId": dataset.id})
        return f"{DATAVERSE}/dataset.xhtml?{query}"
    if dataset.source == "zenodo":
        return f"{ZENODO}/records/{dataset.id}"
    return f"{HUGGINGFACE}/datasets/{dataset.id}"


def count(value) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"Downloads must be a nonnegative integer: {value!r}")
    return value


def parse_count(dataset: Dataset, body: str) -> int:
    data = json.loads(body)
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object")
    if dataset.source == "dataverse":
        return count(data["downloadCount"])
    if dataset.source == "zenodo":
        if str(data["conceptrecid"]) != dataset.id:
            raise ValueError("Zenodo returned a different concept record")
        return count(data["stats"]["downloads"])
    if data["id"] != dataset.id:
        raise ValueError("Hugging Face returned a different dataset")
    return count(data["downloadsAllTime"])


def collect(datasets: list[Dataset], client: Client) -> dict:
    started = timestamp()
    results = []
    for dataset in datasets:
        url = count_url(dataset)
        row = {
            **asdict(dataset),
            "url": url,
            "page": page_url(dataset),
            "coverage": COVERAGE[dataset.source],
            "downloads": None,
            "status": "error",
        }
        try:
            record = client.fetch(url)
            row["fetched_at"] = record["fetched_at"]
            row["response_sha256"] = record["sha256"]
            row["downloads"] = parse_count(dataset, record["body"])
            row["status"] = "ok"
        except (FetchError, ValueError, KeyError, TypeError) as exc:
            row["error"] = str(exc)
        results.append(row)
    subtotals = {}
    for source in ID_PATTERNS:
        selected = [r for r in results if r["source"] == source]
        good = [r for r in selected if r["status"] == "ok"]
        subtotals[source] = {
            "datasets": len(selected),
            "counted": len(good),
            "complete": len(good) == len(selected),
            "observed_downloads": sum(r["downloads"] for r in good),
        }
    complete = all(r["status"] == "ok" for r in results)
    observed = sum(s["observed_downloads"] for s in subtotals.values())
    return {
        "started_at": started,
        "finished_at": timestamp(),
        "complete": complete,
        "total_downloads": observed if complete else None,
        "observed_downloads": observed,
        "subtotals": subtotals,
        "datasets": results,
    }
