# DATASET — Observable Stablecoin Freeze Events (Ethereum, USDT/USDC)

**Dataset card / 数据说明（快照至 2026-11-18，审计 67/67 通过）**

本数据集把 Ethereum 上 USDT 与 USDC 的公开冻结事件重建为可复核的事件表、余额表与验证表。
它只记录链上可观测的事实；冻结原因、执法请求时间、地址实体归属与跨链迁移不在数据中，
也不做推断。

- 批次：`core_64c8c7f9452291663926113a2b1ecc20`
- 契约：`docs/definitions.md`、`docs/data-dictionary.md`（契约 v1）
- 审计：67 项检查全部通过（`data/derived/dataset_audit.json`）
- 获取：第 5 节给出从固定快照重建全部交付表的完整命令

## 1. 范围

| 项 | 取值 |
| --- | --- |
| 链 | Ethereum（`chain_id = 1`） |
| 代币 | USDT `0xdac17f...1ec7`、USDC `0xa0b869...eB48`，均 6 位小数 |
| 事件类别 | `freezing` / `unfreezing` / `destruction`，互斥定义 |
| 观察期 | 区块 10,274,701（2020-06-16T04:39:08Z）– 26,404,630（2026-11-13T07:44:47Z） |
| 事件 | 5,970（USDT 4,786 / USDC 1,184） |
| 余额行 | 9,466（D1 4,077 / `at_event` 1,816 / D2 3,573） |
| 核对行 | 21,492，全部 `match` |
| 唯一地址 | 3,753（事件涉及地址） |
| 观察期外 | 90 条 USDT 事件，逐条在 `scope_excluded.csv`，不进入核心集 |

链范围只到 Ethereum。反馈只在“小型代表性样本”条款里写了 Ethereum，核心分析条款写的是
all retrievable events；Tron 仅登记为 `registered_pending`，未采集。因此本数据集**不能**表述为
“全部链”或“所有稳定币冻结”。

## 2. 文件

| 路径 | 行数 | 作用 |
| --- | --- | --- |
| `data/recon/core_64c8.../events.csv` | 5,970 × 56 | 一条日志一行；类别、地址、位置、版本、状态、D1 诊断值、缺失与资格 |
| `.../balances.csv` | 9,466 × 19 | D1 / `at_event` / D2 三类余额，逐行带查询摘要与来源 |
| `.../validation.csv` | 21,492 × 14 | 位置、版本、余额、人工参照、schema 五类核对 |
| `.../chain_registry.csv` | 2 | Ethereum `verified_sample`、Tron `registered_pending` |
| `.../coverage_gaps.csv` | 0 | 快照与独立检索的差集；本次为 0 |
| `.../scope_excluded.csv` | 90 | 观察期外事件，逐条给原因与决策版本 |
| `.../manifest.json` | — | 覆盖、误差、缺失、排除、摘要、输入 sha256 |
| `data/recon/match_e7a3.../matches.csv` | 11,940 | 每事件 × 2 层（`ofac_sdn` / `exchange_label`），带证据、实体级指定日与日期口径 |
| `.../missingness.csv` | 96 字段 | 逐字段空值计数与原因码 |
| `data/samples/sample_*.csv` | 6 / 11 / 12 / 25 | 小样本：事件、余额、匹配、验证，供不重跑全量时检查字段 |
| `docs/data-dictionary.{md,csv}` | 5 表 / 113 字段 | 字段、枚举、排除规则 R1–R9、可得性 A/B/C |
| `docs/validation-table.md` | — | 验证协议、样本结果、全量基线 |
| `docs/dataset-audit.md` | — | 67 项契约审计：分组、关键不变量、不能证明什么 |
| `docs/known-issues.md` | — | 7 项已登记缺口（G-01/G-02 已关闭）与验证入口 |
| `docs/manual-signoff.md` + `data/derived/manual_signoff_sample.csv` | 26 | 人工抽样签署说明与已签表（26 行全 `match`，2026-11-17） |

CSV 均有同列 `.parquet` 副本；两套逐行一致由 `audit_dataset.py` 的 F1 检查。

## 3. 质量指标

| 指标 | 结果 | 口径 |
| --- | --- | --- |
| event retrieval coverage | `1.000000`（5,970 / 5,970；区块级 4,980 / 4,980） | 分母为独立端点在观察期内按代币 × topic 全量检索的日志数 |
| balance reconstruction error | 9,482 / 9,482 完全一致，最大绝对误差 0 raw | 每条余额对照独立归档端点重算 |
| unverifiable share | `0.000000`（0 / 21,492；记录口径 0 / 15,436） | 无第二来源的核对行占比 |
| public-record match rate | `0.032328`（193 / 5,970；地址口径 0.025047） | OFAC SDN 快照字符串完全匹配，不做实体推断 |
| dated sanctions coverage | `1.000000`（193 / 193 匹配事件；94 / 94 地址） | 指定日来自 SDN Advanced EntryEvent，实体级、precision=day |
| 排除 | 0 条被排除事件；观察期外 90 条单独登记 | R1/R7/R8 均未触发 |

## 4. 组成

| | USDT | USDC |
| --- | --- | --- |
| `freezing` | 3,181 | 957 |
| `unfreezing` | 345 | 227 |
| `destruction` | 1,260 | 0（结构性零） |
| episode 起点 | 3,174 | 903 |
| 截止日仍冻结地址 | 2,867 | 706 |
| D1 合计（token） | 3,325,815,917.70 | 988,767,955.74 |
| D2 合计（token） | 2,352,542,587.68 | 961,694,776.41 |

`destruction` 对 USDC 是合约定义上的零，不是缺失，也不能与 USDT 放在同一分母上直接比较。
D1 与 D2 不相加、不互换：D1 是 episode 起点的冻结时余额，D2 是截止日仍冻结地址的余额。
D2 总体包含 29 个观察期前冻结、观察期内无事件的 USDT 地址，因此 D2 地址数与事件涉及地址数
不能相减对齐。

## 5. 获取与复现

当前交付就是 `data/recon/` 下五批目录（核心集、匹配层、比较、受限分析、支撑材料包），
按批次 id 直接取用，不需要先跑脚本。要从固定快照从零重建或核验，按下列顺序执行：

```bash
python scripts/parse_ofac_advanced.py             # 官方 SDN Advanced XML → 地址/指定日提取表
python scripts/validate_sample.py --check-scope   # 先验证、后范围决策
python scripts/reconstruct_core.py --workers 10   # 冷跑约 2h15m；检索缓存补齐后约 15 分钟
python scripts/reconstruct_matches.py             # 读核心集，写独立目录，全程离线
python scripts/reconstruct_comparison.py
python scripts/reconstruct_restricted.py
python scripts/reconstruct_materials.py
python scripts/audit_dataset.py                   # 67 项契约审计
```

每个入口都有 `--check`（不联网、不写盘、重算摘要逐行比对）。退出码：0 通过、1 运行失败、
2 数据摘要不符、3 前置条件未满足、4 离线复核不一致。

已知摘要：`data_digest = 49db21c8…2fd5`、`artifact_digest = fdea03ea…7b01`（核心集）；
`2d8cbf6b…c3b6`（匹配层）；`4768622b…4ec8`（比较）；`dedfccd8…80f1`（受限分析）；
`34432a2c…3f8e`（支撑材料）。完整值见各批次 `manifest.json`。

## 6. 审计结论

`audit_dataset.py` 67/67 通过。主表当前口径：

- D2 总体 **3,573** 行，含 29 个观察期前冻结、观察期内无事件、截止日仍冻结的 USDT 地址
  （+62,418.456433 USDT）。
- 1,260 条 `destruction` 的 `blacklist_state_before/after` 取事件位置状态，全部
  `blacklisted`、`blacklist_transition = false`。
- 193/193 个匹配事件、94/94 个地址带官方 SDN Advanced XML 的实体级指定日
  （`designation_date` / `day`），M6 检查逐行溯源。

人工签署已按 26 行分层抽样完成（`pbn`，2026-11-17，26/26 `match`，`check_signoff.py --validate`
退出码 0），全量验证表仍为脚本跨端点核对。仍存在的 5 项已登记缺口（交易所标签、215 条低置信版本、
Tron、观察期外 90 条、多事件交易为 0）见 `docs/known-issues.md` §2。

## 7. 使用边界

- 只做 ex post、聚合的描述性分析；不用于实时或“抢在冻结前转移资金”的指引。
- 不推断冻结原因、私有执法请求的响应速度、规避行为或执法有效性。
- 不把有限匹配子样本的结论外推到全部冻结活动；`unmatched` 不等于“未受制裁”。
- 地址是链上伪匿名标识；本数据集不做实体归属，也不合并跨链身份。

### 7.1 明确不回答的问题

1. **冻结前有多少资金到达交易所**（2026-11-18 决定不答）。交易所不公开充值地址；唯一能给出「充值地址 + 首次归集时间」的来源需要付费，免费热钱包名单只覆盖最后一跳，免费标签快照又没有逐条日期。没有可信的对手方标签，任何数字都会把“不知道”写成“没有”。细节见 `docs/restricted-subset-analyses.md` §8。
2. 冻结的真实原因、执法请求的提交与到达时间、地址背后的实体。
3. 资金是否通过桥或 wrapped token 迁移到其他链，以及这种迁移的规模。

## 8. 许可与引用

链上原始数据来自公开账本；数据集的整理、字典与脚本由项目组（PENG Beining、CHANG Juxiao、
LI Mingzheng）完成。**许可尚未指定**，在项目负责人确定前仅供内部研究与评审使用，不对外分发。

引用建议：

> PENG Beining, CHANG Juxiao, LI Mingzheng. *Observable Stablecoin Freeze Events: A Reproducible
> Ethereum Dataset with Documented Gaps*. MSBD5017 project, 2026.
