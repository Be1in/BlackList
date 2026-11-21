# 人工签署抽样核对（G-01）

验证表的 `reviewer` 目前是 `agent:auto-cross-check`：脚本做了跨端点与公开记录比对，
但没有真人逐条签字。本表把这一步变成 26 行、约 15–30 分钟的抽样核对，不要求重跑全量，
也不改动交付表。

- 抽样表：`data/derived/manual_signoff_sample.csv`（26 行，表头是第 1 行，数据行 2–27）
- 生成：`python scripts/make_signoff_sample.py`（确定性分层抽样，重跑结果相同）
- 辅助脚本：`python scripts/check_signoff.py --row <Excel 行号>`（只读，不填表）

## 1. 打开表

用 Excel 或 WPS 打开 `data/derived/manual_signoff_sample.csv`。每行是一笔待核对记录：

| 列 | 含义 |
| --- | --- |
| `sheet_row` | Excel 行号（2–27），辅助脚本用它定位 |
| `row_type` | `event`（事件）或 `d2`（截止日余额） |
| `token_symbol` / `event_class` | USDT/USDC、freezing/unfreezing/destruction；D2 行为 `at_study_cutoff` |
| `block_number` / `log_index` / `tx_hash` | 链上位置；D2 行没有 tx/log |
| `state_before` / `state_after` | 重建的黑名单状态 |
| `balance_reference` / `balance_token` | `at_freeze` / `at_event` / `at_study_cutoff`，token 单位（6 位小数） |
| `etherscan_url` / `address_url` | 事件行看交易日志；D2 行看地址页 |
| `reviewer` / `reviewed_at_utc` / `decision` / `note` | **留给你填的四列** |

## 2. 逐行核对（推荐顺序）

对每一行：

1. **看链上原始记录**：点 `etherscan_url`（D2 行点 `address_url`），确认交易收据里
   `log_index` 对应的日志存在，日志的 `address` 参数等于表里的 `affected_address`，
   事件类型与 `event_class` 一致。USDT 的 `AddedBlackList` / `DestroyedBlackFunds` 地址在
   Data 里，USDC 的 `Blacklisted` / `UnBlacklisted` 地址在 Topics 里；Etherscan 通常会解码成
   可读参数。
2. **让脚本复算状态与余额**：在项目根目录运行

   ```bash
   python scripts/check_signoff.py --row 2
   ```

   它会做三件事（只读、不写表）：

   - 在固定日志快照里按 `(tx_hash, log_index)` 找日志，解码地址，确认与表格一致；
   - 用归档端点在 `block-1` / `block` 读 `isBlackListed` / `isBlacklisted`；
   - 在 `block-1`（D2 为 cutoff）读 `balanceOf`，与 `balance_token` 比对。

   输出示例：

   ```
   行 2 | event | USDC freezing | block 10274701
     表格: before=not_blacklisted after=blacklisted balance=100000.0 (at_freeze)
     日志: 快照命中 Blacklisted(address)，解码地址 0xaa05...
     链上: state=not_blacklisted -> blacklisted balance(block-1/cutoff)=100000 adjustment=0
     数值: 一致
   ```

   若 `adjustment` 不为 0，D1 还包含同区块更早的 `Transfer` 调整量；脚本已把 adjustment
   加回去比对，输出仍会写 `一致` 或 `不一致`。
3. **填四列**：
   - `reviewer`：真实姓名或姓名缩写（例如 `Peng Beining` / `PBN`），不要填 `agent`；
   - `reviewed_at_utc`：RFC3339，例如 `2026-11-17T13:30:00Z`（UTC，不是本地时间）；
   - `decision`：`match` / `mismatch` / `unverifiable` 三选一；
   - `note`：`mismatch` 必须写清哪个字段、表格值、链上值；`match` 可留空；
     `unverifiable` 写清为什么取不到第二来源。
4. **一次看完全部**：可以连续跑

   ```bash
   python scripts/check_signoff.py --all
   ```

   它会按行号顺序打印 26 行，方便你对照 Etherscan 逐行填。
5. **收尾校验**：填完后运行

   ```bash
   python scripts/check_signoff.py --validate
   ```

   - 退出码 0：26 行都填了，且 26 行数值全部一致；
   - 退出码 4：还有未填行，或有数值不一致；输出会列出具体行号。

## 3. 结论怎么用

- 这是**抽样签署**，不是 21,492 行逐条签字；它证明“重建输出经真人对照链上原始记录”，
  不能写成“每一行都有人签过”。
- 只要出现一条 `mismatch`，先按 `docs/data-dictionary.md` 的偏差处理流程记录
  `deviation_reason`，再决定是否需要重跑；不要直接改交付 CSV。
- 26 行全为 `match` 时，可在论文/报告中写“a 26-row stratified sample was manually checked
  against block explorers and archival state”，并附本表路径。
- 签署结论已写入 `docs/validation-table.md` §8；G-01 在 `docs/known-issues.md` 关闭。
  `config/study_scope.json` 不改动（其 sha256 由核心批次 F3 锚定），`open_items` 保留决策时表述。

## 4. 当前状态（已签署，2026-11-17）

- 抽样表：26 行已由 `pbn` 于 `2026-11-17T14:08:54Z` 签署，`decision` 全部 `match`，无 `mismatch`、无 `unverifiable`。
- 校验：`python scripts/check_signoff.py --validate` → `rows 26 / unsigned 0 / numeric check 26 ok / sign-off sheet complete and numerically consistent`，退出码 0。
- 全量验证表：`reviewer = agent:auto-cross-check`，21,492 行全部 `match`；抽样签署不改变该列，也不宣称逐行签字。
- 结论已写入 `docs/validation-table.md` §8，G-01 已关闭。
