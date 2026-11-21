# 08: 受限子样本的 timing-gap 与 exchange-outflow 分析

**What to build:** 只在证据充分的子样本上给出 public sanctions timing gaps、address outflows 与 frozen balances 的描述性统计，并与核心分析明确分开、不互相拼接。

**Blocked by:** 06: 匹配层与缺失报告

**Status:** done

- [x] 子集由匹配证据与标签日期的充分性界定，界定规则可复核
- [x] timing-gap 与 exchange-outflow 分析仅使用该子集，不与核心结论拼接或互相补充
- [x] 对 outflow windows 做敏感性分析，报告结论对窗口选择的依赖
- [x] frozen balances 明确使用哪一套余额口径
- [x] 统计标为描述性；不外推、不推断私有请求响应、规避或执法有效性

## Comments

### 2026-11-14 交付

入口：`python scripts/reconstruct_restricted.py`（读工单 05 核心集与工单 06 匹配层，写独立目录），
离线复核 `python scripts/reconstruct_restricted.py --check`。

产物：`data/recon/restrict_8ecac83eb54bc07329c52170d22a35b1/`
`{subset_eligibility,timing_gaps,exchange_outflows,frozen_balances,outflow_window_sensitivity}.csv|.parquet`、
`report.json`、`manifest.json`。核心四表、`matches` 与工单 07 的三张比较表未被改动
（`reconstruct_core.py --check`、`reconstruct_matches.py --check`、`reconstruct_comparison.py --check`
均退出 0）。

代码：

- `scripts/recon/restricted.py`：两个子集的逐级筛选（`subset_eligibility`）、timing-gap 统计、
  转账窗口内 outflow、D1 冻结余额、窗口敏感性、描述性结论与措辞自检、离线复核。
- `scripts/reconstruct_restricted.py`：单一入口；`--check` / `--expect-data-digest` /
  `--outflow-window` / `--transfers` / `--core-id`。
- `docs/restricted-subset-analyses.md`：界定规则、表读法、余额口径、窗口敏感性、分离性、复现与限制。
- `docs/data-dictionary.md` §1：登记 `data/raw/transfers/` 原始层与五张派生视图（不新增字典表/字段）。

数字（`data_digest = 11b28a49…63c4`，`artifact_digest = 1222d9d0…810e`，取证 `2026-11-14T02:10:33Z`）：

| 指标 | 结果 |
| --- | --- |
| 表行 | 16 / 12 / 6 / 8 / 20（subset / timing / outflow / balance / sensitivity） |
| timing-gap 子集 | USDT `4786 → 3174 → 95 → 0`；USDC `1184 → 903 → 94 → 0`（core → episode 起点 → OFAC matched → 有制裁日期） |
| exchange-outflow 子集 | USDT `4786 → 3174 → 0`；USDC `1184 → 903 → 0`（标签层 `not_attempted`） |
| timing gaps | 全部 `not_reportable`（子集 0 条）；`gap_resolution_days = 1` |
| exchange outflows | 全部 `not_reportable`（子集 0 且无转账快照）；窗口覆盖计数 0 / 0 |
| frozen balances | 全部 `not_reportable`；每行 `balance_reference = at_freeze`（D1），子集不报告 D2 |
| 窗口敏感性 | 5 个窗口 × 2 issuer × 2 指标 = 20 行，`not_assessable` 20 行 |
| 规则自检 | 13 条全部 `ok` |

子集界定可复核：`subset_eligibility.csv` 逐级给 `rule`、`events_in`、`events_dropped`、
`events_remaining`、`dropped_reason_codes`（原因码取自字典枚举），`events_in − events_dropped =
events_remaining` 且逐级单调不增，`--check` 重算这两条不变量以及"末级剩余 = 报告里的子集规模"。

分离性不是自述：独立批次目录、五个表名与工单 07 的 `comparison/differences/sensitivity` 不重名
（`--check` 断言批次目录里不出现同名 CSV）、每行 `coverage_scope = restricted_subset` 与
`statement_type = descriptive`、本模块不产出 issuer 差值（`separation.cross_issuer_difference_produced
= false`）。

复现：连续两次运行五张表与 `report.json` 逐字节相同，`data_digest` 与 `generated_at_utc` 不变
（只有 manifest 的 `last_verified_at_utc` 每次变化）；手改 `timing_gaps.csv` 一个值后 `--check`
退出码 4；`--expect-data-digest` 给错值退出码 2；`--outflow-window 72h` 生成独立批次
`restrict_e9ea2809…2624`（`data_digest 5f39855f…978e`）并通过 `--check`，验证后删除。

偏差与限制：

1. **timing gap 没有可报告的行**：OFAC 只有单一快照，`sanctions_public_timestamp` 全空。95 / 94
   个 episode 起点匹配上清单，但只有清单发布日；按 `definitions.md` 4.2 与 R6，快照发布日只写
   `label_date`，不能顶替制裁日期。补入带 `sanctions_date_basis` 的历史记录后，本段代码无需改动。
2. **exchange outflow 有双重缺口**：标签层没有带日期来源（子集 0 条），且 `data/raw/transfers/`
   转账快照未采集。两者各自都会导致不可报告；补齐其一仍需补齐另一项才能出数。
3. **frozen balances 只用 D1**：D2 是"截止日仍冻结地址"的地址级口径，与"事件是否落在子集内"
   粒度不同；子集报告 D2 会把口径差读成行为差，因此不报告，并在 `report.json` 写明理由。
4. **窗口敏感性当前全部 `not_assessable`**：没有测量就谈不上"窗口依赖"。为避免这句话变成空话，
   规则自检用合成事件 + 合成转账快照验证窗口确实会改变数值（冻结前 2 小时的转出在 `6h` 计入
   `0.250000`、在 `1h` 为 `0.000000`，`conclusion_affected` 分别为 `no` / `yes`）。
5. **窗口覆盖是硬约束**：转账快照必须覆盖 `[freeze_time − window, freeze_time)`；不覆盖的事件被
   排除并计入 `window_not_covered_events`，不当作 0。快照缺覆盖区间直接退出，不静默降级。
6. **规则自检是一处内部断言**（13 条），与工单 06/07 的偏离同例：交付子集为空，日期规则、
   窗口过滤与"零 vs 缺失"在真实数据上不会被触发。断言写在 `report.json` 的 `rule_checks`，
   `--check` 会重跑，验证边界仍是输出本身。
7. **人工签署仍未完成**，与工单 04/05/06/07 的开放项一致。
8. 仓库仍不是 git 仓库（`git status` 报 not a repository），无法按流程提交分支。
