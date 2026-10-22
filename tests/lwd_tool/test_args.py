# SPDX-License-Identifier: Apache-2.0

import pytest
from scenario import CLOUD, EDGE

from lwd_tool import LwdToolError
from lwd_tool.args import LaunchArgs, parse_cards, parse_peer
from lwd_tool.cli import build_parser


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("4", (4,)),
        ("0-3", (0, 1, 2, 3)),
        ("0,1,2", (0, 1, 2)),
        ("0,2-3", (0, 2, 3)),
        ("3-3", (3,)),
    ],
)
def test_parse_cards_accepts_documented_forms(text, expected):
    assert parse_cards(text) == expected


@pytest.mark.parametrize(
    "text",
    ["", "4-", "a", "-1", "1,1", "3-1", "0,,1", "0,x"],
)
def test_parse_cards_rejects_bad_input(text):
    with pytest.raises(LwdToolError):
        parse_cards(text)


def test_parse_peer_splits_ip_and_cards():
    assert parse_peer("76.76.26.187:0-3", "--cloud") == CLOUD


def test_parse_peer_rejects_missing_cards():
    with pytest.raises(LwdToolError, match="IP:cards"):
        parse_peer("76.76.26.187", "--cloud")


def test_parse_peer_rejects_bad_ip():
    with pytest.raises(LwdToolError, match="invalid IP"):
        parse_peer("300.0.0.1:0-3", "--cloud")


def test_lwd_roles_require_both_sides():
    with pytest.raises(LwdToolError, match="both --edge"):
        LaunchArgs(role="edge", model="m", edge=EDGE)
    with pytest.raises(LwdToolError, match="both --edge"):
        LaunchArgs(role="cloud", model="m", cloud=CLOUD)


def test_lwd_roles_reject_host_cards():
    with pytest.raises(LwdToolError, match="--role server"):
        LaunchArgs(role="edge", model="m", edge=EDGE, cloud=CLOUD, host="10.0.0.9")


def test_server_role_needs_a_source():
    with pytest.raises(LwdToolError, match="--role server requires"):
        LaunchArgs(role="server", model="m")


def test_port_ranges_are_checked():
    with pytest.raises(LwdToolError, match="port-base"):
        LaunchArgs(role="cloud", model="m", edge=EDGE, cloud=CLOUD, port_base=70000)


def test_spec_and_no_spec_conflict():
    with pytest.raises(LwdToolError, match="mutually exclusive"):
        LaunchArgs(
            role="cloud",
            model="m",
            edge=EDGE,
            cloud=CLOUD,
            spec="mtp:2",
            no_spec=True,
        )


def run_namespace(argv: list[str]):
    parser = build_parser()
    namespace = parser.parse_args(argv)
    return LaunchArgs.from_namespace(namespace)


def test_from_namespace_maps_the_cli_surface():
    largs = run_namespace(
        [
            "run",
            "--role",
            "cloud",
            "--model",
            "qwen3.6-27b",
            "--edge",
            f"{EDGE.ip}:4",
            "--cloud",
            f"{CLOUD.ip}:0-3",
            "--env",
            "FOO=bar",
            "--env",
            "BAZ=qux",
        ]
    )
    assert largs.edge == EDGE
    assert largs.cloud == CLOUD
    assert largs.env_overrides == {"FOO": "bar", "BAZ": "qux"}


def test_from_namespace_rejects_bad_env():
    with pytest.raises(LwdToolError, match="VAR=VAL"):
        run_namespace(
            [
                "run",
                "--role",
                "cloud",
                "--model",
                "m",
                "--edge",
                f"{EDGE.ip}:4",
                "--cloud",
                f"{CLOUD.ip}:0-3",
                "--env",
                "NOEQUALS",
            ]
        )


@pytest.mark.parametrize(
    ("flags", "expected"),
    (
        (["--prefix-caching"], True),
        (["--no-prefix-caching"], False),
        ([], None),
    ),
)
def test_prefix_caching_is_tri_state(flags, expected):
    argv = [
        "run",
        "--role",
        "cloud",
        "--model",
        "m",
        "--edge",
        f"{EDGE.ip}:4",
        "--cloud",
        f"{CLOUD.ip}:0-3",
        *flags,
    ]
    assert run_namespace(argv).prefix_caching is expected
