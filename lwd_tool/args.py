# SPDX-License-Identifier: Apache-2.0
"""Parsed and validated launch arguments, before any derivation."""

from dataclasses import dataclass, field
from ipaddress import ip_address
from typing import Any

from . import LwdToolError

ROLE_EDGE = "edge"
ROLE_CLOUD = "cloud"
ROLE_SERVER = "server"
ROLES = (ROLE_EDGE, ROLE_CLOUD, ROLE_SERVER)


@dataclass(frozen=True)
class PeerSpec:
    """One side of an LWD pair: the host IP and its card list."""

    ip: str
    cards: tuple[int, ...]


def parse_cards(text: str) -> tuple[int, ...]:
    """Parse "4", "0-3", "0,1,2" or mixed "0,2-3" into an ordered card tuple."""
    if not text:
        raise LwdToolError("card list is empty")
    cards: list[int] = []
    for item in text.split(","):
        item = item.strip()
        if not item:
            raise LwdToolError(f"card list has an empty item: {text!r}")
        low, sep, high = item.partition("-")
        if not (low.isascii() and low.isdigit()):
            raise LwdToolError(f"card list item is not a number: {item!r}")
        if sep:
            if not (high.isascii() and high.isdigit()):
                raise LwdToolError(f"card range end is not a number: {item!r}")
            start, end = int(low), int(high)
            if end < start:
                raise LwdToolError(f"card range is reversed: {item!r}")
            span = range(start, end + 1)
        else:
            span = range(int(low), int(low) + 1)
        cards.extend(span)
    if len(set(cards)) != len(cards):
        raise LwdToolError(f"card list has duplicates: {text!r}")
    return tuple(cards)


def parse_peer(text: str, flag: str) -> PeerSpec:
    """Parse "IP:cards" such as "76.76.26.187:0-3"."""
    ip_text, sep, cards_text = text.rpartition(":")
    if not sep or not ip_text:
        raise LwdToolError(f"{flag} requires IP:cards, got {text!r}")
    try:
        ip = ip_address(ip_text)
    except ValueError as exc:
        raise LwdToolError(f"{flag}: invalid IP {ip_text!r}") from exc
    if ip.is_unspecified or ip.is_multicast:
        raise LwdToolError(f"{flag}: {ip_text!r} is not a usable unicast IP")
    return PeerSpec(str(ip), parse_cards(cards_text))


@dataclass
class LaunchArgs:
    """Validated CLI inputs; derived values live in plan.LaunchPlan."""

    role: str
    model: str
    edge: PeerSpec | None = None
    cloud: PeerSpec | None = None
    host: str | None = None
    cards: tuple[int, ...] | None = None
    serve_port: int | None = None
    port_base: int = 9724
    master_port: int = 29600
    ctrl_port_base: int = 5550
    early_recv: bool = False
    scramble: bool = False
    max_model_len: int | None = None
    max_num_seqs: int | None = None
    max_num_batched_tokens: int | None = None
    gpu_memory_utilization: float | None = None
    spec: str | None = None
    no_spec: bool = False
    cudagraph: str | None = None
    prefix_caching: bool | None = None
    served_name: str | None = None
    weights: str | None = None
    weights_root: tuple[str, ...] = ()
    log_level: str = "INFO"
    lwd_log_level: str = "INFO"
    env_overrides: dict[str, str] = field(default_factory=dict)
    passthrough: tuple[str, ...] = ()
    launch_dir: str = "/vllm-workspace/lwd_launch"
    log_dir: str = "/home/lwd/logs"
    vllm_root: str | None = None

    def __post_init__(self) -> None:
        self._validate()

    def _validate(self) -> None:
        if self.role not in ROLES:
            raise LwdToolError(f"--role must be one of {ROLES}, got {self.role!r}")
        if self.role in (ROLE_EDGE, ROLE_CLOUD):
            if self.edge is None or self.cloud is None:
                raise LwdToolError(
                    f"--role {self.role} requires both --edge IP:cards and "
                    "--cloud IP:cards"
                )
            if self.host is not None or self.cards is not None:
                raise LwdToolError(
                    "--host/--cards only apply to --role server; "
                    "use --edge/--cloud instead"
                )
        if self.role == ROLE_SERVER and not self._has_server_source():
            raise LwdToolError(
                "--role server requires --host IP --cards ..., or --edge/--cloud "
                "so the local side can be picked"
            )
        for name, port in (
            ("--serve-port", self.serve_port),
            ("--port-base", self.port_base),
            ("--master-port", self.master_port),
            ("--ctrl-port-base", self.ctrl_port_base),
        ):
            if port is not None and not 1 <= port <= 65535:
                raise LwdToolError(f"{name} must be in [1, 65535], got {port}")
        if self.spec is not None and self.no_spec:
            raise LwdToolError("--spec and --no-spec are mutually exclusive")

    def _has_server_source(self) -> bool:
        return (self.host is not None and self.cards is not None) or bool(
            self.edge or self.cloud
        )

    @classmethod
    def from_namespace(cls, ns: Any) -> "LaunchArgs":
        """Build from an argparse namespace (see cli.build_parser)."""
        role = ns.role
        edge = parse_peer(ns.edge, "--edge") if ns.edge else None
        cloud = parse_peer(ns.cloud, "--cloud") if ns.cloud else None
        host: str | None = None
        if ns.host:
            try:
                host = str(ip_address(ns.host))
            except ValueError as exc:
                raise LwdToolError(f"--host: invalid IP {ns.host!r}") from exc
        cards = parse_cards(ns.cards) if ns.cards else None
        if role == ROLE_SERVER and (host is None) != (cards is None):
            raise LwdToolError("--host and --cards must be given together")
        env_overrides = {}
        for item in ns.env or ():
            name, sep, value = item.partition("=")
            if not sep or not name:
                raise LwdToolError(f"--env requires VAR=VAL, got {item!r}")
            env_overrides[name] = value
        return cls(
            role=role,
            model=ns.model,
            edge=edge,
            cloud=cloud,
            host=host,
            cards=cards,
            serve_port=ns.serve_port,
            port_base=ns.port_base,
            master_port=ns.master_port,
            ctrl_port_base=ns.ctrl_port_base,
            early_recv=ns.early_recv,
            scramble=ns.scramble,
            max_model_len=ns.max_model_len,
            max_num_seqs=ns.max_num_seqs,
            max_num_batched_tokens=ns.max_num_batched_tokens,
            gpu_memory_utilization=ns.gpu_memory_utilization,
            spec=ns.spec,
            no_spec=ns.no_spec,
            cudagraph=ns.cudagraph,
            prefix_caching=ns.prefix_caching,
            served_name=ns.served_name,
            weights=ns.weights,
            weights_root=tuple(ns.weights_root or ()),
            log_level=ns.log_level,
            lwd_log_level=ns.lwd_log_level,
            env_overrides=env_overrides,
            passthrough=tuple(getattr(ns, "passthrough", ())),
            launch_dir=ns.launch_dir,
            log_dir=ns.log_dir,
            vllm_root=ns.vllm_root,
        )
