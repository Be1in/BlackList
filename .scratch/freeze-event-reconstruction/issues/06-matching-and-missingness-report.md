# 06: 匹配层与缺失报告

**What to build:** 把 OFAC 地址匹配与历史交易所标签作为独立的一层接入核心事件集，让"哪些能匹配上、哪些缺失、标签是哪一天的"成为可单独检查的报告，而不影响核心事件集本身。

**Blocked by:** 05: 核心事件集全量重建

**Status:** done

- [x] 匹配结果与核心事件表分离保存，带匹配证据与标签日期
- [x] 单独报告 public-record match rates、missing values、label dates
- [x] 历史交易所标签带日期，不用后来的标签解释过去的行为
- [x] 匹配失败与缺失均不被当作零值或成功匹配
- [x] 报告明确匹配覆盖可能只占事件的一小部分，结论不外推到全部冻结活动

## Comments

### 2026-11-14 交付

入口：`python scripts/reconstruct_matches.py`（读工单 05 的核心集，写独立目录；全程不需要网络），
离线复核 `python scripts/reconstruct_matches.py --check`。

产物：`data/recon/match_d1eef47976c369477ed62528e153b8c5/{matches,missingness}.csv|.parquet`、
`report.json`、`manifest.json`。核心集四表未被改动（`reconstruct_core.py --check` 仍退出 0）。

代码：

- `scripts/recon/matching.py`：两层判定（`ofac_sdn` 地址串精确比对、`exchange_label` 读带日期注册表）、
  日期规则、缺失统计、报告、规则自检与离线复核。核心集位置由输入摘要派生（`core.core_id_for`），
  不猜"最新目录"，因此不存在读错批次的可能。
- `scripts/reconstruct_matches.py`：单一入口，`--check` / `--expect-data-digest` / `--core-id` / `--labels`。
- `scripts/recon/core.py`：把批次 id 的推导提为 `core_id_for(...)`，`CoreRun.core_id` 复用同一函数
  （核心集 `core_id` 与摘要不变，`reconstruct_core.py --check` 通过）。
- `scripts/recon/tables.py`：`matches` 的 `matched_at_utc` 加入 `PROVENANCE_TIME_FIELDS`，使重放的数据摘要稳定。
- `config/exchange_labels.json`：标签注册表（每条须带 `label_date` + `label_date_basis`）；当前 0 来源、0 标签。
- `docs/matching-and-missingness.md`：报告正文（方法、比率、日期、缺失、边界）；`docs/data-dictionary.md`
  §1/§6 更新落盘位置与匹配层规则（未新增字段）。

数字（`data_digest = 63fc9525…e2d4`，取证 `2026-11-14T01:22:21Z`；`artifact_digest` 含 `matched_at_utc`，
逐次运行都会变，不作复核判据）：

| 指标 | 结果 |
| --- | --- |
| `matches` 行 | 11,940（5,970 事件 × 2 层） |
| public-record match rate（OFAC） | 事件口径 `0.032328`（193 / 5,970）；地址口径 `0.025047`（94 / 3,753） |
| 分代币（OFAC，事件口径） | USDT `0.020476`（98 / 4,786）、USDC `0.080236`（95 / 1,184） |
| 分事件类（OFAC，事件口径） | `freezing` `0.046158`（191 / 4,138）、`unfreezing` `0.001748`（1 / 572）、`destruction` `0.000794`（1 / 1,260） |
| 交易所标签层 | `not_attempted` 5,970 行、`external_source_unavailable`；比率 `null`（`not_reportable`），不是 0 |
| label dates | 匹配行 `label_date = 2026-11-09`（`list_snapshot_date`）；`sanctions_public_timestamp` 0 行 |
| 带日期子集 | `sanctions 0`、`exchange 0`（`analysis_subsets.reportable = false`） |
| missing values | 96 个字段中 23 个有可选空值；**必填字段缺口 0** |
| 匹配导致的剔除 | 0（`separation.match_based_exclusions = 0`） |

缺口的去向：未匹配 5,777 行记 `sanctions_record_missing`，未尝试 5,970 行记
`external_source_unavailable`，匹配但缺指定日期 193 行记 `label_date_missing`，全部留空不写 0。

规则自检（`report.json` 的 `rule_checks`，`--check` 重跑）：`dated_label_before_event`、
`label_after_event_not_used`、`undated_label_kept_as_r5`、`no_source_is_not_attempted`、
`ofac_unmatched_is_not_a_zero` 全部 `ok`。这些用合成 fixture 证明规则会触发，因为交付的注册表为空，
否则日期与前视规则只是散文。

复现：连续两次运行只有 `matched_at_utc` 变化（其余列与 `data_digest` 不变，`generated_at_utc` 保持首次
取证时刻）；手改一行 `match_status` 后 `--check` 退出码 4（摘要不符 + 与重建结果不一致 + 缺证据）；
`--expect-data-digest` 给错值退出码 2；跑完匹配后 `reconstruct_core.py --check` 与
`validate_sample.py --check-scope` 均仍退出 0。

评审后修正（两轴评审，Standards / Spec）：

- `--check` 原本把"非 `matched` 却带证据"一律判错，与"事后标签保留证据"冲突：改为只对
  `unmatched` / `not_attempted` 判错，前视行（`not_applicable` + 证据）单独要求证据完整。
- 比率分母为 0 时改走 `tables.share()`，返回 `null` 而不是 `0.000000`（含 `addresses` / `any_layer`），
  并补齐 `--check` 对分桶比率的同类不变量。
- `matched_at_utc` 改回字典定义的"匹配执行时刻"（原写 OFAC 快照抓取时刻），并在文档写明
  `artifact_digest` 因此逐次变化、复核判据是 `data_digest`。
- `matched` 行必须带 `match_source_snapshot_sha256`；标签来源缺 `snapshot_sha256` 或"有标签无来源"
  直接退出，不再静默降级成 `not_attempted`。
- 必填字段为空只在"没有原因码"时判错（R1/R2 允许空字段占位），与字典 §4 对齐。
- 匹配率新增 `matched_without_sanctions_date` / `matched_without_label_date` / `dated_by_event` 与
  `any_layer.composition`：把"匹配成功"与"能进带日期分析"分开，且合并口径不再掩盖标签层 0 覆盖。
- `separation.match_based_exclusions` 之外新增派生的 `eligibility_unchanged`（交付事件表资格分布与核心
  manifest 逐项相等），分离不再只靠一句自述。
- `parquet_written` 改用 `write_parquet` 返回值；核心四表读取时先 `assert_columns`；`manifest` 不再复制
  `report.json` 的数字；批次字段改名 `match_batch_id`（`match_id` 是表主键）。
- `ratio` / `read_table` 下沉到 `scripts/recon/tables.py`，`core.py`、`sample.py`、`matching.py` 共用一份。

偏差与限制：

1. **没有带日期的历史交易所标签来源**（`.env` 的 `EXCHANGE_LABELS_API_URL` 仍未确定）。标签层因此整层
   `not_attempted`，比率报 `not_reportable`：这是"没尝试"，没有伪装成"试过没有"。注册表格式与规则已就位，
   填入带日期标签即可生效，不需改代码。
2. **OFAC 只有单一快照，没有指定日期**（`SDN.csv` 只给 `Publish_Date = 2026-11-09`）。按字典，快照发布日
   只进 `label_date`（`list_snapshot_date`），`sanctions_public_timestamp` 留空并记 `label_date_missing`；
   因此**没有可用于 timing gap 的行**，工单 08 不能拿快照日期顶替。
3. **快照晚于全部事件**：匹配只说明"该地址在当前清单上"，不构成"冻结时已被指定"的证据。报告
   `caveats.snapshot_postdates_events` 明确写出，避免用后来的清单解释过去的行为。
4. **匹配覆盖很小**（3.23% 事件 / 2.50% 地址）：`coverage_statement` 与 `caveats.coverage_not_extrapolable`
   写明不得外推到全部冻结活动；`unmatched` 不等于未受制裁。
5. **样本原因码对齐**：`data/samples/sample_matches.csv` 的匹配行由 `not_collected` 改为
   `label_date_missing`（前者不在 `matches.missing_reason_code` 的字典枚举内），只改原因码，证据与数值不变。
   `docs/validation-table.md` §6 已记录。
6. **本工单不产出 timing gap / outflow**：只登记受限子集规模（当前都为 0），分析与措辞约束属工单 07/08。
7. **规则自检是一处新增的内部断言**：spec 写"不新增内部测试接口"，但交付的标签注册表为空，日期与前视
   规则在真实数据上不会被触发。折中做法是把 5 条断言写在报告里、用与交付同一套函数跑合成 fixture，
   验证边界仍是匹配输出本身（`--check` 会重跑）。这是本工单对 spec 的唯一偏离，登记在此以便评审取舍。
8. 仓库仍不是 git 仓库（`git status` 报 not a repository），无法按流程提交分支。
