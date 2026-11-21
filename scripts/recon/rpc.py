"""Read-only chain access with an on-disk replay cache.

Two cache layers, both replayed byte-for-byte so a second run of the same input
returns the same record without touching the network:

* ``data/raw/state_calls/<query_id>.json`` — ``eth_call`` payloads, the only
  source of balance truth (``docs/definitions.md`` 3.1).
* ``data/raw/chain_probes/<probe_id>.json`` — every other raw response (block
  header, receipt, code, block logs) plus reference-provider calls.

Providers come in two flavours: plain JSON-RPC and the Etherscan v2 REST
facade. A provider that answers "I cannot serve this" (Etherscan's historical
``eth_call`` / ``eth_getCode`` are the live examples) is cached as a negative
probe so replays stay offline, and the next provider in the chain is tried.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from .config import Config, Provider


PAGE_LIMIT = 1000


class RpcError(RuntimeError):
    """Raised when no provider can serve a request."""


class RpcMethodUnavailable(RpcError):
    """Provider answered with an error: retrying the same endpoint is pointless."""


@dataclass(frozen=True)
class StateCall:
    query_id: str
    payload_sha256: str
    request: dict
    result: str
    queried_at_utc: str
    from_cache: bool


@dataclass(frozen=True)
class Probe:
    probe_id: str
    provider_label: str
    method: str
    params: list
    result: object
    retrieved_at_utc: str
    from_cache: bool


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def now_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# The full core run drives many reconstructions from a thread pool. Requests are
# serialized per (provider, method, params) so the same evidence is fetched once
# and every thread re-reads the same cached record; different requests still run
# concurrently. Cache files are replaced atomically so a reader never sees a
# half-written JSON body.
_KEY_LOCKS: dict[str, threading.Lock] = {}
_KEY_LOCKS_GUARD = threading.Lock()


def _key_lock(key: str) -> threading.Lock:
    with _KEY_LOCKS_GUARD:
        lock = _KEY_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _KEY_LOCKS[key] = lock
        return lock


def _write_json_atomic(path: Path, payload: dict) -> None:
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}_{threading.get_ident()}")
    tmp.write_text(
        json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(tmp, path)


# The core run is the first workload with tens of thousands of calls. A per-endpoint
# pacing clock keeps the whole process inside each endpoint's budget no matter how
# many workers are reconstructing; set the env vars to 0 to disable pacing.
_RATE_BY_KIND = {
    "jsonrpc": float(os.getenv("ETHEREUM_RPC_REQUESTS_PER_SECOND", "8") or 0),
    "etherscan": float(os.getenv("ETHEREUM_ETHERSCAN_REQUESTS_PER_SECOND", "3") or 0),
}
_PACE_LOCK = threading.Lock()
_NEXT_SLOT: dict[str, float] = {}

_TRANSIENT_HINTS = (
    "exceeded",
    "rate limit",
    "too many",
    "compute units",
    "timeout",
    "temporarily",
    "try again",
    "capacity",
    "429",
)


def _throttle(provider: Provider) -> None:
    rate = _RATE_BY_KIND.get(provider.kind, 8.0)
    if rate <= 0:
        return
    # Reserve a slot under the lock, sleep outside it: a slow Etherscan slot must
    # not stall JSON-RPC requests queued on another endpoint.
    with _PACE_LOCK:
        now = time.monotonic()
        start = max(now, _NEXT_SLOT.get(provider.label, 0.0))
        _NEXT_SLOT[provider.label] = start + 1.0 / rate
    wait = start - now
    if wait > 0:
        time.sleep(wait)


def is_transient(message: object) -> bool:
    text = str(message).lower()
    return any(hint in text for hint in _TRANSIENT_HINTS)


_SESSION_LOCAL = threading.local()


def _thread_session() -> requests.Session:
    """One pooled session per thread.

    A reconstruction instance is created per event; giving each its own session
    leaves thousands of half-used connection pools behind and exhausts sockets
    on the long core run ("Max retries exceeded"). Reusing one session per worker
    thread keeps the pool small without sharing a session across threads.
    """
    session = getattr(_SESSION_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=8, pool_maxsize=32, max_retries=0
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _SESSION_LOCAL.session = session
    return session


class RpcCache:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.cfg.state_call_dir.mkdir(parents=True, exist_ok=True)
        self.cfg.probe_dir.mkdir(parents=True, exist_ok=True)
        self._session = _thread_session()

    # ---------------------------------------------------------------- transport
    def _post_jsonrpc(self, provider: Provider, method: str, params: list) -> object:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        last_error: Exception | None = None
        for attempt in range(10):
            _throttle(provider)
            try:
                response = self._session.post(provider.url, json=payload, timeout=30)
                if response.status_code == 429:
                    raise RpcError("HTTP 429 rate limited")
                body = response.json()
            except Exception as exc:  # noqa: BLE001 - provider availability varies
                last_error = exc
                time.sleep(min(8.0, 0.5 * (2**attempt)))
                continue
            if isinstance(body, dict) and body.get("error"):
                detail = body["error"]
                message = (
                    detail.get("message") if isinstance(detail, dict) else detail
                )
                if is_transient(message):
                    # 限流不是"该方法不可得"，不写否定缓存，退避后重试同一端点。
                    last_error = RpcError(f"{method}: {message}")
                    time.sleep(min(8.0, 0.5 * (2**attempt)))
                    continue
                raise RpcMethodUnavailable(f"{method}: {message}")
            if isinstance(body, dict) and "result" in body:
                if body["result"] is None:
                    raise RpcMethodUnavailable(f"{method}: result 为 null（该区块/状态不可得）")
                return body["result"]
            last_error = RpcError(f"{method}: 响应缺少 result")
            time.sleep(min(8.0, 0.5 * (2**attempt)))
        raise RpcError(f"{method}: 请求失败 ({last_error})")

    def _post_etherscan(self, provider: Provider, method: str, params: list) -> object:
        query: dict[str, object] = {
            "chainid": self.cfg.chain_id,
            "apikey": provider.api_key,
        }
        from_block = to_block = -1
        if method == "eth_call":
            call, tag = params
            # Etherscan v2 的 proxy eth_call 会忽略历史 tag 并返回 latest 状态
            # （实测：USDT balanceOf 在区块 10,000,000 与 latest 返回同一数值，
            # 而归档端点在该区块返回 0）。余额真值只能来自真正的归档端点，
            # 因此历史 tag 一律判为该方法不可得，顺延到下一个参照端点。
            if str(tag).lower() != "latest":
                raise RpcMethodUnavailable(
                    "eth_call: Etherscan 历史状态不可得（tag 被忽略，返回 latest）"
                )
            query.update(
                module="proxy", action="eth_call", to=call["to"], data=call["data"], tag=tag
            )
        elif method == "eth_getCode":
            query.update(module="proxy", action="eth_getCode", address=params[0], tag=params[1])
        elif method == "eth_getStorageAt":
            query.update(
                module="proxy",
                action="eth_getStorageAt",
                address=params[0],
                position=params[1],
                tag=params[2],
            )
        elif method == "eth_getTransactionReceipt":
            query.update(
                module="proxy", action="eth_getTransactionReceipt", txhash=params[0]
            )
        elif method == "eth_getBlockByNumber":
            query.update(
                module="proxy",
                action="eth_getBlockByNumber",
                tag=params[0],
                boolean="false",
            )
        elif method == "eth_getLogs":
            log_filter = params[0]
            from_block = int(log_filter["fromBlock"], 16)
            to_block = int(log_filter["toBlock"], 16)
            # Etherscan 只认十进制区块号；传 hex 会被静默忽略并返回最早的日志。
            query.update(
                module="logs",
                action="getLogs",
                address=log_filter["address"],
                fromBlock=from_block,
                toBlock=to_block,
                offset=PAGE_LIMIT,
                page=1,
            )
            for index, topic in enumerate(log_filter.get("topics", [])):
                query[f"topic{index}"] = topic
        else:
            raise RpcMethodUnavailable(f"etherscan 不支持 {method}")

        last_error: object = None
        for attempt in range(10):
            _throttle(provider)
            try:
                response = self._session.get(provider.url, params=query, timeout=30)
                if response.status_code == 429:
                    raise RpcError("HTTP 429 rate limited")
                body = response.json()
            except Exception as exc:  # noqa: BLE001 - provider availability varies
                last_error = exc
                time.sleep(min(8.0, 0.5 * (2**attempt)))
                continue
            # Etherscan 的限流形态不止 {"error": ...}：非 proxy 模块会回
            # {"status":"0","message":"NOTOK","result":"Max rate limit reached"}，
            # 没有 error 键。限流是暂时失败，退避重试，不写否定缓存。
            transient = self._etherscan_transient_message(body)
            if transient:
                last_error = transient
                time.sleep(min(8.0, 0.5 * (2**attempt)))
                continue
            return self._etherscan_result(method, body, from_block, to_block)
        raise RpcError(f"{method}: 请求失败 ({last_error})")

    @staticmethod
    def _etherscan_transient_message(body: object) -> str:
        if not isinstance(body, dict):
            return ""
        parts: list[object] = []
        detail = body.get("error")
        if detail:
            parts.append(
                detail.get("message") if isinstance(detail, dict) else detail
            )
        if str(body.get("status", "1")) == "0":
            parts.append(f"{body.get('message')} / {str(body.get('result'))[:80]}")
        for part in parts:
            if part and is_transient(part):
                return str(part)
        return ""

    def _etherscan_result(
        self, method: str, body: object, from_block: int, to_block: int
    ) -> object:
        if isinstance(body, dict) and body.get("error"):
            detail = body["error"]
            raise RpcMethodUnavailable(
                f"{method}: {detail.get('message') if isinstance(detail, dict) else detail}"
            )
        if isinstance(body, dict) and body.get("message") == "No records found":
            return []
        if isinstance(body, dict) and "result" in body:
            # 非限流的 status=0（如参数错误）是该方法/参数的确定性失败，可写否定缓存。
            if str(body.get("status", "1")) == "0":
                raise RpcMethodUnavailable(
                    f"{method}: {body.get('message')} / {str(body.get('result'))[:80]}"
                )
            result = body["result"]
            if method == "eth_getLogs":
                return self._checked_logs(result, from_block, to_block)
            if method in {"eth_call", "eth_getCode", "eth_getStorageAt"} and (
                not isinstance(result, str) or not result.startswith("0x")
            ):
                raise RpcMethodUnavailable(
                    f"{method}: 非 hex 结果 {str(result)[:60]}"
                )
            return result
        raise RpcMethodUnavailable(
            f"{method}: {body.get('message')} / {str(body.get('result'))[:80]}"
        )

    @staticmethod
    def _checked_logs(result: object, from_block: int, to_block: int) -> list:
        if not isinstance(result, list):
            raise RpcMethodUnavailable(
                f"日志结果不是列表（{str(result)[:60]}），不能当作空日志"
            )
        logs = result
        if len(logs) >= PAGE_LIMIT:
            raise RpcMethodUnavailable(
                f"日志分页截断（≥{PAGE_LIMIT} 条），该端点不适合本区块"
            )
        expected = hex(from_block).lower()
        stray = [
            log
            for log in logs
            if from_block == to_block
            and str(log.get("blockNumber", "")).lower() != expected
        ]
        if stray:
            raise RpcMethodUnavailable("该端点忽略了 fromBlock/toBlock 过滤")
        return logs

    def _send(self, provider: Provider, method: str, params: list) -> object:
        if provider.kind == "etherscan":
            return self._post_etherscan(provider, method, params)
        return self._post_jsonrpc(provider, method, params)

    # ------------------------------------------------------------- state calls
    def state_call(
        self, *, to: str, data: str, block_number: int, label: str
    ) -> StateCall:
        request = {"to": to, "data": data, "block": hex(block_number)}
        payload_sha256 = sha256_hex(canonical_json(request).encode())
        query_id = "bq_" + payload_sha256[:32]
        path = self.cfg.state_call_dir / f"{query_id}.json"

        with _key_lock(f"state:{query_id}"):
            if path.exists() and not self.cfg.refresh:
                record = json.loads(path.read_text(encoding="utf-8"))
                if record.get("request") != request:
                    raise RpcError(f"{query_id}: 缓存文件的请求与本次不一致")
                return StateCall(
                    query_id=query_id,
                    payload_sha256=record.get("query_payload_sha256", payload_sha256),
                    request=request,
                    result=record["response"]["result"],
                    queried_at_utc=record["queried_at_utc"],
                    from_cache=True,
                )

            provider = self.cfg.primary
            result = self._send(provider, "eth_call", [request, hex(block_number)])
            queried_at = now_utc()
            record = {
                "label": label,
                "request": request,
                "response": {"result": result},
                "queried_at_utc": queried_at,
                "query_payload_sha256": payload_sha256,
                "provider_label": provider.label,
            }
            _write_json_atomic(path, record)
            return StateCall(
                query_id=query_id,
                payload_sha256=payload_sha256,
                request=request,
                result=result,
                queried_at_utc=queried_at,
                from_cache=False,
            )

    # ----------------------------------------------------------------- probes
    def _probe_path(self, provider_label: str, method: str, params: list) -> Path:
        digest = sha256_hex(canonical_json([provider_label, method, params]).encode())
        return self.cfg.probe_dir / f"cp_{digest[:32]}.json"

    def _candidates(self, provider: str) -> tuple[tuple[Provider, ...], bool]:
        if provider == "primary":
            return (self.cfg.primary,), True
        if provider == "reference":
            return self.cfg.reference_providers, False
        if provider == "reference_archive":
            # 历史状态只认真正的归档节点：Etherscan 的历史 eth_call 会返回 latest，
            # 已被适配层判为不可得。核心集逐事件核对走这条链，省掉每次必然失败的请求。
            return (
                tuple(
                    candidate
                    for candidate in self.cfg.reference_providers
                    if candidate.kind == "jsonrpc"
                ),
                False,
            )
        raise ValueError(f"未知 provider: {provider}")

    def probe(self, method: str, params: list, *, provider: str = "primary") -> Probe:
        # 同一请求在多线程下只抓一次：后来的线程等锁后在缓存里命中原记录。
        # provider 进键：同一参数在 primary 与 reference 上是两次独立的取证。
        with _key_lock(f"probe:{provider}:{method}:{canonical_json(params)}"):
            return self._probe_locked(method, params, provider)

    def _probe_locked(self, method: str, params: list, provider: str) -> Probe:
        candidates, cache_only = self._candidates(provider)
        if not candidates:
            raise RpcError(f"{method}: 没有可用的 {provider} 端点")

        errors: list[str] = []
        for candidate in candidates:
            path = self._probe_path(candidate.label, method, params)
            if not path.exists() or self.cfg.refresh:
                continue
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("error"):
                errors.append(f"{candidate.label}: {record['error'].get('message')} (缓存)")
                continue
            return Probe(
                probe_id=record["probe_id"],
                provider_label=candidate.label,
                method=method,
                params=params,
                result=record["response"]["result"],
                retrieved_at_utc=record["retrieved_at_utc"],
                from_cache=True,
            )

        if cache_only:
            candidate = candidates[0]
            result = self._send(candidate, method, params)
            return self._store(candidate, method, params, result=result)

        for candidate in candidates:
            try:
                result = self._send(candidate, method, params)
            except RpcMethodUnavailable as exc:
                errors.append(f"{candidate.label}: {exc}")
                self._write_error(candidate, method, params, str(exc))
                continue
            except RpcError as exc:
                errors.append(f"{candidate.label}: {exc}")
                continue
            return self._store(candidate, method, params, result)
        raise RpcError("所有候选端点均失败：" + " | ".join(errors))

    def _record_header(self, provider: Provider, method: str, params: list) -> tuple[Path, dict]:
        path = self._probe_path(provider.label, method, params)
        header: dict[str, object] = {
            "probe_id": path.stem,
            "provider_label": provider.label,
            "method": method,
            "params": params,
            "probe_payload_sha256": sha256_hex(
                canonical_json([provider.label, method, params]).encode()
            ),
            "retrieved_at_utc": now_utc(),
        }
        return path, header

    def _store(self, provider: Provider, method: str, params: list, result: object) -> Probe:
        path, record = self._record_header(provider, method, params)
        record["response"] = {"result": result}
        _write_json_atomic(path, record)
        return Probe(
            probe_id=path.stem,
            provider_label=provider.label,
            method=method,
            params=params,
            result=result,
            retrieved_at_utc=str(record["retrieved_at_utc"]),
            from_cache=False,
        )

    def _write_error(
        self, provider: Provider, method: str, params: list, message: str
    ) -> None:
        path, record = self._record_header(provider, method, params)
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if not existing.get("error"):
                # 已有成功证据时不回写失败：--refresh 遇到限流/超时不应把
                # 已核对的参照值销毁成 unverifiable（那会改变 data_digest）。
                return
        record["error"] = {"message": message}
        _write_json_atomic(path, record)
