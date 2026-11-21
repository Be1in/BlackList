# GLOSSARY — 冻结事件数据集

本文件只固定项目内反复出现的术语。字段级定义仍以 `docs/data-dictionary.md` 为准；两者冲突时，
字典优先。

| 术语 | 含义 | 不表示 |
| --- | --- | --- |
| 事件（event） | 一条已登记的合约日志：`freezing`、`unfreezing` 或 `destruction`。以 `(tx_hash, log_index)` 定位 | 不等于状态变化；重复调用同一状态也会产生日志 |
| 日志 / 状态变化 | 日志是链上事实；状态变化由 `blacklist_state_before != blacklist_state_after` 判定 | 两者不是一一对应（R9） |
| 冻结 episode | 同一地址的一段连续 `blacklisted` 区间，由 `freezing` 打开、`unfreezing` 关闭；`destruction` 不关闭 | 不是一次日志，也不是一次交易 |
| R9 | 重复 `freezing` / `unfreezing` 日志的归属规则：计入事件覆盖，但不产生 D1、不计入冻结地址数 | 不是排除规则 |
| D1（`at_freeze`） | episode 起点事件前一刻的余额：`STATE_AT_BLOCK_END(block-1)` + 同区块更早 `Transfer` 调整量 | 不是“曾经冻结的总金额”在全量事件上的简单相加；也不与 D2 相加 |
| `at_event` | 解冻 / 销毁事件的诊断性事件前余额 | 不得当作 D1 报告 |
| D2（`at_study_cutoff`） | 截止区块结束时仍为 `blacklisted` 的地址在该区块的余额 | 不是“全部曾冻结金额”，也不含已销毁金额 |
| 观察期 | 区块 `10,274,701`–`26,404,630`，由验证结果决定，不由抓取时刻决定 | 不是日历区间；起点前 90 条 USDT 事件不在核心集 |
| 研究截止（cutoff） | 固定日志快照的 `end_block`，canonical 且不高于验证时的 finalized 头 | 不是脚本运行时间 |
| 覆盖率 | 已检索事件 ÷ 独立端点在观察期内应返回的日志数；分母含被排除项 | 不是快照自身的完整度 |
| 不可核验（unverifiable） | 缺少独立第二来源的核对行；保留记录并标注 | 不是 `mismatch`，也不是 0 |
| 结构性零（structural zero） | 合约定义上不存在该类事件（USDC `destruction`） | 不是“未发生”或“数据缺失” |
| `unmatched` | 地址串不在当前 OFAC 快照中 | 不等于“未受制裁” |
| `not_attempted` | 数据源未接入，未尝试匹配（交易所标签层） | 不等于 `unmatched`，也不等于 0 |
| 可得性 A/B/C | A 一致可得（日志、状态、余额）；B 条件可得（外部匹配、日期）；C 不可观测（私有请求、冻结原因、实体归属） | B 层指标不得与 A 层拼接 |
| 批次 id | 由输入摘要派生的目录名（`core_…` / `match_…` / `compare_…`） | 不是内容版本号 |
| `data_digest` | 对交付表按固定规则计算的摘要，排除抓取时刻字段 | 不覆盖 `manifest.json` 的说明文字 |
| 修正视图 | 对主表两点口径（D2 总体、销毁状态）的逐行证据表 | 不是新批次，也不替代主表 |
