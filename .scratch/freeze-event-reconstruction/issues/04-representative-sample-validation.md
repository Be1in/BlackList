# 04: 代表性样本验证与研究期/样本范围决策

**What to build:** 用一个小型代表性样本跑通整条链路，让"这份事件数据有多可靠"有可量化的答案，并据此（而非事先假定）写下研究期与样本范围。

**Blocked by:** 03: 生命周期事件闭环

**Status:** done

- [x] 样本覆盖 USDT 与 USDC，以及 freezes、unfreezes、destruction 和多事件交易（多事件交易在本范围结构性不可得：0 / 6,060 笔，以多日志交易与同一地址的跨交易序列替代验证，并在验证表登记该结构性结论）
- [x] 对照人工核对样本与可查询历史状态，产出验证表
- [x] 报告 event retrieval coverage、balance reconstruction error、unverifiable share
- [x] 研究期与样本范围决策写明由哪些验证结果支撑
- [x] 执行顺序可核验：验证在前、范围决策在后

## Comments

### 2026-11-13 交付

入口：

- `python scripts/validate_sample.py`：跑 5 个案例、6 个样本槽位的整条链路，产出样本交付与指标。
- `python scripts/validate_sample.py --check-scope`：不访问网络，重算交付摘要并核对 `config/study_scope.json`。

产物：`data/recon/vs_3b3a9d4db71b1186ad8204411a891f41/{events,balances,validation}.csv|.parquet` + `manifest.json`（`metrics`、`scope_inputs`、摘要、取证时刻）。

代码：

- `scripts/recon/sample.py`：样本层只做合并、追加核对与指标汇总，不重解日志——事件与余额来自 `CaseRun`（工单 03），核对边界仍是重建输出。
- `scripts/validate_sample.py`：单一入口；`--check-scope` 只读产物。
- `scripts/recon/lifecycle.py`：`validation_row` / `flatten` / `merge_bundles` 提升为包内共享（行为不变），供样本层复用。
- `config/lifecycle_cases.json`：新增 `unfreeze_usdc`（USDC 解冻、截止日不再冻结、无 D2；17 条核对全 `match`，`data_digest = 018d6127…825a`），补齐 USDC × `unfreezing` 覆盖。
- `config/study_scope.json`：研究期与样本范围决策（机器可读）。

指标（批次 `vs_3b3a9d4db71b1186ad8204411a891f41`，取证 `2026-11-13T10:26:12Z`，`data_digest = c69ad3b4…5f63`）：

| 指标 | 结果 |
| --- | --- |
| `event retrieval coverage` | 13/13 日志 = `1.000000`（USDT 10、USDC 3；6/6 槽位，USDT 3/3、USDC 3/3）；区块级同 topic 日志集合一致 6/6 |
| `balance reconstruction error` | 15/15 完全一致，最大绝对误差 `0` raw / `0.000000` token（D1 6、`at_event` 3、D2 5、销毁金额 1） |
| `unverifiable share` | `0.000000`（0 / 161）；`source_position` 34、`order_version` 18、`balance_state` 61、`manual_reference` 38、`schema_conformance` 10 |

样本规模：10 条事件（`freezing` 7、`unfreezing` 2、`destruction` 1）、14 条余额行（`at_freeze` 6、`at_event` 3、`at_study_cutoff` 5）、161 条核对全 `match`。覆盖 `TetherToken`、`FiatTokenV1`、`FiatTokenV2_2` 三个版本。

新增核对项（`field_name` 均为字典内字段）：`log_index`（同区块独立端点日志中可定位）、`block_number`（同 topic 日志条数一致；截止区块不高于 finalized 头）、`balance_reference`（截止日黑名单状态决定 D2 归属）、`balance_raw`（D2 独立重算）、`balance_block_hash`（截止区块 canonical）、`destruction_amount_raw`（销毁金额 = 销毁前余额）。

研究期与样本范围决策（`config/study_scope.json`，说明 `docs/study-scope.md`）：

- `observation_start_block = 10,274,701`：USDC 首个黑名单事件（S6），之前快照 USDT 90 条、USDC 0 条；并入这段会让 issuer 比较把"USDC 尚无事件"读成"USDC 冻结更少"。
- `study_cutoff_block = 26,404,630`：日志快照 `end_block`；canonical 区块头 `0x17925a9e…c0b50` 由独立端点复核，且不高于验证时 finalized 头 `26,405,361`。
- 样本范围：Ethereum、USDT/USDC、三类事件；核心路径覆盖观察期内全部可检索事件（匹配失败不剔除），受限路径只用于 timing gap 与 exchange outflow；多事件交易标注结构性不可得。

执行顺序可核验：验证产物 `generated_at_utc = 2026-11-13T10:26:12Z` 早于决策 `decided_at_utc = 2026-11-13T10:27:00Z`；`--check-scope` 从交付 CSV 重算数据摘要，比对决策引用的批次/摘要/5 项指标、观察期边界、finalized 头、观察期前事件数与 `supported_by` 证据路径。实测反例（把决策时刻改早、起点改到 USDT 首个事件、覆盖率改成 `0.500000`）同时报出四条失败并以退出码 4 结束。

复现：连续两次运行摘要相同；仅把 `ETHEREUM_ARCHIVE_RPC_URL` 指向不可达地址重放，`data_digest` 与 `artifact_digest` 均不变；`generated_at_utc` 在重放时保持首次取证时刻，避免复跑破坏"验证在前"的证明。`--expect-data-digest c69ad3b4699d166ee6261d731f931f7d05b6221ed7e948efcf93f6b07dd15f63` 可直接复核。

偏差与限制：

1. **人工签署仍未完成**：`reviewer` 仍为 `agent:auto-cross-check`，跨端点与公开记录比对由脚本执行；人工逐条签署是唯一未由人完成的输入，已列入 `config/study_scope.json` 的 `open_items`，`docs/study-scope.md` §6 同步声明。
2. **指标只覆盖样本**：`coverage = 1.000000` 的 6 个槽位不是全量覆盖率，`unverifiable share = 0` 也不代表全量事件都可核验；全量口径属工单 05。
3. **多事件交易仍不可得**：全量 6,060 条日志中 0 笔；`--check-scope` 会拒绝"宣称可得但快照为 0"的决策。一旦出现需先补人工核对样本。
4. **观察期前 90 条 USDT 事件不进入任何 issuer 比较**；如需描述 USDT 早期历史，只能作为单 issuer 描述另报。
5. **交易所标签层未接入**（`EXCHANGE_LABELS_API_URL` 未配置），受限子集当前无法成立，属工单 06/08。
6. 仓库仍不是 git 仓库，无法按流程提交分支。
