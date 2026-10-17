from lwd_checks.catalog import (
    LWD_CHECK_CATALOG,
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
        "lwd-unit-tests",
        "mypy",
    }


def test_catalog_lanes_and_gating():
    fast_required = {
        c.check_id
        for c in LWD_CHECK_CATALOG.values()
        if c.lane == "fast" and c.gating == "required"
    }
    assert fast_required == {"pr-title", "commits", "pre-commit"}
    slow = {
        c.check_id: c.gating for c in LWD_CHECK_CATALOG.values() if c.lane == "slow"
    }
    assert slow == {"lwd-unit-tests": "required", "mypy": "advisory"}


def test_catalog_specs_are_frozen_and_keyed_by_id():
    for check_id, spec in LWD_CHECK_CATALOG.items():
        assert isinstance(spec, LwdCheckSpec)
        assert spec.check_id == check_id
        assert spec.title
