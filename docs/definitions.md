# 冻结事件定义契约 v1

契约版本：`2026-11-13 / v1`（对应工单 `.scratch/freeze-event-reconstruction/issues/01-definitions-and-data-dictionary.md`）

本文件定义"一条事件记录长什么样"：三类事件的边界、两套余额口径、两类时间来源的可观测性。字段级契约见 `docs/data-dictionary.md`。后续采集、匹配与分析只消费数据字典声明的字段；本文件出现的概念若未落到字典字段（采集参数除外），即视为未定义。

## 1. 范围

| 项 | 取值 | 依据 |
| --- | --- | --- |
| 链 | `ethereum`（`chain_id = 1`） | 反馈把"小型代表性样本"限定在 Ethereum |
| 代币 | `USDT` `0xdAC17F958D2ee523a2206206994597C13D831ec7`；`USDC` `0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48`，均 6 位小数 | 反馈指定的两个 issuer |
| 观察期 | 参数 `observation_start_block` / `observation_end_block`（取值见下） | 由工单 04 依据验证结果确定，本契约只规定记账方式 |
| 研究截止日 | 参数 `study_cutoff_block`（按区块高度定义，不按日历日定义；取值见下） | 同上 |
| 核心分析范围 | 研究范围内全部可检索事件 | spec「两条分析路径显式分离」 |
| 其他链 | 仅登记、不采集 | `chain_registry` 中标记 `registered_pending` |

`study_cutoff_block` 取"截止日最后一个已 finalize 的区块"，其区块时间写入 `study_cutoff_time_utc`。所有"仍冻结"的判定都相对这个区块，而不是相对抓取脚本的运行时刻。

已确定的观察期（工单 04，依据 `docs/validation-table.md` §4.1 的样本验证结果）：

| 参数 | 取值 |
| --- | --- |
| `observation_start_block` | `10,274,701`（USDC 首个黑名单事件；之前 USDT 90 条、USDC 0 条） |
| `observation_end_block` / `study_cutoff_block` | `26,404,630`（日志快照 `end_block`；canonical 且不高于 finalized 头） |

决策记录 `config/study_scope.json`（机器可读，唯一数值来源），说明见 `docs/study-scope.md`，核验命令 `python scripts/validate_sample.py --check-scope`。改参数即改决策：须先重跑样本验证，再更新决策并重新核验。

## 2. 三类事件

事件状态机只有两个状态：`blacklisted` / `not_blacklisted`。三类事件各自对应一种不可再分的变化：

### 2.1 freezing（冻结）

- 触发：合约发出 `AddedBlackList` / `Blacklisted` 日志。日志存在即记一条 `freezing` 事件，但日志存在不等于状态真的改变（见 2.4 第 6 条）。
- 日志：USDT `AddedBlackList(address)`，USDC `Blacklisted(address)`。
- 含义：从该日志生效起，被列地址的转出（USDC 部分版本另含转入）被合约拒绝。
- 一次 `freezing` 只对应一条日志、一个被冻结地址。

### 2.2 unfreezing（解冻）

- 触发：合约发出 `RemovedBlackList` / `UnBlacklisted` 日志；同样以日志为准，状态是否改变由历史状态查询确认。
- 日志：USDT `RemovedBlackList(address)`，USDC `UnBlacklisted(address)`。
- 解冻不返还任何金额，也不保证余额仍存在（可能已被销毁）。解冻只改变状态位，余额变化一律来自 `Transfer` 或 `destruction`。

### 2.3 destruction（销毁）

- 触发：issuer 直接销毁被列地址持有的余额，总供应量因此下降。
- 日志：USDT `DestroyedBlackFunds(address,uint256)`。该事件只出现在 USDT；销毁**不** emit `Transfer` 到零地址，余额减少在日志层不可见，金额只能取自该事件的第二个参数。
- 前置条件：合约要求被销毁地址在销毁前处于 `blacklisted`；采集时逐条用历史状态核验，不满足的记录进入验证表，不静默接受。
- 销毁**不改变**黑名单状态位：销毁后地址仍为 `blacklisted`，因此不能同时记为 `freezing` 或 `unfreezing`。
- USDC 的 Ethereum 合约没有 issuer 发起的销毁入口，故 `destruction` 对 USDC 是**结构性零**（`event_class` 计数为 0，`missing_reason_code = not_applicable`），不是缺失，也不能与 USDT 的销毁数放在同一分母上直接比较。

### 2.4 互斥与计数规则

1. 一条日志只映射到一个 `event_class`；`event_class` 是枚举，不允许多值。
2. `Transfer` / `Approval` 日志不是事件，只作为余额调整证据（销毁不 emit `Transfer`）；它们不能产生事件行。
3. 同一交易内多个事件各自成行，按 `(block_number, tx_index, log_index)` 排序；`log_order_key` 是唯一排序依据，禁止按时间戳或日志内容排序。
4. `event_id` 由 `(chain, tx_hash, log_index)` 确定性派生，重跑必须得到同一 `event_id`；同一 `event_id` 出现两次即视为重复，保留首次、其余标记 `exclusion_rule_id = R7`。
5. 以下情况不产生事件行：交易 revert、非 canonical 区块（重组后孤立）中的日志、仅内部调用且未 emit 的状态变化、`BlacklisterChanged` 之类的权限变更日志。
6. **日志与状态变化不是一一对应**：合约入口不校验当前状态，同一地址可以连续收到多条 `freezing`。实测全量 6,060 条日志中，USDT 有 7 条 `freezing`、1 条 `unfreezing`，USDC 有 54 条 `freezing`、30 条 `unfreezing` 落在状态未改变的地址上。因此 `blacklist_state_before/after` 必须来自历史状态查询，`blacklist_transition` 记录是否真的变化；`blacklist_transition = false` 的日志按 R9 处理：计入事件覆盖，但不产生 D1，也不计入冻结地址数。
7. 同一地址的连续 `blacklisted` 区间称为一个 **freeze episode**（`unfreezing` 关闭区间，`destruction` 不关闭）。`balance_at_freeze` 只归属每个 episode 的第一条 `freezing`（`is_episode_start = true`）。

## 3. 余额口径

### 3.1 唯一历史状态原语

`STATE_AT_BLOCK_END(b)` = 对归档节点执行 `eth_call`，读取代币合约 `balanceOf(address)`，区块参数取 `b`（即区块 `b` 结束时的状态）。

- 这是唯一的余额真值来源。不允许用当前状态倒推，不允许用 `Transfer` 累加作为独立口径（累加只能作为**调整量**叠加在 `STATE_AT_BLOCK_END` 上）。
- 每次查询记录 `balance_block_number`、`balance_block_hash`、`query_payload_sha256`、`queried_at_utc`、`balance_source`，使复核者能用同一输入重放。
- 黑名单状态查询按代币 ABI 取选择器：USDT `isBlackListed(address)`、USDC `isBlacklisted(address)`。二者不可混用，取错会 revert 或返回错误值。

### 3.2 D1：冻结时余额（`balance_at_freeze`）

对 `freezing` 事件，被冻结地址在**冻结日志生效前一刻**的余额：

1. 取 `STATE_AT_BLOCK_END(block_number - 1)` 为基础值。
2. 若同一区块内、该日志之前存在涉及该地址的 `Transfer`（按 `log_order_key` 早于本日志，含同一交易内更早的日志），把这些带符号金额求和为 `balance_pre_event_adjustment_raw`，加到基础值上。
3. 两者之和即 `balance_pre_event_raw`（token 最小单位，十进制字符串），并给出 `balance_pre_event_token`（除以 `10^decimals`）。
4. 调整量为 `0` 表示该日志之前同区块没有涉及该地址的 `Transfer`；若因该区块日志不完整无法确认调整项，`balance_pre_event_precision = lower_bound_only` 并在验证表登记。

同一规则对 `unfreezing` / `destruction` 行计算 `balance_pre_event_raw`，但该值在余额表中记为 `balance_reference = at_event`，只作诊断，不得当作 `balance_at_freeze` 指标。只有 `freezing` 行的 `at_freeze` 数值可被引用为 D1。任何指标必须声明使用哪套口径（字段 `balance_reference`）。

### 3.3 D2：研究截止日仍冻结余额（`balance_frozen_at_study_cutoff`）

- 总体：截至 `study_cutoff_block` 结束时黑名单状态仍为 `blacklisted` 的地址集合。
- 每地址取值：`STATE_AT_BLOCK_END(study_cutoff_block)` 的 `balanceOf`，即 `balance_reference = at_study_cutoff` 的 `balances` 表行。
- 截止日前已解冻、且之后未再冻结的地址**不进该总体**；先解冻后重新冻结的地址按当前 episode 归入总体。截止日前已销毁的余额不在余额中，销毁金额单独由 `destruction_amount_raw` 记录。
- 汇总口径必须写清：`sum(balance_frozen_at_study_cutoff)` 只在"仍冻结地址集"上求和，不得与 `sum(destruction_amount_raw)` 相加后当作"冻结金额"，也不得把前者解释为"曾经被冻结的金额"。后者只能用 D1 求和。

### 3.4 单位、精度与禁止事项

- 数值以最小单位整数存储（十进制字符串，避免 `uint256` 溢出），另存 `token` 单位（6 位小数）。
- 本契约不做 USD 换算；任何 USD 数值属于下游另行声明的参数，不得写成事件字段。
- 缺失一律留空并给 `missing_reason_code`；只有日志或状态确实为 0 时才写 `0`。
- 同一报告内不得混用 D1 与 D2；`balance_reference` 缺失的指标视为未定义。

## 4. 时间来源

### 4.1 事件时间（`block_time_utc`）

- 来源：区块头 `timestamp`，精度到秒，UTC。
- `event_time_precision = second`；时间源固定为区块头，不引入第二来源。
- 链上时间戳由出块者写入，与真实墙钟可能有偏差；该偏差不建模、不修正，只在验证表记录取到的区块头时间。

### 4.2 公开制裁时间戳（`sanctions_public_timestamp`）

- 定义：某地址第一次出现在可公开核验的制裁记录中的日期，带来源、记录标识与证据链接。
- 精度：`day`（公开清单只公布日期）。`sanctions_date_basis` 枚举：`designation_date`（指定生效日）、`list_publication_date`（清单发布日）、`list_update_observed_date`（观测到更新日）；三者不得混用，也不得与链上事件时间共用一个字段。
- 清单快照的发布日期（如 SDN 导出文件的 `Publish_Date`）**不是**上述任何一种，只写 `matches.label_date`（`list_snapshot_date`），不写 `sanctions_public_timestamp`；用快照日期计算 timing gap 会得到错误的"响应速度"。
- 该时间戳不是执法请求时间，不是资金流出时间，也不是行为发生时间。
- 由此派生的 `sanctions_timing_gap_days = date(block_time_utc) − sanctions_public_timestamp`（天）标记为 `derived`，分辨率 1 天；|gap| < 1 天只能记为同日，不得声称更细的先后顺序。

### 4.3 私有执法请求（private enforcement request）

- 观测性：`not_observable`。
- 字段：无。本契约不为它保留时间字段、标志位或推断值，它不进入任何指标。
- 因此：公开制裁时间与冻结时间之间的差值只能描述为"公开记录与链上事件的时间差"，不得解读为对私有请求的响应速度。
- 两个时间口径不共用字段：事件时间只有 `block_time_utc`，制裁时间只有 `sanctions_public_timestamp`；任何把两者写入同一列的表格视为违约。

## 5. 缺失与不可核验

- 缺失在采集阶段就地标记（`missingness_status`、`missing_fields`、`missing_reason_code`），在派生阶段按固定规则排除（`exclusion_rule_id`），两步都不允许静默丢弃。
- 排除规则按记录类型统一施加，不看 issuer、不看事件类别；具体规则见 `docs/data-dictionary.md` 第 4 节。
- 无法独立核验 ≠ 缺失：前者是 `verification_status = unverifiable`（记录存在，但缺少独立第二来源），后者是 `missingness_status = unavailable`。两者分别计入 `unverifiable share` 与缺失统计。

## 6. 措辞约束

`public sanctions timing gaps`、`address outflows`、`frozen balances` 一律是描述性统计。不得由公开时间差推断私有请求响应速度，不得由地址流出推断规避，不得由冻结余额推断执法有效性；不得把有限匹配子样本的结论外推到全部冻结活动。

## 7. 术语

| 术语 | 本项目的含义 |
| --- | --- |
| freeze lifecycle | 单个地址从 `freezing` 到 `unfreezing` / `destruction` / 截止日仍冻结的完整状态序列 |
| event retrieval coverage | 已检索到的事件数 ÷ 应有事件数（分母由区块范围内日志总数给出） |
| balance reconstruction error | 重建余额与独立参照（人工核对值、另一来源的历史状态）之间的偏差 |
| unverifiable share | `verification_status = unverifiable` 的记录占比 |
| missingness | 字段级不可得，含原因码，不参与零值填充 |
| study cutoff | 由 `study_cutoff_block` 定义的截止时点 |
| matched subset | 具备公开记录匹配证据且标签日期完整的子集，仅用于受限分析 |

## 8. 变更规则

改定义即改契约：升版本号、在验证表登记受影响字段、重跑受影响表。新增字段必须先写入 `docs/data-dictionary.md`，否则该字段不得出现在任何交付表（含样例）中。
