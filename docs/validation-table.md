# 验证表 v1

契约版本：`2026-11-13 / v1`。列定义来自 `docs/data-dictionary.md` 第 7 节；本文件记录核对协议、已交付样本与核对结果。

## 1. 列

`validation_id, event_id, check_type, field_name, contract_value, reference_value, reference_source, reference_retrieved_at_utc, agreement, deviation, deviation_reason, resolution, reviewer, reviewed_at_utc`

表中不得出现字典外字段。`field_name` 必须是字典内字段名；`schema_conformance` 用 `docs/data-dictionary.csv` 的字段清单作为参照。

| `check_type` | 核对内容 | 本次使用的 `reference_source` |
| --- | --- | --- |
| `source_position` | 事件的 `block_number` / `tx_index` / `log_index` 与位置 | Etherscan API v2 `module=logs`（与采集端点不同的提供商） |
| `order_version` | 合约实现与代码哈希 | Alchemy `eth_getCode`（与采集端点不同的提供商） |
| `balance_state` | `balance_pre_event_raw` | Alchemy 历史 `eth_call`（与采集端点不同的提供商） |
| `manual_reference` | 公开记录中的地址/匹配状态 | OFAC SDN 快照（`SDN.CSV`，sha256 记录在匹配表） |
| `schema_conformance` | 交付表列集合 | `docs/data-dictionary.csv` |

## 2. 核对协议

1. 每个被核对字段至少两个互相独立的来源：采集端点（Tenderly gateway）与另一家提供商（Etherscan / Alchemy），两者都记录 `reference_source` 与 `reference_retrieved_at_utc`。
2. 余额分歧以历史状态重放为准；其余分歧进入 `deviation` 并记录原因。
3. 只有单一来源或来源无法复核时 `agreement = unverifiable`，计入 `unverifiable share`，不得记为 `match`。
4. 每个字段可追问到"哪次请求、哪个区块、哪个日志位置"：`source_query`、`query_payload_sha256`（`data/raw/state_calls/`）、`raw_log_sha256`、`match_source_snapshot_sha256` 提供凭据。
5. 先验证后定范围：本表结果（覆盖率、偏差、结构性发现）是工单 04 确定研究期与样本范围的输入。

## 3. 已交付样本

采集范围：Ethereum 全量，区块 `4,634,748 – 26,404,630`（USDT 部署至采集时最新块），5 个黑名单日志 topic，6,060 条日志，`raw_log_sha256 = 10fdb3899a2de7252b976becf8a2fdc3d2097b89733dbdb28a65aff026c3a1eb`。研究截止块 `26,404,630`（`2026-11-13T07:44:47Z`）；工单 04 已确认它同时是研究截止（`config/study_scope.json`），观察期起点定为 `10,274,701`。

| 槽位 | 事件 | 区块 | 被冻结地址 | 余额（token） | 合约版本 | 截止日状态 | OFAC |
| --- | --- | --- | --- | --- | --- | --- | --- |
| S1 | USDT `freezing` | 26,292,598 | `0x12ea…4e8d` | 5.844309 | TetherToken | 仍冻结，D2 = 5.844309 | 匹配（DPRK2） |
| S2 | USDC `freezing` | 25,705,174 | `0xe05f…4a2a` | 0.000230 | FiatTokenV2_2 | 仍冻结，D2 = 0.000230 | 匹配（SDGT） |
| S3 | USDT `unfreezing` | 22,766,755 | `0xd850…e10e` | 8,123（`at_event`） | TetherToken | 已解冻后在 23,141,909 再次冻结，D2 = 8,123 | 匹配（RUSSIA/CYBER4） |
| S4 | USDC `unfreezing` | 26,038,856 | `0x284b…9a7a` | 23,409.304687（`at_event`） | FiatTokenV2_2 | 解冻后未再冻结，无 D2 | 未匹配 |
| S5 | USDT `destruction` | 24,721,916 | `0x4f47…270c` | 58,148.619145（`at_event`，与销毁金额相等） | TetherToken | 仍冻结，D2 = 0 | 匹配（NPWMD） |
| S6 | USDC `freezing`（V1 时代） | 10,274,701 | `0xaa05…1ee0` | 100,000.000000 | FiatTokenV1 | 仍冻结，D2 = 100,000.000000 | 未匹配 |

样本文件：`data/samples/sample_events.csv`（6 行 / 56 列）、`sample_balances.csv`（11 行 / 19 列）、`sample_matches.csv`（12 行 / 21 列）、`sample_validation.csv`（25 行 / 14 列）。列集合与字典逐字段一致，无字典外字段。

选样规则：每个槽位取"满足条件且优先 OFAC 匹配、其次最新"的事件，地址与槽位不重复。S6 专为覆盖合约版本差异（V1 → V2_2）而取最早的一例 USDC 冻结。

### 3.1 生命周期案例（工单 03）

每例由 `config/lifecycle_cases.json` 声明，独立运行（`scripts/reconstruct_lifecycle.py --case <id>`），产物在 `data/recon/lc_<id>/`。

| 案例 | 事件序列（按 `log_order_key`） | 余额行 | 验证行 | 结论 |
| --- | --- | --- | --- | --- |
| `unfreeze` | USDT `freezing` 18,693,913 → `unfreezing` 22,766,755 → `freezing` 23,141,909 | `at_freeze`×2、`at_event`×1、`at_study_cutoff`×1 | 32，全 `match` | 解冻关闭 episode 1；重新冻结开启 episode 2，D1 重新起算；截止日仍冻结，D2 = 8,123 |
| `destruction` | USDT `freezing` 16,750,720 → `freezing` 18,749,899（R9）→ `destruction` 24,721,916 | `at_freeze`×1、`at_event`×1、`at_study_cutoff`×1 | 33，全 `match` | 重复冻结不产生第二个 D1；销毁不改变状态位，`destruction_amount_raw` = 58,148,619,145 = 销毁前余额；D2 = 0 |
| `tx_scope` | USDT `freezing` 26,292,598（交易共 3 条日志，事件在中间） | `at_freeze`×1、`at_study_cutoff`×1 | 20，全 `match` | 只有已登记事件成行；`tx_event_count` = 1；未登记日志不进入事件表 |
| `version_switch` | USDC `freezing` 10,274,701（`FiatTokenV1`）→ `freezing` 25,705,174（`FiatTokenV2_2`） | `at_freeze`×2、`at_study_cutoff`×2 | 33，全 `match` | 两次事件同一条流水线解码（地址均取自 topic）、同一组选择器；版本随事件记录；实现切换在 10,743,414 与 18,963,716 |
| `unfreeze_usdc` | USDC `unfreezing` 26,038,856 | `at_event`×1（无 D2） | 17，全 `match` | 截止日已不在黑名单集合，不产生 `at_study_cutoff` 行；补齐样本中 USDC × `unfreezing` 的覆盖（工单 04） |

案例级核对除逐事件的 5 项外，另加：交易内事件序列与该交易收据过滤后的序列一致（`source_position`/`log_order_key`）、目标交易的事件行数与该交易在快照中的日志数一致（`source_position`/`tx_event_count`）、与工单 01 人工核对样本逐字段对照（`manual_reference` / `balance_state` / `order_version`，含样本自带的调整量）、代理实现切换两侧的代码哈希（`order_version`/`contract_code_hash`，切换点须经两侧取值复核），以及 `expect` 声明的预期值。

## 4. 核对结果

- 25 条检查全部 `match`：`source_position` 6、`order_version` 6、`balance_state` 6、`manual_reference` 6、`schema_conformance` 1。
- 6 个事件的 `balance_pre_event_raw` 在两个独立提供商上完全一致（均为整数最小单位，无舍入）；其中 3 个 `freezing` 行即 D1，另 3 行为 `at_event` 诊断值。
- 6 个事件的实现代码哈希在两个独立提供商上一致。
- 状态转移与事件类型一致：三例 `freezing` 为 `not_blacklisted → blacklisted`，两例 `unfreezing` 为反向，一例 `destruction` 前后均为 `blacklisted`（`blacklist_transition = false`）。
- `reviewer` 记为 `agent:auto-cross-check`：跨源比对由代理执行；人工签署由其后的 26 行分层抽样完成（见 §8），覆盖抽样而非逐行。
- 工单 03 的四个案例共 118 条检查全部 `match`（32 / 33 / 20 / 33），无 `mismatch`、无 `unverifiable`；案例级预期无一条失败。
- 案例重放：`--case all` 连续两次的 `data_digest` 逐一相同；`tx_scope` 的 `--refresh` 与「主端点指向不可达地址」重放都得到同一 `data_digest`。
- 工单 03 复核发现并修正一处参照端缺陷：Etherscan v2 的 `proxy eth_call` 忽略历史区块参数、返回 latest 状态。旧版 `balance_state` 因此可能拿「当前余额」当历史余额（在余额未变的地址上恰好一致，例如 S1；在已销毁的地址上直接为 0）。修复后历史余额只来自归档端点，工单 02 的 `data_digest` 由 `207f25f3…e646` 变为 `f259582b…e03b3`，差异仅在 `reference_source`（Etherscan → drpc），5 条 `match` 与全部数值不变。

### 4.1 代表性样本验证（工单 04）

一次运行覆盖 5 个案例、6 个样本槽位，产物 `data/recon/vs_3b3a9d4db71b1186ad8204411a891f41/`（`events` / `balances` / `validation` 与 `manifest.json`）。入口 `python scripts/validate_sample.py`，范围决策核验 `--check-scope`。

| 指标 | 结果 | 口径 |
| --- | --- | --- |
| `event retrieval coverage` | 13/13 日志（`1.000000`）、6/6 槽位；区块级日志集合一致 6/6 | 分母为样本槽位所在区块内独立端点的日志总数（USDT 10、USDC 3）；全量覆盖属工单 05 |
| `balance reconstruction error` | 15/15 完全一致，最大绝对误差 `0` raw / `0.000000` token | D1 6、`at_event` 3、D2 5、销毁金额 1；参照为独立端点的历史状态重算 |
| `unverifiable share` | `0.000000`（0 / 161） | 本批次全部核对行；`source_position` 34、`order_version` 18、`balance_state` 61、`manual_reference` 38、`schema_conformance` 10 |

- 样本矩阵：USDT `freezing` / `unfreezing` / `destruction`；USDC `freezing` / `unfreezing`（`destruction` 结构性零）；合约版本 `TetherToken`、`FiatTokenV1`、`FiatTokenV2_2`。
- 新增核对项（均为字典内字段）：`source_position`/`log_index`（事件在同区块独立端点日志中可定位）、`source_position`/`block_number`（同区块同 topic 日志条数一致；截止区块不高于 finalized 头）、`balance_state`/`balance_reference`（截止日黑名单状态决定 D2 归属）、`balance_state`/`balance_raw`（D2 独立重算）、`balance_state`/`balance_block_hash`（截止区块 canonical）、`balance_state`/`destruction_amount_raw`（销毁金额 = 销毁前余额）。
- 复现：连续运行 `data_digest = c69ad3b4…5f63` 与 `artifact_digest = 3c5e407f…0e31` 逐一相同；仅把 `ETHEREUM_ARCHIVE_RPC_URL` 指向不可达地址重放，结果不变（重放不需要网络）；`--expect-data-digest c69ad3b4699d166ee6261d731f931f7d05b6221ed7e948efcf93f6b07dd15f63` 可复核。
- 结果用途：本表是 `docs/study-scope.md` 与 `config/study_scope.json` 的唯一输入；决策不得引用本批次以外的数字，`--check-scope` 会从交付 CSV 重算摘要并逐条比对。

### 4.2 核心集全量核对（工单 05）

产物 `data/recon/core_64c8c7f9452291663926113a2b1ecc20/`（`events` / `balances` / `validation` / `chain_registry` 与 `manifest.json`、`coverage_gaps.csv`、`scope_excluded.csv`）。入口 `python scripts/reconstruct_core.py`，离线复核 `--check`。

| 指标 | 结果 | 口径 |
| --- | --- | --- |
| `event retrieval coverage` | 5,970 / 5,970（`1.000000`，严格口径同值）；区块级日志集合一致 4,980 / 4,980 | 分母为独立端点在观察期内按代币 × topic 全量检索的日志数；差集 0 行 |
| `balance reconstruction error` | 9,482 / 9,482 完全一致，最大绝对误差 `0` raw；不可比较 0 | D1 4,077、`at_event` 1,832（含 16 个 `query_id` 去重事件的逐事件核对）、D2 3,573；每项均对照独立归档端点 |
| `unverifiable share` | 行口径 `0.000000`（0 / 21,492）；记录口径 `0.000000`（0 / 15,436） | `source_position` 5,971、`balance_state` 9,543、`manual_reference` 5,972、`order_version` 5、`schema_conformance` 1 |

- 21,492 条核对全部 `match`；R1 占位、R7/R8 排除均为 0；`analysis_eligibility` 5,970 全为 `core`。
- 观察期外 90 条 USDT 事件逐条写入 `scope_excluded.csv`（`before_observation_start`），不进入核心集也不参与比较；匹配结果未剔除任何事件（`match_based_exclusions = 0`）。
- R9 归属行 92 条（`freezing` / `unfreezing` 且 `blacklist_transition = false`），保留在核心集、不产生额外 D1；另有 16 个同区块重复销毁共用一次事件前查询。
- 合约版本按 5 个不同代码哈希核对：`TetherToken`、`FiatTokenV1`、`FiatTokenV2_2` 与两段未登记指纹的 USDC 中间实现（215 个事件，R4 → `contract_version_confidence = low`）。
- 复现：冷跑约 2 小时 15 分（归档端点配额与配速）；检索缓存补齐后 15 分钟完成，`data_digest = 49db21c8…2fd5`、`artifact_digest = fdea03ea…7b01` 不变，`generated_at_utc` 保持 `2026-11-17T12:47:27Z`；主端点指向不可达地址重放同样得到两个相同摘要。

## 5. 数据推翻的先验假设

这四条是"验证先行"的实际产出，已写回 `docs/definitions.md` 与 `docs/data-dictionary.md`：

1. **一个交易内不会出现两条以上黑名单事件。** 全量 6,060 条日志按 `tx_hash` 分组，`tx_event_count ≥ 2` 的交易数为 0。三类事件都是单地址的 owner 调用，批量交易只表现为多笔交易。工单 03 要求的"多事件交易"在本范围内不可得；替代用例改为"同一交易内黑名单事件被非 token 日志前后包围"（S1/S3/S5 均为 3 条日志中的第 2 条）。
2. **USDT 销毁不 emit `Transfer` 到零地址。** 销毁交易只含 `DestroyedBlackFunds` 与被调用合约自身的事件，余额减少在日志层不可见。因此销毁金额只能取事件参数，用 `Transfer` 累加会漏掉销毁，D2 与 D1 的差额也无法由 Transfer 解释。
3. **日志数不等于状态变化数。** 重复调用使同一地址重复收到状态日志：USDT 7 条 `freezing`、1 条 `unfreezing`；USDC 54 条 `freezing`、30 条 `unfreezing` 未改变状态。已引入 `blacklist_transition`、freeze episode 与规则 R9：这些日志计入覆盖，但不产生第二个 D1，也不计入冻结地址数。
4. **SDN 导出不含指定日。** `SDN.CSV` 只有清单快照发布日（`2026-11-09`），没有逐条指定日期。因此 `sanctions_public_timestamp` 在样本中留空（`missing_reason_code = not_collected`），快照日期只写入 `label_date`（`list_snapshot_date`）。timing-gap 分析需要另取带日期的 OFAC 行动记录，作为工单 08 的输入。
5. **「参照端点返回 200 就算数」是错的。** Etherscan v2 的 `proxy eth_call` 接受历史区块参数却返回 latest 状态：USDT `balanceOf(0x4f47…270c)` 在销毁前的区块 24,721,915 返回 0（当前余额），而归档端点返回 58,148,619,145。这类「成功但错误」的响应只能靠第二来源对照发现，因此历史状态一律只接受归档端点，且每个数值都要有两个互相独立的来源。
6. **同一地址的冻结可能在两个 issuer 合约上交错发生。** `0xd850…e10e` 在 23,140,604 收到 USDC `Blacklisted`，在 23,141,909 收到 USDT `AddedBlackList`；`0x4f47…270c` 在 USDT 与 USDC 上各有冻结日志。按地址合并事件会把 USDC 的日志误当作 USDT episode 的边界，因此事件序列、episode 与余额都必须按代币合约分别计算。

## 6. 全量基线（供工单 04/05 使用）

| 代币 | `freezing` | `unfreezing` | `destruction` | `freezing` 命中 SDN 地址 |
| --- | --- | --- | --- | --- |
| USDT | 3,220 | 347 | 1,309 | 96（3.0%） |
| USDC | 957 | 227 | 0（结构性） | 95（9.9%） |

- 去重后地址 4,113 个；USDT 的 3,210 个被冻结地址中有 1,212 个（37.8%）出现过 `destruction`。
- 样本 6 例中 4 例命中 SDN（66%），远高于总体 3.0%/9.9%：这是"优先取匹配项"的选样偏差，不能把样本匹配率当作总体匹配率。
- 交易所标签层未接入（`EXCHANGE_LABELS_API_URL` 未配置），样本中相应行为 `not_attempted`。
- 样本 `sample_matches.csv` 的原因码已与工单 06 的交付表对齐：匹配成功但缺指定日期记 `label_date_missing`
  （原记 `not_collected`，不在 `matches.missing_reason_code` 的字典枚举内）。只改原因码，证据、日期与
  数值未动；口径见 `docs/matching-and-missingness.md`。

## 7. 已知限制

- 全量验证表没有逐行人工签字（`reviewer = agent:auto-cross-check`）；26 行分层抽样已签署，结果见 §8。`manual_reference` 是公开记录字符串比对，不是人读核对。
- 单次 SDN 快照，无历史快照，因此只能判定"当前是否在列"，不能判定"当时是否在列"。
- 采集端点单一（Tenderly gateway）用于全量扫描；样本阶段的交叉核对只覆盖 6 个事件。全量覆盖率的独立复核已由工单 05 完成（§4.2：5,970 / 5,970，区块级 4,980 / 4,980）。
- 全量 `balance_state` 逐事件核对的是事件前余额与 D2 余额；`blacklist_state_before/after` 本身仍只由归档端点取得，其独立核对依赖 D2 归属与工单 04 的人工样本，不逐事件重复。
- `balances` 以 `query_id` 为主键：同一区块内同一地址的多次销毁共用一次事件前查询，余额表只保留一行（16 个事件），这些事件的 `events.balance_pre_event_raw` 仍各自记录且逐条核对。
- `attribution`、冻结原因、私有执法请求时间仍不可观测，无字段、不参与指标。
- 多事件交易在本范围结构性不可得（0 / 6,060 笔），交易作用域只用「多日志交易 + 只成已登记事件行」验证；一旦出现含两条以上事件的交易，需要重新以人工核对样本覆盖该路径。
- USDC 的中间实现（V2、V2_1）未登记在 `config/contracts.json` 指纹表，切换边界的版本标签留空、`contract_version_confidence = low`（R4）；边界本身以代码哈希确认，不依赖标签。
- 生命周期案例每类只有 1–3 例，用于证明路径可走通与口径正确，不代表该类事件的全量特征；覆盖与缺失仍归工单 05/06。
- 工单 04 的覆盖率 `1.000000` 只对 6 个样本槽位成立，不是全量覆盖率；`unverifiable share = 0` 也只表示本批次的核对项都有独立第二来源。
- 研究期与样本范围由本表结果决定，`config/study_scope.json` 的每个 `supported_by` 证据路径都由 `--check-scope` 对产物校验；改动重建逻辑导致数据摘要变化时，旧决策自动失效。
- 版本切换边界用二分定位，假设代理实现槽在区间内单调（切换后不回退）；若出现临时替换再回退，二分只返回首个变化点。

## 8. 人工抽样签署（2026-11-17）

`data/derived/manual_signoff_sample.csv` 的 26 行已由 `pbn` 于 `2026-11-17T14:08:54Z` 签署，全部 `match`。

- 校验：`python scripts/check_signoff.py --validate` → `rows 26 / unsigned 0 / numeric check 26 ok / sign-off sheet complete and numerically consistent`，退出码 0。
- 构成：20 条事件行（USDT × freezing/unfreezing/destruction 各 4、USDC × freezing/unfreezing 各 4；USDC `destruction` 为结构性零，无行可抽）＋ 6 条 D2 行（两个 issuer 各 3）。余额口径分布为 `at_freeze` 8、`at_event` 12、`at_study_cutoff` 6。
- 每行由 `check_signoff.py` 按 `(tx_hash, log_index)` 重取日志、在 `block-1`（D2 为 cutoff）读黑名单状态与余额，再与交付表比对；两条 `note` 记录辅助表的 10 位有效数字展示值与交付表 6 位小数原值的差异，复算一致，交付表未改动。
- 边界：这是**抽样签署**，不是 21,492 行逐行签字。`validation.csv` 的 `reviewer` 仍为 `agent:auto-cross-check`，审计 V3 按此断言；对外只能写 "a 26-row stratified sample was manually checked against block explorers and archival state"。
