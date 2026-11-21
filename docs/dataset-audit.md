# 数据集契约审计报告（2026-11-18）

审计对象：`core_64c8c7f9452291663926113a2b1ecc20` 与 `match_e7a32e2a4120dccb5d5cfe1edf360766`
交付表。审计是只读的：不修表、不写回批次，只把每张表对回 `docs/definitions.md` 与
`docs/data-dictionary.md` 的声明。

- 入口：`python scripts/audit_dataset.py --write`
- 机器可读结果：`data/derived/dataset_audit.json`
- 结果：**67 项，67 通过、0 失败**（含 M6 指定日逐行溯源）

## 1. 检查分组

| 组 | 项数 | 看什么 |
| --- | --- | --- |
| F 文件与清单 | 8 | CSV/Parquet 行列一致、manifest 计数、日志快照与范围决策的 sha256、字典路径 |
| E 事件表 | 29 | `event_id` 唯一与派生、`log_order_key`、交易内事件数、区块哈希与时间单调、枚举、必填非空、链、观察期边界、三类事件状态契约、D1 归属、R9、合约版本置信度 |
| B 余额表 | 13 | 主键、外键、D1 行数与 episode 起点一一对应、D1 查询区块与状态、D2 区块与状态、D2 总体完整性、单位换算、来源与缺失 |
| M 匹配层 | 6 | 每事件 × 2 层、外键、交易所层 `not_attempted`、OFAC 证据与日期字段、指定日逐行溯源 |
| V 验证表 | 4 | `agreement` 全为 `match`、检查类型枚举、reviewer、字段名在字典内 |
| C 跨批次 | 2 | 匹配层 manifest 指向当前核心摘要、链登记与事件链一致 |
| D 字典列 | 5 | 五张交付表不出现字典外列 |

## 2. 关键不变量

- 5,970 个 `event_id` 全部可由 `sha256("ethereum|tx_hash|log_index")[:32]` 重算；`log_order_key` 唯一。
- 4,077 条 `at_freeze` 与 4,077 个 episode 起点一一对应；查询区块全部为 `block_number - 1`，状态全部为 `not_blacklisted`。
- 3,573 条 `at_study_cutoff` 全部在截止区块、全部 `blacklisted`；与全量日志重建的仍冻结集合逐一相等，无多无少。
- 1,260 条 `destruction` 全部 `blacklist_state_before = after = blacklisted`、`transition = false`。
- 21,492 条核对全部 `match`；无 `mismatch`、无 `unverifiable`。
- 11,940 条匹配 = 5,970 事件 × 2 层；交易所层 5,970 条全部 `not_attempted` + `external_source_unavailable`。
- 193 条 OFAC 匹配全部带 `sanctions_public_timestamp`，`basis = designation_date`、`precision = day`，且逐行能在登记的 SDN Advanced 提取表中找到同一地址、同一日期。
- 观察期内无事件被排除；观察期外 90 条逐条在 `scope_excluded.csv`。

## 3. 两项口径与证据

| 项 | 当前取值 | 证据 |
| --- | --- | --- |
| D2 总体 | 3,573 行（USDT 2,867 / USDC 706），含 29 个观察期前冻结、观察期内无事件的地址 | `data/derived/d2_population_full.csv`（3,573 行）、`cutoff_blacklist_gap.json`（29 行查询） |
| destruction 状态 | 1,260 条全部 `before = after = blacklisted`、`transition = false` | `data/derived/event_state_corrections.csv`（16 行同区块证据，与主表逐行一致） |

两项口径的字段依据见 `docs/known-issues.md` §1。

## 4. 审计不能证明什么

- 覆盖率的分母来自一个独立端点；该端点自身漏日志不会自我暴露（两个方向的差集已落盘）。
- 独立参照仍依赖第三方归档端点；`unverifiable = 0` 只说明本次每条核对都拿到了第二来源。
- 审计检查的是**表与声明的契约**，不是语义正确性：例如措辞是否为描述性，仍需人工审阅。
- 审计只断言全量验证表的 `reviewer = agent:auto-cross-check`；真人抽样签署（26 行，`docs/manual-signoff.md`）是表外证据，不在 67 项检查内。

## 5. 复跑

```bash
python scripts/audit_dataset.py --write
python scripts/audit_dataset.py --core-dir <dir> --match-dir <dir>
python scripts/build_dataset_manifest.py --check
```

`--core-dir` / `--match-dir` 可指向任意批次；默认指向当前交付批次。退出码 0 表示全部检查通过。
