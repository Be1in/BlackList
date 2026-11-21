"""Contract registry helpers: topics, calldata, log decoding, version fingerprints."""

from __future__ import annotations

from dataclasses import dataclass

from eth_utils import keccak

from .rpc import RpcCache, RpcError


def keccak_hex(data: bytes) -> str:
    return "0x" + keccak(data).hex()


def function_selector(signature: str) -> str:
    return "0x" + keccak(signature.encode())[:4].hex()


def encode_address_call(signature: str, address: str) -> str:
    word = address.lower().removeprefix("0x").rjust(64, "0")
    return function_selector(signature) + word


def word_at(hex_data: str, index: int) -> str:
    body = (hex_data or "").removeprefix("0x")
    word = body[index * 64 : (index + 1) * 64]
    if len(word) != 64:
        raise RpcError(
            f"返回数据不足：需要第 {index} 个 32 字节字，实际 {len(body)} hex 字符"
        )
    return word


def word_to_int(word: str) -> int:
    if not word:
        raise RpcError("空字不能当作 0 解码")
    return int(word, 16)


def word_to_bool(word: str) -> bool:
    return word_to_int(word) != 0


def word_to_address(word: str) -> str:
    return "0x" + word[-40:].lower()


@dataclass(frozen=True)
class EventSpec:
    token_symbol: str
    event_class: str
    signature: str
    topic0: str


def verify_topic_table(registry: dict) -> dict[str, EventSpec]:
    """Check the pinned topic0 table against locally computed keccak256."""
    table: dict[str, EventSpec] = {}
    for symbol in registry["token_order"]:
        token = registry["tokens"][symbol]
        for event_class, spec in token["events"].items():
            computed = keccak_hex(spec["signature"].encode())
            if computed != spec["topic0"].lower():
                raise RpcError(
                    f"{symbol}/{event_class}: pinned topic0 {spec['topic0']} "
                    f"≠ keccak256({spec['signature']}) = {computed}"
                )
            table[spec["topic0"].lower()] = EventSpec(
                token_symbol=symbol,
                event_class=event_class,
                signature=spec["signature"],
                topic0=spec["topic0"].lower(),
            )
    return table


def contract_for_address(registry: dict, address: str) -> str | None:
    wanted = address.lower()
    for symbol in registry["token_order"]:
        if registry["tokens"][symbol]["contract"].lower() == wanted:
            return symbol
    return None


def decode_event_log(log: dict, spec: EventSpec) -> tuple[str, str, str | None]:
    """Return (affected_address, address_decode_source, destruction_amount_raw)."""
    topics = log["topics"]
    data = log["data"]
    if len(topics) >= 2:
        address = word_to_address(topics[1])
        source = "topic"
        amount_index = 0
    else:
        address = word_to_address(word_at(data, 0))
        source = "data_word"
        amount_index = 1

    amount: str | None = None
    if spec.event_class == "destruction":
        raw = word_at(data, amount_index)
        if not raw:
            raise RpcError(f"销毁日志缺少金额字段: {log['transactionHash']}")
        amount = str(word_to_int(raw))
    return address, source, amount


def _code_at(rpc: RpcCache, address: str, block_number: int) -> str:
    probe = rpc.probe("eth_getCode", [address.lower(), hex(block_number)])
    return probe.result or ""


def code_hash(rpc: RpcCache, address: str, block_number: int) -> str:
    """keccak256 of the deployed bytecode (``events.contract_code_hash``)."""
    code = _code_at(rpc, address, block_number)
    if code in {"", "0x"}:
        raise RpcError(f"{address} 在区块 {block_number} 无代码")
    return keccak_hex(bytes.fromhex(code.removeprefix("0x")))


@dataclass(frozen=True)
class ContractIdentity:
    is_proxy: bool
    proxy_address: str
    implementation_address: str
    contract_code_hash: str


def resolve_identity(
    rpc: RpcCache, registry: dict, token_symbol: str, block_number: int
) -> ContractIdentity:
    token_contract = registry["tokens"][token_symbol]["contract"].lower()
    proxy_hash = code_hash(rpc, token_contract, block_number)

    for slot in registry["proxy_slots"].values():
        probe = rpc.probe(
            "eth_getStorageAt", [token_contract, slot, hex(block_number)]
        )
        candidate = word_to_address(word_at(probe.result, 0))
        if int(candidate, 16) == 0:
            continue
        # 槽位非零但地址上没有代码 = 该槽位对这份合约不是代理槽（存储被别处占用）；
        # 取码请求本身失败则向上抛，不能静默降级成"非代理"。
        code = _code_at(rpc, candidate, block_number)
        if code in {"", "0x"}:
            continue
        candidate_hash = keccak_hex(bytes.fromhex(code.removeprefix("0x")))
        if candidate_hash != proxy_hash:
            return ContractIdentity(
                is_proxy=True,
                proxy_address=token_contract,
                implementation_address=candidate,
                contract_code_hash=candidate_hash,
            )

    return ContractIdentity(
        is_proxy=False,
        proxy_address="",
        implementation_address=token_contract,
        contract_code_hash=proxy_hash,
    )


@dataclass(frozen=True)
class VersionLabel:
    label: str
    source: str
    confidence: str


def version_for_code_hash(
    registry: dict, token_symbol: str, contract_hash: str
) -> VersionLabel:
    entry = registry["contract_fingerprints"].get(contract_hash.lower())
    if entry and entry.get("token_symbol") == token_symbol:
        return VersionLabel(
            label=entry["label"], source=entry["source"], confidence="high"
        )
    return VersionLabel(label="", source="", confidence="low")


def abi_source(abi_sha256: str, abi_relative_path: str, label: str) -> str:
    return (
        f"local_abi_snapshot:{abi_relative_path}; abi_sha256:{abi_sha256}"
        f"; contract_version_fingerprint:{label or 'unknown'}"
    )
