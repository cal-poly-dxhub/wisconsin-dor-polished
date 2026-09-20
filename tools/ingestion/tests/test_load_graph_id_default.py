"""`load.py` must target the pinned graph when --graph-id is omitted.

The Fargate task definition carries NEPTUNE_GRAPH_ID (from the `neptuneGraphId`
CDK context), so `run_fargate.sh load` with no flags lands on the same graph the
retrieval Lambda queries. --graph-id stays as the blue/green opt-out.
"""

import pytest

from tools.ingestion.load import GRAPH_ID_ENV_VAR, build_arg_parser, resolve_graph_id


def parse(argv):
    return build_arg_parser().parse_args(["--work-bucket", "wb", *argv])


def test_env_var_is_used_when_flag_omitted():
    args = parse([])
    assert args.graph_id is None
    assert resolve_graph_id(args.graph_id, {GRAPH_ID_ENV_VAR: "g-pinned"}) == "g-pinned"


def test_explicit_flag_overrides_env_var():
    args = parse(["--graph-id", "g-staging"])
    assert resolve_graph_id(args.graph_id, {GRAPH_ID_ENV_VAR: "g-pinned"}) == "g-staging"


def test_flag_alone_is_enough_without_env_var():
    args = parse(["--graph-id", "g-staging"])
    assert resolve_graph_id(args.graph_id, {}) == "g-staging"


@pytest.mark.parametrize("env", [{}, {GRAPH_ID_ENV_VAR: ""}, {GRAPH_ID_ENV_VAR: "   "}])
def test_no_flag_and_no_env_var_exits_instead_of_guessing(env):
    """Never silently fall back to some other graph."""
    with pytest.raises(SystemExit) as excinfo:
        resolve_graph_id(parse([]).graph_id, env)
    assert GRAPH_ID_ENV_VAR in str(excinfo.value)


def test_graph_id_is_no_longer_a_required_flag():
    """Regression guard: argparse used to hard-require --graph-id."""
    assert parse([]).work_bucket == "wb"
