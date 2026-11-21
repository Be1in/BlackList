# 已登记缺口（2026-11-18）

本文件只记录当前仍存在的缺口、取值与影响面，数据口径以主表为准。

- 审计：`python scripts/audit_dataset.py` → 67 项，67 通过、0 失败（`data/derived/dataset_audit.json`；
  分组说明见 `docs/dataset-audit.md`）
- 核心集：`data/recon/core_64c8c7f9452291663926113a2b1ecc20/`（`data_digest = 49db21c8…2fd5`）

## 1. 与缺口相关的口径

- D2 总体 3,573 行 = USDT 2,867 + USDC 706。其中 29 个 USDT 地址在观察期前首次冻结、观察期内
  无事件、截止日仍未解冻，只写入 `balances`（`reference_event_id` 为空，`notes` 记录首次冻结区块），
  不写入 `events`，也不参与事件计数或 issuer 事件比较；合计 62,418.456433 USDT，D2 USDT 合计
  2,352,542,587.677342。因此 D2 地址数（2,867）与 USDT 事件涉及地址数不能相减对齐。
- `blacklist_transition = false` 共 1,352 条：1,260 条 `destruction`（销毁不改变黑名单状态位，
  `before = after = blacklisted`）与 92 条按 R9 不产生 D1 的 `freezing` / `unfreezing`。
  按 `blacklist_transition` 过滤事件时，这 1,352 条都不进入。

## 2. 缺口清单（G-01、G-02 已关闭，其余五项仍在）

G-01 已于 2026-11-17 关闭；G-02 已于 2026-11-18 关闭。保留行号以便引用。

| ID | 缺口 | 当前取值 | 影响 | 登记位置 |
| --- | --- | --- | --- | --- |
| G-01 | 人工签署（**2026-11-17 已关闭**） | 26 行分层抽样由 `pbn` 签署，26/26 `match`；`check_signoff.py --validate` 退出码 0；全量验证表 `reviewer` 仍为 `agent:auto-cross-check`（设计如此） | 抽样签署覆盖 26 行，不等于 21,492 行逐行签字；该差异已在验证表与正文声明 | `docs/manual-signoff.md` §4、`docs/validation-table.md` §8 |
| G-02 | 制裁指定日期（**2026-11-18 已关闭**） | 官方 SDN Advanced XML 的实体级 `EntryEvent` 日期已接入：193/193 个匹配事件、94/94 个地址有日期，`designation_date` / `day`；与 2026-10-09 第三方解析表在 91 个重叠地址上零差异 | 日期是实体级，不是地址级上榜日；>365 天与负值在受限分析里单列，不进入中位数 | `config/ofac_advanced.json`、`docs/matching-and-missingness.md` |
| G-03 | 交易所标签层（**决定不补，2026-11-18**） | 0 个来源、0 条标签，`not_attempted` | 「冻结前资金是否到达交易所」不回答：无可信的带日期充值地址来源；理由见 `docs/restricted-subset-analyses.md` §8 | `docs/b-layer-options.md` |
| G-04 | 215 条 USDC 事件的中间实现未登记指纹 | `contract_version_confidence = low` | 不进入版本敏感比较 | `docs/core-event-set.md` |
| G-05 | 链范围只做 Ethereum | Tron `registered_pending`、0 事件 | 不能做跨链或“全链”表述 | `docs/supporting-materials.md` §4 |
| G-06 | 观察期前 90 条 USDT 事件不入核心集 | 逐条在 `scope_excluded.csv` | 事件计数与 issuer 比较不含它们；其中仍冻结地址的 **D2 余额**已计入 `balances` 的 `at_study_cutoff` 行 | `docs/study-scope.md` §3 |
| G-07 | 多事件交易结构性为 0 | 0 / 6,060 笔 | 以多日志交易与跨交易序列替代验证 | `docs/study-scope.md` §2 |

## 3. 验证

```bash
python scripts/audit_dataset.py --write     # 67/67 通过
python scripts/reconstruct_core.py --check  # 摘要、列集合、链登记、排除与覆盖算术
python scripts/reconstruct_matches.py --check
python scripts/reconstruct_comparison.py --check
python scripts/reconstruct_restricted.py --check
python scripts/reconstruct_materials.py --check
```

每个入口的 `--check` 都不联网、不写盘，只按当前主表重算摘要与计数并逐行比对。
