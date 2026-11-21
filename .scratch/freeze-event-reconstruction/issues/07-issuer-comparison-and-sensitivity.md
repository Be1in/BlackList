# 07: 同口径 issuer 描述性比较与敏感性分析

**What to build:** 从原始日志到一张 issuer 比较结论表可复跑，且任何看起来的 USDT / USDC 差异都能排除观察期、事件定义与缺失处理不同所造成的影响。

**Blocked by:** 05: 核心事件集全量重建, 06: 匹配层与缺失报告

**Status:** done

- [x] 比较使用同一观察期、同一事件定义、同一缺失处理，否则不产出比较结论
- [x] 对 timestamp precision 与 missing labels 做参数化敏感性分析，结果可对照
- [x] 每个指标标注其数据覆盖范围（全量 / 受限子集）
- [x] 结论一律标为描述性，不推断私有请求响应速度、规避或执法有效性
- [x] 从原始输入复跑能得到相同结论表

## Comments

### 2026-11-14 交付

入口：`python scripts/reconstruct_comparison.py`（读工单 05 核心集与工单 06 匹配层，写独立目录），
离线复核 `python scripts/reconstruct_comparison.py --check`。

产物：`data/recon/compare_c1f94b30f0fcacdb30368c1427c695d1/{comparison,differences,sensitivity}.csv|.parquet`、
`report.json`、`manifest.json`。核心四表与 `matches` 未被改动。

代码：

- `scripts/recon/comparison.py`：五项前置条件（同一观察期、事件定义、缺失处理、余额定义、匹配层）、
  19 个指标的 issuer 长表、差值表、两个参数的一次一改敏感性、描述性结论与措辞自检、离线复核。
- `scripts/reconstruct_comparison.py`：单一入口；`--check` / `--expect-data-digest` /
  `--timestamp-precision` / `--missing-labels` / `--core-id`。
- `scripts/recon/core.py`：新增 `locate_core_bundle`（由输入派生核心批次，不猜最新目录）；
  `matching.MatchingRun.core_dir` 改为复用同一函数，输出不变（`reconstruct_matches.py --check` 仍退出 0）。
- `docs/issuer-comparison.md`：前置条件、结论表、敏感性、覆盖范围、复现与限制。
- `docs/data-dictionary.md` §1：登记三张派生分析视图（不新增字典表/字段）。

数字（`data_digest = 0cd7f543…7d8f`，`artifact_digest = 3e6c783c…9e22`，取证 `2026-11-14T02:03:30Z`）：

| 指标 | 结果 |
| --- | --- |
| 前置条件 | 5 / 5 通过；过滤丢弃 USDT 0、USDC 0；D1 4,077 行 = episode 起点；D2 全部 `blacklisted`；余额来源单一 |
| 比较 / 差值 / 敏感性行 | 38（19 指标 × 2）/ 19（17 可比、2 不可比）/ 26 |
| 覆盖范围（38 行） | `core_full` 27（13 指标 + USDT destruction）、`core_closed_episodes` 8、`restricted_subset` 2、`structural_zero` 1 |
| 事件 | USDT 4,786（freezing 3,181 / unfreezing 345 / destruction 1,260）；USDC 1,184（957 / 227 / 0 结构性零） |
| episode | 起点 USDT 3,174、USDC 903；已闭合 341 / 197，截止日仍开放 2,833 / 706 |
| 时长（second，天） | 中位数 385.847361 / 305.724028；P90 575.820278 / 956.376007 |
| D1 / D2 合计（token） | 3,325,815,917.704031 / 2,352,480,169.220909（USDT）；988,767,955.740959 / 961,694,776.408311（USDC） |
| OFAC 匹配率 | 0.020476（98 / 4,786）/ 0.080236（95 / 1,184） |
| 交易所标签 | `not_reportable`（0 个来源、0 条带日期标签） |
| 敏感性 | `second → day` 只改时长小数（差值中位数 80.123333 → 81.000000）；`drop → retain` 只改标签分母（0 → 全部事件）；`conclusion_affected` 2 行（两个 issuer 的最短时长在 day 口径下退化为 0 天） |

复现：连续两次运行三张表与 `report.json` 逐字节相同，摘要与 `generated_at_utc` 不变；
手改一个时长值后 `--check` 退出码 4；`--expect-data-digest` 给错值退出码 2；
`--timestamp-precision day` 与 `--missing-labels retain_unlabelled` 各自生成独立批次并通过 `--check`
（验证后删除，参数对照保留在 `sensitivity.csv`）；窗口外事件、单 issuer 专有排除规则、两种余额来源
三个反例探针均返回 `passed = false`，运行会在写盘前失败。

偏差与限制：

1. **时长删失**：2,833 / 706 个 episode 截止日仍开放，不参与中位数/P90；时长只描述已解冻部分，
   不能读成"冻结通常持续多久"。
2. **事件数 ≠ 地址数**：R9 重复日志计入覆盖但不产生 episode 起点；USDT 的 3,179 个事件涉及地址中
   有 8 个没有观察期内的 episode 起点（5 个只在销毁行、3 个只有解冻/销毁），因此地址数 3,179
   与 episode 起点 3,174（分布在 3,171 个地址上）不是同一口径。
3. **没有带日期的交易所标签来源**：标签指标 `not_reportable`，missing-label 敏感性只能证明
   "参数只改分母、不改结论"。
4. **OFAC 只有单一快照且晚于全部事件**：匹配率是公共记录覆盖差异，不是行为差异，也不能外推。
5. **人工签署仍未完成**，与工单 04/05/06 的开放项一致。
6. **规则自检是一处内部断言**（4 条：day 精度取整、结构性零不比较、缺失标签不写 0、
   missing-label 敏感性随分母改变），与工单 06 的偏离同例：交付的标签注册表为空、USDC 没有
   destruction，这些分支在真实数据上不会触发。断言写在 `report.json` 的 `rule_checks`，
   `--check` 会重跑。
7. 仓库仍不是 git 仓库（`git status` 报 not a repository），无法按流程提交分支。
