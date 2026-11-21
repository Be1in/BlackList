# 数据字典 v1（模块间契约）

契约版本：`2026-11-13 / v1`。与 `docs/definitions.md` 配套；本文件是字段与规则的唯一权威，`docs/data-dictionary.csv` 是同一字段清单的机器可读副本（字段名冲突以本文件为准，修改须同时更新两处）。

## 1. 表结构与位置

| 表 | 粒度 | 主键 | 落盘位置 |
| --- | --- | --- | --- |
| `events` | 一条合约日志一行 | `event_id` | `data/events/events.parquet` |
| `balances` | 一次余额查询一行 | `query_id` | `data/balances/balances.parquet` |
| `matches` | 一个事件 × 一个匹配层一行 | `match_id` | `data/recon/match_<id>/matches.parquet`（两层同一张表，用 `match_layer` 区分） |
| `validation` | 一次对照检查一行 | `validation_id` | `data/validation/validation.parquet` |
| `chain_registry` | 一条链一行 | `chain` | `data/chain_registry.parquet` |

原始层：`data/raw/logs/<label>[_<start_block>-<end_block>].json`（RPC 原样返回，含 `sha256`）、`data/raw/state_calls/<query_id>.json`（`eth_call` 请求与返回，`query_id = bq_ + sha256(canonical_json(request))` 前 16 字节 hex）、`data/raw/chain_probes/<probe_id>.json`（其余 RPC 原样返回：区块头、收据、`eth_getCode`、区块内日志、参照端点的 `eth_call`；`probe_id = cp_ + sha256(canonical_json([provider_label, method, params]))` 前 16 字节 hex）、`data/raw/abi/`、`data/raw/ofac/`、`data/raw/transfers/<label>.json`（工单 08 的 `Transfer` 日志快照：`label`、`start_block`、`end_block`、`start_block_time_utc`、`end_block_time_utc` 与 `transfers[]`；缺覆盖区间即视为不可用，因为无法区分"窗口内没有流出"与"没有查"）、`data/raw/sample_records.json`（工单 01 人工核对样本，验证表的独立参照之一）。派生层：`data/derived/exclusions.csv`（被排除记录的汇总视图，不改动主表）。

单笔重建产物（工单 02 起）：`data/recon/<event_id>/{events,balances,validation}.csv|.parquet` 与 `manifest.json`，列集合与本字典一致；全量表落盘位置仍按上表。

生命周期案例产物（工单 03 起）：`data/recon/lc_<case>/{events,balances,validation}.csv|.parquet` 与 `manifest.json`。一个案例可含多笔交易与多条事件行，列集合与主键规则同上；案例级核对行写在同一个 `validation` 表内（`event_id` 可为空），不新增表、不新增字段。

代表性样本验证产物（工单 04 起）：`data/recon/vs_<id>/{events,balances,validation}.csv|.parquet` 与 `manifest.json`。`<id>` 由样本声明、案例清单、日志快照与 `study_cutoff_block` 的摘要派生，重跑命中同一目录；样本槽位跨多个案例，事件按 `event_id`、余额按 `query_id` 去重，核对行不去重。三张表列集合仍与本字典一致；`manifest.metrics` 与 `manifest.scope_inputs` 是覆盖、误差、不可核验比例与研究期参数的机器可读记录，不写入上述三张表。

核心事件集产物（工单 05 起）：`data/recon/core_<id>/{events,balances,validation,chain_registry}.csv|.parquet` 与 `manifest.json`。`<id>` 由日志快照、研究范围决策、观察期边界、字典与契约版本的摘要派生。四张表列集合与本字典一致；同目录另有三个**派生视图**，不属于字典表：`coverage_gaps.csv`（快照与独立检索的差集，逐条）、`scope_excluded.csv`（观察期外事件，逐条），以及 `data/derived/exclusions.csv`（被排除记录汇总）。`manifest.metrics` 记录全量口径的 event retrieval coverage、balance reconstruction error、unverifiable share、排除计数与链集合。

同口径比较产物（工单 07 起）：`data/recon/compare_<id>/{comparison,differences,sensitivity}.csv|.parquet`、`report.json` 与 `manifest.json`。`<id>` 由核心集摘要、匹配层摘要、决策、字典、契约版本与比较参数派生。这三张表是**派生分析视图，不属于本字典的五张表**；列集合由 `scripts/recon/comparison.py` 声明并在写出前断言，`comparison` 的每一行都带 `coverage_scope`、`status`、`balance_reference` 与 `statement_type = descriptive`。任何后续采集表不得引用这些列作为字段。

支撑材料包与措辞审查产物（工单 09 起）：`data/recon/materials_<id>/{materials_index,field_availability,metric_coverage,wording_checks,chain_scope}.csv|.parquet`、`report.json` 与 `manifest.json`。`<id>` 由上游四个批次（核心集、匹配层、比较、受限子样本）的数据摘要、字典、研究期决策与四份样本文件的摘要派生。五张表同样是**派生分析视图，不属于本字典的五张表**；列集合由 `scripts/recon/materials.py` 声明并在写出前断言。它们只索引与核对已交付产物，不采集、不重算事件与余额：`materials_index` 逐组件记录路径、摘要与离线核对命令，`field_availability` 逐字段复用本字典第 9 节的 `availability_tier`，`metric_coverage` 逐指标标注报告范围与 `causal_inference_permitted = no`，`wording_checks` 逐条对到结构化证据，`chain_scope` 记录链范围与待确认项。

受限子样本产物（工单 08 起）：`data/recon/restrict_<id>/{subset_eligibility,timing_gaps,exchange_outflows,frozen_balances,outflow_window_sensitivity}.csv|.parquet`、`report.json` 与 `manifest.json`。`<id>` 由核心集摘要、匹配层摘要、转账快照摘要、字典、契约版本与 `outflow_window` 参数派生。五张表同样是**派生分析视图，不属于本字典的五张表**；列集合由 `scripts/recon/restricted.py` 声明并在写出前断言。每一行都带 `coverage_scope = restricted_subset` 与 `statement_type = descriptive`，`frozen_balances` 的每一行都声明 `balance_reference = at_freeze`（D1），`subset_eligibility` 用逐级筛选（`events_in` / `events_dropped` / `events_remaining` / `dropped_reason_codes`）记录子集界定。这些表与比较视图不重名、不写入同一批次目录，也不产出 issuer 差值。

小样本：`data/samples/sample_<table>.csv`，列必须与对应表完全一致，不含字典外字段。

事件签名与 topic0（本地 `keccak256` 计算，供采集脚本与复核者共用）：

| token | 事件 | canonical signature | `event_topic0` |
| --- | --- | --- | --- |
| USDT | freezing | `AddedBlackList(address)` | `0x42e160154868087d6bfdc0ca23d96a1c1cfa32f1b72ba9ba27b69b98a0d819dc` |
| USDT | unfreezing | `RemovedBlackList(address)` | `0xd7e9ec6e6ecd65492dce6bf513cd6867560d49544421d0783ddf06e76c24470c` |
| USDT | destruction | `DestroyedBlackFunds(address,uint256)` | `0x61e6e66b0d6339b2980aecc6ccc0039736791f0ccde9ed512e789a7fbdd698c6` |
| USDT / USDC | 余额证据 | `Transfer(address,address,uint256)` | `0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef` |
| USDC | freezing | `Blacklisted(address)` | `0xffa4e6181777692565cf28528fc88fd1516ea86b56da075235fa575af6a4b855` |
| USDC | unfreezing | `UnBlacklisted(address)` | `0x117e3210bb9aa7d9baff172026820255c6f6c30ba8999d1c2fd88e2848137c4e` |
| USDC | 权限变更（非事件） | `BlacklisterChanged(address)` | `0xc67398012c111ce95ecb7429b933096c977380ee6c421175a71a4a4c6c88c06e` |

地址参数是否 `indexed` 由各合约 ABI 决定，解码结果必须用 `raw_topics` + `raw_data` + `abi_source` 三者复核；`address_decode_source` 记录地址取自 topic 还是 data，使复核者可独立重解。

## 2. `events`

| 字段 | 类型 | 来源 / 推导 | 缺失与排除 | 可得性 |
| --- | --- | --- | --- | --- |
| `event_id` | string | `ev_` + `sha256("ethereum\|" + tx_hash + "\|" + log_index)` 前 16 字节 hex | 必需 | A |
| `chain` | enum | 采集参数 | 必需 | A |
| `chain_id` | int | 链参数（`ethereum = 1`） | 必需 | A |
| `token_symbol` | enum `USDT`/`USDC` | 日志地址映射 | 必需 | A |
| `token_contract` | address（小写 0x） | 采集参数 | 必需 | A |
| `block_number` | int | 日志元数据 | 必需 | A |
| `block_hash` | string | 区块头 | 必需 | A |
| `block_time_utc` | RFC3339（秒） | 区块头 `timestamp` | 必需 | A |
| `event_time_precision` | enum | 固定 `second` | 必需 | A |
| `tx_hash` | string | 日志元数据 | 必需 | A |
| `tx_index` | int | 交易在区块中的序号 | 必需 | A |
| `log_index` | int | 日志在区块中的全局序号 | 必需 | A |
| `log_order_key` | string | `{block_number:010d}:{tx_index:04d}:{log_index:06d}` | 必需，唯一排序键 | A |
| `tx_event_count` | int | 同一 `tx_hash` 下事件行数 | 必需 | A |
| `event_topic0` | string | 日志 `topics[0]` | 必需 | A |
| `event_signature` | string | 由 topic0 查表（见第 1 节） | 必需 | A |
| `abi_source` | string | 已验证源码 / ABI 标识 + 快照 `sha256` | 无 ABI 时为空并置 `missing_reason_code = abi_unavailable` | A |
| `raw_topics` | JSON | 日志原样 | 必需 | A |
| `raw_data` | hex | 日志原样 | 必需 | A |
| `is_proxy` | bool | 实现槽读取（USDC 为 `0x7050c9e0…`），不靠 Etherscan 的 `Proxy` 标记 | 无法判定为空 | A |
| `proxy_address` | address | 非代理时为空 | 可空 | A |
| `implementation_address` | address | 代理槽 / 非代理时等于 `token_contract` | 无法判定为空 + `contract_version_confidence = low` | A |
| `contract_code_hash` | string | 该区块下 `eth_getCode(implementation)` 的 `keccak256` | 同上 | A |
| `contract_version_label` | string | 源码版本标注（如 `FiatTokenV2_2`） | 无法确定为空 | A |
| `contract_version_source` | string | 版本判定依据（源码 / 公告 / 代理槽） | 可空 | A |
| `contract_version_confidence` | enum | `high` / `low` | 必需 | A |
| `event_class` | enum | 由 `event_topic0` + `token_symbol` 唯一映射 | 必需，不可多值 | A |
| `affected_address` | address（小写 0x） | 由日志 ABI 解码 | 必需 | A |
| `address_decode_source` | enum `topic`/`data_word` | 解码器判定 | 必需 | A |
| `blacklist_state_before` | enum | 历史状态查询（`balances.blacklist_state_at_balance_block`），不由日志推演 | 必需 | A |
| `blacklist_state_after` | enum | 同上；`destruction` 前后均为 `blacklisted` | 必需 | A |
| `blacklist_transition` | bool | `before != after`；来自历史状态查询，不由日志类型推断 | 必需 | A |
| `destruction_amount_raw` | 十进制字符串 | 仅 `destruction` 行：日志第二个参数（USDT 销毁不 emit `Transfer`，金额只在此字段） | 非 `destruction` 行为空（`not_applicable`） | A |
| `destruction_amount_token` | 十进制字符串 | `raw / 10^token_decimals` | 同上 | A |
| `token_decimals` | int | 合约常量（两者均为 6） | 必需 | A |
| `balance_pre_event_raw` | 十进制字符串 | 见 `definitions.md` 3.2；仅 `freezing` 行属于 D1，其余为诊断值 | 归档状态不可得时为空 + `archive_state_unavailable` | A |
| `balance_pre_event_token` | 十进制字符串 | 同上换算 | 同上 | A |
| `balance_pre_event_adjustment_raw` | 十进制字符串 | 同区块内更早 `Transfer` 的带符号和；无调整记 `0` | 日志不完整时为空 + `lower_bound_only` | A |
| `balance_pre_event_state_block` | int | 固定 `block_number - 1` | 必需 | A |
| `balance_pre_event_source` | enum | 固定 `archive_state_end_of_block` | 必需 | A |
| `balance_pre_event_precision` | enum | `exact_from_state_plus_transfers` / `lower_bound_only` | 必需 | A |
| `balance_query_id` | string | 外键 → `balances.query_id` | 无对应 `balances` 行时为空（R9 重复日志、或同一次 `eth_call` 已按另一口径写入）；该次查询仍可在 `data/raw/state_calls/` 追溯 | A |
| `source_endpoint_label` | string | 逻辑端点名（`ethereum_archive_primary` 等），不写密钥 | 必需 | A |
| `source_query` | string | 区块范围 + 地址 + topics 的规范化描述 | 必需 | A |
| `source_retrieved_at_utc` | RFC3339 | 抓取时刻 | 必需 | A |
| `raw_log_path` | string | `data/raw/logs/...` | 必需 | A |
| `raw_log_sha256` | string | 原始响应摘要 | 必需 | A |
| `dataset_contract_version` | string | 固定 `v1` | 必需 | A |
| `missingness_status` | enum | 由空字段推导 | 必需 | A |
| `missing_fields` | string | 分号分隔的字段名，仅列真正缺失项 | 无缺失时为空串 | A |
| `missing_reason_code` | enum | 见第 3 节；`missingness_status = complete` 时为空 | 可空 | A |
| `verification_status` | enum | `verified` / `unverifiable` / `pending` | 必需 | A |
| `analysis_eligibility` | enum | 由第 4 节规则计算 | 必需 | A |
| `exclusion_rule_id` | enum | 见第 4 节；未排除为空 | 可空 | A |
| `exclusion_reason` | string | 规则触发的具体证据 | 可空 | A |
| `notes` | string | 人工说明（不得写入数据值） | 可空 | A |

## 3. 枚举与缺失原因码

| 枚举 | 取值 |
| --- | --- |
| `chain` | `ethereum`（其他链仅出现在 `chain_registry`） |
| `event_class` | `freezing` / `unfreezing` / `destruction` |
| `blacklist_state_*` | `blacklisted` / `not_blacklisted` |
| `balance_reference` | `at_freeze` / `at_event` / `at_study_cutoff` |
| `missingness_status` | `complete` / `partial` / `unavailable` |
| `missing_reason_code` | `not_collected` / `rpc_unavailable` / `log_not_retrievable` / `archive_state_unavailable` / `abi_unavailable` / `external_source_unavailable` / `label_date_missing` / `sanctions_record_missing` / `not_observable` / `not_applicable` |
| `verification_status` | `verified` / `unverifiable` / `pending` |
| `analysis_eligibility` | `core` / `core_unverifiable` / `restricted_subset` / `excluded` |
| `match_layer` | `ofac_sdn` / `exchange_label` |
| `match_status` | `matched` / `unmatched` / `not_attempted` / `not_applicable` |
| `match_confidence` | `exact_address` / `none` |
| `label_date_basis` | `label_publication_date` / `list_snapshot_date` / `observed_first_seen_date` |
| `sanctions_date_basis` | `designation_date` / `list_publication_date` / `list_update_observed_date`；清单快照发布日期不等于以上任何一项，只能写进 `label_date` |

`not_observable` 只用于声明"字段不存在"，例如私有执法请求；它不得作为某个数值字段的缺失原因而被解释成"暂时未知"。

## 4. 一致排除规则

标记在采集阶段完成（`missingness_*`、`verification_status`），排除在派生阶段按固定函数计算，规则只依赖记录自身证据，与 issuer、事件类别、链无关。

| ID | 触发条件 | 结果 | `analysis_eligibility` |
| --- | --- | --- | --- |
| R1 | 已知存在该日志（有外部来源指向），但在配置端点重试后仍取不到 | 记录保留占位行，`log_not_retrievable` | `excluded` |
| R2 | 归档历史状态不可查（节点不支持历史 `eth_call` / 超出保留窗口） | 事件保留，余额字段为空 | `core`（余额类指标只在可查子集上报告） |
| R3 | 缺少独立第二来源，无法人工或外部核验 | 保留，`verification_status = unverifiable` | `core_unverifiable` |
| R4 | 合约实现无法确定（代理槽/代码哈希不可得） | 保留，`contract_version_confidence = low` | `core`，但不进入版本敏感比较 |
| R5 | 交易所标签无日期 | 保留在匹配表，`label_date` 为空 | 仅参与 missing-label 敏感性参数，不进入带日期分析 |
| R6 | 无公开制裁记录，或匹配成功但地址不在指定日提取源中（`sanctions_public_timestamp` 为空） | `match_status = unmatched` 时不推断"未受制裁"；缺日期时保留匹配证据并记 `label_date_missing` | `core`；两者都不进入 timing-gap 子集 |
| R7 | 重复 `event_id` 或非 canonical 区块 | 保留首次，其余排除 | `excluded` |
| R8 | 链未在研究范围内 | 排除并登记在 `chain_registry` | `excluded` |
| R9 | `blacklist_transition = false` 的 `freezing` / `unfreezing` 日志（重复状态调用） | 保留在核心事件表，但 D1、冻结地址数与 episode 指标只归到该地址该轮 blacklist 区间的第一条 `freezing` | `core` |

R9 是指标归属规则，不是排除：重复日志仍然计入事件检索覆盖，但不产生第二个 `balance_at_freeze`。

派生规则（任一命中即取最严格的资格等级）：

```
if exclusion_rule_id in {R1, R7, R8}: eligibility = excluded
elif verification_status == unverifiable: eligibility = core_unverifiable
elif 该指标所需字段全部存在: eligibility = core        # 否则该指标只用受限子集
```

- 排除不改动主表行、不改 `event_class`、不删除原始日志；被排除项在 `data/derived/exclusions.csv` 汇总，分母（覆盖率、unverifiable share）必须包含被排除项。
- 任何人工覆盖需在 `validation` 表留 `deviation_reason` 与 `reviewer`，无记录的覆盖视为违约。

## 5. `balances`

| 字段 | 类型 | 来源 / 推导 | 说明 |
| --- | --- | --- | --- |
| `query_id` | string | `bq_` + `sha256(payload)` 前 16 字节 | 主键 |
| `chain` | enum | 查询参数 | 与 `events` 一致 |
| `token_symbol` | enum | 查询参数 | `USDT` / `USDC` |
| `token_contract` | address | 查询参数 | 小写 |
| `address` | address | 被查询地址 | 小写 |
| `balance_reference` | enum | `at_freeze` / `at_event` / `at_study_cutoff` | 声明该数值属于哪套口径；`at_event` 是 `unfreezing`/`destruction` 的事件前诊断值，不得当作 D1 报告 |
| `reference_event_id` | string | `at_freeze` / `at_event` 时指向 `events.event_id` | 截止日口径为空 |
| `balance_block_number` | int | `block_number - 1` 或 `study_cutoff_block` | 实际查询区块 |
| `balance_block_hash` | string | 区块头 | 用于确认查询落在 canonical 链上 |
| `balance_raw` | 十进制字符串 | `eth_call` 返回 | 最小单位；不得为估算值 |
| `balance_token` | 十进制字符串 | 除以 `10^decimals` | 6 位小数 |
| `token_decimals` | int | 合约常量 | 6 |
| `blacklist_state_at_balance_block` | enum | 历史状态查询：USDT `isBlackListed(address)`、USDC `isBlacklisted(address)` | 选择器按代币 ABI 取，不能混用；与 `events` 状态机互相校验 |
| `balance_source` | enum | `archive_eth_call`（销毁金额不写入本表，见 `events.destruction_amount_raw`） | 禁止 `derived_from_transfers` 作为独立口径 |
| `query_payload_sha256` | string | 请求 JSON 摘要 | 重放凭据 |
| `queried_at_utc` | RFC3339 | 抓取时刻 | 必需 |
| `missingness_status` | enum | 同第 3 节 | 空值必须带原因 |
| `missing_reason_code` | enum | 同第 3 节 | 空值必须带原因 |
| `notes` | string | 人工说明 | 可空 |

## 6. `matches`

| 字段 | 类型 | 来源 / 推导 |
| --- | --- | --- |
| `match_id` | string | `mt_` + `sha256(event_id + layer + source)` 前 16 字节 |
| `event_id` | string | 外键 → `events.event_id` |
| `match_layer` | enum | `ofac_sdn` / `exchange_label` |
| `match_status` | enum | `matched` / `unmatched` / `not_attempted` / `not_applicable` |
| `matched_entity_name` | string | 清单条目名 / 交易所名；未匹配为空 |
| `matched_record_id` | string | SDN UID 或标签库条目 ID |
| `match_evidence_address_string` | string | 外部记录中原样地址串（用于证明是精确匹配） |
| `match_evidence_text` | string | 清单条目原文片段 |
| `match_source_name` | string | `OFAC SDN` / 标签提供方名称 |
| `match_source_url` | string | 可公开访问的证据链接 |
| `match_source_snapshot_sha256` | string | 抓取快照摘要 |
| `matched_at_utc` | RFC3339 | 匹配执行时刻 |
| `sanctions_public_timestamp` | date | 公开指定日：OFAC SDN Advanced XML 中该地址所属实体的 `EntryEvent` 原始发布日期。是**实体级**日期（同一实体的多个地址共享），不是地址级上榜日；只接受有日期依据的记录，不接受清单快照日期 |
| `sanctions_date_basis` | enum | `designation_date` / `list_publication_date` / `list_update_observed_date`；本批用 `designation_date`（实体 `EntryEvent`）。快照日期不属此类，只写 `label_date` |
| `sanctions_date_precision` | enum | `day`；与 `events.event_time_precision` 分属两个时间口径，不共用字段 |
| `label_date` | date | 标签/清单对应的生效或发布日；交易所标签必填才能进入带日期分析 |
| `label_date_basis` | enum | 见第 3 节 |
| `label_date_precision` | enum | `day` |
| `match_confidence` | enum | 只有字符串完全一致的地址才是 `exact_address`，其余为 `none` |
| `missing_reason_code` | enum | 未匹配时写 `sanctions_record_missing` / `external_source_unavailable`，不得留空 |
| `notes` | string | 可空 |

匹配层与核心事件表分离：`matches` 缺行或 `unmatched` 不影响事件是否进入核心分析。

匹配层的落盘与解读规则（工单 06 落地，字段不变）：

- **只读核心集**：`matches` 由 `events` 派生，不改 `analysis_eligibility`、不产生剔除、不写回核心表；核心集事件数与摘要不因匹配改变。
- **两层各自独立判定**：`ofac_sdn` = 地址串完全一致（大小写归一后比对，不做模糊匹配），来源为 `data/raw/ofac` 的固定快照；`exchange_label` = 带日期的历史标签注册表 `config/exchange_labels.json`。两层互不代偿，也不合并成一个布尔值。
- **状态语义**：`matched` 只在有证据（`match_evidence_address_string` + `matched_record_id` + 快照摘要）时出现；`unmatched` = 尝试过但无记录；`not_attempted` = 该层没有可用来源；`not_applicable` = 该层对这条记录不成立（地址不在标签集内，或标签日期晚于事件时间）。
- **缺失与失败都带原因码**：未匹配 `sanctions_record_missing`、无来源 `external_source_unavailable`、匹配但缺日期 `label_date_missing`、不适用 `not_applicable`。不得留空，不得写 0，不得计为成功匹配。
- **日期规则**：`sanctions_public_timestamp` 只接受有日期依据的制裁记录（配 `sanctions_date_basis`）；清单快照的发布日只写 `label_date`（`list_snapshot_date`），不写 `sanctions_public_timestamp`。交易所标签必须带 `label_date` 才能进入带日期分析（无日期按 R5 只进 missing-label 敏感性）；`label_date` 晚于事件时间的标签记 `not_applicable` 并保留证据，不得用于解释该事件。
- **比率分母**：`attempted = matched + unmatched`；`not_attempted` / `not_applicable` 不进分母。分母为 0 时比率写空（JSON `null`）并标 `not_reportable`，不写 `0`。
- **不外推**：匹配覆盖可能只占事件的一小部分；任何基于匹配子集的结论不得外推到全部冻结活动，也不得由公开时间差推断私有请求响应速度。

## 7. `validation`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `validation_id` | string | `vd_` + 顺序号或摘要 |
| `event_id` | string | 被核对事件；整表级检查可为空 |
| `check_type` | enum | `source_position` / `order_version` / `balance_state` / `manual_reference` / `schema_conformance` |
| `field_name` | string | 必须是字典内字段名 |
| `contract_value` | string | 重建输出值 |
| `reference_value` | string | 独立参照值 |
| `reference_source` | string | 人工核对来源 / 外部历史状态来源 / 区块浏览器 |
| `reference_retrieved_at_utc` | RFC3339 | 参照获取时刻 |
| `agreement` | enum | `match` / `mismatch` / `unverifiable` |
| `deviation` | string | 偏差量或差异描述 |
| `deviation_reason` | string | 已定位的原因；未定位写 `unresolved` |
| `resolution` | string | 处置（修正、接受并说明、排除规则 ID） |
| `reviewer` | string | 核对人 |
| `reviewed_at_utc` | RFC3339 | 核对时刻 |

## 8. `chain_registry`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `chain` | enum | `ethereum`、`tron`（待确认）等 |
| `chain_id` | int | EVM 链号；非 EVM 为空 |
| `in_scope` | enum | `verified_sample` / `registered_pending` / `excluded` |
| `scope_basis` | string | 依据（反馈原文、评审决定、待确认项编号） |
| `rpc_endpoint_label` | string | 逻辑端点名 |
| `notes` | string | 可空 |

只有 `in_scope = verified_sample` 的链可出现在 `events` 中；其余链的存在本身也必须登记，避免"未声明链范围"。

## 9. 可得性分层

| 层 | 含义 | 覆盖字段 | 可支撑的指标 |
| --- | --- | --- | --- |
| A 一致可得 | 由日志、收据与归档状态直接得到，两个 issuer 同法可得 | 全部位置/来源字段、`event_class`、`affected_address`、`destruction_amount_*`、`balance_pre_event_*`、`contract_code_hash`、状态机字段 | event retrieval coverage、balance reconstruction error、unverifiable share（全量口径） |
| B 条件可得 | 依赖外部数据源，只覆盖子集 | `matches.*`、`sanctions_public_timestamp`、`label_date`、派生的 `sanctions_timing_gap_days` | public-record match rates、timing gaps、exchange outflow（仅受限子样本） |
| C 不可观测 | 无字段 | 私有执法请求时间、冻结原因、地址实体归属、跨链迁移 | 不产出指标，只在正文声明 |

报告任何指标必须标注其 `analysis_eligibility` 与可得性层；B 层指标不得与 A 层指标拼接成一条结论。

## 10. 派生字段

| 字段 | 定义 | 约束 |
| --- | --- | --- |
| `sanctions_timing_gap_days` | `date(block_time_utc) − sanctions_public_timestamp`，单位天 | 分辨率 1 天；负值与 >365 天单列计数，不进入中位数 / P90；不得用于推断私有请求响应 |
| `blacklist_duration_blocks` | 同一地址相邻 `freezing` 与 `unfreezing` / 截止区块之差 | 缺任一端点则不计 |
| `gross_frozen_amount` | 同一地址 D1 求和 | 不得与 D2 相加 |
| `freeze_episode_id` | 同一地址按 `log_order_key` 切分连续 `blacklisted` 区间，第 n 段记 n | `unfreezing` 关闭当前区间，`destruction` 不关闭 |
| `is_episode_start` | 该 `freezing` 日志是否为所在区间的第一条 | 只有它为 true 的行归入 D1 |
| `event_retrieval_coverage` | 已检索事件数 ÷ 区块范围内日志应有数 | 分母含被排除项 |

派生字段只写入派生表，不写回 `events`。
