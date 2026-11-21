# 05: 核心事件集全量重建

**What to build:** 在已确定的研究期与样本范围内采集全部可检索事件，产出可复跑的核心事件/余额表，让核心结论不再依赖匹配成功的少数样本。

**Blocked by:** 04: 代表性样本验证与研究期/样本范围决策

**Status:** done

- [x] 核心集覆盖研究范围内全部可检索事件，并附覆盖与不可核验记录
- [x] 匹配失败的事件不被剔除出核心集
- [x] 不可获得或无法独立核验的数据按一致规则排除，并逐条记录原因
- [x] 事件集可从原始日志复跑得到，且仍在同一验证 seam 上可对照
- [x] 每个事件登记所属链，不隐含未声明的链范围

## Comments

### 2026-11-13 交付

入口：`python scripts/reconstruct_core.py --workers 10`（观察期从 `config/study_scope.json` 读取，命令行与决策不一致直接退出）；
离线复核 `python scripts/reconstruct_core.py --check`。

产物：`data/recon/core_64c8c7f9452291663926113a2b1ecc20/{events,balances,validation,chain_registry}.csv|.parquet`、
`manifest.json`、`coverage_gaps.csv`（0 行）、`scope_excluded.csv`（90 行）；排除汇总 `data/derived/exclusions.csv`（0 行）。

代码：

- `scripts/recon/core.py`：观察期枚举、全量独立日志检索、并发重建、排除函数、指标与落盘。事件行仍由 `Reconstruction`（工单 02/03）产出，没有另起解析路径。
- `scripts/recon/pipeline.py`：新增快照/OFAC 进程内缓存；全量模式下位置核对改用同一份独立检索集合、版本按代码哈希核对、逐事件 schema 行改为整表一条。默认参数保持原行为，工单 02/04 的 `data_digest` 未变。
- `scripts/recon/rpc.py`：按请求键串行化、缓存原子写、线程内复用连接池、端点配速与限流退避；新增 `reference_archive`（历史状态只走归档 JSON-RPC，跳过已知不可得的 Etherscan 历史 `eth_call`）。
- `docs/core-event-set.md`：方法、覆盖口径、排除规则落地、复现与限制。

数字（`data_digest = f1a0dd82…3eec`，`artifact_digest = 0a6d7449…d4a8`，取证 `2026-11-13T13:41:05Z`）：

| 指标 | 结果 |
| --- | --- |
| `event retrieval coverage` | `1.000000`（5,970 / 5,970 独立日志，严格口径同值；区块级日志集合一致 4,980 / 4,980；差集 0） |
| `balance reconstruction error` | 9,453 / 9,453 完全一致，最大绝对误差 `0` raw / `0.000000` token；不可比较 0（D1 4,077、`at_event` 1,832、D2 3,544） |
| `unverifiable share` | 行口径 `0.000000`（0 / 21,463，全部 `match`）；记录口径 `0.000000`（0 / 15,407） |
| 排除 | 0 条；观察期外 90 条逐条登记；匹配导致的剔除 0 |

核心集：5,970 事件（USDT 4,786、USDC 1,184；`freezing` 4,138、`unfreezing` 572、`destruction` 1,260），
余额 9,437 行（`at_freeze` 4,077、`at_event` 1,816、`at_study_cutoff` 3,544），`analysis_eligibility` 全为 `core`。
版本：`TetherToken`、`FiatTokenV1`、`FiatTokenV2_2`；215 条 USDC 事件落在未登记指纹的中间实现（R4，`contract_version_confidence = low`，保留在核心集）。
链：全部事件为 `ethereum`，`chain_registry` 2 行（`ethereum` = `verified_sample`，`tron` = `registered_pending`，只登记不采集）。

复现：命中缓存重放约 1 分钟，两个摘要不变，`generated_at_utc` 保持首次取证时刻；仅把 `ETHEREUM_ARCHIVE_RPC_URL`
指向不可达地址重放，摘要同样不变；`--expect-data-digest f1a0dd82…3eec` 可直接复核，错值退出码 2。
`--check` 不访问网络：重算摘要与列集合，并从固定日志快照重算观察期内应有的事件集合与交付事件表对照。
冷跑（无缓存）约 2 小时 15 分，受归档端点 compute-unit 配额与配速限制。

评审后修正（两轴评审，Standards / Spec）：

- 覆盖率补 `strict_coverage`（分母取快照数与独立检索数的较大者）：快照多出日志时不再显示 1.0。
- `unverifiable share` 同时报告行口径（与工单 04 可比）与记录口径（`definitions.md` §7 的定义），并在 manifest 写明两者分母不同。
- R9 明确为归属规则而非排除：`exclusion_rule_id` 保持为空，新增 `core_set.r9_attribution_rows = 92`，并说明可由 `blacklist_transition = false` 逐条筛出；R7 改为在 `merge_bundles` 之前判定，重复项写入排除视图而不是被静默去重。
- `--check` 增加"从固定日志快照重算观察期内事件集合"的对照，使"可从原始日志复跑"有离线检查，而不只靠摘要相等。
- 位置修复改为"先取到替代日志再替换原区块"：替代端点失败时保留原记录并把 `complete` 置 false，不让分母静默缩小。
- 合约版本核对校验返回代码非空、非十六进制时记 `unverifiable`，不再把空代码哈希当成功。
- 配速改为按端点预留时隙、锁外睡眠；连接池按线程复用（见偏差 1）。
- `verification_scope` 写入 manifest：逐事件独立核对的是位置、事件前余额、D2 余额与地址串；状态机字段不逐事件重取第二来源。

偏差与限制：

1. **首个全量批次出现过 108 个事件缺辅助证据**（Alchemy 连接池耗尽，`Max retries exceeded`）。当时按 R2/R3 保留了 128 条 `unverifiable` 行；定位为每个事件各建一个 `requests.Session` 导致套接字耗尽。改为线程内复用连接池 + 限流退避后重跑同一输入，0 失败、0 不可核验。保留这条记录是因为它说明"全量跑得下来"本身需要工程保障，而不是数据问题。
2. **`balance_state` 全量核对的是事件前余额与 D2 余额**；`blacklist_state_before/after` 仍只由归档端点取得，其独立核对依赖 D2 归属与工单 04 人工样本，未逐事件重复。`unverifiable share = 0` 不应读作"每个字段都有第二来源"。
3. **`balances` 以 `query_id` 为主键**：同一区块内同一地址的多次销毁共用一次事件前查询，余额表只保留一行（16 个事件）；这些事件的 `events.balance_pre_event_raw` 仍各自记录并逐条核对。
4. **覆盖分母的独立性止于一个端点**：独立检索自身漏日志不会自我暴露；两个方向的差集都落盘供第三方复核。
5. **人工签署仍未完成**（`reviewer = agent:auto-cross-check`），与工单 04 的开放项一致。
6. 匹配层只作为核对行出现，`matches` 表与 match rates 属工单 06；timing gap / exchange outflow 不在本工单。
7. 仓库仍不是 git 仓库（`git status` 报 not a repository），无法按流程提交分支。

### 2026-11-17 重跑

两处契约偏差修复后重跑，数字更新为：5,970 事件 / 9,466 余额 / 21,492 核对；D2 3,573
（USDT 2,867 / USDC 706）；余额误差 9,482 / 9,482；`data_digest = 49db21c8…2fd5`、
`artifact_digest = fdea03ea…7b01`、取证 `2026-11-17T12:47:27Z`。`blacklist_state_before/after`
改取事件位置状态，16 条 destruction 的 transition 变为 false；D2 总体补入 29 个观察期前冻结地址。
详见 `docs/known-issues.md` 与工单 11。
