# 重建流程的复现说明（工单 02 / 03 / 04 / 05 / 06 / 07 / 08 / 09）

一个流程，一个验证边界：输入是固定的链上日志快照 + 归档状态查询，输出是事件记录、两类余额与验证表。
工单 02 重建单笔事件；工单 03 把同一入口扩展到交易作用域与完整生命周期（unfreezing、destruction、
多日志交易、合约版本切换）；工单 04 在代表性样本上报告覆盖、余额误差与不可核验比例，并据此固定研究期与
样本范围。解析步骤不单独验证（`docs/definitions.md`、`docs/data-dictionary.md` v1 为准）。
工单 06 在核心集之外附加匹配层与缺失报告，不改动上述任何表；工单 08 只在证据充分的子样本上
做 timing gap / outflow / frozen balance 描述性统计，写在独立批次目录。

## 1. 交付对象

| 项 | 值 |
| --- | --- |
| 事件 | USDT `freezing`，区块 26,292,598，`log_index = 60` |
| 交易 | `0x5e520c7bff1f053a6a319397b1518f15923c23329e0639b6fc66a48f47772bd4` |
| `event_id` | `ev_050309e736cfaf844e9451cfaec78302` |
| 被冻结地址 | `0x12ea80238c7d805d003ce793594e92d84a6f4e8d` |
| 合约版本 | `TetherToken`（`contract_code_hash 0xb44fb4e9…`，非代理） |
| 事件时间 | `2026-10-28T17:02:47Z`，`event_time_precision = second` |
| D1 `at_freeze` | `events.balance_pre_event_raw` = 5,844,309 raw = 5.844309 token（`balance_block_number 26,292,597`，调整量 0）；`balances` 的 `at_freeze` 行记该次 `eth_call` 原值，本例两者相同 |
| D2 `at_study_cutoff` | 5,844,309 raw = 5.844309 token（`study_cutoff_block 26,404,630`，截止时仍 `blacklisted`） |
| 状态转移 | `not_blacklisted → blacklisted`，`blacklist_transition = true` |
| 验证 | 5 条检查全部 `match`，参照来自独立端点与本地 OFAC 快照 |

产物目录 `data/recon/ev_050309e736cfaf844e9451cfaec78302/`：`events.csv`、`balances.csv`、`validation.csv`（各附 `.parquet`）与 `manifest.json`。列集合由 `docs/data-dictionary.csv` 给出，写出前断言，无字典外字段。

### 1.1 生命周期案例（工单 03）

案例清单与事先声明的预期在 `config/lifecycle_cases.json`；每个案例独立运行、独立落盘到 `data/recon/lc_<case>/`。

| 案例 | 覆盖 | 事件 / 余额 / 验证行 | `data_digest` |
| --- | --- | --- | --- |
| `unfreeze` | USDT 冻结 → 解冻 → 重新冻结（两段 episode），截止日仍冻结 | 3 / 4 / 32 | `cc4edae2…c466` |
| `destruction` | USDT 冻结 → 重复冻结（R9）→ 销毁全部余额，截止日余额 0 | 3 / 3 / 33 | `a2bf2421…0890` |
| `tx_scope` | 3 条日志的交易中只有已登记事件成行 | 1 / 2 / 20 | `c84826fa…49ad` |
| `version_switch` | USDC 代理在 V1 与 V2_2 各一次冻结，另定位两次实现切换 | 2 / 4 / 33 | `93ac1b88…e4d0` |
| `unfreeze_usdc` | USDC 解冻后截止日不再冻结，无 D2 行 | 1 / 1 / 17 | `018d6127…825a` |

语义要点（对应 `docs/definitions.md`）：

- `unfreeze`：`at_freeze` 只出现在两段 episode 的第一条 `freezing`（18,693,913 / 23,141,909），`at_event` 只出现在解冻行（22,766,755），三者余额同为 8,123（raw），截止日仍 `blacklisted`，故 D2 = 8,123。
- `destruction`：第二次冻结（18,749,899）状态未变，按 R9 计入覆盖但不产生 D1；销毁行 `at_event` = 58,148,619,145 raw，与 `destruction_amount_raw` 相等（销毁不 emit `Transfer`），截止日 `blacklisted` 但余额为 0。
- `tx_scope`：同一笔交易共 3 条日志，黑名单事件在中间（`log_index 60`），另两条来自别的合约；事件表只有 1 行，`tx_event_count = 1`。
- `version_switch`：两次冻结分别在 `FiatTokenV1`（10,274,701）与 `FiatTokenV2_2`（25,705,174），地址均取自 topic，版本随事件记录；二分定位到实现切换在区块 10,743,414（V1 → 未登记实现）与 18,963,716（未登记实现 → V2_2），两侧代码哈希由独立端点复核。
- `unfreeze_usdc`：USDC 解冻行 `at_event` = 23,409,304,687 raw；截止日状态为 `not_blacklisted`，因此不产生 `at_study_cutoff` 行（与 `docs/definitions.md` 3.3 的总体定义一致）。

### 1.2 代表性样本验证（工单 04）

`scripts/validate_sample.py` 把 5 个案例合成一份样本交付：`data/recon/vs_3b3a9d4db71b1186ad8204411a891f41/{events,balances,validation}.csv|.parquet` 与 `manifest.json`。

| 项 | 值 |
| --- | --- |
| 事件 / 余额 / 验证行 | 10（`freezing` 7、`unfreezing` 2、`destruction` 1）/ 14（`at_freeze` 6、`at_event` 3、`at_study_cutoff` 5）/ 161，全部 `match` |
| `event retrieval coverage` | 13/13 日志、6/6 槽位，区块级日志集合一致 6/6 |
| `balance reconstruction error` | 15/15 完全一致，最大绝对误差 0 raw |
| `unverifiable share` | 0 / 161 |
| `data_digest` / `artifact_digest` | `c69ad3b4…5f63` / `3c5e407f…0e31` |

产物同时写入 `metrics`（三项指标的分子分母与分桶结果）与 `scope_inputs`（快照内各代币首个事件区块、分类计数、多事件交易数、截止区块哈希），供 `docs/study-scope.md` 引用；决策本身不在本脚本内产生。

### 1.3 核心事件集（工单 05）

`scripts/reconstruct_core.py` 在同一观察期内重建**全部可检索事件**，不做匹配过滤。观察期从
`config/study_scope.json` 读取，命令行给的边界必须与决策一致，否则拒绝运行。

产物 `data/recon/core_<id>/`：

| 文件 | 内容 |
| --- | --- |
| `events` / `balances` / `validation` / `chain_registry` 的 `.csv|.parquet` | 四张字典表；列集合写出前断言 |
| `coverage_gaps.csv` | 快照与独立全量检索的差集，逐条（R1 占位与快照独有都记） |
| `scope_excluded.csv` | 观察期外事件，逐条记录排除原因 |
| `manifest.json` | 三项全量指标、排除计数、链集合、输入摘要与复现命令 |

排除汇总另写 `data/derived/exclusions.csv`（R1/R7/R8 的实际排除行）。排除不改动主表行，
分母（覆盖率、unverifiable share）包含被排除项。方法与限制见 `docs/core-event-set.md`。

本次交付（`core_64c8c7f9452291663926113a2b1ecc20`）：

| 项 | 值 |
| --- | --- |
| 事件 / 余额 / 核对 / 链登记 | 5,970 / 9,466 / 21,492 / 2 |
| 覆盖 | 5,970 / 5,970 独立日志，区块级一致 4,980 / 4,980 |
| 余额误差 | 9,482 / 9,482 完全一致，最大误差 0 raw |
| 不可核验占比 | 0 / 21,492 |
| 排除 | 0；观察期外 90（USDT，逐条登记）；匹配剔除 0 |
| 摘要 | `49db21c8…2fd5` / `fdea03ea…7b01` |

冷跑（无缓存）约 2 小时 15 分；检索缓存补齐后 15 分钟完成，两个摘要不变。`--check` 可离线复核
摘要、列集合、链登记、排除与覆盖算术，并从固定日志快照重算观察期内应有的事件集合与交付表对照。

### 1.4 匹配层与缺失报告（工单 06）

`scripts/reconstruct_matches.py` 读工单 05 的核心集，把 OFAC 地址匹配与历史交易所标签作为**独立一层**
写进单独目录，核心四表只读不写。产物 `data/recon/match_<id>/`：

| 文件 | 内容 |
| --- | --- |
| `matches.csv|.parquet` | 一个事件 × 一个匹配层一行（`ofac_sdn`、`exchange_label`），带匹配证据与标签日期 |
| `missingness.csv` | 逐表逐字段的空值计数与原因码，并标出字典 `required = yes` 的字段 |
| `report.json` | public-record match rates、缺失、标签日期、带日期子集规模与措辞约束 |
| `manifest.json` | 输入摘要、分离声明、计数、规则自检结果与复现命令 |

本次交付（`match_e7a32e2a4120dccb5d5cfe1edf360766`）：

| 项 | 值 |
| --- | --- |
| `matches` 行 | 11,940（5,970 事件 × 2 层） |
| OFAC 匹配 | 193 事件 / 94 地址（事件口径 `0.032328`、地址口径 `0.025047`） |
| 交易所标签层 | `not_attempted` 5,970 行：无带日期的历史标签来源，比率 `not_reportable` |
| 日期 | 匹配行 `label_date = 2026-11-09`（`list_snapshot_date`），`sanctions_public_timestamp` 0 行 |
| 缺失 | 96 个字段中 23 个有可选空值，必填缺口 0 |
| 摘要 | `data_digest = 2d8cbf6b…c3b6`；`artifact_digest` 含 `matched_at_utc`，逐次运行都会变 |

`--check` 不访问网络：从核心 `events` 重建整张匹配表逐行比对、重算 `missingness.csv` 与 `report.json`、
重跑规则自检，并核对核心四表摘要未被改动。方法与限制见 `docs/matching-and-missingness.md`。

### 1.5 同口径 issuer 比较与敏感性（工单 07）

`scripts/reconstruct_comparison.py` 读核心集与匹配层，先核验同一观察期、同一事件定义、同一缺失
处理、同一余额定义与同一匹配层，任一不通过即退出并且不写产物。通过后写 `compare_<id>/`：

| 文件 | 内容 |
| --- | --- |
| `comparison.csv|.parquet` | 一个指标 × 一个 issuer 一行，带 `coverage_scope`、`status`、`balance_reference` 与 `statement_type` |
| `differences.csv|.parquet` | 每指标的 `USDT − USDC`，不可比时给原因而不是数值 |
| `sensitivity.csv|.parquet` | `timestamp_precision`（second / day）与 `missing_labels`（drop / retain）的逐项对照 |
| `report.json` | 五项前置条件、参数网格、描述性结论、必须存在的措辞边界与规则自检 |
| `manifest.json` | 输入摘要、参数、前置条件、计数、摘要与复现命令 |

本次交付（`compare_9f973ff43a20c53a9fdf9bfac13675be`）：

| 项 | 值 |
| --- | --- |
| 比较 / 差值 / 敏感性行 | 38（19 指标 × 2）/ 19（17 可比、2 不可比）/ 26 |
| 前置条件 | 五项全部通过；过滤丢弃 USDT 0、USDC 0 |
| 覆盖范围 | `core_full` 13 个 + USDT destruction（共 27/38 行）、`core_closed_episodes` 4 个（8 行）、`restricted_subset` 1 个（2 行，不可报告）、`structural_zero` 1 个（1 行） |
| 摘要 | `data_digest = 4768622b…4ec8`；`artifact_digest = 61b93348…0804` |

`--check` 不访问网络：重算前置条件、逐行重建三张表、重算报告与规则自检，并核对核心四表与
`matches` 摘要未被改动。方法与限制见 `docs/issuer-comparison.md`。

### 1.6 受限子样本分析（工单 08）

`scripts/reconstruct_restricted.py` 读核心集与匹配层，只在两个子集上出数：timing-gap 子集
（episode 起点 × OFAC matched × 有公开制裁日期）与 exchange-outflow 子集（episode 起点 ×
带日期交易所标签）。写 `restrict_<id>/`：

| 文件 | 内容 |
| --- | --- |
| `subset_eligibility.csv` | 逐级筛选：规则、进/出/剩余事件、淘汰原因码 |
| `timing_gaps.csv` | 公开制裁时间差（天）的中位数 / P90 / 最小 / 最大 / 同日比例 |
| `exchange_outflows.csv` | 冻结前窗口内有转出的占比与金额合计（基线 24h） |
| `frozen_balances.csv` | 两个子集各自的 D1 合计与中位数（`balance_reference = at_freeze`） |
| `outflow_window_sensitivity.csv` | `outflow_window`（1h / 6h / 24h / 72h / 168h）逐设定对照 |
| `report.json` | 子集规则、规模、分离声明、描述性结论、措辞边界与规则自检 |
| `manifest.json` | 输入摘要、参数、计数与复现命令 |

本次交付（`restrict_4c003f027d634313a28140234c40a510`）：

| 项 | 值 |
| --- | --- |
| 子集 | timing-gap USDT 95 / USDC 94（OFAC matched 95 / 94，全部有实体级指定日）；exchange-outflow 0 / 0（标签层 `not_attempted`） |
| 表行 | 16 / 16 / 6 / 8 / 20（subset / timing / outflow / balance / sensitivity） |
| 摘要 | `data_digest = dedfccd8…80f1`；`artifact_digest = 4223a79a…0eff` |

五个表名与工单 07 的 `comparison` / `differences` / `sensitivity` 不重名，`--check` 断言批次目录
里不出现同名 CSV，并重算筛选链算术。方法与限制见 `docs/restricted-subset-analyses.md`。

### 1.7 支撑材料包与措辞审查（工单 09）

`scripts/reconstruct_materials.py` 读四个上游批次与四项支撑材料（样本、字典、脚本、验证表），
只做索引与核对，不采集、不重算事件与余额。写 `materials_<id>/`：

| 文件 | 内容 |
| --- | --- |
| `materials_index.csv` | 逐组件：路径、行数、字节、`sha256`、离线核对命令与它能证明什么 |
| `field_availability.csv` | 逐字段（116）：可得性层 A/B、分析角色、两个 issuer 的非空计数与 `value_share` |
| `metric_coverage.csv` | 逐指标行（110）：覆盖范围、报告范围、证据层与 `causal_inference_permitted` |
| `wording_checks.csv` | 措辞约束 × 材料（32 行）：证据类型、证据指针、观察值与是否通过 |
| `chain_scope.csv` | 链登记、采集状态、反馈的限定范围与待确认项纳入后的影响 |
| `report.json` | 材料齐备性、A/B/C 分层、指标范围、措辞核对、链范围、结论、caveat 与规则自检 |
| `manifest.json` | 上游摘要、计数、规则自检结果与复现命令 |

本次交付（`materials_fe2495c8b40a1d848c29802a422bbb22`）：

| 项 | 值 |
| --- | --- |
| 组件 / 字段 / 指标 / 措辞行 / 链 | 32（样本 4、字典 2、脚本 10、验证表 8、视图 8）/ 116 / 110 / 32 / 2 |
| 可得性分层 | A 81（events 56、balances 19、chain_registry 6）、B 35（matches 21、validation 14）、C 0 个字段 |
| 指标报告范围 | `all_events` 43、`core_subpopulation` 12、`supported_subset` 53、`definitional_zero` 2 |
| 仅受支持子集可报的指标 | 14 个（`timing_gap_*` 8、`outflow_*` 3、`frozen_balance_at_freeze_*` 2、`exchange_label_dated_coverage` 1） |
| 措辞核对 | 8 条约束 32 行，未通过 0；数据抽样签署 26/26 `match`（2026-11-17），正文语义审查仍属人工 |

`--check` 不访问网络：重算五张表逐行比对、重算报告与规则自检、核对上游四个批次的摘要未变，
并拒绝任何 `passed = no` 的措辞行。方法与限制见 `docs/supporting-materials.md`。

## 2. 运行

依赖见 `requirements.txt`（Python 3.13 + `.venv`）。需要 `.env` 中的 `ETHEREUM_ARCHIVE_RPC_URL`；可选 `ETHEREUM_ARCHIVE_ENDPOINT_LABEL`（默认 `tenderly_mainnet_gateway`）、`ETHEREUM_REFERENCE_RPC_URLS`（逗号分隔，默认 `https://eth.drpc.org`、`https://eth-mainnet.public.blastapi.io`）、`ETHERSCAN_API_KEY`（存在即启用 Etherscan v2 参照端）、`ETHEREUM_REFERENCE_USE_ETHERSCAN=0`（显式停用）。

参照链顺序：`reference:api.etherscan.io` → `reference:eth.drpc.org` → `reference:eth-mainnet.public.blastapi.io`。前一个端点明确回绝某方法（Etherscan 的历史 `eth_getCode`）时，该否定结果也落缓存，再顺延到下一个端点，重放因此完全离线。

```
python scripts/reconstruct_single_event.py
```

参数：`--tx-hash`、`--log-index`（默认即上表这笔）、`--token`（默认 `USDT`，与快照命中的合约不一致时报错）、`--cutoff-block`（默认取日志快照 `end_block`，不随运行时刻漂移）、`--refresh`（忽略缓存重抓）、`--expect-data-digest`（比对数据摘要，不一致退出码 2）、`--json`（打印完整 manifest）。

生命周期案例（同一入口，交易作用域，一个案例可含多笔交易）：

```
python scripts/reconstruct_lifecycle.py --case all
python scripts/reconstruct_lifecycle.py --case unfreeze --expect-data-digest cc4edae20e07d3bf4254d28ce0ee23cdff9db325b348fa9f533e7efb064dc466
```

`--case` 取 `unfreeze` / `destruction` / `tx_scope` / `version_switch` / `unfreeze_usdc` 或 `all`；每个案例可单独运行，互不依赖。
`--expect-data-digest` 只能与单案例一起使用。退出码：0 通过；1 案例运行失败（端点不可用、快照中找不到交易）；
2 数据摘要不符；3 验证表出现 `mismatch` 或案例预期不符。案例级新增的核对行不改变单笔入口的输出。

代表性样本验证与研究期决策：

```
python scripts/validate_sample.py
python scripts/validate_sample.py --expect-data-digest c69ad3b4699d166ee6261d731f931f7d05b6221ed7e948efcf93f6b07dd15f63
python scripts/validate_sample.py --check-scope
```

`--check-scope` 不抓取数据：重算交付 CSV 的数据摘要，并核对 `config/study_scope.json` 引用的批次、摘要、指标、观察期边界、样本清单与 `supported_by` 证据路径。退出码：0 通过；1 运行失败；2 数据摘要不符；3 核对表出现 `mismatch`；4 范围决策与产物不一致或执行顺序不成立。

核心事件集（观察期取自 `config/study_scope.json`）：

```
python scripts/reconstruct_core.py --workers 12
python scripts/reconstruct_core.py --expect-data-digest <hex>
python scripts/reconstruct_core.py --max-events 200 --workers 8   # 冒烟，partial_run = true
```

`--workers` 只影响速度，不影响产物：同一请求在进程内按请求键串行化，缓存文件原子替换。
端点配速由 `ETHEREUM_RPC_REQUESTS_PER_SECOND`（默认 `8`）与
`ETHEREUM_ETHERSCAN_REQUESTS_PER_SECOND`（默认 `3`）控制；限流按暂时失败退避重试，不写否定缓存。
`--check` 不访问网络：重算摘要与列集合，并用**固定日志快照**重算观察期内应有的事件集合与交付事件表对照。
退出码：0 通过；1 运行失败；2 数据摘要不符；3 核对表出现 `mismatch`（含覆盖缺口）；4 `--check` 不一致。

匹配层与缺失报告（核心集必须先存在；匹配不改动核心集）：

```
python scripts/reconstruct_matches.py
python scripts/reconstruct_matches.py --check
python scripts/reconstruct_matches.py --expect-data-digest <hex>
```

`--core-id` 可显式指定核心集批次；缺省按输入摘要派生，不猜"最新目录"。`--labels` 指定标签注册表
（默认 `config/exchange_labels.json`）。全部输入都在本地，运行不需要网络。退出码：0 通过；
1 运行失败（核心集缺失、注册表有非法条目、规则自检失败）；2 数据摘要不符；4 `--check` 不一致。

同口径 issuer 比较（核心集与匹配层都必须先存在；比较不改动它们）：

```
python scripts/reconstruct_comparison.py
python scripts/reconstruct_comparison.py --check
python scripts/reconstruct_comparison.py --expect-data-digest <hex>
python scripts/reconstruct_comparison.py --timestamp-precision day
python scripts/reconstruct_comparison.py --missing-labels retain_unlabelled
```

`--timestamp-precision` 与 `--missing-labels` 参与批次 id：换参数写到另一个 `compare_<id>/`，
两次运行可并排对照。`--check` 重算五项前置条件、三张表、报告与规则自检，不写盘。退出码：
0 通过；1 运行失败；2 数据摘要不符；3 比较前置条件未满足（不写产物）；4 `--check` 不一致。

受限子样本分析（核心集与匹配层都必须先存在；不读工单 07 的比较表）：

```
python scripts/reconstruct_restricted.py
python scripts/reconstruct_restricted.py --check
python scripts/reconstruct_restricted.py --expect-data-digest <hex>
python scripts/reconstruct_restricted.py --outflow-window 72h
python scripts/reconstruct_restricted.py --transfers <path>
```

`--outflow-window` 参与批次 id；`--transfers` 指向 `Transfer` 日志快照（默认
`data/raw/transfers/ethereum_token_transfers.json`，不存在即按不可报告处理）。`--check` 重算
五张表、报告与规则自检，核对筛选链算术与分离性，不写盘。退出码：0 通过；1 运行失败；
2 数据摘要不符；4 `--check` 不一致。

支撑材料包与措辞审查（四个上游批次都必须先存在；本包只读不写回流）：

```
python scripts/reconstruct_materials.py
python scripts/reconstruct_materials.py --check
python scripts/reconstruct_materials.py --expect-data-digest <hex>
python scripts/reconstruct_materials.py --compare-id <batch> --restrict-id <batch>
```

批次 id 由上游四个批次摘要、字典、研究期决策与四份样本文件的摘要派生；上游任一摘要变化即
换新目录。候选批次多于一个时要求显式 `--match-id` / `--compare-id` / `--restrict-id`，不猜
"最新目录"。`--check` 重算五张表、报告与规则自检，核对上游摘要未变，并拒绝任何
`passed = no` 的措辞行。退出码：0 通过；1 运行失败（材料缺失、上游批次缺失或存在多个候选）；
2 数据摘要不符；4 `--check` 不一致。

## 3. 输入与便宜性

| 输入 | 位置 | 凭据 |
| --- | --- | --- |
| 黑名单日志快照（6,060 条，区块 4,634,748–26,404,630） | `data/raw/logs/ethereum_blacklist_logs.json` | `raw_log_sha256 = 10fdb389…c3a1eb`，取快照内 `retrieved_at_utc` |
| 历史状态 `eth_call` | `data/raw/state_calls/<query_id>.json` | `query_id` 由请求体的规范化 JSON 哈希派生，重跑命中同一文件 |
| 其余 RPC 原始响应（区块头、收据、代码、区块内日志、参照端点 `eth_call`） | `data/raw/chain_probes/<probe_id>.json` | `probe_id` 含端点标签，参照与采集互不覆盖；端点明确回绝的方法记 `error` 否定缓存 |
| 合约指纹表 | `config/contracts.json` | 版本标签绑定 `contract_code_hash`，哈希对不上则 `contract_version_confidence = low` |
| OFAC 快照 | `data/raw/ofac/` | `sdn_manifest.json` 的 `csv_sha256`、`publish_date` |
| 样本槽位声明（S1–S6，人工核对记录） | `data/raw/sample_records.json` | 声明事件、参照余额、代码哈希与截止日取值；`sha256` 记入验证 manifest |
| 案例与事先声明的预期 | `config/lifecycle_cases.json` | 事件类别、状态转移、版本标签、D1/D2 期望值；`sha256` 记入验证 manifest |
| 研究期与样本范围决策 | `config/study_scope.json` | 引用验证批次、摘要与指标；`--check-scope` 校验 |
| 历史交易所标签注册表（工单 06） | `config/exchange_labels.json` | 每条标签须带 `label_date` 与 `label_date_basis`；当前 0 个来源、0 条标签，故标签层记 `not_attempted` |

余额只来自 `STATE_AT_BLOCK_END(b)`：D1 用 `block_number − 1` 的 `balanceOf` 加同区块更早 `Transfer` 的带符号调整；D2 用 `study_cutoff_block` 的 `balanceOf`。没有用当前状态倒推，也没有用 `Transfer` 累加充当独立口径。

## 4. 复现判据

manifest 记录两个摘要：

- `data_digest`：事件记录、两类余额、验证表三类数据的规范化摘要，剔除 `source_retrieved_at_utc` / `queried_at_utc` / `reference_retrieved_at_utc` / `reviewed_at_utc` 四个抓取时刻字段。同一输入必须同值。
- `artifact_digest`：落盘 CSV 的字节摘要，含抓取时刻。

已验证的三种运行（本机，同一输入）：

| 运行 | 条件 | `data_digest` | `artifact_digest` |
| --- | --- | --- | --- |
| A | 命中缓存 | `f259582b…e03b3` | `4c27c148…dcf` |
| B | 命中缓存（A 之后立刻重跑） | 同 A | 同 A（逐字节一致） |
| C | `--refresh`，全部重新抓取（含参照端点） | 同 A | 不同（仅抓取时刻变化） |
| D | 命中缓存 + `ETHEREUM_ARCHIVE_RPC_URL` 指向不可达地址 | 同 A | 同 A（证明重放不需要网络） |

即：重放不改变记录与余额；换一次抓取只改变取证时刻，不改变数据。`--expect-data-digest f259582bd3497e6132e1fc19bd052e253aca03dbe45dd1b71bd0e94c161e03b3` 可在评审环境直接复核这一条。

工单 03 复核时修正了一条参照端行为：Etherscan v2 的 `proxy eth_call` 会忽略历史区块参数并返回 latest 状态
（实测：USDT `balanceOf` 在区块 10,000,000 与 latest 返回同一数值，而归档端点在该区块返回 0），因此历史余额
不再由它提供，`balance_state` 的参照回落到 drpc。修正后 `data_digest` 由 `207f25f3…e646` 变为 `f259582b…e03b3`，
差异只在 `balance_state` 的 `reference_source`（Etherscan → drpc）；数值与 5 条 `match` 未变。

生命周期案例的四种运行（同一输入）：

| 运行 | 条件 | 结果 |
| --- | --- | --- |
| E | `--case all` 连续两次 | 四个案例 `data_digest` 逐一相同 |
| F | `--case tx_scope --refresh` | `data_digest` 同 E，`artifact_digest` 变化（仅抓取时刻） |
| G | `--case tx_scope` + `ETHEREUM_ARCHIVE_RPC_URL` 指向不可达地址 | `data_digest` 同 E（重放不需要网络） |
| H | `--case <x> --expect-data-digest <digest>` | 退出码 0；错值退出码 2 |

代表性样本的三种运行（同一输入）：

| 运行 | 条件 | 结果 |
| --- | --- | --- |
| I | `validate_sample.py` 连续两次 | `data_digest` 与 `artifact_digest` 逐一相同；`generated_at_utc` 不被重放改写（取证时刻固定为证据首次产生的时刻） |
| J | 仅 `ETHEREUM_ARCHIVE_RPC_URL` 指向不可达地址 | 摘要同 I（全部证据来自 `data/raw/` 缓存，重放不需要网络） |
| K | `--check-scope` | 退出码 0；把决策的起点、指标或时刻改错则退出码 4 |

核心集的三种运行（同一输入）：

| 运行 | 条件 | 结果 |
| --- | --- | --- |
| L | `reconstruct_core.py --workers 10` 重放 | `data_digest = 49db21c8…2fd5`、`artifact_digest = fdea03ea…7b01`，与交付批次逐字节相同；`generated_at_utc` 保持 `2026-11-17T12:47:27Z` |
| M | 仅 `ETHEREUM_ARCHIVE_RPC_URL` 指向不可达地址 | 摘要同 L（`data/raw/` 缓存完整，重放不需要网络） |
| N | `--check` | 退出码 0：重算摘要、列集合、链登记、排除与覆盖算术一致，且交付事件集与快照观察期日志集合一致 |
| O | `--expect-data-digest <hex>` 给错值 | 退出码 2 |

## 5. 验证表怎么读

| `check_type` | `contract_value` | 参照 | 本次实际来源 |
| --- | --- | --- | --- |
| `source_position` | `log_order_key` | 独立端点的收据（`transactionIndex` / `logIndex`） | Etherscan 收据 |
| `source_position` | `log_index` / `block_number` | 样本事件在独立端点同区块日志中可定位；同区块同 topic 日志条数一致 | Etherscan `logs/getLogs`（失败顺延 drpc / blastapi） |
| `order_version` | `contract_code_hash` | 独立端点在同区块的 `eth_getCode` keccak256 | drpc（Etherscan 历史代码不可得） |
| `balance_state` | `balance_pre_event_raw` | 独立端点的 `balanceOf(block−1)` + 其自身区块日志按同一规则重算 | drpc `eth_call` + drpc 区块日志（Etherscan 历史 `eth_call` 已停用，见 §4） |
| `balance_state` | `balance_raw` / `balance_reference` / `balance_block_hash` | D2 余额、D2 总体归属（截止日黑名单状态）、截止区块 canonical | 独立端点截止区块的 `eth_call` 与 `eth_getBlockByNumber` |
| `balance_state` | `destruction_amount_raw` | 销毁金额等于独立重算的销毁前余额 | 同事件 `balance_state` 行的参照值 |
| `manual_reference` | `affected_address` | OFAC SDN 快照的地址串（匹配/未匹配都记） | 本地 OFAC 快照 |
| `schema_conformance` | 交付表列集合 | `docs/data-dictionary.csv` | 本地字典 |

生命周期案例在同一张表里追加三类核对，`field_name` 仍是字典内字段：

| 核对 | 内容 | 参照 |
| --- | --- | --- |
| `source_position` / `log_order_key` | 该交易内已登记事件的完整序列 | 独立端点收据按「已登记合约 + topic」过滤后的序列（同时记录交易日志总数，说明未登记日志不成行） |
| `source_position` / `tx_event_count` | 案例交易里含两条以上事件的交易数 | 快照按 `tx_hash` 分组重算（独立于重建代码） |
| `manual_reference` / `balance_state` / `order_version` | 与工单 01 人工核对样本逐字段对照（状态前后、事件前余额、代码哈希、D2） | `data/raw/sample_records.json` |
| `order_version` / `contract_code_hash` | 代理实现切换区块与切换前后代码哈希（版本标签写在 `resolution`） | 独立端点 `eth_getCode`（切换两侧各一次） |

案例级预期（`config/lifecycle_cases.json` 的 `expect`）也落成核对行：`contract_value` 是重建结果，
`reference_value` 是事先声明的预期；不一致时进程以退出码 3 失败，禁止事后改预期对结果。

`agreement` 只有 `match` / `mismatch` / `unverifiable`；独立端点不可用时按 R3 记 `unverifiable` 并写明原因，不记 `match`。`reviewer = agent:auto-cross-check`：跨端点与公开记录比对由脚本执行；**人工签署以 26 行分层抽样完成**（`pbn`，2026-11-17，26/26 `match`，`check_signoff.py --validate` 退出码 0），全量仍无逐行签字，登记见 `config/study_scope.json` 的 `open_items`。

## 6. 已知限制

- 单笔入口仍只覆盖一笔事件；USDT / USDC 的 `freezing` / `unfreezing` / `destruction` 三类事件已由工单 03 的五个案例覆盖（工单 04 补齐 `unfreeze_usdc`），但每类各含少量样本，不是全量覆盖。
- episode 起点只依据 `block_number - 1` 的黑名单状态与代币各自的状态函数；同一区块内"先解冻再冻结"同一地址的极端情形不会被识别（全量 6,060 条日志中未出现同交易多事件，见 `docs/validation-table.md` §5）。
- 全量 6,060 条日志按 `tx_hash` 分组，含两条以上黑名单事件的交易数为 0：**多事件交易在本范围结构性不可得**。交易作用域入口已支持（同一交易内逐条按 `log_index` 成行、各自查询状态与余额），但只有 `tx_scope` 案例的多日志交易可作为替代验证；一旦出现多事件交易，需要以人工核对样本重新覆盖该路径。
- 工单 04 的 `event retrieval coverage` 与 `balance reconstruction error` 只在 6 个样本槽位及其所在区块上成立；全量覆盖率与全量不可核验比例属工单 05。
- 合约版本标签来自 `config/contracts.json` 的字节码指纹，指纹本身是工单 01 期间的固化结论；指纹表未收录的哈希会降级为 `contract_version_confidence = low`（R4）。USDC 的中间实现（V2、V2_1）未登记，因此版本切换边界只能以代码哈希确认，标签留空。
- 公开指定日来自官方 SDN Advanced XML（实体级、`designation_date` / `day`），只用于工单 08 的受限路径；
  timing gap 已在 189 个 episode 起点上可报告，负值与 `>365` 天单列，不进入中位数。
- 匹配层的外部来源都不完整：OFAC 地址匹配仍用单一快照（`publish 2026-11-09`，晚于全部事件），
  因此"在清单上"不等于"冻结时已被指定"；交易所标签没有带日期的历史来源（注册表为 0 条），
  故标签层 `not_attempted`、比率 `not_reportable`，exchange outflow 仍不可报告
  （子集逐级筛选见 `data/recon/restrict_<id>/subset_eligibility.csv`）。
- 匹配覆盖只占事件的一小部分（193 / 5,970 事件、94 / 3,753 地址）：任何基于匹配子集的结论不得外推到
  全部冻结活动；`unmatched` 不等于未受制裁。
- Etherscan v2 只能服务日志、收据与区块头。历史状态一律不可得：`eth_getCode` / `eth_getStorageAt` 直接返回 `historical state … is not available`；`eth_call` 更危险——它忽略历史 tag 返回 latest 状态（工单 03 实测），适配层已把历史 tag 的 `eth_call` 判为不可得，历史状态只来自归档端点。
- Etherscan 的 `logs/getLogs` 只认十进制 `fromBlock`/`toBlock`，传 hex 会被静默忽略并返回最早日志；适配层已换算为十进制，并校验返回日志的区块号与分页上限（≥1,000 条视为截断）。其 `logIndex` 偶见 `0x`，缺索引时直接报错，不猜顺序。
- Etherscan 约 1/4 首次请求会 `ReadTimeout` / `SSLError`，由参照链顺延吸收；`--refresh` 时如遇连续失败会按 R3 记 `unverifiable`，不记 `match`。
- 同一地址可能在两个 issuer 合约上分别被冻结（如 `0xd850…e10e` 在 23,140,604 收到 USDC `Blacklisted`、在 23,141,909 收到 USDT `AddedBlackList`）。episode 与余额都按代币各自的状态函数与合约计算，跨代币日志不进入对方序列。
