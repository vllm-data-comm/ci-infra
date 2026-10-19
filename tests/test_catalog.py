from lwd_checks.catalog import (
    LWD_CHECK_CATALOG,
    LWD_LANES,
    LwdCheckSpec,
)


def test_catalog_defines_every_designed_check():
    ids = set(LWD_CHECK_CATALOG)
    assert ids == {
        "pr-title",
        "commits",
        "pre-commit",
        "param-count",
        "function-length",
        "class-prefix",
        "new-file-path",
        "patch-usage",
        "rfc-link",
        "bugfix-tests",
    }


def test_catalog_lanes_and_gating():
    fast_required = {
        c.check_id
        for c in LWD_CHECK_CATALOG.values()
        if c.lane == "fast" and c.gating == "required"
    }
    assert fast_required == {"pr-title", "commits", "pre-commit"}
    assert {c.lane for c in LWD_CHECK_CATALOG.values()} == set(LWD_LANES)


def test_catalog_specs_are_frozen_and_keyed_by_id():
    for check_id, spec in LWD_CHECK_CATALOG.items():
        assert isinstance(spec, LwdCheckSpec)
        assert spec.check_id == check_id
        assert spec.title
