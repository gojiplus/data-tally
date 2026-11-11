import io
import json
import tomllib
import urllib.error

import pytest

from data_tally import cli, refresh
from data_tally.core import (
    Client,
    Dataset,
    FetchError,
    collect,
    count_url,
    load_datasets,
    parse_count,
)
from data_tally.discovery import discover, manifest_text, read_config

DV = Dataset("dataverse", "doi:10.7910/DVN/AAAAAA", "Rolls | parsed")
ZN = Dataset("zenodo", "111", "Bihar")
HF = Dataset("huggingface", "soodoku/pai")
BODIES = {
    "dataverse": json.dumps({"id": 1, "downloadCount": 100}),
    "zenodo": json.dumps({"conceptrecid": "111", "stats": {"downloads": 20}}),
    "huggingface": json.dumps({"id": "soodoku/pai", "downloadsAllTime": 3}),
}


class FakeClient:
    """Serves count bodies by source and canned search pages by URL fragment."""

    def __init__(self, fail=(), pages=None):
        self.fail = set(fail)
        self.pages = pages or {}
        self.calls = []

    def fetch(self, url):
        self.calls.append(url)
        for fragment, body in self.pages.items():
            if fragment in url:
                return {"body": json.dumps(body)}
        for dataset in (DV, ZN, HF):
            if url == count_url(dataset):
                if dataset.source in self.fail:
                    raise FetchError(f"HTTP 503: {url}")
                return {
                    "body": BODIES[dataset.source],
                    "fetched_at": "now",
                    "sha256": "0",
                }
        raise FetchError(f"HTTP 404: {url}")


def write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def test_manifest_round_trips_and_sorts(tmp_path):
    path = write(tmp_path / "d.toml", manifest_text([ZN, HF, DV]))
    assert load_datasets(path) == [DV, HF, ZN]
    assert tomllib.loads(path.read_text())["datasets"][0]["title"] == "Rolls | parsed"


@pytest.mark.parametrize(
    "text",
    [
        "",
        '[[datasets]]\nsource = "figshare"\nid = "1"\n',
        '[[datasets]]\nsource = "zenodo"\nid = "10.5281/zenodo.1"\n',
        '[[datasets]]\nsource = "dataverse"\nid = "hdl:1902.1/1"\n',
        '[[datasets]]\nsource = "zenodo"\nid = "1"\nowner = "x"\n',
        '[[datasets]]\nsource = "zenodo"\nid = 1\n',
        '[[datasets]]\nsource = "zenodo"\nid = "1"\n' * 2,
    ],
)
def test_manifest_rejects_invalid_input(tmp_path, text):
    with pytest.raises(ValueError):
        load_datasets(write(tmp_path / "d.toml", text))


@pytest.mark.parametrize(
    "dataset, body",
    [
        (DV, json.dumps({"downloadCount": "100"})),
        (DV, json.dumps({"downloadCount": -1})),
        (DV, json.dumps({"status": "ERROR"})),
        (DV, "[]"),
        (ZN, json.dumps({"conceptrecid": "999", "stats": {"downloads": 20}})),
        (ZN, json.dumps({"conceptrecid": "111", "stats": {"downloads": 20.5}})),
        (HF, json.dumps({"id": "other/pai", "downloadsAllTime": 3})),
        (HF, json.dumps({"id": "soodoku/pai", "downloadsAllTime": True})),
    ],
)
def test_parse_count_rejects_unusable_responses(dataset, body):
    with pytest.raises((ValueError, KeyError, TypeError)):
        parse_count(dataset, body)


def test_dataverse_count_includes_make_data_count_period():
    assert "includeMDC=true" in count_url(DV)
    assert "persistentId=doi%3A10.7910%2FDVN%2FAAAAAA" in count_url(DV)


def test_collect_totals_by_source():
    report = collect([DV, ZN, HF], FakeClient())
    assert report["complete"] and report["total_downloads"] == 123
    assert report["subtotals"]["dataverse"]["observed_downloads"] == 100
    assert report["subtotals"]["zenodo"]["counted"] == 1


def test_failed_lookup_is_unavailable_not_zero():
    report = collect([DV, ZN, HF], FakeClient(fail={"zenodo"}))
    row = next(r for r in report["datasets"] if r["source"] == "zenodo")
    assert row["downloads"] is None and row["status"] == "error"
    assert report["total_downloads"] is None and not report["complete"]
    assert report["observed_downloads"] == 103
    assert "UNAVAILABLE" in cli.markdown(report)


class Response(io.BytesIO):
    headers = {"Content-Type": "application/json", "Date": "today"}


def test_client_sets_user_agent_saves_snapshot_and_retries(tmp_path):
    seen, delays = [], []

    def opener(request, timeout):
        seen.append(request)
        if len(seen) == 1:
            raise urllib.error.HTTPError(request.full_url, 503, "busy", {}, None)
        return Response(b'{"downloadCount": 1}')

    client = Client(tmp_path / "raw", opener=opener, sleep=delays.append)
    record = client.fetch("https://example.org/x")
    assert record["body"] == '{"downloadCount": 1}' and delays == [2]
    assert seen[0].get_header("User-agent").startswith("data-tally/")
    saved = list((tmp_path / "raw").iterdir())
    assert len(saved) == 1 and json.loads(saved[0].read_text())["url"] == record["url"]


def test_client_does_not_retry_client_errors(tmp_path):
    def opener(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 403, "no", {}, None)

    with pytest.raises(FetchError, match="HTTP 403"):
        Client(tmp_path, opener=opener, sleep=lambda _: None).fetch("https://x.org")


CONFIG = {
    "dataverse_authors": ["Sood, Gaurav"],
    "zenodo_creators": ["Sood, Gaurav"],
    "huggingface_authors": ["soodoku"],
}


def dataverse_item(pid, authors=("Sood, Gaurav", "Other, A")):
    return {"global_id": pid, "name": f"title {pid}", "authors": list(authors)}


def zenodo_hit(concept, kind="dataset", creator="Sood, Gaurav"):
    return {
        "id": concept + 1,
        "conceptrecid": concept,
        "metadata": {
            "title": f"z{concept}",
            "creators": [{"name": creator}],
            "resource_type": {"type": kind},
        },
    }


def search_pages():
    return {
        "dataverse.harvard.edu/api/search": {
            "status": "OK",
            "data": {
                "total_count": 4,
                "items": [
                    dataverse_item(DV.id),
                    dataverse_item("doi:10.7910/DVN/NEWNEW"),
                    dataverse_item("doi:10.7910/DVN/NAMESAKE", ["Sood, Gaurav K."]),
                    dataverse_item("doi:10.25940/ROPER-1"),
                ],
            },
        },
        "zenodo.org/api/records?": {
            "hits": {
                "total": 3,
                "hits": [
                    zenodo_hit(222),
                    zenodo_hit(333, kind="software"),
                    zenodo_hit(444, creator="Sood, G."),
                ],
            }
        },
        "huggingface.co/api/datasets?": [{"id": "soodoku/new"}, {"id": "other/x"}],
    }


def test_discovery_adds_only_verified_new_datasets():
    datasets, report = discover([DV], CONFIG, FakeClient(pages=search_pages()))
    added = {(d["source"], d["id"]) for d in report["added"]}
    assert added == {
        ("dataverse", "doi:10.7910/DVN/NEWNEW"),
        ("zenodo", "222"),
        ("huggingface", "soodoku/new"),
    }
    assert DV in datasets and len(datasets) == 4
    reasons = {r["id"]: r["why"] for r in report["rejected"]}
    assert reasons == {
        "doi:10.7910/DVN/NAMESAKE": "author",
        "doi:10.25940/ROPER-1": "harvested",
        "333": "not a dataset",
        "444": "creator",
        "other/x": "owner",
    }


def test_discovery_follows_dataverse_pagination():
    pages = search_pages()
    pages["dataverse.harvard.edu/api/search"]["data"]["total_count"] = 150
    client = FakeClient(pages=pages)
    discover([DV], CONFIG, client)
    starts = [u for u in client.calls if "api/search" in u]
    assert len(starts) == 2 and "start=100" in starts[1]


def test_read_config_rejects_unknown_and_malformed_fields(tmp_path):
    assert read_config(write(tmp_path / "a.toml", ""))["zenodo_creators"] == []
    for text in ('pypi_owner = "x"\n', 'zenodo_creators = "Sood"\n'):
        with pytest.raises(ValueError):
            read_config(write(tmp_path / "b.toml", text))


def run_refresh(tmp_path, monkeypatch, client):
    write(tmp_path / "datasets.toml", manifest_text([DV, ZN, HF]))
    write(
        tmp_path / "discovery.toml",
        "".join(f"{k} = {json.dumps(v)}\n" for k, v in CONFIG.items()),
    )
    monkeypatch.setattr(refresh, "Client", lambda directory: client)
    return refresh.main(
        [
            "--manifest",
            str(tmp_path / "datasets.toml"),
            "--config",
            str(tmp_path / "discovery.toml"),
            "--reports",
            str(tmp_path / "reports"),
        ]
    )


def test_refresh_publishes_badge_only_when_complete(tmp_path, monkeypatch):
    pages = {
        "dataverse.harvard.edu/api/search": {
            "status": "OK",
            "data": {"total_count": 1, "items": [dataverse_item(DV.id)]},
        },
        "zenodo.org/api/records?": {"hits": {"total": 0, "hits": []}},
        "huggingface.co/api/datasets?": [],
    }
    assert run_refresh(tmp_path, monkeypatch, FakeClient(pages=pages)) == 0
    badge = json.loads((tmp_path / "reports" / "badge.json").read_text())
    assert badge == {
        "schemaVersion": 1,
        "label": "data downloads",
        "message": "123",
        "color": "2E7D32",
    }
    assert (tmp_path / "reports" / "latest.md").exists()


@pytest.mark.parametrize("broken", ["count", "discovery"])
def test_refresh_keeps_previous_badge_on_failure(tmp_path, monkeypatch, broken):
    pages = {
        "dataverse.harvard.edu/api/search": {
            "status": "OK" if broken == "count" else "ERROR",
            "data": {"total_count": 0, "items": []},
        },
        "zenodo.org/api/records?": {"hits": {"total": 0, "hits": []}},
        "huggingface.co/api/datasets?": [],
    }
    client = FakeClient(fail={"zenodo"} if broken == "count" else (), pages=pages)
    before = (tmp_path / "datasets.toml", manifest_text([DV, ZN, HF]))
    assert run_refresh(tmp_path, monkeypatch, client) == 1
    assert not (tmp_path / "reports" / "badge.json").exists()
    assert before[0].read_text() == before[1]
    snapshots = [p for p in (tmp_path / "reports").iterdir() if p.is_dir()]
    assert (snapshots[0] / "summary.md").exists()
