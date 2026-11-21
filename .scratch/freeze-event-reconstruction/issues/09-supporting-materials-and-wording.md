# 09: 支撑材料打包与措辞审查

**What to build:** 把四项支撑材料整理成评审可独立复跑、可逐项核对的一套交付物，并标明哪些字段一致可得、哪些指标只能对受支持子集报告。

**Blocked by:** 07: 同口径 issuer 描述性比较与敏感性分析, 08: 受限子样本的 timing-gap 与 exchange-outflow 分析

**Status:** done

- [x] 小数据样本、数据字典、可复现采集脚本、验证表四项齐备，且不需全量重跑即可检查字段与口径
- [x] 明确区分「一致可得字段」与「仅受支持子集可报的指标」
- [x] 逐条核对描述性措辞约束：public sanctions timing gaps / address outflows / frozen balances 不作因果推断
- [x] 记录链范围待确认项及其对采集范围的影响，不把 Ethereum 写成反馈对全部链的显式限定
- [x] 描述性统计与因果性判断在材料中可一眼区分

## Comments

### 2026-11-14 交付

入口：`python scripts/reconstruct_materials.py`（读工单 05 核心集、06 匹配层、07 比较、08 受限子样本，
只读不写回），离线复核 `python scripts/reconstruct_materials.py --check`。

产物：`data/recon/materials_ee94e7bd9495ab5e709b347146825f7d/`
`{materials_index,field_availability,metric_coverage,wording_checks,chain_scope}.csv|.parquet`、
`report.json`、`manifest.json`。四个上游批次的摘要未被改动（`validate_sample.py --check-scope`、
`reconstruct_core/matches/comparison/restricted.py --check` 均退出 0）。

代码：

- `scripts/recon/materials.py`：材料索引（32 组件）、字段可得性（116 字段）、指标覆盖范围（106 行）、
  措辞核对（8 约束 × 材料 = 32 行）、链范围（2 链）、描述性与因果性分档、规则自检、离线复核。
- `scripts/reconstruct_materials.py`：单一入口；`--check` / `--expect-data-digest` /
  `--match-id` / `--compare-id` / `--restrict-id`。上游批次按输入摘要派生，多候选时拒绝猜"最新目录"。
- `docs/supporting-materials.md`：四项材料索引、A/B/C 分层、措辞逐条核对、链范围、描述性与
  因果性怎么区分、复现与限制。
- `docs/data-dictionary.md` §1：登记五张派生分析视图（不新增字典表/字段）。
- `docs/reproduction.md` §1.7 + §2 运行命令；`docs/study-scope.md` §1 补链范围边界（不改决策文件）。

数字（`data_digest = e7d6431f…ad6a`，`artifact_digest = 99bba2d5…f1e0`，取证 `2026-11-14T02:27:42Z`）：

| 指标 | 结果 |
| --- | --- |
| 组件 | 32（样本 4、字典 2、脚本 10、验证表 8、分析视图 8）；四项材料全部存在，每项带离线核对命令 |
| 字段可得性 | A 81（events 56、balances 19、chain_registry 6）、B 35（matches 21、validation 14）、C 0 个字段 |
| 指标覆盖范围（106 行） | `all_events` 43、`core_subpopulation` 12、`supported_subset` 49、`definitional_zero` 2 |
| 仅受支持子集可报的指标 | 12 个（`timing_gap_*` 6、`outflow_*` 3、`frozen_balance_at_freeze_*` 2、`exchange_label_dated_coverage` 1） |
| 措辞核对 | 8 条约束 32 行，未通过 0；结论 17 条全 `descriptive`，106 行指标 `causal_inference_permitted = no` |
| 链范围 | `ethereum=verified_sample`（5,970 事件）、`tron=registered_pending`（0，待确认项 `chain_scope_pending`） |
| 样本比对 | 事件 6/6、余额 11/11、匹配 12/12 命中交付表，取值字段 0 处不同；未比对字段在 `note` 逐项列出 |

复现：连续两次运行五张表与 `report.json` 逐字节相同，两个摘要与 `generated_at_utc` 不变；
手改 `wording_checks.csv` 一个值后 `--check` 退出码 4；删除 `docs/supporting-materials.md` 里
一条约束原文后 `--check` 报 `W3 / package_doc` 未通过并退出 4；`--expect-data-digest` 给错值
退出码 2；临时移走 `data/samples/sample_matches.csv` 后运行以退出码 1 失败（提示材料不齐）。

偏差与限制：

1. **措辞只做到"结构化 + 声明"**：脚本核对 `statement_type`、`coverage_scope`、caveats 键、列取值与
   文档锚点，不做语义判断；人工签署仍未完成（`wording.manual_signoff = pending`），与工单 04–08 一致。
2. **样本与交付表按取值字段比对**：`abi_source`、`source_query`、`notes`、`match_id`、`matched_at_utc`、
   `match_source_name`、`match_source_snapshot_sha256`、`match_evidence_text` 由不同入口在不同时刻写入，
   比对它们只会暴露时间差；`materials_index.csv` 的 `note` 逐样本记录排除了哪些字段。
3. **`sample_validation.csv` 只核对列与枚举**：参照文本是工单 01 的独立端点记录，格式与核心批次不同，
   逐值比对会得到假偏差。
4. **B 层指标当前没有数值**：timing gap 与 exchange outflow 子集为空，`frozen_balances` 与窗口
   敏感性同样不可报告/不可评估。本包只保证"有数据时同一段代码能出数、且必带 B 层标记"，
   不主张已测到行为差异。
5. **规则自检是一处内部断言**（5 条：样本漂移、快照日期顶替、因果结论、B 层标注、缺失材料），
   与工单 06/07/08 的偏离同例：交付数据全部通过，因此需要合成 fixture 证明这些检查会失败。
6. 仓库仍不是 git 仓库（`git status` 报 not a repository），无法按流程提交分支。

### 2026-11-17 重跑

上游四个批次更新后重跑：`materials_ee94e7bd9495ab5e709b347146825f7d`，32 个组件不变，
`data_digest = a564d2dd…77f8`。A/B/C 分层、106 指标覆盖与 32 条措辞核对结果不变。
