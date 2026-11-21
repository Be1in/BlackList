# 受限子样本的 timing-gap 与 exchange-outflow 分析（工单 08）

入口：`python scripts/reconstruct_restricted.py`（读工单 05 的核心集与工单 06 的匹配层，写独立目录）。
离线复核：`python scripts/reconstruct_restricted.py --check`。

本文件只处理一条路径：在**证据充分的子样本**上给出 public sanctions timing gaps、address
outflows 与 frozen balances 的描述性统计，并说明该子样本如何界定。核心分析（全量事件）与
同口径 issuer 比较属工单 05/07，两者与本文件的数字不拼接、不互相补充。字段定义仍以
`docs/data-dictionary.md` v1 为准；本工单**不新增字典表**，五张表是派生分析视图。

## 0. 本次交付

| 项 | 值 |
| --- | --- |
| 批次 | `restrict_4c003f027d634313a28140234c40a510`（`data/recon/restrict_4c003f027d634313a28140234c40a510/`） |
| 取证时刻 | `2026-11-18T09:32:31Z`（重放同一输入不改写） |
| 输入核心集 | `core_64c8c7f9452291663926113a2b1ecc20`（`data_digest = 49db21c8…2fd5`），5,970 事件 |
| 输入匹配层 | `match_e7a32e2a4120dccb5d5cfe1edf360766`（`data_digest = 2d8cbf6b…c3b6`），11,940 行 |
| `subset_eligibility.csv` | 16 行（2 个分析 × 2 个 issuer × 4 级筛选） |
| `timing_gaps.csv` | 16 行（2 issuer × 8 个指标） |
| `exchange_outflows.csv` | 6 行（2 issuer × 3 个指标，基线窗口 24h） |
| `frozen_balances.csv` | 8 行（2 子集 × 2 issuer × 2 个指标） |
| `outflow_window_sensitivity.csv` | 20 行（5 个窗口 × 2 issuer × 2 个指标） |
| 摘要 | `data_digest = dedfccd8…80f1`；`artifact_digest = 4223a79a…0eff` |

五张表的列集合由 `scripts/recon/restricted.py` 的 `*_FIELDS` 声明并在写出前断言；它们不进
`docs/data-dictionary.csv`，也不进入任何采集表。

## 1. 子集怎么界定（可逐级复核）

子集不是"匹配上的事件"，而是"匹配证据与日期都够用的事件"。每个分析都有 4 级筛选，逐级
写在 `subset_eligibility.csv`：`rule` 是规则文字，`events_in` / `events_dropped` /
`events_remaining` 是每级的进/出/剩余，`dropped_reason_codes` 是淘汰原因码（取自字典枚举）。
三者满足 `events_in − events_dropped = events_remaining`，`--check` 会重算这条算术与"逐级
单调不增"。

### 1.1 `timing_gap` 子集

| 级 | 规则 | 淘汰原因码 | USDT | USDC |
| --- | --- | --- | --- | --- |
| 0 | `analysis_eligibility == core` | `not_core_eligible` | 4,786 | 1,184 |
| 1 | episode 起点（`blacklist_transition = true` 且 `state_before = not_blacklisted`） | `not_episode_start` | 3,174 | 903 |
| 2 | `ofac_sdn` 层 `match_status = matched` | `sanctions_record_missing` | 95 | 94 |
| 3 | `sanctions_public_timestamp` 非空且有 `sanctions_date_basis` | `label_date_missing` | 95 | 94 |

第 3 级不再归零：官方 SDN Advanced XML 的实体级 `EntryEvent` 日期已接入匹配层，95 / 94 个
episode 起点全部有指定日（`designation_date`、`precision = day`）。因此 timing gap 可报告，
但口径必须写清三点：

- 日期是**实体级**，不是地址级上榜日；同一实体的多个地址共享一个日期。
- `gap < 0`（冻结早于公开指定）单列为 `timing_gap_freeze_before_designation_events`，
  不进入中位数，不读作负延迟。
- `gap > 365` 天单列为 `timing_gap_address_added_later_events`，不读作响应延迟；中位数 / P90 /
  同日比例只在 0–365 天子集上计算。

### 1.2 `exchange_outflow` 子集

| 级 | 规则 | 淘汰原因码 | USDT | USDC |
| --- | --- | --- | --- | --- |
| 0 | `analysis_eligibility == core` | `not_core_eligible` | 4,786 | 1,184 |
| 1 | episode 起点 | `not_episode_start` | 3,174 | 903 |
| 2 | `exchange_label` 层 `match_status = matched` | `external_source_unavailable` | 0 | 0 |
| 3 | `label_date` 非空且不晚于事件日期 | `label_date_missing` | 0 | 0 |

第 2 级就归零：标签层整层 `not_attempted`（`config/exchange_labels.json` 没有来源）。无日期
标签按 R5 也不进本子集，只参与 missing-label 敏感性（工单 07）。

两个子集互不合并：一个事件可以只满足其中一条，也可以两条都不满足；本文件不产出"合并子集"。

## 2. 输出表怎么读

| 列 | 取值 | 含义 |
| --- | --- | --- |
| `coverage_scope` | 固定 `restricted_subset` | 每行都属于受限子集；不与 `core_full` 相加或互换 |
| `status` | `reported` / `not_reportable` | `not_reportable` 不是 0；对应 `value` 留空 |
| `statement_type` | 固定 `descriptive` | 所有数字只作描述，不作因果推断 |
| `denominator` | 事件数 | 每个指标自带分母与分母定义 |
| `gap_resolution_days` / `timestamp_precision` | `1` / `day` | 时间差分辨率一天；同日不能声称先后 |

`timing_gaps.csv`：8 个指标 × 2 issuer。`timing_gap_events`（子集规模）、两个单列计数
（`_freeze_before_designation_events`、`_address_added_later_events`），以及只在 0–365 天
子集上计算的 `_days_median` / `_p90` / `_min` / `_max` / `_same_day_share`。本批结果：

| issuer | 子集 | 冻结早于指定 | >365 天 | 0–365 天 | 中位数 | P90 | 同日占比 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| USDT | 95 | 3 | 36 | 56 | 0 天 | 108 天 | `0.660714`（37/56） |
| USDC | 94 | 0 | 27 | 67 | 0 天 | 1 天 | `0.656716`（44/67） |

中位数为 0 天表示一半以上的 episode 起点在公开指定当天或之前一天内被冻结；`precision = day`，
所以"同日"只是分辨率下限，不声称先后顺序。

`exchange_outflows.csv`：`outflow_subset_events`（子集规模）、`outflow_event_share`（窗口内
有转出的事件占比）、`outflow_token_total`（窗口内转出金额合计，token 单位）。窗口定义：
`[freeze_time − window, freeze_time)`，只看 `direction = out`；窗口内无转出记为真实 0，窗口
未被快照覆盖或没有快照则 `not_reportable`，两者在表里可区分。

`frozen_balances.csv`：按 `subset_id`（两个子集分开）+ `issuer` 给 D1 合计与中位数。每行
`balance_reference = at_freeze`。

## 3. frozen balances 用哪一套余额口径

**只用 D1（`at_freeze`）**：子集事件的 `events.balance_pre_event_raw`，对应
`balances.balance_reference = at_freeze`。理由：受限子集的单位是"事件是否落在子集内"，而
D2 的单位是"截止日仍冻结的地址"，两者粒度不同，混用就是把口径差读成行为差。因此本子集
不报告 D2，`frozen_balances.csv` 的每一行都写死 `balance_reference = at_freeze`，
`report.json` 的 `balance_definition` 复述同一口径与不报告 D2 的理由。

## 4. outflow 窗口敏感性

参数只有一个：`outflow_window`，网格 `1h / 6h / 24h / 72h / 168h`，基线 `24h`。基线写在
`exchange_outflows.csv`，全部设定写进 `outflow_window_sensitivity.csv`，每行与基线对照给
`delta_vs_baseline`。

`conclusion_affected` 有三态：`yes`（状态改变或数值相对基线变化 ≥ 10%）、`no`、
**`not_assessable`**（该窗口下没有可测事件）。本次 20 行全部 `not_assessable`：子集为空，
所以"结论不随窗口变化"这句话不成立——没有测量，就谈不上依赖。报告 R5 明确写出这一点，
`--check` 拒绝把 `not_assessable` 与 `reported` 混在一行。

机制本身是有效的，不是空壳：规则自检里用合成事件 + 合成转账快照验证过
`window_sensitivity_reacts_to_window`（冻结前 2 小时的转出在 `6h` 窗口计入 `0.250000`、
在 `1h` 窗口不计入 `0.000000`，前者 `conclusion_affected = no`、后者 `yes`）。

## 5. 与核心结论的分离

| 维度 | 做法 | 复核方式 |
| --- | --- | --- |
| 目录 | 独立批次目录 `restrict_<id>/`，不写回核心集或比较批次 | `--check` 重算核心四表与 `matches` 摘要 |
| 表名 | 五个表名与工单 07 的 `comparison` / `differences` / `sensitivity` 不重名 | `--check` 断言无重名、批次目录里也不出现同名 CSV |
| 行标记 | 每行 `coverage_scope = restricted_subset` 且 `statement_type = descriptive` | `--check` 逐行断言 |
| 差值 | 本模块不产出 issuer 差值（"USDT − USDC"只在工单 07 的前置条件下产生） | `separation.cross_issuer_difference_produced = false` |
| 报告 | `report.json` 的 `separation` 段声明不拼接，`coverage_statement` 写明分母之外的 5,970 条事件不参与任何受限指标 | `--check` 比对 `report.json` 与 `manifest.separation` |

## 6. 复现与复核

```
python scripts/reconstruct_restricted.py
python scripts/reconstruct_restricted.py --check
python scripts/reconstruct_restricted.py --expect-data-digest <hex>
python scripts/reconstruct_restricted.py --outflow-window 72h
python scripts/reconstruct_restricted.py --transfers <path>
```

- 输入全部在本地（核心集 CSV、匹配层 CSV、可选转账快照）；不需要网络。
- `--outflow-window` 参与批次 id：换窗口写到另一个 `restrict_<id>/`，两次运行可并排对照。
- 转账快照格式：`{"label", "start_block", "end_block", "start_block_time_utc",
  "end_block_time_utc", "transfers": [{"address", "direction", "amount_raw",
  "block_time_utc", "block_number", "tx_hash", "log_index"}]}`。缺覆盖区间直接退出：没有
  区间就分不清"窗口内没流出"和"没查"。
- `--check` 不写盘：重算五张表逐行比对、重算报告与规则自检、核对筛选链算术与单调性、
  核对上游摘要未变、核对 `not_reportable` 行没有值、核对余额只用 D1。
- 退出码：0 通过；1 运行失败；2 数据摘要不符；4 `--check` 不一致。

已验证（本机，同一输入）：

| 运行 | 条件 | 结果 |
| --- | --- | --- |
| A | 连续两次运行 | 五张表与 `report.json` 逐字节相同，两个摘要不变，`generated_at_utc` 不被改写（只有 manifest 的 `last_verified_at_utc` 每次变化，与工单 06/07 同例） |
| B | `--check` | 退出码 0 |
| C | 手改 `timing_gaps.csv` 一个值后 `--check` | 退出码 4（与重建结果不一致），重跑后恢复并再次退出 0 |
| D | `--expect-data-digest` 给错值 | 退出码 2 |
| E | `--outflow-window 72h` | 独立批次 `restrict_e9ea2809…2624`，`data_digest 5f39855f…978e`，`--check` 退出码 0；窗格对照同样全部 `not_assessable`；验证后删除，只保留基线交付 |
| F | 注入证据探针（O1 之前：10 条 OFAC 匹配 episode 起点加制裁日期、4 条加带日期标签 + 合成转账） | 当年用于证明"有数据时这条路能算"：子集 0 → 10 / 4，`24h` 窗口占比 1.000000、`1h` 窗口 0.000000；O1 接入后交付批次已有 189 条真实 episode 起点，探针不再需要，只留记录 |
| G | 上游 `reconstruct_core.py --check`、`reconstruct_matches.py --check`、`reconstruct_comparison.py --check`、`validate_sample.py --check-scope` | 均退出 0（本次未改动上游产物与代码） |

F 是临时探针，只用于证明"有数据时这条路能算"，不留产物；O1 接入后交付批次已用真实日期出数。

## 7. 措辞边界

- 所有结论标为 `descriptive`；不推断冻结原因、私有执法请求的响应速度、规避行为或执法有效性。
- 私有执法请求时间不可观测（`not_observable`），无对应字段，不参与任何指标。
- 公开制裁时间差不是"响应速度"；快照发布日不是制裁日期。
- 冻结前转出只描述资金移动，不读成规避。
- 冻结余额只用 D1，且不得读成执法有效性。
- 受限子集的结论不外推到全部冻结活动，也不与核心指标拼接。

## 8. 已知限制

1. **timing gap 的日期是实体级**：来自官方 SDN Advanced XML 的 `EntryEvent`，同一实体的多个
   地址共享一个日期，不是地址级上榜日。因此负值（先冻结后指定）与 `>365` 天（实体早年指定、
   地址后来才上榜）必须单列，不进入中位数；中位数 0 天只说明"公开指定日与冻结日同日"，
   不说明执法请求的到达时间。
2. **exchange outflow 不回答（2026-11-18 决定）**：本数据集不给出「冻结前有多少资金到达
   交易所」。原因是标签源：交易所不公开充值地址，唯一能给出「充值地址 + 首次归集时间」的
   来源需要付费或试用；免费的热钱包名单只能覆盖「充值地址 → 热钱包」的最后一跳，中间跳
   断开；免费标签快照没有逐条日期，用在 2023 年前的事件上就是前视偏差。没有可信的对手方
   标签，任何「0 到交易所」都会把「不知道」写成「没有」。转账日志快照也未采集，这一条不再
   作为待办。
3. **子集规模对证据门槛敏感**：只看"OFAC 匹配"是 95 / 94 个 episode 起点，加上"有指定日"
   仍是 95 / 94（本批全覆盖）；0–365 天子集则为 56 / 67。任何引用这些数字的地方必须说明用
   的是哪一级。
4. **窗口覆盖是硬约束**：转账快照的区块时间区间必须覆盖整个窗口；不覆盖的事件被排除并
   计数（`window_not_covered_events`），不会当作 0。
5. **规则自检是一处内部断言**（13 条），与工单 06/07 同例：窗口过滤与"零 vs 缺失"在 outflow
   子集为空时不会被真实数据触发；timing 侧的日期规则已由 189 条实际 episode 起点覆盖。
   断言写在 `report.json` 的 `rule_checks`，`--check` 会重跑，验证边界仍是输出本身。
6. **人工签署按抽样完成**（26 行，`pbn`，2026-11-17，26/26 `match`），不覆盖本批受限表逐行。
7. 仓库仍不是 git 仓库（`git status` 报 not a repository），无法按流程提交分支。
