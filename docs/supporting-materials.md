# 支撑材料包与措辞审查（工单 09）

入口：`python scripts/reconstruct_materials.py`（读工单 02–08 的已交付产物，写独立目录）。
离线复核：`python scripts/reconstruct_materials.py --check`。

本文件只做三件事：把四项支撑材料整理成可逐项核对的一套交付物；标明哪些字段一致可得、
哪些指标只能在受支持子集上报告；把描述性措辞约束逐条对到结构化证据上。字段定义仍以
`docs/data-dictionary.md` v1 为准；本工单**不新增字典表**，五张表是派生视图。

## 0. 本次交付

| 项 | 值 |
| --- | --- |
| 批次 | `materials_fe2495c8b40a1d848c29802a422bbb22`（`data/recon/materials_fe2495c8b40a1d848c29802a422bbb22/`） |
| 输入 | `core_64c8c7f9452291663926113a2b1ecc20`、`match_e7a32e2a4120dccb5d5cfe1edf360766`、`compare_9f973ff43a20c53a9fdf9bfac13675be`、`restrict_4c003f027d634313a28140234c40a510` |
| `materials_index.csv` | 32 行（样本 4、字典 2、脚本 10、验证表 8、分析视图 8） |
| `field_availability.csv` | 116 行（字典字段逐一一行） |
| `metric_coverage.csv` | 106 行（比较 38、差值 19、受限 46、核心全量 3） |
| `wording_checks.csv` | 32 行（8 条约束 × 各自的材料） |
| `chain_scope.csv` | 2 行（`ethereum`、`tron`） |
| 摘要 | `data_digest` / `artifact_digest` 见同目录 `manifest.json` |

五张表列集合由 `scripts/recon/materials.py` 的 `*_FIELDS` 声明，写出前断言；它们不进
`docs/data-dictionary.csv`，也不进入任何采集表。批次 id 由上游四个批次的摘要、字典、
研究期决策与四份样本文件的摘要派生，与本文件内容无关。

## 1. 四项支撑材料

`materials_index.csv` 一行一个组件，列 `path` / `sha256` / `rows` / `check_command` /
`check_scope`。四项材料的核对命令都不需要全量重跑：

| 材料 | 组件 | 离线核对 | 能证明什么 |
| --- | --- | --- | --- |
| M1 小数据样本 | `data/samples/sample_{events,balances,matches,validation}.csv` | `python scripts/reconstruct_materials.py --check` | 列集合与字典逐字段一致；样本行按 `event_id` / `query_id` / `(event_id, match_layer)` 命中交付表，比对取值字段 0 处不同 |
| M2 数据字典 | `docs/data-dictionary.md`、`docs/data-dictionary.csv` | 同上 + `python scripts/reconstruct_core.py --check` | 交付表列集合由字典断言；`field_availability.csv` 逐字段取自字典的 `availability_tier` |
| M3 可复现采集脚本 | 8 个入口脚本 + `scripts/recon/*.py` + `requirements.txt` | 各入口自带的 `--check` / `--check-scope` | 同一输入重放得到同一摘要；`--check` 不访问网络、不写盘 |
| M4 验证表 | `docs/validation-table.md`、核心集与样本/案例批次的 7 份 `validation.csv` | `python scripts/reconstruct_materials.py --check` | 列集合对字典第 7 节；`check_type` / `agreement` 取值在字典枚举内；逐批核对行数与结果分布 |

M1 的比对只覆盖取值字段：`abi_source`、`contract_version_source`、`source_query`、`notes`、
`match_id`、`matched_at_utc`、`match_source_name`、`match_source_snapshot_sha256`、
`match_evidence_text` 是来源或叙述字段，由工单 01 与工单 06 两个入口在不同时刻写入，
比对它们只会暴露时间差。`sample_validation.csv` 的参照值来自工单 01 的独立端点记录，
与核心批次不逐字节相同，因此只核对列与枚举。

## 2. 一致可得字段 vs 仅受支持子集可报的指标

两层划分直接落在字典 §9 的 `availability_tier` 上，`field_availability.csv` 逐字段给出
`analysis_role`、`subset_only`、`rows_with_value` 与 `issuers_with_value`。

| 层 | 字段数 | 落在哪些表 | 可支撑的指标 |
| --- | --- | --- | --- |
| A 一致可得 | 81 | `events` 56、`balances` 19、`chain_registry` 6 | event retrieval coverage、balance reconstruction error、unverifiable share、事件与余额的 issuer 比较 |
| B 条件可得 | 35 | `matches` 21、`validation` 14 | public-record match rates、timing gaps、exchange outflow、受限子集的 frozen balances |
| C 不可观测 | 0 个字段 | 无 | 私有执法请求时间、冻结原因、地址实体归属、跨链迁移：只在正文声明，不产出指标 |

本批实测的两个要点：

- `matches.sanctions_public_timestamp` 193 / 11,940 行有值（全部是实体级 `designation_date`），
  `matches.label_date` 也有 193 行、全部是 `list_snapshot_date`。依赖指定日的 timing-gap 指标
  因此在两个 issuer 上都可报告；依赖交易所标签的指标仍连分母都没有（标签层 `not_attempted`）。
- `validation` 的 14 个字段是核对记录，不进入任何分析指标；它们是 B 层，但用途是复现
  凭据，不是结论来源。

指标侧（`metric_coverage.csv`，110 行）按报告范围分四档，每一档都带 `evidence_tier`：

| 报告范围 | 行数 | 含义 | `evidence_tier` |
| --- | --- | --- | --- |
| `all_events` | 43 | 观察期内全部可检索事件 | A |
| `core_subpopulation` | 12 | 核心集内已闭合 episode（截止日仍开放的是删失数据） | A |
| `supported_subset` | 53 | 需要匹配证据与完整日期的子集 | B |
| `definitional_zero` | 2 | 合约定义上不存在的事件类（USDC `destruction`） | A（定义性零，不产出差值） |

`supported_subset` 涉及的 14 个指标 id：`timing_gap_*`（8 个）、`outflow_*`（3 个）、
`frozen_balance_at_freeze_*`（2 个）、`exchange_label_dated_coverage`（1 个）。这 14 个指标
**只能**对受支持子集报告，不与 A 层指标相加或拼接；`metric_coverage.csv` 的
`causal_inference_permitted` 列对全部 110 行固定为 `no`。

`status` 列沿用来源表的取值：比较表与受限表为 `reported` / `not_reportable` /
`structural_zero`，差值表为 `comparable` / `not_comparable`。`not_reportable` 与
`not_comparable` 都不是 0，`structural_zero` 是定义性零。

## 3. 措辞约束逐条核对

`wording_checks.csv` 一行 = 一条约束 × 一个材料，列 `evidence_kind` / `evidence_pointer` /
`observed` / `passed`。核对对象是结构化证据（`statement_type`、`coverage_scope`、报告
caveats、表列取值、链登记行），不是散文；文档侧只核对"是否出现对应声明"（锚点），
语义审查仍属人工职责；26 行数据抽样签署已完成，见 `docs/manual-signoff.md`。

| 约束 | 内容 | 主要证据 | 本次结果 |
| --- | --- | --- | --- |
| W1 | 所有结论、指标与差值行都标为 descriptive（描述性），材料中不出现因果判断：描述性统计与因果性判断可一眼区分。 | 两个报告的 `conclusions[].statement_type`；`metric_coverage.csv` 的 `statement_type` 与 `causal_inference_permitted` | 7 + 6 条结论全为 `descriptive`；106 行指标 0 行允许因果推断 |
| W2 | public sanctions timing gaps 只描述公开记录日期与链上事件时间之间的差，不推断私有执法请求的响应速度。 | `caveats.no_private_request_inference`、`no_snapshot_date_as_sanctions_date`；`matches.csv` 的 `sanctions_public_timestamp`；timing 行 `timestamp_precision = day` | 12 行 timing 指标全为 `day` 精度、全 `not_reportable`；OFAC matched 但缺日期的 193 行原因码为 `label_date_missing` |
| W3 | address outflows 只描述冻结前窗口内的资金移动，不推断规避行为。 | `caveats.no_evasion_inference`、`transfer_absence_not_zero`；`exchange_outflows.csv` 的 `value` 与 `status` | 6 行全 `not_reportable` 且 `value` 为空（不是 0 流出）；6 个 outflow 指标行均在 B 层 |
| W4 | frozen balances 只描述余额口径与数值，不推断执法有效性；D1 与 D2 分别声明、不混用、不相加。 | `caveats.balances_two_definitions`、`no_enforcement_effectiveness_inference`；`balance_reference` 列；受限批次 `balance_definition` | 20 行 `frozen_balance_*` 指标（含 4 行差值）全部声明或继承口径，未声明 0 行（D1 2 个 / D2 2 个 id）；受限子集 8 行全为 `at_freeze` |
| W5 | 有限匹配子样本的结论不外推到全部冻结活动；受限子集指标与核心指标不拼接、不互相补充。 | `caveats.not_extrapolated`；B 层行的 `coverage_scope` | 49 行 B 层指标全部 `restricted_subset`；核心指标无一混入 |
| W6 | 两条分析路径物理分离：独立批次目录、独立表名、受限行的 coverage_scope 唯一，且不产出受限口径的 issuer 差值。 | `restrict_*/manifest.json#separation`；两侧表名 | `concatenated_with_core=false`、`cross_issuer_difference_produced=false`；比较表名与受限表名重名 0 个 |
| W7 | 清单快照发布日不是制裁日期：sanctions_public_timestamp 只接受有日期依据的记录，快照日期只写 label_date。 | `matches.csv` 的日期列；字典 `sanctions_date_basis` 枚举；`docs/definitions.md` 4.2 | 快照日期顶替制裁日期的行 0；制裁日期缺依据的行 0 |
| W8 | Ethereum 只被反馈限定在「小型代表性样本」；核心分析条款写的是 all retrievable events，未限定链；其他链登记为待确认项并写明纳入后的采集影响。 | `chain_registry.csv`；`config/study_scope.json#open_items`；`docs/study-scope.md` | `ethereum=verified_sample`、`tron=registered_pending`；open_items 含链范围待确认 |

## 4. 链范围：待确认项及其对采集范围的影响

| 链 | `in_scope` | 核心集事件 | 状态 | 纳入后的影响 |
| --- | --- | --- | --- | --- |
| `ethereum` | `verified_sample` | 5,970 | 已采集并验证 | 无需扩展：核心分析已在本链观察期内全部可检索事件上完成 |
| `tron` | `registered_pending` | 0 | 仅登记、不采集 | 需采集该链黑名单日志并重跑核心集、匹配层、比较与受限分析，重做研究期决策（观察期起点可能改变），并补验证样本覆盖该链 |

**反馈的限定范围**：反馈只在「小型代表性样本」条款中写明 Ethereum；核心分析条款写的是
all retrievable events，未限定链。因此本包的 Ethereum 结果是"本次实际采集范围"，
**不是**反馈对全部链的限定，也不能读成"反馈只要求做 Ethereum"。待确认项登记在
`config/study_scope.json` 的 `open_items`、`chain_registry.csv` 的 `scope_basis` 与 spec 的
Further Notes 三处；`report.json` 的 `chain_scope.pending_items` 同时给出
`effect_on_collection`。

确认前不得做的事：把 `ethereum` 的数字写成"所有链"、把其他链的缺失读成"没有冻结"、
在未扩采集的情况下做跨链比较。

## 5. 描述性统计与因果性判断怎么区分

一眼可分辨，靠的是固定取值而不是措辞习惯：

| 位置 | 取值 | 含义 |
| --- | --- | --- |
| 指标/差值/受限表 `statement_type` | 固定 `descriptive` | 只描述数据，不含原因 |
| `metric_coverage.csv` `causal_inference_permitted` | 固定 `no` | 该指标不允许作因果解读 |
| `metric_coverage.csv` `reporting_scope` | `all_events` / `core_subpopulation` / `supported_subset` / `definitional_zero` | 结论覆盖到哪一层，四档不相加 |
| 报告 `caveats` | 逐条列出禁止的推断 | 私有请求响应、规避、执法有效性、外推 |
| 本文件 §3 | 8 条约束逐条对到证据 | 描述性与因果性的边界 |

四类禁止的推断与其证据位置一致：**public sanctions timing gaps** 不推断私有请求响应速度
（W2）；**address outflows** 不推断规避（W3）；**frozen balances** 不推断执法有效性（W4）；
受限子样本不外推（W5）。材料中出现的每个"不可报告（`not_reportable`）"都是"未测量"，
不是 0；每个"结构性零（`structural_zero`）"都是合约定义，不是行为差异。

## 6. 复现与复核

```
python scripts/reconstruct_materials.py
python scripts/reconstruct_materials.py --check
python scripts/reconstruct_materials.py --expect-data-digest <hex>
python scripts/reconstruct_materials.py --compare-id <batch> --restrict-id <batch>
```

- 输入全部在本地（四个上游批次 + 样本 + 字典 + 决策），不需要网络。
- 批次 id 由输入摘要派生；上游任一摘要变化即换新目录，不会读错批次。多个候选批次时
  要求显式 `--match-id` / `--compare-id` / `--restrict-id`，不猜"最新目录"。
- `data_digest` 覆盖五张表；`report.json` 只进 `artifact_digest`。同一输入重放逐字节相同，
  `generated_at_utc` 保持首次取证时刻（每个表不含抓取时刻）。
- `--check` 不写盘：重算五张表逐行比对、重算报告与规则自检、核对上游四个批次的摘要未变、
  并拒绝任何 `passed = no` 的措辞行。
- 退出码：0 通过；1 运行失败（材料缺失、上游批次缺失或存在多个候选）；2 数据摘要不符；
  4 `--check` 不一致。

## 7. 已知限制

1. **措辞只做到"结构化 + 声明"**：脚本核对 `statement_type`、`coverage_scope`、caveats 键、
   列取值与文档锚点，不做语义判断。数据抽样签署已完成 26 行（`data_signoff`，26/26 `match`），
   正文语义审查仍待人工（`prose_semantic_review = pending`）。
2. **样本与交付表的差异字段被显式排除**：`abi_source`、`source_query`、`notes`、
   `match_id`、`matched_at_utc`、`match_source_name`、`match_source_snapshot_sha256`、
   `match_evidence_text` 由不同入口在不同时刻写入；`materials_index.csv` 的 `note` 记录了
   每个样本排除了哪些字段。
3. **`sample_validation.csv` 只核对列与枚举**：参照文本是工单 01 的独立端点记录，与核心
   批次的参照字符串不同格式，逐值比对会得到假偏差。
4. **B 层指标部分有数值**：timing gap 的 8 个指标在 USDT / USDC 上都已报告（189 个 episode
   起点）；exchange outflow 的子集仍为空，`frozen_balances` 的 outflow 行与窗口敏感性
   仍 `not_reportable` / `not_assessable`。本包只保证"有数据时同一段代码能出数、且必带
   B 层标记"，不主张已测到资金流出行为。
5. **规则自检是内部断言**（5 条）：样本漂移、快照日期顶替、因果结论、B 层标注、缺失材料
   各用一条合成 fixture 证明"该检查会失败"。断言写在 `report.json` 的 `rule_checks`，
   `--check` 会重跑；验证边界仍是本包的输出。
