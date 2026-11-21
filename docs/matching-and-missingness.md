# 匹配层与缺失报告（工单 06）

产物：`python scripts/reconstruct_matches.py`（读工单 05 的核心集，写独立目录）。
本次交付批次见 `data/recon/match_<id>/manifest.json`；下表数字取自该批次的 `report.json`。

本文件只解释两件事：匹配结果怎么来、覆盖到哪；以及哪些值缺失、为什么缺失。字段定义仍以
`docs/data-dictionary.md` v1 为准（第 6 节），本文不新增字段。

## 0. 本次交付

| 项 | 值 |
| --- | --- |
| 批次 | `match_e7a32e2a4120dccb5d5cfe1edf360766`（`data/recon/match_e7a32e2a4120dccb5d5cfe1edf360766/`） |
| 取证时刻 | `2026-11-17T12:47:58Z`（重放同一输入不改写） |
| 输入核心集 | `core_64c8c7f9452291663926113a2b1ecc20`（`data_digest = 49db21c8…2fd5`），5,970 事件 / 3,753 地址 |
| `matches` 行 | 11,940（5,970 事件 × 2 层） |
| `ofac_sdn` 层 | `matched` 193 行 / 94 个地址；`unmatched` 5,777 行 |
| `exchange_label` 层 | `not_attempted` 5,970 行（无带日期的历史标签来源） |
| public-record match rate | 事件口径 `0.032328`（193 / 5,970）；地址口径 `0.025047`（94 / 3,753） |
| 交易所标签 rate | `not_reportable`（分母 0，比率写空而不是 `0`） |
| 可用日期的子集 | `sanctions 0`、`exchange 0`（`report.json` 的 `analysis_subsets`） |
| missing values | 96 个字段中 23 个有可选空值；**必填字段缺口 0** |
| 摘要 | `data_digest = 2d8cbf6b…c3b6`（`artifact_digest` 含 `matched_at_utc`，逐次运行都会变） |

## 1. 匹配层为什么不进核心事件表

核心集回答"发生了什么"，匹配回答"这个地址在某个外部清单里算谁"。后者依赖的外部数据会更新、
会缺日期、会只覆盖一小部分地址，一旦写进 `events`，就会让"事件是否存在"和"地址能否归属"混成
一件事。所以这一层单独落盘：

| 约束 | 落地方式 |
| --- | --- |
| 只读核心集 | 匹配读 `core_<id>/{events,balances}.csv`，不写回；`--check` 重算核心四表摘要并与核心 manifest 比对 |
| 不产生剔除 | `events.analysis_eligibility` 不参与匹配；manifest 的 `separation.match_based_exclusions = 0`，另报 `eligibility_unchanged`（交付事件表的资格分布与核心 manifest 逐一相等） |
| 一个事件 × 一个匹配层一行 | 5,970 × 2 = 11,940 行，任何事件都不会因匹配失败而少一行 |
| 证据可复核 | 每行带 `match_evidence_address_string`、`matched_record_id`、`match_source_snapshot_sha256` |

## 2. 两层各自的判定规则

**`ofac_sdn`**：地址串完全一致（大小写归一后比对），来源 `data/raw/ofac` 的固定快照
（`csv_sha256 = 7f43332d…55b8`，`publish_date = 2026-11-09`）。不做模糊匹配、不做实体推断。

**`exchange_label`**：读 `config/exchange_labels.json`。该注册表当前登记 **0 个来源、0 条标签**，
因为没有带日期的历史交易所标签数据源（`.env` 的 `EXCHANGE_LABELS_API_URL` 仍未确定）。因此每行
记 `not_attempted` + `external_source_unavailable`：这是"没尝试"，不是"试过没有"。

日期的三条规则（在注册表非空时生效，`report.json` 的 `rule_checks` 用合成 fixture 证明它们真的会触发）：

| 情形 | 结果 |
| --- | --- |
| 标签带日期且 `label_date ≤ 事件时间` | `matched`，日期参与带日期子集 |
| 标签无日期 | `matched` + `label_date` 留空 + `label_date_missing`（R5：只进 missing-label 敏感性） |
| 标签日期晚于事件时间 | `not_applicable` + `not_applicable`，证据保留但不用于解释该事件（前视保护） |
| 地址不在标签集 | `not_applicable` + `not_applicable`（标签集只覆盖交易所地址） |

## 3. public-record match rates

| 分层 | 事件 | `matched` | 比率 | 分母 |
| --- | --- | --- | --- | --- |
| 全部核心集（`ofac_sdn`） | 5,970 | 193 | `0.032328` | 全部事件（含不可核验行） |
| USDT | 4,786 | 98 | `0.020476` | 同代币事件 |
| USDC | 1,184 | 95 | `0.080236` | 同代币事件 |
| `freezing` | 4,138 | 191 | `0.046158` | 同类别事件 |
| `unfreezing` | 572 | 1 | `0.001748` | 同类别事件 |
| `destruction` | 1,260 | 1 | `0.000794` | 同类别事件 |
| 地址口径 | 3,753 | 94 | `0.025047` | 唯一被冻结地址 |
| `exchange_label` | 5,970 | 0 | `not_reportable` | 已登记来源覆盖的事件 = 0 |

`report.json` 另把"匹配成功"与"能进带日期分析"分开计数：

| 字段 | 值 | 含义 |
| --- | --- | --- |
| `ofac_sdn.matched_without_sanctions_date` | 0 | 匹配成功且都有指定日：193 行全部来自 SDN Advanced EntryEvent |
| `ofac_sdn.dated_by_event` | `0.032328`（193 / 5,970） | 带可用指定日的匹配率 |
| `exchange_label.matched_without_label_date` | 0 | 无日期标签（R5） |
| `any_layer.composition` | `ofac_only 193`、`exchange_only 0`、`both_layers 0` | 合并口径拆开来源，避免掩盖标签层 0 覆盖 |

读法：

- `unmatched`（5,777 行）不等于"未受制裁"：SDN 快照只是一个时点的清单，地址可能从未上榜、可能
  在快照之前被移出、也可能上榜但用的是别的链/别的地址串。
- 比率默认给事件口径和地址口径两个，不合并；`USDC 0.080` 与 `USDT 0.020` 的差别是**公共记录覆盖**
  的差别，不能读成冻结行为的差别（`docs/definitions.md` §6）。分代币/分类别只是同一分母的分解，
  真正的同口径比较属工单 07。
- `exchange_label` 的比率不是 0：JSON 里是 `null` + `not_reportable`，`--check` 会拒绝"没有尝试却
  报告比率"的产物。

## 4. 标签日期

| 层 | 日期情况 | 影响 |
| --- | --- | --- |
| `ofac_sdn` | 193 个匹配行都写 `label_date = 2026-11-09`、`label_date_basis = list_snapshot_date` | 这是快照发布日，只作标签日期，不顶替指定日 |
| `ofac_sdn` | `rows_with_sanctions_public_timestamp = 193`，`basis = designation_date`、`precision = day` | 日期取官方 SDN Advanced XML 的实体级 `EntryEvent`，193/193 个匹配事件有值 |
| `exchange_label` | 0 条标签，0 条带日期 | 带日期子集为空 |

要点：快照发布日**不是**制裁日期。`SDN.CSV` 只有 `Publish_Date`，没有每个地址的指定生效日，
因此指定日单独从 `SDN Advanced` XML 取（`config/ofac_advanced.json` 固定了 XML 与提取表的
sha256，审计 M6 逐行核对）。日期是**实体级**：同一实体的多个地址共享一个 `EntryEvent` 日期，
不是地址级上榜日；`>365` 天与负值因此必须在受限分析里单列。OFAC 快照（2026-11-09）晚于核心集
全部事件：匹配只说明该地址在当前清单上，**不构成"冻结时已被指定"的证据**。

## 5. missing values

`missingness.csv` 逐表逐字段记录空值数量与原因码，并标出字典里 `required = yes` 的字段：

| 表 | 行 | 有可选空值的字段 | 必填缺口 |
| --- | --- | --- | --- |
| `events` | 5,970 | 10（`proxy_address`、`contract_version_label`、`destruction_amount_*`、`balance_query_id` 等） | 0 |
| `balances` | 9,466 | 2（`reference_event_id`、`missing_reason_code`） | 0 |
| `matches` | 11,940 | 11（证据、日期、URL 等非必填字段） | 0 |

`matches.missing_reason_code` 的分布：

| 原因码 | 行 | 含义 |
| --- | --- | --- |
| `sanctions_record_missing` | 5,777 | 尝试比对，SDN 快照里没有该地址串（R6） |
| `external_source_unavailable` | 5,970 | 标签层没有可用来源，未尝试 |
| `label_date_missing` | 0 | 匹配成功但缺指定日期：本批 0 行（193 行全部有实体级指定日） |

三条读法：

1. **可选字段为空不是缺口**：`destruction_amount_raw` 在 `freezing` 行按定义就该为空；把它算成
   "缺失率"会让报告看起来比实际糟。所以报告分成"必填缺口"与"可选空值"两份清单。
2. **空值不写 0**：未匹配、未尝试、不适用都保留空串并带原因码，不被折进任何数值。
3. **`events.missingness_status` 全为 `complete`**：核心集的缺失已在工单 05 处理（本次 0 条排除、
   0 条不可核验），匹配层没有新增缺失。

## 6. 已核验的规则

`report.json` 的 `rule_checks` 用合成 fixture 逐条验证规则本身（不只是散文），`--check` 会重跑：

| 规则 | 断言 |
| --- | --- |
| `dated_label_before_event` | 有日期且早于事件的标签 → `matched`，日期保留 |
| `label_after_event_not_used` | 晚于事件的标签 → `not_applicable`，证据保留、不带匹配效果 |
| `undated_label_kept_as_r5` | 无日期标签 → `matched` + 空 `label_date` + `label_date_missing` |
| `no_source_is_not_attempted` | 无来源 → `not_attempted` + `external_source_unavailable`，置信度 `none` |
| `ofac_unmatched_is_not_a_zero` | 未匹配 → `unmatched` + `sanctions_record_missing`，记录 ID 与制裁日期为空 |

## 7. 复现与复核

```
python scripts/reconstruct_matches.py
python scripts/reconstruct_matches.py --check
python scripts/reconstruct_matches.py --expect-data-digest <hex>
```

- 输入全是本地快照（核心集 CSV + `data/raw/ofac` + `config/exchange_labels.json`），**不需要网络**。
- `data_digest` 只覆盖 `matches` 表并剔除 `matched_at_utc`；同一输入重放得到同一摘要，`generated_at_utc`
  保持首次取证时刻。
- `matched_at_utc` 按字典是"匹配执行时刻"，因此复跑会更新它：`artifact_digest` 每次运行都变，
  数据值与 `data_digest` 不变。复核判据是 `data_digest` 与 `--check`，不是 `artifact_digest`。
- `--check` 不写盘：重算摘要与列集合、从核心 `events` 重建整张匹配表逐行比对、重算
  `missingness.csv` 与 `report.json`、重跑规则自检、并确认核心四表摘要未被改动。
- 退出码：0 通过；1 运行失败；2 数据摘要不符；4 `--check` 不一致。

已验证（本机，同一输入）：

| 运行 | 条件 | 结果 |
| --- | --- | --- |
| A | 连续两次运行 | 只有 `matched_at_utc` 变化（11,940 行全部），`data_digest` 不变，`generated_at_utc` 不被改写 |
| B | `--check` | 退出码 0 |
| C | 手改一行 `match_status` 后 `--check` | 退出码 4（摘要不符 + 与重建结果不一致 + 缺证据） |
| D | `--expect-data-digest` 给错值 | 退出码 2 |
| E | 匹配后跑 `reconstruct_core.py --check` | 退出码 0（核心集未被改动） |
| F | `validate_sample.py --check-scope` | 退出码 0（研究期决策未受影响） |

## 8. 结论边界

- 匹配覆盖只占事件的一小部分：193 / 5,970 事件、94 / 3,753 地址命中 OFAC 快照。任何基于匹配子集的
  结论都不得外推到全部冻结活动。
- `unmatched`、`not_attempted`、`not_applicable` 都不等于未受制裁，也不等于成功匹配。
- 带日期的 OFAC 子集为 193 个事件（`sanctions 193`），timing gap 因此可报告；exchange-outflow
  子集仍为 0（`exchange 0`），在本批数据上**不可报告**。两者的分母不同，不得互相补充，也不得
  回退到 OFAC 快照日期。
- 私有执法请求时间不可观测（`not_observable`），本层没有对应字段，也不由公开时间差推断。
- `matches` 表没有 `reviewer` 字段：匹配证据由脚本按固定快照生成；人工签署以 26 行验证抽样
  完成（`pbn`，2026-11-17，26/26 `match`），不覆盖 `matches` 逐行。
