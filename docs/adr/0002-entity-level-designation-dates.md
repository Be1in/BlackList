# ADR 0002 — 用官方 SDN Advanced XML 的实体级指定日，而不是地址级上榜日

- 状态：accepted
- 日期：2026-11-18
- 相关：G-02、`config/ofac_advanced.json`、`scripts/parse_ofac_advanced.py`、`docs/b-layer-options.md`

## 背景

`matches.sanctions_public_timestamp` 一直是空的：`SDN.CSV` 只有清单发布日（`Publish_Date`），
没有每个地址的指定日期。结果是 timing-gap 子集为 0，工单 08 的 R2 只能写 `not_reportable`。
反馈要求"公开制裁时间戳与私有执法请求分开"，但没要求放弃公开时间戳。

## 决定

1. 从 **官方 `SDN_ADVANCED.XML`** 解析地址与实体指定日（`SanctionsEntry/EntryEvent/Date`），
   不采用第三方解析表的地址级推断。
2. `sanctions_date_basis` 取字典既有枚举 **`designation_date`**，`precision = day`；
   **不新增字段、不改 `docs/data-dictionary.csv`**——该文件的 sha256 被核心批次 id 锚定，
   改一个枚举值就要重跑核心集。
3. 日期是**实体级**：同一实体的多个地址共享一个 `EntryEvent` 日期。逐行在 `notes` 记录
   entity 与 `sdn_profile_id`，来源 sha256 记入 `config/ofac_advanced.json` 与批次 manifest；
   审计 M6 逐行核对"地址 → 日期"能在提取表中复现。
4. 负值（冻结早于指定）与 `>365` 天（实体早年指定、地址后来上榜）**单列计数**，不进入
   中位数 / P90；中位数与同日比例只在 0–365 天子集上计算。
5. 地址匹配仍用原有 `SDN.CSV` 快照；日期源是另一份快照，单独固定、单独记录，不合并成一个
   "OFAC 快照"。

## 备选与否决理由

| 方案 | 否决理由 |
| --- | --- |
| 继续留空（`not_reportable`） | 官方文件可得且可复现；放弃会让 B 层分析整章为空 |
| 用 2026-10-09 的 SanctionsAI 解析表 | 快照比 SDN.CSV 早一个月，3 个 2026-10-28 冻结的地址没有日期；且来源不是官方 |
| OpenSanctions `first_seen`（O2） | 是"被观测到"的日期，不是指定日；语义不符，只适合做敏感性 |
| 地址级历史快照差分（O3） | 工程量 1–2 天，收益是把实体级换成地址级；当前不作为前置条件 |
| 新增 `sdn_advanced_entry_event` 枚举或新字段 | 会改 `data-dictionary.csv` 的 sha256，触发核心批次重跑；收益不抵成本 |

## 验证

- 解析器与 2026-10-09 第三方表在 **91 个重叠地址上日期零差异**；新增 3 个地址的日期正是
  冻结日 2026-10-28。
- 覆盖：94/94 个匹配地址、193/193 个匹配事件有日期；`matched_rows_without_sanctions_date = 0`。
- `--check`：matches 重建一致；审计 67/67（新增 M6）；材料包措辞核对 32/32。
- 交付结果：timing-gap 子集 USDT 95 / USDC 94；中位数 0 天；同日占比 0.660714 / 0.656716；
  负值 3 / 0；`>365` 天 36 / 27。

## 后果

- 匹配层 `data_digest` 变化（`2d8cbf6b…c3b6`），下游 comparison / restricted / materials 全部
  重生成；核心集 `core_64c8…` 不变。
- 措辞约束新增一条边界：不得把实体级日期写成"地址上榜日"，不得把中位数 0 天读成
  "执法请求即时响应"。
- 若 OFAC 更新 XML，只需重跑 `scripts/parse_ofac_advanced.py` 并更新 `config/ofac_advanced.json`
  的 sha256 与日期，再重跑匹配层；`known_gap` 字段记录快照时差。
