# 同口径 issuer 描述性比较与敏感性分析（工单 07）

入口：`python scripts/reconstruct_comparison.py`（读工单 05 的核心集与工单 06 的匹配层，写独立目录）。
离线复核：`python scripts/reconstruct_comparison.py --check`。

本文件只回答两件事：在什么条件下才允许产出 USDT / USDC 比较结论；以及这些结论随
`timestamp precision` 与 `missing labels` 参数如何变化。字段定义仍以
`docs/data-dictionary.md` v1 为准；本工单**不新增字典表**，三张分析表是派生视图。

## 0. 本次交付

| 项 | 值 |
| --- | --- |
| 批次 | `compare_9f973ff43a20c53a9fdf9bfac13675be`（`data/recon/compare_9f973ff43a20c53a9fdf9bfac13675be/`） |
| 取证时刻 | `2026-11-17T12:48:05Z`（重放同一输入不改写） |
| 输入核心集 | `core_64c8c7f9452291663926113a2b1ecc20`（`data_digest = 49db21c8…2fd5`），5,970 事件 / 9,466 余额行 |
| 输入匹配层 | `match_e7a32e2a4120dccb5d5cfe1edf360766`（`data_digest = 2d8cbf6b…c3b6`），11,940 行 |
| 比较表 | `comparison.csv`：19 个指标 × 2 个 issuer = 38 行 |
| 差值表 | `differences.csv`：19 行（17 行可比、2 行 `not_comparable`） |
| 敏感性表 | `sensitivity.csv`：26 行（两个参数、各自基线与对照） |
| 摘要 | `data_digest = 4768622b…4ec8`；`artifact_digest = 61b93348…0804` |

三张表列集合由 `scripts/recon/comparison.py` 的 `*_FIELDS` 声明，写出前断言；它们不进
`docs/data-dictionary.csv`，也不进入任何采集表。

## 1. 前置条件：不满足就不产出比较结论

运行开始时先核验五条，任何一条不通过就以退出码 3 结束且**不写任何比较产物**。
`--check` 会用同一套函数重新核验。

| 前置条件 | 核验内容 | 本次证据 |
| --- | --- | --- |
| `same_observation_window` | 每个事件的 `block_number` 在决策窗口内；核心 manifest 声明同一窗口；链与代币范围一致 | 区块 `10,274,701–26,404,630`；窗口外事件 0；USDT 4,786、USDC 1,184；链 `ethereum` |
| `same_event_definition` | `event_class` 来自同一声明枚举；观察到的类别并集等于声明集合；字典/契约版本与核心集一致（批次 id 由当前输入重算） | 三类事件齐全；`dataset_contract_version = v1` |
| `same_missingness_treatment` | 两个 issuer 用同一资格表达式 `analysis_eligibility == core`；过滤不丢行；排除规则集相同；指标用到的余额全部 `complete`；匹配层声明 0 条按匹配剔除 | 过滤丢弃 USDT 0、USDC 0；排除规则集都为空；余额全部 `complete`；`match_based_exclusions = 0` |
| `same_balance_definition` | D1 行恰好对应 episode 起点；D2 行全部是截止日仍 `blacklisted` 的地址；两个 issuer 共用同一余额来源 | D1 4,077 行 = episode 起点 4,077；D2 3,573 行全部 `blacklisted`；来源均为 `archive_eth_call` |
| `same_match_layer` | 匹配批次由同一核心集生成（id 与数据摘要一致）；每个事件两层各一行 | `core_id` 与摘要一致；11,940 行 / 5,970 事件 × 2 层 |

观察期外 90 条 USDT 事件在工单 05 就已逐条登记排除，不进入本比较；它们不能作为
"USDT 早期历史"并入任何 issuer 比较（`docs/study-scope.md` §3）。

## 2. 比较表怎么读

`comparison.csv` 是长表：一行 = 一个指标 × 一个 issuer。关键列：

| 列 | 取值 | 含义 |
| --- | --- | --- |
| `coverage_scope` | `core_full` / `core_closed_episodes` / `restricted_subset` / `structural_zero` | 该指标的数据覆盖范围；不同范围的数值不得拼接 |
| `status` | `reported` / `not_reportable` / `structural_zero` | `not_reportable` 不是 0，`structural_zero` 是定义性零 |
| `comparable` | `yes` / `no` | 是否允许与另一 issuer 作差 |
| `balance_reference` | `at_freeze` / `at_study_cutoff` / 空 | 余额指标声明使用的是 D1 还是 D2 |
| `statement_type` | 固定 `descriptive` | 所有结论只作描述，不作因果推断 |

`differences.csv` 对每个指标给出 `USDT − USDC`。任一 issuer 不可报告或结构性零时，
`difference_status = not_comparable` 且差值为空，并写明原因——不把定义性/缺失差异算成
行为差异。每行同时带三个前置条件标记，便于复核"差值不是处理差异"。

## 3. 结论表（本次交付）

| 指标 | USDT | USDC | 差值 USDT−USDC | 覆盖范围 |
| --- | --- | --- | --- | --- |
| 事件总数 | 4,786 | 1,184 | 3,602 | `core_full` |
| `freezing` 事件 | 3,181 | 957 | 2,224 | `core_full` |
| `unfreezing` 事件 | 345 | 227 | 118 | `core_full` |
| `destruction` 事件 | 1,260 | 0（结构性零） | 不可比 | `structural_zero` |
| episode 起点 | 3,174 | 903 | 2,271 | `core_full` |
| 状态未改变的 `freezing`（R9） | 7 | 54 | −47 | `core_full` |
| 事件涉及地址（去重） | 3,179 | 903 | 2,276 | `core_full` |
| 截止日仍冻结地址 | 2,867 | 706 | 2,161 | `core_full` |
| 已闭合 episode | 341 | 197 | 144 | `core_closed_episodes` |
| 截止日仍开放 episode | 2,833 | 706 | 2,127 | `core_full` |
| episode 时长中位数（天） | 385.847361 | 305.724028 | 80.123333 | `core_closed_episodes` |
| episode 时长 P90（天） | 575.820278 | 956.376007 | −380.555729 | `core_closed_episodes` |
| episode 时长最小值（天） | 0.014444 | 0.001806 | 0.012638 | `core_closed_episodes` |
| D1 冻结时余额合计（token） | 3,325,815,917.704031 | 988,767,955.740959 | 2,337,047,961.963072 | `core_full` |
| D1 冻结时余额中位数（token） | 35,380.000000 | 0.000000 | 35,380.000000 | `core_full` |
| D2 截止日仍冻结余额合计（token） | 2,352,542,587.677342 | 961,694,776.408311 | 1,390,847,811.269031 | `core_full` |
| D2 截止日仍冻结余额中位数（token） | 0.008100 | 0.000000 | 0.008100 | `core_full` |
| OFAC 事件口径匹配率 | 0.020476（98/4,786） | 0.080236（95/1,184） | −0.059760 | `core_full` |
| 带日期交易所标签覆盖率 | `not_reportable` | `not_reportable` | 不可比 | `restricted_subset` |

读法约束：

- **`destruction` 不可比**：USDC 在 Ethereum 上没有 issuer 发起的销毁入口，0 是结构性零，
  不是"USDC 销毁更少"。
- **事件数不等于地址数**：重复 `freezing` 日志（R9）计入事件覆盖但不产生 episode 起点、
  不产生 D1、不计入冻结地址。`unique_addresses` 是"事件涉及地址"，不是"被冻结地址"。
- **两套余额口径分开**：D1 与 D2 不相加、不互换；D1 合计只来自 episode 起点事件，
  D2 合计只来自截止日仍冻结地址。
- **匹配率是公共记录覆盖**：OFAC 快照晚于全部事件，`unmatched` 不等于未受制裁；
  USDT 与 USDC 的匹配率差异不能读成冻结行为差异。
- **标签层不可报告**：注册表 0 个来源、0 条带日期标签；任何依赖交易所标签的比较不产出数值。

## 4. 敏感性分析

设计：一次只改一个参数（one-at-a-time），其余保持基线
（`timestamp_precision = second`、`missing_labels = drop_undated`）。每行与基线对照，
`delta_vs_baseline` 给数值差；`conclusion_affected` 在状态变化、issuer 间差值变号，
或数值相对基线变化 ≥ 10%（基线为 0 时非零即 `yes`）时记 `yes`。本次 26 行中 2 行为
`yes`：day 口径下两个 issuer 的最短时长从秒级退化为 0 天（相对变化 100%），其余为 `no`。
最短时长不进入任何结论，这两行只是把精度下限显式标出来。

### 4.1 `timestamp_precision`（second vs day）

| 指标 | USDT second | USDT day | USDC second | USDC day |
| --- | --- | --- | --- | --- |
| 时长中位数（天） | 385.847361 | 386.000000 | 305.724028 | 305.000000 |
| 时长 P90（天） | 575.820278 | 576.000000 | 956.376007 | 957.000000 |
| 时长最小值（天） | 0.014444 | 0.000000 | 0.001806 | 0.000000 |
| 事件总数 | 4,786 | 4,786 | 1,184 | 1,184 |

- 差值中位数：`80.123333`（second）→ `81.000000`（day），方向与量级不变。
- 计数差值为 0：事件总数不依赖时间戳精度。
- 最小值在 day 口径下必然是整日 0；这只说明精度下限，不构成"最短冻结时长"的结论。

### 4.2 `missing_labels`（drop vs retain）

| 设定 | USDT 分子/分母 | USDC 分子/分母 | 状态 | 事件总数差值 |
| --- | --- | --- | --- | --- |
| `drop_undated`（基线） | 0 / 0 | 0 / 0 | `not_reportable` | 0 |
| `retain_unlabelled` | 0 / 4,786 | 0 / 1,184 | `not_reportable` | 0 |

参数只改变分母（0 → 全部核心事件），不改变"标签层不可报告"这一结论，也不改变核心事件
计数。未标注事件没有被读成 0 或"不属于交易所"。

## 5. 指标覆盖范围

| 覆盖范围 | 指标 | 说明 |
| --- | --- | --- |
| `core_full` | 13 个（事件计数、episode 起点、地址、D1/D2、OFAC 匹配率）+ USDT `destruction`（27/38 行） | 观察期内全部可检索事件；不依赖匹配成功 |
| `core_closed_episodes` | 4 个（已闭合 episode 数、时长中位数 / P90 / 最小值，8 行） | 只用已闭合 episode；截止日仍开放的不参与 |
| `restricted_subset` | 1 个（带日期交易所标签覆盖率，2 行） | 需要匹配证据与完整标签日期；当前不可报告 |
| `structural_zero` | 1 个（USDC `destruction`，1 行） | 合约定义上不存在该类事件 |

报告不把任何子集或结构性零的数值并入 `core_full` 指标，也不在它们之间做加减。差值行的
覆盖范围取两侧中更受限的一侧，因此 `destruction` 的差值行是 `structural_zero` 而不是
`core_full`。

## 6. 复现与复核

```
python scripts/reconstruct_comparison.py
python scripts/reconstruct_comparison.py --check
python scripts/reconstruct_comparison.py --expect-data-digest <hex>
python scripts/reconstruct_comparison.py --timestamp-precision day
python scripts/reconstruct_comparison.py --missing-labels retain_unlabelled
```

- 输入全部在本地（核心集 CSV + 匹配层 CSV）；不需要网络。
- `parameters` 参与批次 id：换参数会写到另一个 `compare_<id>/`，两次运行可并排对照；
  本次交付保留基线批次，`sensitivity.csv` 已含两组参数的对照。
- `data_digest` 覆盖三张分析表；`report.json` 只进 `artifact_digest`。三张表不含抓取时刻，
  同一输入重放逐字节相同，`generated_at_utc` 保持首次取证时刻。
- `--check` 不写盘：重算五项前置条件、从核心集与匹配层重建三张表逐行比对、重算报告与
  规则自检，并核对核心四表与 `matches` 的摘要未被改动。
- 退出码：0 通过；1 运行失败；2 数据摘要不符；3 比较前置条件未满足（不写产物）；
  4 `--check` 不一致。

已验证（本机，同一输入）：

| 运行 | 条件 | 结果 |
| --- | --- | --- |
| A | 连续两次运行 | 三张表与 `report.json` 逐字节相同，两个摘要不变，`generated_at_utc` 不被改写 |
| B | `--check` | 退出码 0 |
| C | 手改 `comparison.csv` 一个时长值后 `--check` | 退出码 4（与重建结果不一致） |
| D | `--expect-data-digest` 给错值 | 退出码 2 |
| E | `--timestamp-precision day` | 独立批次 `compare_16ee7159…07c6`，`data_digest 57c2f9e0…1466`，`--check` 退出码 0；结论集合与基线相同 |
| F | `--missing-labels retain_unlabelled` | 独立批次 `compare_2bc61bbe…d574`，`data_digest a712f2d0…1ba6`，`--check` 退出码 0；标签指标仍 `not_reportable` |
| G | 前置条件反例（窗口外事件、单 issuer 专有排除规则、两种余额来源） | 三个探针均返回 `passed = false`，运行会在写盘前失败 |
| H | 上游 `reconstruct_matches.py --check`、`reconstruct_core.py --check`、`validate_sample.py --check-scope` | 均退出 0（本次重构未改动上游产物） |

E/F 的批次在验证后删除，只保留基线交付；它们的所有参数对照都已在
`sensitivity.csv` 中存在。

## 7. 措辞边界

- 所有结论标为 `descriptive`；不推断冻结原因、私有执法请求的响应速度、规避行为或执法有效性。
- 私有执法请求时间不可观测（`not_observable`），无对应字段，不参与任何指标。
- 有限匹配子集（当前 `restricted_subset` 不可报告，0 个带日期标签）不得外推到全部冻结活动。
- 时长只描述已闭合 episode；截止日仍开放的 episode 是删失数据，不能读成"冻结通常持续多久"。
- 时间戳精度为区块头秒级，不建模出块者时钟偏差；day 口径只看整日。

## 8. 已知限制

1. **时长删失**：USDT 2,833 / USDC 706 个 episode 在截止日仍开放，不参与中位数与 P90。
   因此 `385.85` / `305.72` 天只是"已解冻部分"的描述，不是全体冻结的典型时长。
2. **事件数与地址数不同口径**：R9 重复日志计入事件覆盖但不计入 episode 起点；USDT 的
   3,179 个事件涉及地址中有 8 个没有观察期内的 episode 起点（5 个只出现在销毁行、3 个只有
   解冻/销毁行），因此地址数（3,179）与 episode 起点数（3,174，分布在 3,171 个地址上）
   不是同一口径。
3. **匹配覆盖小**：OFAC 事件口径 3.23%（USDT 2.05%、USDC 8.02%），快照晚于全部事件；
   匹配率差异是公共记录覆盖差异，不是冻结行为差异。
4. **交易所标签层缺失**：没有带日期的历史标签来源，标签指标不可报告；填入带日期标签后
   同一段代码即可产出，但本次没有可用数值。
5. **时间精度**：事件时间来自区块头（秒级），链上时间与墙钟的偏差不建模；`day` 口径
   把时长截断到整日，最小值因此为 0。
6. **人工签署按抽样完成**：比较表由脚本从固定输入生成；26 行分层抽样已由 `pbn` 于
   2026-11-17 签署（26/26 `match`），覆盖验证行而非比较表逐行签字。
7. **D2 总体大于事件地址集**：29 个观察期前冻结、观察期内无事件的 USDT 地址只进入 D2
   余额行，不进入事件表；因此 D2 地址数（2,867）不能与事件涉及地址数（3,179）相减或对齐。
8. 仓库仍不是 git 仓库（`git status` 报 not a repository），无法按流程提交分支。
