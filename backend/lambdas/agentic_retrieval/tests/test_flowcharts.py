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
    assert "available" in r


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
