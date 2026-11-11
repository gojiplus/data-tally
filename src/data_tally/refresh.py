"""Discover datasets, save a snapshot, and publish the latest complete tally."""

import argparse
import hashlib
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

from data_tally.cli import write_report
from data_tally.core import Client, FetchError, collect, load_datasets
from data_tally.discovery import discover, manifest_text, read_config


def publish(report: dict, snapshot: Path, reports: Path) -> None:
    if not report["complete"] or not report["discovery"]["complete"]:
        raise ValueError("Cannot publish an incomplete tally")
    for suffix in ("json", "md"):
        shutil.copyfile(snapshot / f"summary.{suffix}", reports / f"latest.{suffix}")
    shutil.copyfile(snapshot / "datasets.csv", reports / "latest.csv")
    badge = {
        "schemaVersion": 1,
        "label": "data downloads",
        "message": f"{report['total_downloads']:,}",
        "color": "2E7D32",
    }
    (reports / "badge.json").write_text(
        json.dumps(badge, indent=2) + "\n", encoding="utf-8"
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("datasets.toml"))
    parser.add_argument("--config", type=Path, default=Path("discovery.toml"))
    parser.add_argument("--reports", type=Path, default=Path("reports"))
    args = parser.parse_args(argv)
    try:
        datasets = load_datasets(args.manifest)
        config = read_config(args.config)
        snapshot = args.reports / datetime.now(UTC).strftime("%Y-%m-%dT%H%M%SZ")
        snapshot.mkdir(parents=True, exist_ok=False)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    shutil.copyfile(args.config, snapshot / "discovery.toml")
    try:
        datasets, discovery = discover(
            datasets, config, Client(snapshot / "discovery-raw")
        )
    except (FetchError, ValueError, KeyError, TypeError, AttributeError) as exc:
        discovery = {"complete": False, "error": str(exc), "added": []}
    manifest = manifest_text(datasets)
    (snapshot / "datasets.toml").write_text(manifest, encoding="utf-8")
    report = collect(datasets, Client(snapshot / "raw"))
    report["discovery"] = discovery
    report["manifest_sha256"] = hashlib.sha256(manifest.encode()).hexdigest()
    write_report(report, snapshot)
    if not discovery["complete"] or not report["complete"]:
        print(
            f"Refresh incomplete; retained last successful badge. See {snapshot}",
            file=sys.stderr,
        )
        return 1
    publish(report, snapshot, args.reports)
    args.manifest.write_text(manifest, encoding="utf-8")
    print(
        f"Published {report['total_downloads']:,} downloads "
        f"across {len(datasets)} datasets; "
        f"added {len(discovery['added'])} datasets. Report: {snapshot}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
