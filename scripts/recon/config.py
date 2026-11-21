"""Reconstruction configuration: paths, endpoints and the pinned contract registry."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_LOG_SNAPSHOT = "data/raw/logs/ethereum_blacklist_logs.json"
DEFAULT_CONTRACT_REGISTRY = "config/contracts.json"
DEFAULT_DICTIONARY = "docs/data-dictionary.csv"
DEFAULT_STATE_CALL_DIR = "data/raw/state_calls"
DEFAULT_PROBE_DIR = "data/raw/chain_probes"
DEFAULT_OFAC_DIR = "data/raw/ofac"
DEFAULT_OUT_ROOT = "data/recon"
DEFAULT_PRIMARY_LABEL = "tenderly_mainnet_gateway"
DEFAULT_REFERENCE_URLS = (
    "https://eth.drpc.org",
    "https://eth-mainnet.public.blastapi.io",
)
ETHERSCAN_V2_URL = "https://api.etherscan.io/v2/api"
ETHERSCAN_LABEL = "reference:api.etherscan.io"


def reference_label(url: str) -> str:
    """Logical endpoint name: scheme-free host, no keys or paths."""
    return "reference:" + (urlparse(url).netloc or url)


@dataclass(frozen=True)
class Provider:
    """A read-only evidence endpoint: JSON-RPC, or the Etherscan v2 REST facade."""

    label: str
    kind: str  # "jsonrpc" | "etherscan"
    url: str
    api_key: str = ""


@dataclass(frozen=True)
class Config:
    repo_root: Path
    log_snapshot: Path
    contract_registry: Path
    dictionary_csv: Path
    state_call_dir: Path
    probe_dir: Path
    ofac_dir: Path
    out_root: Path
    providers: tuple[Provider, ...]
    refresh: bool
    registry: dict

    def path(self, relative: str) -> Path:
        return self.repo_root / relative

    def rel(self, path: Path) -> str:
        return path.resolve().relative_to(self.repo_root.resolve()).as_posix()

    @property
    def primary(self) -> Provider:
        return self.providers[0]

    @property
    def reference_providers(self) -> tuple[Provider, ...]:
        return self.providers[1:]

    @property
    def tokens(self) -> dict:
        return self.registry["tokens"]

    @property
    def token_order(self) -> list[str]:
        return self.registry["token_order"]

    @property
    def chain(self) -> dict:
        return self.registry["chain"]

    @property
    def chain_id(self) -> int:
        return int(self.registry["chain"]["chain_id"])


def _build_providers(reference_urls: tuple[str, ...]) -> tuple[Provider, ...]:
    primary_url = os.getenv("ETHEREUM_ARCHIVE_RPC_URL", "").strip()
    if not primary_url:
        raise SystemExit(
            "ETHEREUM_ARCHIVE_RPC_URL 未配置；归档状态是唯一余额真值来源，无法跳过。"
        )

    providers = [
        Provider(
            label=os.getenv("ETHEREUM_ARCHIVE_ENDPOINT_LABEL", "").strip()
            or DEFAULT_PRIMARY_LABEL,
            kind="jsonrpc",
            url=primary_url,
        )
    ]

    # Etherscan v2 只可服务日志、收据与区块头；历史状态不可得（eth_getCode/存储直接报错，
    # eth_call 会忽略历史 tag 返回 latest），因此它排在参照链首位，历史状态自动回落到归档端点。
    etherscan_key = os.getenv("ETHERSCAN_API_KEY", "").strip()
    if etherscan_key and os.getenv("ETHEREUM_REFERENCE_USE_ETHERSCAN", "1") != "0":
        providers.append(
            Provider(
                label=ETHERSCAN_LABEL,
                kind="etherscan",
                url=ETHERSCAN_V2_URL,
                api_key=etherscan_key,
            )
        )

    for url in reference_urls:
        providers.append(
            Provider(label=reference_label(url), kind="jsonrpc", url=url)
        )
    return tuple(providers)


def load_config(
    *,
    repo_root: Path | None = None,
    log_snapshot: str = DEFAULT_LOG_SNAPSHOT,
    contract_registry: str = DEFAULT_CONTRACT_REGISTRY,
    dictionary_csv: str = DEFAULT_DICTIONARY,
    out_root: str = DEFAULT_OUT_ROOT,
    refresh: bool = False,
) -> Config:
    load_dotenv(repo_root / ".env" if repo_root else REPO_ROOT / ".env")

    root = Path(repo_root) if repo_root else REPO_ROOT
    reference_env = os.getenv("ETHEREUM_REFERENCE_RPC_URLS", "").strip()
    reference_urls = (
        tuple(u.strip() for u in reference_env.split(",") if u.strip())
        if reference_env
        else DEFAULT_REFERENCE_URLS
    )

    registry_path = root / contract_registry
    registry = json.loads(registry_path.read_text(encoding="utf-8"))

    return Config(
        repo_root=root,
        log_snapshot=root / log_snapshot,
        contract_registry=registry_path,
        dictionary_csv=root / dictionary_csv,
        state_call_dir=root / DEFAULT_STATE_CALL_DIR,
        probe_dir=root / DEFAULT_PROBE_DIR,
        ofac_dir=root / DEFAULT_OFAC_DIR,
        out_root=root / out_root,
        providers=_build_providers(reference_urls),
        refresh=refresh,
        registry=registry,
    )
