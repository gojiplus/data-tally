# data-tally

[![Data downloads](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fgojiplus%2Fdata-tally%2Fmain%2Freports%2Fbadge.json)](reports/latest.md)

Count downloads across a list of datasets on Harvard Dataverse, Zenodo, and
Hugging Face. Produces per-dataset counts, per-source subtotals, and a combined
tally, with the original responses saved alongside the report. The included
inventory covers datasets that list Gaurav Sood as an author.

[View the latest tally](reports/latest.md), or browse the dated snapshots in
[reports](reports). The badge always represents the latest successful refresh.

## What is counted

| Source | Counter | Unit |
| --- | --- | --- |
| Dataverse | `/api/datasets/:persistentId/download/count?includeMDC=true` | Every file download, all versions |
| Zenodo | `stats.downloads` of the record | Downloads across all versions |
| Hugging Face | `downloadsAllTime` | One per IP address per 5-minute window |

The total adds unlike units. A Dataverse dataset with 50 files downloaded once
as a bundle counts 50; a Hugging Face dataset fetched once counts 1. None of the
counters are unique users. Read the per-source subtotals in the report before
comparing sources.

The Dataverse counter was checked against a second route: for a collection, the
sum of per-dataset counts equals `/api/info/metrics/downloads?parentAlias=...`,
provided deaccessioned datasets are included. Deaccessioned datasets keep their
download history but are hidden from search, so they are listed in
`datasets.toml` by hand with a note.

## Run

Requires Python 3.11 or newer. No runtime dependencies or API keys.

```sh
python -m pip install -e .
data-tally datasets.toml
```

Each run creates a new timestamped directory under `reports/` containing
`summary.md`, `summary.json`, `datasets.csv`, a copy of the input manifest, and
`raw/` response snapshots. A failed lookup stays unavailable, never zero: the
command still writes the partial report but exits with status 1, its JSON
`total_downloads` is null, and `observed_downloads` is the subtotal of
successful lookups. Invalid input exits 2.

## Weekly updates and dataset discovery

The **Tally** GitHub Actions workflow runs every Monday at 08:17 UTC, or on
demand. It discovers new datasets, collects totals, and commits the updated
inventory, dated snapshot, and badge. Run the same refresh locally with:

```sh
data-tally-refresh
```

[discovery.toml](discovery.toml) names the author to search for on each source:

- **Dataverse:** datasets whose author list contains the exact name. Records
  harvested from other repositories are skipped, because their files and
  downloads live elsewhere.
- **Zenodo:** records of type `dataset` whose creators contain the exact name.
  Each is stored by its concept record id, which is stable across versions.
- **Hugging Face:** datasets owned by the listed account.

Discovery trusts the author name a depositor typed. Anyone can deposit a record
under the same name and it will be added on the next run, so skim the
"Dataset discovery" section of each report and delete entries that are not
yours.

Discovery adds entries; it never removes them. It cannot see deaccessioned
Dataverse datasets, datasets filed under a different spelling of the name, or
datasets on other Dataverse installations. Add those to `datasets.toml` by hand.

`reports/latest.md`, `latest.json`, `latest.csv`, and `badge.json` update only
after both discovery and every download lookup succeed. A larger inventory can
raise the total by a new dataset's whole history, so the difference between two
snapshots is not necessarily downloads during the intervening week.

## Your own datasets

Edit `datasets.toml` and `discovery.toml`, or supply another manifest:

```toml
[[datasets]]
source = "dataverse"
id = "doi:10.7910/DVN/LIIBNB"

[[datasets]]
source = "zenodo"
id = "22772026"

[[datasets]]
source = "huggingface"
id = "soodoku/pai"
```

`title` and `note` are optional.

## Develop

```sh
python -m pip install -e '.[dev]'
make ci
make ci-docker
```
