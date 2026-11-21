# 研究期与样本范围决策 v1

决策记录：`config/study_scope.json`（机器可读、唯一数值来源）。本文件解释该决策，并说明它由哪一次验证的哪些结果支撑。

复核：`python scripts/validate_sample.py --check-scope`。该命令不访问网络，重算交付 CSV 的数据摘要，逐条比对决策引用的批次、摘要、指标、观察期边界与样本清单；不一致时退出码 4。

## 1. 决策

| 项 | 取值 | 依据 |
| --- | --- | --- |
| 观察期起点 `observation_start_block` | `10,274,701` | USDC 在快照中的首个黑名单事件；之前 USDC 为 0、USDT 为 90 条 |
| 研究截止 `study_cutoff_block` | `26,404,630` | 固定日志快照的 `end_block`；canonical 区块头 `0x17925a9e…c0b50` 由独立端点复核，且不高于验证时的 finalized 头 `26,405,361` |
| 链 | `ethereum`（`chain_id = 1`） | 反馈把小型代表性样本限定在 Ethereum；其他链只登记 |
| 代币 | `USDT`、`USDC` | 反馈指定的两个 issuer |
| 事件类别 | `freezing`、`unfreezing`、`destruction` | 三类事件互斥定义；USDC `destruction` 为结构性零 |
| 核心路径 | 观察期内全部可检索事件 | 匹配失败不剔除；不可核验按 R2/R3 标注后仍留在核心集 |
| 受限路径 | 仅有匹配证据且标签日期完整的子集 | 仅用于 timing gap 与 exchange outflow 描述性统计，不与核心结论拼接 |
| 多事件交易 | 结构性不可得（0 / 6,060 笔） | 快照内没有任何交易含两条以上黑名单事件 |

链范围的边界：反馈只在「小型代表性样本」条款中写明 Ethereum，核心分析条款写的是
all retrievable events、未限定链。因此上表的 `ethereum` 是本次实际采集与验证范围，
不是反馈对全部链的限定；其他链只登记不采集，待确认项与纳入后的采集影响见
`docs/supporting-materials.md` §4 与 `chain_registry` 的 `registered_pending` 行。

观察期按区块高度定义，不按日历日定义（`docs/definitions.md` §1、§3.3）。区块时间仅供定位：起点区块时间 `2020-06-16T04:39:08Z`（样本槽位 S6 的区块头），截止区块时间 `2026-11-13T07:44:47Z`（`data/raw/sample_records.json` 的 `cutoff`）。

## 2. 决策输入：代表性样本验证

验证批次 `vs_3b3a9d4db71b1186ad8204411a891f41`，产物在 `data/recon/vs_3b3a9d4db71b1186ad8204411a891f41/`，取证时刻 `2026-11-13T10:26:12Z`（重放同一输入不改写该时刻）。

| 指标 | 结果 | 范围 |
| --- | --- | --- |
| `event retrieval coverage` | `13/13` 日志（`1.000000`）、`6/6` 槽位；区块级日志集合一致 `6/6` | 分母为样本槽位所在区块内独立端点的日志总数（USDT 10、USDC 3）；全量覆盖属工单 05 |
| `balance reconstruction error` | `15/15` 完全一致；最大绝对误差 `0` raw（`0.000000` token） | D1 6 项、`at_event` 3 项、D2 5 项、销毁金额 1 项 |
| `unverifiable share` | `0.000000`（0 / 161 行） | 本批次全部核对行 |
| 验证行 | 161 行全部 `match` | `source_position` 34、`order_version` 18、`balance_state` 61、`manual_reference` 38、`schema_conformance` 10 |

样本覆盖（10 条事件 / 14 条余额行）：

| 维度 | 覆盖 |
| --- | --- |
| USDT | `freezing`、`unfreezing`、`destruction` |
| USDC | `freezing`、`unfreezing`（`destruction` 结构性零） |
| 合约版本 | `TetherToken`、`FiatTokenV1`、`FiatTokenV2_2`（含实现切换边界两侧代码哈希） |
| 交易作用域 | 3 条日志的交易中只有已登记事件成行；跨交易生命周期（解冻→重新冻结、销毁） |
| 多事件交易 | 0 / 6,060，替代验证为多日志交易与同一地址的跨交易序列 |

样本槽位 S1–S6 即工单 01 的人工核对样本；案例清单与事先声明的预期在 `config/lifecycle_cases.json`（5 个案例，含本工单新增的 `unfreeze_usdc`）。

## 3. 起点为什么取 USDC 首个事件

- 快照在 `10,274,701` 之前只有 USDT 的 90 条事件，USDC 为 0（`scope.events_before_start_by_token`，由 `--check-scope` 从快照实算比对）。
- 若把这段并入观察期，任何 issuer 比较都会把"USDC 尚无事件"读成"USDC 冻结更少"，属于观察期造成的假差异，而不是数据结论。
- 起点正好是样本槽位 S6 的区块：该事件在同一条流水线下重建，余额、状态、代码哈希与独立端点一致，说明观察期的左边界本身是已验证的，不是外推的。
- 代价是 90 条 USDT 事件不进入任何 issuer 比较。若要描述 USDT 早期历史，只能作为单 issuer 描述另报，不得并入比较。

## 4. 截止为什么取快照 `end_block`

- 截止按区块高度定义，取固定日志快照的 `end_block`，重放范围因此闭合：同一输入可以在断网条件下复现（已实测）。
- 该区块的 canonical 区块头由独立端点复核（`0x17925a9e…c0b50`），且不高于验证时的 finalized 头 `26,405,361`；D2 余额与截止日黑名单状态也在该区块上完成独立核对，因此"仍冻结"的判定不依赖抓取脚本的运行时刻。
- 若未来把截止日外推，必须重新采集该区间日志并重跑本验证与 `--check-scope`；决策引用的摘要会变，旧决策自动失效。

## 5. 执行顺序如何核验

1. 验证先产出：`python scripts/validate_sample.py` 写出 `manifest.json`（`generated_at_utc = 2026-11-13T10:26:12Z`）、三张 CSV 与数据摘要 `c69ad3b4…5f63`。
2. 决策后写：`config/study_scope.json` 的 `decided_at_utc = 2026-11-13T10:27:00Z`，引用上述批次、摘要与指标。
3. `--check-scope` 重新从交付 CSV 计算数据摘要，要求：引用的批次/摘要/指标与产物一致、`decided_at_utc` 不早于 `generated_at_utc`、观察期起点等于较晚发行代币的首个事件区块、截止区块与快照 `end_block`、canonical 哈希及 finalized 头一致、观察期前事件数与快照实算一致、决策的每条 `supported_by` 证据路径真实存在。
4. 实测反例：把 `decided_at_utc` 改早、把起点改到 USDT 首个事件、把覆盖率改成 `0.500000`，校验同时报出四条失败（顺序、指标、起点、观察期前事件数）并以退出码 4 结束。

## 6. 支撑边界

- 本决策只主张"在这份样本上，公开事件可以被可靠重建"。样本每类事件只有 1–3 例，不能据此推断该类事件的全量特征；覆盖与缺失率属工单 05。
- `unverifiable share = 0` 是"本批次所有核对项都有独立第二来源"的结果，不等于全量事件都可核验；工单 05 需给出全量口径。
- 交易所标签层未接入，`exchange outflow` 受限子集当前无法成立；权限边界与缺失处理见工单 06/08。
- 验证表的 `reviewer` 仍为 `agent:auto-cross-check`；人工签署以 26 行分层抽样完成（`pbn`，
  2026-11-17，26/26 `match`，`check_signoff.py --validate` 退出码 0），覆盖抽样而非全部
  21,492 行。`config/study_scope.json` 的 `open_items` 保留决策时表述（逐条签署未完成），
  不改动该文件：它的 sha256 由核心批次 F3 锚定。

## 7. 何时必须重做本决策

- 观察期内出现多事件交易（当前 0 笔），或出现新的事件类别/新的合约实现。
- 截止日外推、日志快照重采、链范围扩展（含 Tron 等待确认项）。
- 任何会改变数据摘要的重建逻辑修正。届时按"先跑验证、再改 `config/study_scope.json`、最后 `--check-scope`"的顺序重走。
