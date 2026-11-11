"""Collect a dated download snapshot and write JSON, CSV, and Markdown."""

import argparse
import csv
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from data_tally.core import Client, collect, load_datasets


def cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def markdown(report: dict) -> str:
    rows = report["datasets"]
    counted = sum(r["status"] == "ok" for r in rows)
    label = "Total" if report["complete"] else "Partial subtotal"
    lines = [
        "# Dataset download tally",
        "",
        f"**{label}: {report['observed_downloads']:,} downloads** "
        f"across {counted}/{len(rows)} datasets.",
        "",
        f"Retrieved {report['finished_at']}.",
        "",
        "The sources count differently, so the total adds unlike units. Dataverse "
        "counts every file download. Zenodo counts downloads across all versions "
        "of a record. Hugging Face counts one download per IP address per "
        "5-minute window. None of these are unique users.",
        "",
        "| Source | Downloads observed | Datasets counted |",
        "| --- | ---: | ---: |",
    ]
    for name, subtotal in report["subtotals"].items():
        lines.append(
            f"| {name} | {subtotal['observed_downloads']:,} | "
            f"{subtotal['counted']}/{subtotal['datasets']} |"
        )
    lines += [
        "",
        "| Dataset | Source | Downloads | Note |",
        "| --- | --- | ---: | --- |",
    ]
    for row in sorted(rows, key=lambda r: (-(r["downloads"] or 0), r["id"])):
        value = f"{row['downloads']:,}" if row["status"] == "ok" else "UNAVAILABLE"
        lines.append(
            f"| [{cell(row['title'] or row['id'])}]({row['page']}) | "
            f"{row['source']} | {value} | {cell(row['note'])} |"
        )
    discovery = report.get("discovery")
    if discovery:
        lines += ["", "## Dataset discovery", ""]
        if discovery["complete"]:
            lines.append(f"Found {len(discovery['added'])} new datasets.")
            for dataset in discovery["added"]:
                lines.append(f"- {dataset['source']}/{dataset['id']}")
        else:
            lines.append(
                "Discovery failed; this snapshot covers the previous inventory. "
                "The public badge and latest report were not updated."
            )
            lines += ["", discovery["error"]]
    errors = [row for row in rows if row["status"] != "ok"]
    if errors:
        lines += ["", "## Unavailable counts", ""]
        for row in errors:
            lines.append(f"- {row['source']}/{row['id']}: {row['error']}")
    return "\n".join(lines) + "\n"


def write_report(report: dict, directory: Path) -> None:
    (directory / "summary.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    (directory / "summary.md").write_text(markdown(report), encoding="utf-8")
    columns = [
        "source",
        "id",
        "title",
        "downloads",
        "status",
        "fetched_at",
        "page",
        "url",
        "note",
        "error",
    ]
    with (directory / "datasets.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(report["datasets"])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", nargs="?", type=Path, default=Path("datasets.toml"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        datasets = load_datasets(args.manifest)
        directory = args.output or Path("reports") / datetime.now(UTC).strftime(
            "%Y-%m-%dT%H%M%SZ"
        )
        directory.mkdir(parents=True, exist_ok=False)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    manifest_bytes = args.manifest.read_bytes()
    (directory / "datasets.toml").write_bytes(manifest_bytes)
    report = collect(datasets, Client(directory / "raw"))
    report["manifest_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
    write_report(report, directory)
    label = "Total" if report["complete"] else "Partial subtotal"
    print(f"{label}: {report['observed_downloads']:,} downloads")
    print(f"Report: {directory / 'summary.md'}")
    if not report["complete"]:
        print("Some counts are unavailable; see the report.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
