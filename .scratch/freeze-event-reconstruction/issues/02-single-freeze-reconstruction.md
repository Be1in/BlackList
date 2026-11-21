# 02: 单笔 freeze 事件的可复现重建

**What to build:** 给定一笔已知的 Ethereum USDT freeze 交易，从链上日志与历史状态一路走到一条可复核的事件记录与两类余额，并在同一张验证表中与人工核对结果对齐。这是唯一验证 seam 的首次落地。

**Blocked by:** 01: 定义与数据字典契约

**Status:** done

- [x] 同一输入重复运行得到相同的事件记录与两类余额（可复现采集脚本）
- [x] 记录带原始来源、所属链、交易与日志位置、合约版本、时间戳精度，并声明使用哪一套余额口径
- [x] 余额来自可查询历史状态，不是用当前状态倒推的近似值
- [x] 与人工核对结果在验证表中一致；若有偏差，记录偏差及原因
- [x] 不为解析步骤另建验证边界，验证只发生在重建输出处

## Comments

### 2026-11-13 交付

目标事件：USDT `freezing`，区块 26,292,598，`log_index = 60`，tx `0x5e520c7b…72bd4`（即工单 01 样本槽 S1）。

脚本与配置：

- `scripts/reconstruct_single_event.py`：单一入口，一条流水线（定位日志 → 区块头 → 合约身份 → 历史状态 → 余额 → 验证 → 落盘）。
- `scripts/recon/{config,rpc,contracts,tables,pipeline}.py`：端点与路径、两级原始响应缓存、ABI/解码、字典列约束、流水线本体。没有为解析步骤建测试 seam。
- `config/contracts.json`：代币、事件 topic0（运行时用本地 keccak256 校验）、代理槽、合约版本指纹表。
- `docs/reproduction.md`：运行方式、输入凭据、复现判据、验证表读法、已知限制。

产物：`data/recon/ev_050309e736cfaf844e9451cfaec78302/{events,balances,validation}.csv|.parquet` + `manifest.json`。

数字：

- `TetherToken`（`contract_code_hash 0xb44fb4e9…`，非代理），`block_time_utc = 2026-10-28T17:02:47Z`，`event_time_precision = second`。
- 状态：`not_blacklisted → blacklisted`（`blacklist_transition = true`），冻结为 episode 起点。
- D1 `at_freeze` = 5,844,309 raw / 5.844309 token（`balance_block_number 26,292,597`，同区块更早 Transfer 调整量 0）。
- D2 `at_study_cutoff` = 5,844,309 raw / 5.844309 token（`study_cutoff_block 26,404,630`，截止时仍 `blacklisted`）。
- 验证表 5 条：`source_position`（Etherscan 收据）/ `order_version`（drpc `eth_getCode`）/ `balance_state`（Etherscan 历史 `eth_call` + 其区块日志重算）/ `manual_reference`（OFAC 快照 uid=58886）/ `schema_conformance`（字典列集合），全部 `match`。

复现：`data_digest = 207f25f3…e646`。命中缓存重跑两次，CSV 逐字节一致（`artifact_digest = 2dc080fc…63e`）；`--refresh` 全量重抓后 `data_digest` 不变、仅抓取时刻字段变化；把 `ETHEREUM_ARCHIVE_RPC_URL` 指向不可达地址后仍得到同一对摘要，证明重放完全离线。`--expect-data-digest` 可用于评审侧复核。

口径：D1 与 D2 分别落在 `balances.balance_reference = at_freeze` / `at_study_cutoff`；余额只取 `STATE_AT_BLOCK_END(b)`（`block_number-1` / `study_cutoff_block`）加同区块更早 `Transfer` 的带符号调整，未用当前状态倒推，也未用 Transfer 累加当独立口径。`balances` 行记该次 `eth_call` 的原值（字典第 5 节），D1 合计记在 `events.balance_pre_event_raw`；本例调整量为 0，两者相等。

与工单 01 样例逐字段比对：数据字段完全一致（含 `event_id`、位置、状态机、余额、`query_id` 与 `query_payload_sha256`，缓存复用同一 `bq_` 文件）。差异仅 4 个说明性字段：

- `abi_source`：本工单记 `local_abi_snapshot:data/raw/abi/USDT.abi.json; abi_sha256:a217ba5d…; contract_version_fingerprint:TetherToken`；样例记 `etherscan_verified_source:…`。ABI 哈希相同，来源改记为本地快照（Etherscan 源码接口不在本流程依赖内，见下）。
- `contract_version_source`：改为"字节码指纹绑定标签"的表述，标签仍为 `TetherToken@v0.4.18+commit.9cf6e910`。
- `source_query`：签名改为带参数的 canonical 形式（`AddedBlackList(address)` 等），区块范围与 topic 集合不变。
- `notes`：记录重建用途与同区块 Transfer 日志数（83）、episode 起点判定。

偏差与限制：

1. 参照端最终为三选一链：`reference:api.etherscan.io` → `reference:eth.drpc.org` → `reference:eth-mainnet.public.blastapi.io`。Etherscan v2 现已可用，`source_position` 与 `balance_state` 由它复核（余额用它自己的历史 `eth_call` 与自己的区块日志按同一规则重算）；它的历史 `eth_getCode` / `eth_getStorageAt` 返回 `historical state … is not available`，故 `order_version` 与代理槽判定回落 drpc，否定结果同样入缓存。Etherscan 的 `getLogs` 只认十进制区块号（hex 会被静默忽略并返回最早日志），已在适配层换算并校验区块号与分页上限。
2. `reviewer` 仍为 `agent:auto-cross-check`，人工签署未完成，留给工单 04；这是唯一未由人完成的环节。
3. USDC 分支（代理槽、`FiatTokenV2_2`）已冒烟通过并与工单 01 样例行逐字段一致，但未写入本工单验证表；`unfreezing` / `destruction` 的 `at_event` 诊断行同样留给工单 03。
4. 原始层新增 `data/raw/chain_probes/`（区块头、收据、`eth_getCode`、区块内日志、参照端点 `eth_call`），已登记进 `docs/data-dictionary.md` 第 1 节；`state_calls` 的 `bq_` 命名与哈希规则未变。
5. 仓库当前不是 git 仓库（`git status` 报 not a repository），无法按流程提交分支。

### 2026-11-13 工单 03 复核修正

工单 03 扩展交易作用域时发现参照端缺陷：Etherscan v2 的 `proxy eth_call` 会忽略历史区块参数并返回 latest 状态，
旧实现因此把「当前余额」当历史余额（本例地址余额未变，所以恰好与归档端点一致）。历史 `eth_call` 已判为不可得，
`balance_state` 改由 drpc 提供，受污染的 7 个正缓存已删除并由修复后的代码写否定缓存。

结果：`data_digest` 由 `207f25f3…e646` 变为 `f259582b…e03b3`，`artifact_digest` 由 `2dc080fc…63e` 变为
`4c27c148…dcf`；差异只在 `reference_source`，5 条检查、事件记录与两类余额数值均不变。复现说明已同步更新。

评审后修正（两轴评审，Standards / Spec）：返回数据不足时不再把空字解码成 0 / false；RPC `result = null` 视为错误而非写入缓存；参照端点不可用时 `reference_value` 写固定串，避免异常文本让 `data_digest` 随网络波动；代理槽判定不再吞掉取码失败；`balances.balance_raw` 改为 `eth_call` 原值、D1 合计只留在 `events`；`blacklist_state_at_balance_block` 改由查询结果解码；`events.notes` 去掉派生布尔值；manifest 记录 parquet 是否写成；OFAC 路径进配置。
