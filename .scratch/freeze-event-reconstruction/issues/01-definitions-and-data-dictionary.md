# 01: 定义与数据字典契约

**What to build:** 用少量人工核对的 USDT / USDC 交易，把"一条事件记录长什么样"固定下来：从原始日志到一条字段完整、可追溯、可复核的记录，再到验证表中的一条对照项。后续所有采集、匹配与分析只消费这份契约声明的字段。

**Blocked by:** None (can start immediately)

**Status:** done

- [x] freezing / unfreezing / destruction 各自独立且互斥地定义；冻结时余额与研究截止日仍冻结余额分别定义
- [x] 公开制裁时间戳与私有执法请求各有定义与可观测性声明；私有请求标为不可观测、无对应字段、不参与任何指标，两者不共用时间字段
- [x] 数据字典字段覆盖原始来源、所属链、交易与日志位置、余额定义、匹配证据、时间戳精度、missingness、排除原因
- [x] 写明一致排除规则：不可获得或无法独立核验的数据如何标记与排除
- [x] 人工核对的样例交易填满字典字段与验证表模板，评审可逐字段复述来源与口径，样例不含字典外字段

## Comments

### 2026-11-13 交付

契约文件：

- `docs/definitions.md`：三类事件（互斥、单日志单事件）、两套余额口径（D1 `balance_at_freeze` / D2 `balance_at_study_cutoff`，外加 `at_event` 诊断口径）、公开制裁时间与私有执法请求（后者 `not_observable`、无字段、不参与指标）的边界。
- `docs/data-dictionary.md`：5 张表（`events` / `balances` / `matches` / `validation` / `chain_registry`）、完整枚举、8 条排除规则 + 1 条指标归属规则（R9）、可得性分层 A/B/C、派生字段。
- `docs/data-dictionary.csv`：字段清单的机器可读副本（113 行），供后续脚本校验列集合。
- `docs/validation-table.md`：14 列验证表定义、核对协议、已交付样本与结果。

样本（真实链上数据，非构造）：

- `data/samples/sample_events.csv` 6 行 / 56 列，`sample_balances.csv` 11 行 / 19 列，`sample_matches.csv` 12 行 / 21 列，`sample_validation.csv` 25 行 / 14 列。
- 覆盖 USDT/USDC × freezing/unfreezing/destruction，含 TetherToken、FiatTokenV1、FiatTokenV2_2 三个合约版本；4 个事件命中 OFAC SDN。
- 原始层：`data/raw/logs/ethereum_blacklist_logs.json`（区块 4,634,748–26,404,630，6,060 条日志，sha256 `10fdb389…`）、`data/raw/state_calls/`、`data/raw/abi/`、`data/raw/ofac/`（SDN 快照 sha256 `7f43332d…`）。

核对：25 条检查全部 `match`，余额与代码哈希均由第二家提供商（Alchemy / Etherscan）独立复核。`reviewer` 为 `agent:auto-cross-check`，**人工签署尚未完成**，留给工单 04。

### 数据推翻的先验假设（已写回契约）

1. 全量 6,060 条日志中，`tx_event_count ≥ 2` 的交易为 0：三类事件都是单地址 owner 调用，"同一交易内多个事件"在 Ethereum 上不可得，工单 03 相应的验收口径需要改为"同交易内被非 token 日志包围的顺序"。
2. USDT `destroyBlackFunds` 不 emit `Transfer` 到零地址，销毁金额只能取 `DestroyedBlackFunds` 参数。
3. 日志数 ≠ 状态变化数：USDT 7 条 `freezing` + 1 条 `unfreezing`、USDC 54 条 + 30 条落在状态未变的地址上。已加 `blacklist_transition` 字段、freeze episode 与 R9。
4. SDN 导出只有清单发布日（2026-11-09），没有逐条指定日，因此 `sanctions_public_timestamp` 在样本中为 `not_collected`；timing-gap 需要工单 08 另取带日期的 OFAC 行动记录。

### 遗留

- 交易所标签层未接入（`EXCHANGE_LABELS_API_URL` 未配置），样本中为 `not_attempted`。
- 单次 SDN 快照，只能判定"当前是否在列"。
- 全量扫描的日志完整性尚未由第二提供商复核（属工单 05）。
