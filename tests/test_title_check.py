import pytest

from lwd_checks.title_check import lwd_check_title, lwd_parse_title_tags


def test_parse_title_tags_strips_leading_brackets():
    assert lwd_parse_title_tags("[Bugfix][Lwd] fix x") == ["Bugfix", "Lwd"]
    assert lwd_parse_title_tags("[CI/Build][Misc][Lwd] redo") == [
        "CI/Build",
        "Misc",
        "Lwd",
    ]
    assert lwd_parse_title_tags("no tags here") == []
    assert lwd_parse_title_tags("mid [Bugfix] tags") == []


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("[Bugfix][Lwd] fix xxx sync problem", "pass"),
        ("[CI/Build][Misc][Lwd] redo pipeline", "pass"),
        ("[Feature][Lwd] Lwd batch protocol structs", "pass"),
        ("refactor(lwd): unify config error output", "fail"),
        ("[Lwd][Bugfix] order is wrong", "fail"),
        ("[Bugfix] no lwd tag", "fail"),
        ("[Bugfix][Lwd]no-space", "fail"),
        ("[Wrong][Lwd] unknown type tag", "fail"),
    ],
)
def test_title_check_table(title: str, expected: str):
    result = lwd_check_title(title)
    assert result.status == expected
    assert result.check_id == "pr-title"


def test_title_failure_messages_name_each_rule():
    result = lwd_check_title("[Lwd] no type tag")
    assert "first tag [Lwd] is not a type tag" in result.summary
    result = lwd_check_title("[Bugfix] no scope")
    assert "missing the [Lwd]" in result.summary
    result = lwd_check_title("plain subject")
    assert "must start with" in result.summary
