"""Tests for the flowchart sidecar loader.

Uses the REAL committed sidecars in tools/ingestion/flowcharts/data/ as fixtures
so the tests also guard the authored data (valid JSON, graph integrity, every
decision node has yes+no branches, every edge resolves, all reachable).
"""

import io
import os

import flowcharts
import pytest
from botocore.exceptions import ClientError

_DATA_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "..", "tools", "ingestion", "flowcharts", "data"
)


def _load_local(flowchart_id: str) -> bytes:
    with open(os.path.join(_DATA_DIR, f"{flowchart_id}.json"), "rb") as f:
        return f.read()


class _FakeS3:
    def __init__(self, objects: dict[str, bytes]):
        self._objects = objects
        self.calls = 0

    def get_object(self, Bucket: str, Key: str):  # noqa: N803 (boto3 kwarg names)
        self.calls += 1
        if Key not in self._objects:
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "missing"}}, "GetObject")
        return {"Body": io.BytesIO(self._objects[Key])}


def _s3_with(flowchart_id: str) -> _FakeS3:
    return _FakeS3({f"flowcharts/{flowchart_id}.json": _load_local(flowchart_id)})


@pytest.fixture(autouse=True)
def _clear_cache():
    flowcharts._cache.clear()
    yield
    flowcharts._cache.clear()


def test_list_flowcharts_returns_registry():
    result = flowcharts.list_flowcharts()
    ids = {f["flowchart_id"] for f in result}
    assert "flowcharts-mobile-home" in ids
    assert "flowcharts-ag-classification" in ids
    assert len(result) == 6
    assert all({"title", "summary", "statute", "wpam_page"} <= set(f) for f in result)


def test_list_flowcharts_hides_router_description():
    # router_description is an internal routing anchor — never surfaced to the model.
    result = flowcharts.list_flowcharts()
    assert all("router_description" not in f for f in result)


def test_get_flowchart_loads_and_caches():
    s3 = _s3_with("flowcharts-mobile-home")
    r1 = flowcharts.get_flowchart("flowcharts-mobile-home", raw_bucket="b", s3_client=s3)
    assert r1["title"].startswith("Determining if a Mobile Home")
    assert "nodes" in r1 and "edges" in r1
    assert "disclaimer" in r1 and "thorough review" in r1["disclaimer"]
    # Second call served from cache — no extra S3 hit.
    r2 = flowcharts.get_flowchart("flowcharts-mobile-home", raw_bucket="b", s3_client=s3)
    assert r2 == r1
    assert s3.calls == 1


def test_get_flowchart_unknown_id():
    r = flowcharts.get_flowchart("flowcharts-bogus", raw_bucket="b", s3_client=_FakeS3({}))
    assert "error" in r
    # The error lists the valid ids so the model can retry without a
    # list_flowcharts round-trip.
    assert set(r["available"]) == {f["flowchart_id"] for f in flowcharts.FLOWCHART_REGISTRY}
    assert "list_flowcharts" in r["error"]


class TestIdResolution:
    """Near-miss ids resolve instead of burning a turn on 'Unknown flowchart'."""

    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            ("flowcharts-trust-public", "flowcharts-trust-public"),
            # The id the agent actually guessed in production (Wednesday trace).
            ("flowcharts-trust-public-interest", "flowcharts-trust-public"),
            ("mobile-home", "flowcharts-mobile-home"),
            ("flowcharts-mobile-homes", "flowcharts-mobile-home"),
            ("FLOWCHARTS-BIBLE-CAMP", "flowcharts-bible-camp"),
            ("flowcharts_ag_classification", "flowcharts-ag-classification"),
            ("flowcharts-exempt-70-11-general", "flowcharts-exempt-70-11"),
            ("  flowcharts-manufacturing  ", "flowcharts-manufacturing"),
        ],
    )
    def test_resolves(self, given, expected):
        assert flowcharts.resolve_flowchart_id(given) == expected

    @pytest.mark.parametrize("given", ["", "   ", "worksheets-tidbase", "flowcharts-nonsense-xyz"])
    def test_unresolvable(self, given):
        assert flowcharts.resolve_flowchart_id(given) is None

    def test_near_miss_loads_the_chart_and_says_so(self):
        s3 = _s3_with("flowcharts-trust-public")
        r = flowcharts.get_flowchart(
            "flowcharts-trust-public-interest", raw_bucket="b", s3_client=s3
        )
        assert "error" not in r
        assert r["flowchart_id"] == "flowcharts-trust-public"
        assert r["resolved_from"] == "flowcharts-trust-public-interest"
        # The cached sidecar is never annotated with the caller's typo.
        assert "resolved_from" not in flowcharts._cache["flowcharts-trust-public"]
        exact = flowcharts.get_flowchart("flowcharts-trust-public", raw_bucket="b", s3_client=s3)
        assert "resolved_from" not in exact


class TestCitationFields:
    """The fields a cited flowchart needs to become a citation card."""

    def test_fields_from_sidecar(self):
        f = flowcharts.flowchart_citation_fields(
            "flowcharts-trust-public", raw_bucket="b", s3_client=_s3_with("flowcharts-trust-public")
        )
        assert f["flowchart_id"] == "flowcharts-trust-public"
        assert f["title"] == "Property Held in Trust in Public Interest"
        # source_url + pdf_page are what give the inline citation its anchor.
        assert f["source_url"].startswith("https://")
        assert isinstance(f["pdf_page"], int) and f["pdf_page"] > 0
        assert f["wpam_page"]
        assert "thorough review" in f["disclaimer"]

    def test_accepts_a_near_miss_id(self):
        f = flowcharts.flowchart_citation_fields(
            "flowcharts-trust-public-interest",
            raw_bucket="b",
            s3_client=_s3_with("flowcharts-trust-public"),
        )
        assert f["flowchart_id"] == "flowcharts-trust-public"

    def test_empty_for_unknown_id(self):
        assert flowcharts.flowchart_citation_fields("nope-at-all", raw_bucket="b") == {}

    def test_empty_when_sidecar_missing(self):
        assert (
            flowcharts.flowchart_citation_fields(
                "flowcharts-mobile-home", raw_bucket="b", s3_client=_FakeS3({})
            )
            == {}
        )


@pytest.mark.parametrize("fc", [f["flowchart_id"] for f in flowcharts.FLOWCHART_REGISTRY])
def test_every_sidecar_carries_a_page_anchor(fc):
    """Citation anchors depend on source.pdf_page + source.source_url being authored."""
    doc = flowcharts.get_flowchart(fc, raw_bucket="b", s3_client=_s3_with(fc))
    src = doc["source"]
    assert isinstance(src.get("pdf_page"), int) and src["pdf_page"] > 0, fc
    assert src.get("source_url", "").startswith("https://"), fc
    assert src["source_url"].endswith(f"#page={src['pdf_page']}"), fc


def test_get_flowchart_missing_sidecar_is_graceful():
    s3 = _FakeS3({})  # known id, but no sidecar uploaded yet
    r = flowcharts.get_flowchart("flowcharts-mobile-home", raw_bucket="b", s3_client=s3)
    assert "error" in r
    assert "not yet published" in r["error"]


def test_get_flowchart_requires_bucket():
    r = flowcharts.get_flowchart("flowcharts-mobile-home", raw_bucket="", s3_client=_FakeS3({}))
    assert r["error"] == "Raw bucket not configured"


@pytest.mark.parametrize("fc", [f["flowchart_id"] for f in flowcharts.FLOWCHART_REGISTRY])
def test_every_registered_sidecar_is_valid_graph(fc):
    """Each committed sidecar: valid JSON, edges resolve, reachable, decisions have yes+no."""
    doc = flowcharts.get_flowchart(fc, raw_bucket="b", s3_client=_s3_with(fc))
    assert "error" not in doc, doc
    node_ids = {n["id"] for n in doc["nodes"]}
    assert doc["start_node"] in node_ids
    # Every edge endpoint exists.
    for e in doc["edges"]:
        assert e["from"] in node_ids, f"{fc}: edge from unknown {e['from']}"
        assert e["to"] in node_ids, f"{fc}: edge to unknown {e['to']}"
    # Reachability from start.
    adj: dict[str, list[str]] = {}
    for e in doc["edges"]:
        adj.setdefault(e["from"], []).append(e["to"])
    seen: set[str] = set()
    stack = [doc["start_node"]]
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        stack += adj.get(n, [])
    assert node_ids <= seen, f"{fc}: unreachable nodes {node_ids - seen}"
    # Every decision node offers both yes and no.
    for n in doc["nodes"]:
        if n["type"] == "decision":
            branches = {e.get("branch") for e in doc["edges"] if e["from"] == n["id"]}
            assert {"yes", "no"} <= branches, (
                f"{fc}: decision {n['id']} missing yes/no ({branches})"
            )
    # Disclaimer present verbatim-ish.
    assert "thorough review" in doc.get("disclaimer", "")
