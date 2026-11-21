# B 层缺口解决方案（OFAC 日期 / 交易所标签 / 转账日志）

> **状态（2026-11-18）**：O1 已落地。官方 `SDN_ADVANCED.XML` 的实体级 `EntryEvent` 日期已接入
> 匹配层（94/94 地址、193/193 匹配事件），timing gap 从"0 行"变为 USDT 95 / USDC 94 个
> episode 起点可报告。实现与取舍见 `docs/adr/0002-entity-level-designation-dates.md`。
> 交易所标签（L1–L3）与转账日志（T1）**已决定不做（2026-11-18）**：资金到交易所的比例
> 不在本数据集回答范围，理由见 `docs/restricted-subset-analyses.md` §8；exchange outflow
> 保持 `not_reportable`，不再作为待办。
> 下文保留为决策记录，推荐路线中的 O1 部分已完成。

B 层不是一件事，是三个独立缺口。任何一个补上，都能让对应指标从 `not_reportable`
变成“有覆盖范围的描述性统计”；不需要等三个全齐，也不需要改核心事件集。

| 缺口 | 现状 | 影响指标 | 可补性 |
| --- | --- | --- | --- |
| OFAC 逐地址日期 | **已补（2026-11-18）**：`sanctions_public_timestamp` 193 / 11,940 行有值，94/94 个匹配地址覆盖，实体级 `designation_date` | timing gap | 已完成（O1）；地址级首见日期仍需 O3 |
| 交易所标签（带日期） | 0 个来源、0 条标签，`not_attempted` | exchange outflow、带日期标签覆盖率 | 中：Dune 有 `added_date` / `consolidation_first_block_time`，但需付费或试用；免费源覆盖低 |
| 转账日志 | `data/raw/transfers/...` 不存在 | exchange outflow 的分子 | 高：归档 `eth_getLogs` 或 BigQuery 都能补，只对标注子集做 |

## 1. OFAC 日期

### 试点结果（已用 94 个匹配地址实测）

数据：SanctionsAI 从 OFAC SDN Advanced XML 解析的地址表（2026-10-09 快照，CC BY 4.0，
`data/raw/ofac/advanced/ofac_crypto_wallets_sanctionsai_2026-10-09.csv`）。

- 94 个匹配地址中 **91 个有实体指定日期**（41 个实体），3 个不在该快照。
- 以“实体指定日 → 冻结日”计算，91 个地址的分布：

| 差值 | 地址数 | 读法 |
| --- | --- | --- |
| 冻结早于指定（负值） | 3 | 发行人先冻结、OFAC 后指定；单独归类，不当作“负延迟” |
| 同一天 | 56 | 指定与冻结同日，协调行动的直接候选 |
| 1–7 天 | 1 | — |
| 8–90 天 | 0 | — |
| 91–365 天 | 4 | 地址可能是后来追加的 |
| > 365 天 | 27 | 实体早年指定、地址后来才上榜；不能读成响应延迟 |

这个分布说明两件事：① 官方实体日期足以支撑一个**描述性**的 “public designation → freeze”
统计，且同一天聚类本身就是有意义的发现；② 不能把它写成“地址被制裁后多久冻结”，因为
OFAC 批量文件**不发布每个地址的添加日期**，27 个长尾就是证据。

### 方案

| 方案 | 数据与日期口径 | 覆盖 | 成本 | 有效性/风险 |
| --- | --- | --- | --- | --- |
| **O1 SDN Advanced XML 实体指定日期** | `SDN_ADVANCED.XML` 的 `EntryEvent` 原始发布日期；`basis = sdn_advanced_entry_event`，精度 day | 91/94 地址（试点） | 2–4 小时（已有解析表；官方 XML 120MB 可复核） | 官方、公有领域；但日期是实体级，不是地址级 |
| **O2 OpenSanctions first_seen** | 每个钱包实体的 `first_seen`（首次被 OpenSanctions 观测到）；`basis = opensanctions_first_seen` | 需实测，预计 60–80% | 2–3 小时（API key、94 次查询） | 地址级、日期更接近“上榜”而非“实体成立”；非官方，依赖其抓取频率 |
| **O3 历史 SDN 快照差分** | 用 Wayback/OFAC delta 文件逐月快照，取地址首次出现；`basis = first_observed_in_sdn_snapshot`，精度 month/day | 理论上接近 100%（2018 起） | 1–2 天（下载 70+ 快照、diff、校验） | 最接近“地址级首见”，可复现；月粒度，需处理快照缺口 |
| **O4 OFAC Recent Actions 逐条** | 解析 2018–2026 的 action 页面，取每个地址对应的行动日期 | 取决于页面格式，可能 50–80% | 1–2 天 + 人工核对 | 日期最准；不可扩展，适合做 O1/O2 的验证样本 |
| **O5 不补** | 保持 `not_reportable`，在论文写清边界 | 0 | 0 | 最诚实，但少一个结果章节 |

**推荐**：O1 做主口径，O2 做地址级敏感性；如果时间允许，O3 升级为主口径、O1 退为上下文。
O4 只抽 10–20 个地址做验证，不铺开。理由：O1 已经把 193 个事件中的 91 个地址变成可报告，
且同一天聚类（56 个）足以支撑一个描述性发现；O3 能把“实体日期”与“地址日期”的差异量化，
但工程量大，适合作为稳健性章节而不是前置条件。

**口径约束**（必须写进报告）：

- 指标名用 `public_designation_to_freeze_gap`，不用 `freeze_latency`；
- 负值单列 `freeze_before_designation`，不进入中位数；
- `>365 天` 单列 `address_added_later`，不与同日/短期混算；
- 所有统计标 `descriptive`，不推断私有执法请求速度；
- 每个地址记录 `sanctions_date_basis` 与 `precision`，没有日期就留空。

## 2. 交易所标签（带日期）

核心难点不是“谁是交易所”，而是“**在冻结发生之前**，这个地址是否已经能被证明是交易所地址”。
当前 Etherscan/Dune 的标签是“今天”的，直接用于 2020–2024 的事件就是前视偏差。

### 方案

| 方案 | 日期口径 | 覆盖 | 成本 | 有效性/风险 |
| --- | --- | --- | --- | --- |
| **L1 Dune `cex.deposit_addresses` + `cex.addresses`** | 存款地址用 `consolidation_first_block_time`（首次归集到 CEX 热钱包，行为证据）；交易所钱包用 `added_date` | 高（Dune 自建，覆盖主流 CEX 的存款地址） | 4–8 小时；Dune 免费版已改为只读，需 14 天试用或 Analyst $75/月 | 日期语义最接近“可证明的交易所控制”；许可为内部使用，不能转售标签，论文只报聚合 |
| **L2 交易所 PoR 公开地址表** | 每次 PoR 发布日（OKX 月度、Binance 定期） | 低（只有热/冷钱包，不含用户存款地址） | 1 天 | 免费、高置信、日期明确；覆盖不足以支撑“资金是否到交易所”的主结论 |
| **L3 公开标签快照 + 快照日** | DataDr69 2023-06-07 快照、eth-labels 当前快照；只用“快照日 ≤ 事件日”的标签 | 低–中 | 0.5 天 | 免费；快照日只是“标签已存在”的下界，覆盖有限；当前快照不能用于过去 |
| **L4 付费归因（Chainalysis/TRM/Nansen/Arkham）** | 供应商的 first-seen / label date | 最高 | 合同/数千美元起 | 覆盖与日期最好；课程项目不现实 |
| **L5 不补** | 保持 `not_attempted` / `not_reportable` | 0 | 0 | 与反馈“说明答不了什么”一致 |

**推荐**：先做 **L2 + L3** 的免费子集，量化“到底能标到多少事件”；如果标到的地址少于 ~30 个，
就不值得为 outflow 单独买 Dune。若免费子集能标到 50+ 个事件，再用 **L1 的 14 天试用**补存款地址，
把子集扩大到可报告规模。L4 只在有导师经费时考虑。

**关键约定**：

- `label_date` 必须是“能证明交易所控制的最早日期”，不是标签被爬取的日期；
- 存款地址用 `consolidation_first_block_time`，并记录 `label_date_basis`；
- 事件早于 `label_date` 的，记 `not_applicable`，不进入 outflow 子集；
- 标签只进 `matches` 层，不进 `events`；不因匹配失败剔除事件；
- 论文只报聚合覆盖率和描述性统计，不发布逐地址标签（Dune 许可限制）。

## 3. 转账日志

有了标签，还需要冻结前窗口内该地址的 USDT/USDC 转出记录。代码要求一个固定快照：
`data/raw/transfers/ethereum_token_transfers.json`，含全局 block 窗口、每个地址的 in/out
行（`amount_raw`、`block_number`、`block_time_utc`、`tx_hash`、`log_index`）。

| 方案 | 做法 | 成本 | 风险 |
| --- | --- | --- | --- |
| **T1 本地归档 `eth_getLogs`** | 对标注子集逐地址、逐方向、按 7 天窗口拉 `Transfer`，按 topic 过滤，分块避开 1,000 条上限 | 4–8 小时；数百次 RPC 调用 | 端点配额与限速；但完全可复现，和现有缓存体系一致 |
| **T2 BigQuery 公共 Ethereum** | `token_transfers` 按 `token_address` + `from/to` + 时间过滤，导出固定 CSV/JSON | 2–4 小时；需 GCP 账号；每月 1TB 免费 | 数据集可能滞后/分区裁剪成本；导出要落 sha256 |
| **T3 Dune `tokens.transfers`** | 与标签同一个试用账号，SQL 导出 | 2–3 小时 | 导出配额与许可；不如 T1 可复现 |

**推荐**：T1 做主快照（与现有归档端点、缓存、摘要体系一致），T2 抽 10 个地址做交叉验证。
只对标注子集做，不试图覆盖 5,970 个事件；子集为空就如实 `not_reportable`。

## 4. 三条路线怎么选

### 路线 A：最小可行（2–3 天，免费）

1. O1：把 91 个地址的实体指定日期写入 `matches.sanctions_public_timestamp`，重跑 restricted；
   timing gap 从 0 变 91 个地址（193 个事件中约 47%）。
2. L2+L3：下载 PoR 地址表与 2023-06-07 快照，建立带日期的标签注册表；量化能标到多少事件。
3. 若标签子集 ≥30 个事件，用 T1 拉转账，跑 outflow；若不足，保持 `not_reportable` 并写清原因。

**产出**：一个 timing-gap 描述性章节 + 一个“标签覆盖不足”的量化说明。不改核心集，不买数据。

### 路线 B：稳健（1 周，Dune 试用 + 本地转账）

在 A 的基础上：

1. O2 交叉验证 O1；对 27 个 >365 天的地址改用 OpenSanctions first_seen 做敏感性。
2. L1 Dune 试用，补存款地址标签；标签子集目标 50–150 个事件。
3. T1 拉转账，跑 1h/6h/24h/72h/168h 窗口；报告窗口敏感性与覆盖率。
4. 若 O3 时间允许，做历史快照差分，把 timing gap 升级为地址级首见日期。

**产出**：两个 B 层指标都能报告（带覆盖范围），论文可以写“在受支持子集上”的描述性结果。

### 路线 C：不补（0 天）

保持现状，把“为什么不可报告”写成方法贡献。反馈明确要求说明数据不能回答什么，这条路线
在评审上并不减分，但会少一个实证章节。

## 5. 需要你决定的事

1. **是否接受 O1 的实体级日期**：它是官方日期，但不是地址级；27 个长尾必须单列。若不能接受，
   直接上 O3（多 1–2 天）。
2. **Dune 预算/试用**：免费版已只读；要么用 14 天试用（2,500 credits，够导标签），
   要么 Analyst $75/月。没有 Dune 就只能走 L2+L3，标签覆盖可能不足。
3. **转账用本地归档还是 BigQuery**：本地更可复现但慢；BigQuery 要 GCP 账号。
4. **时间**：路线 A 2–3 天，路线 B 约 1 周；论文/报告的时间节点决定选哪条。

## 6. 建议的默认选择

如果没有人反对，我建议按 **路线 A 先做 O1**：这是唯一一个已经验证过覆盖（91/94）、
不需要新账号、当天就能出数、且能把 193 个事件中的 91 个变成可报告的缺口。做完先看
timing-gap 分布是否值得写；再决定是否为 labels 走 Dune 试用。

O1 落地后的预期变化：

- `matches.sanctions_public_timestamp`：0 → 91 个地址有值（193 个事件中的对应行）；
- `timing_gaps.csv`：从 12 行全 `not_reportable` 变成有子集、有中位数/P90/同日比例；
- `subset_eligibility.csv`：timing-gap 子集的 USDT/USDC 事件数从 0 变为可核对的正数；
- 报告仍需标注 `descriptive`、`precision = day`、`basis = sdn_advanced_entry_event`，
  并把负值与 >365 天单列。

## 7. 数据源与链接

| 用途 | 来源 | 链接 | 许可/说明 |
| --- | --- | --- | --- |
| OFAC 官方 Advanced XML | OFAC Sanctions List Service | `https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN_ADVANCED.XML` | 美国政府作品，公有领域；`EntryEvent` 含实体原始发布日期 |
| OFAC 高级 XML 说明 | OFAC | `https://ofac.treasury.gov/system/files/126/sdn_advanced_notes.pdf` | 说明 `EntryEvent` 日期是“原始发布日期” |
| 地址级首见（备选） | OpenSanctions | `https://www.opensanctions.org/` | 非商业免费；`first_seen` 是观测日期，不是官方指定日 |
| 交易所地址与存款地址 | Dune `cex.addresses` / `cex.deposit_addresses` | `https://docs.dune.com/data-catalog/curated/cex-flows/addresses`；`.../deposit-addresses` | `added_date`；`deposit_first_block_time` / `consolidation_first_block_time` |
| 通用地址标签 | Dune `labels.addresses` | `https://docs.dune.com/data-catalog/curated/labels/address-labels` | `created_at` 是标签创建日，不是地址生效日 |
| 免费标签快照 | DataDr69 labeled_ethereum_addresses | `https://github.com/DataDr69/labeled_ethereum_addresses_dataset` | 快照 2023-06-07，无逐条日期 |
| 免费标签快照 | dawsbot/eth-labels | `https://github.com/dawsbot/eth-labels` | 当前快照，无日期；不能用于过去事件 |
| 交易所 PoR 地址表 | OKX / Binance 等 | 各自 PoR 页面 | 有发布日，只有热/冷钱包 |
| 全量转账 | BigQuery 公共 Ethereum | `bigquery-public-data.crypto_ethereum.token_transfers` | 每月 1TB 查询免费；需 GCP |
| 转账/标签 SQL | Dune `tokens.transfers` / `cex.flows` | `https://docs.dune.com/data-catalog/curated/cex-flows/overview` | 免费版只读；试用/付费才可查询 |

