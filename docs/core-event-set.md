# 核心事件集：范围、口径与核验边界（工单 05）

产物：`python scripts/reconstruct_core.py`（默认按 `config/study_scope.json` 的观察期运行）。
本次交付批次见 `data/recon/core_<id>/manifest.json`；下表数字取自该 manifest。

本文件只解释核心集怎么来、覆盖到哪、哪些记录被排除以及为什么。字段定义、事件类别与余额口径仍以
`docs/definitions.md`、`docs/data-dictionary.md` v1 为准，本文不新增字段。

## 0. 本次交付

| 项 | 值 |
| --- | --- |
| 批次 | `core_64c8c7f9452291663926113a2b1ecc20`（`data/recon/core_64c8c7f9452291663926113a2b1ecc20/`） |
| 取证时刻 | `2026-11-17T12:47:27Z`（重放同一输入不改写） |
| 事件 / 余额 / 核对 / 链登记 | 5,970 / 9,466 / 21,492 / 2 |
| 事件分类 | `freezing` 4,138、`unfreezing` 572、`destruction` 1,260（USDT 4,786、USDC 1,184） |
| 余额口径 | `at_freeze` 4,077（episode 起点）、`at_event` 1,816、`at_study_cutoff` 3,573（USDT 2,867、USDC 706） |
| `event retrieval coverage` | `1.000000`（5,970 / 5,970 独立日志，严格口径同值；区块级日志集合一致 4,980 / 4,980） |
| `balance reconstruction error` | 9,482 / 9,482 完全一致，最大绝对误差 `0` raw / `0.000000` token；不可比较 0 |
| `unverifiable share` | 行口径 `0.000000`（0 / 21,492，全部 `match`）；记录口径 `0.000000`（0 / 15,436） |
| 排除 | 0 条被排除事件；观察期外 90 条（全部为 USDT，逐条在 `scope_excluded.csv`）；匹配导致的剔除 0 |
| 合约版本 | `TetherToken`、`FiatTokenV1`、`FiatTokenV2_2`，另 215 条 USDC 事件的中间实现未收录指纹表（R4，`contract_version_confidence = low`） |
| 摘要 | `data_digest = 49db21c8…2fd5`，`artifact_digest = fdea03ea…7b01` |

覆盖差集 `coverage_gaps.csv` 为 0 行；`data/derived/exclusions.csv` 只有表头。三类事件、两代币、
三个已登记版本与两段 USDC 代理实现切换都在核心集内，未做匹配筛选。

复现（本机）：冷跑（无缓存）约 2 小时 15 分，受归档端点 compute-unit 配额与配速限制；检索缓存
补齐后 15 分钟完成，`data_digest` 与 `artifact_digest` 见上表；仅把 `ETHEREUM_ARCHIVE_RPC_URL` 指向
不可达地址重放，两个摘要同样不变，`--expect-data-digest 49db21c8…2fd5` 可直接复核。

## 1. 这份核心集回答什么

核心集覆盖研究范围内**全部可检索事件**，不做任何匹配过滤：

| 项 | 取值 |
| --- | --- |
| 链 | `ethereum`（`chain_id = 1`），逐事件写入 `events.chain` / `chain_id` |
| 代币 | `USDT`、`USDC` |
| 事件类别 | `freezing` / `unfreezing` / `destruction` |
| 观察期 | `observation_start_block` = 10,274,701；`study_cutoff_block` = 26,404,630（来自 `config/study_scope.json`） |
| 核心路径 | 观察期内全部可检索事件；匹配失败不剔除 |
| 受限路径 | 不在本工单，见工单 06/08 |

观察期不是脚本参数的自由选择：`CoreRun` 启动时要求命令行与决策文件一致，不一致直接退出。
因此"全量"始终指同一个固定窗口，换窗口必须重做范围决策。

核心集事件 = 观察期内全部可检索事件；D2 总体按 `definitions.md` §3.3 覆盖 cutoff 时**全部**
`blacklisted` 地址，因此额外包含 29 个观察期前首次冻结、观察期内无事件、截止日仍未解冻的 USDT 地址。
这 29 个地址只写入 `balances` 的 `at_study_cutoff` 行（`reference_event_id` 为空，`notes` 记录首次冻结区块），
不写入 `events`，也不参与任何事件计数或 issuer 事件比较。

## 2. 覆盖率的分子与分母

`event retrieval coverage` 的分母来自**独立端点的全量检索**，不是快照自身的条数：

1. 按（代币合约 × 5 个事件 topic）在研究区间内调用 `eth_getLogs`；Etherscan 单次最多返回
   1,000 条，遇到分页截断就把区块区间递归对半切分，直到每个分片完整返回。
2. 位置一律用 `(tx_hash, log_index)` 比较；Etherscan 对个别区块返回 `transactionIndex = 0x`，
   这些区块整块改用归档 JSON-RPC 端点重读，不猜位置。
3. 快照与独立检索的差集逐条写入 `coverage_gaps.csv`：
   - 独立端点可检索、快照缺失 → 按 R1 保留占位行（`log_not_retrievable`，`analysis_eligibility = excluded`），计进分母；
   - 快照有、独立端点未返回 → 事件保留在核心集，位置核对记 `unverifiable`，同样逐条登记。

`block_level_log_parity` 另报"同一区块内快照集合与独立集合完全一致"的区块数，用于发现只差一条日志的边界情况。
`coverage` 按字典口径是"匹配数 ÷ 独立端点应有数"；另报 `strict_coverage`（分母取快照数与应有数的较大者），
快照多出日志时不会仍显示 1.0。

## 3. 核验边界没有变

事件行、余额行与逐事件核对仍由 `Reconstruction`（工单 02/03）产出，本工单不重写解析。
全量规模下只改两处，均不降低对照强度：

| 核对 | 全量做法 | 为什么等价 |
| --- | --- | --- |
| `source_position` | 一次独立全量检索得到的 `log_order_key` 集合成员判定 | 与逐事件收据对照同一独立来源；同一份检索同时充当覆盖率分母 |
| `order_version` | 按每个不同 `contract_code_hash` 核对一次（代表事件） | 代码哈希在实现升级之间不变；按行重复不增加信息 |
| `balance_state`（事件前） | 每个事件仍由独立归档端点重新 `eth_call` + 同区块 `Transfer` 重算 | 与样本验证同一算法，覆盖全量事件 |
| `balance_state`（D2） | 每个仍冻结（代币，地址）在截止区块独立重查黑名单状态与余额 | D2 总体按地址定义，逐地址核对 |
| `manual_reference` | OFAC SDN 快照地址串比对，逐事件记录 | 只作为核对行；不参与 `analysis_eligibility` |

`validation.csv` 是核对记录，不是"每行必须一条核对"的配额表：全量模式下 `schema_conformance` 由逐事件
重复改为整表一条，位置核对改用同一份独立检索。

## 4. 一致排除规则怎么落地

排除函数只看记录自身证据，不看 issuer、事件类别或匹配结果：

| 规则 | 触发 | 本工单处理 | `analysis_eligibility` |
| --- | --- | --- | --- |
| R1 | 独立端点可检索、快照缺失 | 保留占位行，`log_not_retrievable`，逐条写 `coverage_gaps.csv` | `excluded` |
| R2 | 归档状态不可得 | 事件保留，余额字段留空并写 `rpc_unavailable`，不写零值 | `core_unverifiable`（无独立核验） |
| R3 | 缺少独立第二来源 | 保留，`verification_status = unverifiable` | `core_unverifiable` |
| R4 | 合约实现无法确定 | 保留，`contract_version_confidence = low` | `core` |
| R7 | 重复 `event_id` | 保留首条，其余排除 | `excluded` |
| R8 | 链不在研究范围 | 排除 | `excluded` |
| R9 | `blacklist_transition = false` | 保留，不产生第二个 D1 | `core` |

R9 是**指标归属规则，不是排除**：`blacklist_transition = false` 的 `freezing` / `unfreezing` 行留在核心集，
`exclusion_rule_id` 保持为空，可由该字段逐条筛出；manifest 的 `core_set.r9_attribution_rows` 给出计数。
本次交付 `blacklist_transition = false` 共 1,352 条（含 1,260 条 `destruction`），其中 `freezing` / `unfreezing`
92 条按 R9 不产生 D1；另有 16 对同区块 `freezing → destruction` 共用一次事件前查询，`balances` 只保留一行，
事件行仍各自记录 `balance_pre_event_raw`。`blacklist_state_before/after` 取**事件位置**状态：
以 `block-1` 的归档状态为基，重放同区块更早的黑名单日志，因此销毁前后都是 `blacklisted`、
`blacklist_transition = false`。

匹配层不参与剔除：`events` 行从不因 `manual_reference` 为 `unmatched` 而被排除；
manifest 的 `metrics.exclusions.match_based_exclusions` 恒为 0，并有一条整表核对行记录未匹配事件数。

观察期外的日志不进入核心集，但**逐条**写入 `scope_excluded.csv`（含 `reason = before_observation_start` /
`after_study_cutoff`），因此"没进入"与"被静默丢弃"可以区分。排除汇总视图另写 `data/derived/exclusions.csv`。

## 5. 复现

```
python scripts/reconstruct_core.py --workers 12
python scripts/reconstruct_core.py --expect-data-digest <hex>
```

- 输入：固定日志快照 + `config/study_scope.json` + 合约指纹表 + OFAC 快照；不依赖运行时刻。
- `data_digest` 剔除四个抓取时刻字段，同一输入必须同值；`artifact_digest` 含抓取时刻。
- 缓存目录不变：`data/raw/state_calls/`、`data/raw/chain_probes/`；重放不需要网络。
- `--max-events N` 只用于冒烟，产物写 `partial_run = true`，不能当交付口径。
- 并发只影响速度：同一请求在进程内只抓一次（按请求键串行化），缓存文件原子替换；每个工作线程复用
  一个连接池，端点限流按 `ETHEREUM_RPC_REQUESTS_PER_SECOND`（默认 8）与
  `ETHEREUM_ETHERSCAN_REQUESTS_PER_SECOND`（默认 3）配速，限流是暂时失败，不写否定缓存。
- `--check` 离线复核：重算摘要与列集合，并用固定日志快照重算观察期内的事件集与交付表对照，同时检查
  链登记、排除/资格与覆盖算术。

## 6. 已知限制

- **分母的独立性止于端点**：覆盖率的"应有日志数"来自一个独立端点；若该端点自身漏日志，缺口不会自我暴露。
  两个方向的差集都逐条落盘，供第三方复核。
- **D2 核验按地址**：同一地址在两个代币上分别成行；跨代币不合并。
- **版本核对按代码哈希**：代理实现切换边界本身不重新二分，边界两侧的代码哈希只在代表区块核对。
- **归档状态不可得时不留近似值**：R2 行余额为空，余额类指标只在可核对子集上报告，manifest 的
  `balance_reconstruction_error.checks` 是实际参与误差统计的行数，不是事件数。
- **人工签署按抽样完成**：全量 `reviewer = agent:auto-cross-check`；26 行分层抽样由 `pbn` 于
  2026-11-17 签署（26/26 `match`），见 `docs/manual-signoff.md` §4。
- 交易所标签层与 timing gap 不在本工单；`matches` 表已由工单 06 交付
  （`data/recon/match_<id>/`，见 `docs/matching-and-missingness.md`），本次交付中匹配覆盖为
  193 / 5,970 事件、94 / 3,753 地址，标签层无带日期的历史来源。
