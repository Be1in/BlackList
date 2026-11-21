# BlackList — 可观测稳定币冻结事件

把 Ethereum 上 USDT / USDC 的公开冻结事件（`freezing` / `unfreezing` / `destruction`）重建为可复核的
事件表、余额表与验证表。观察期区块 10,274,701–26,404,630，5,970 个事件，67 项契约审计全部通过。

链上可见的事实被记录；冻结原因、执法请求时间、地址实体归属、跨链迁移不在数据中，也不做推断。
数据集说明见 `DATASET.md`，事件定义见 `docs/definitions.md`，字段与排除规则见 `docs/data-dictionary.md`。

## 环境

| 项 | 值 |
| --- | --- |
| Python | 3.13 |
| 依赖 | `requirements.txt`：web3 8.0.0、pandas 3.1.1、pyarrow 26.0.1、requests 2.34.2、tqdm 4.70.1、python-dotenv 1.2.4 |

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

macOS / Linux 把激活命令换成 `source .venv/bin/activate`。所有脚本从仓库根目录运行。

## 配置

### `.env`

放在仓库根目录，已被 `.gitignore` 忽略。模板见 `.env.example`，只有 `ETHEREUM_ARCHIVE_RPC_URL` 必需。

| 变量 | 必需 | 说明 |
| --- | --- | --- |
| `ETHEREUM_ARCHIVE_RPC_URL` | 是 | 归档节点的完整 HTTP 端点，历史 `balanceOf` / `eth_getCode` 只从这里取 |
| `ETHEREUM_RPC_API_KEY` | 否 | provider 单独发放 key 时填写；URL 内已含 key 则留空 |
| `ETHEREUM_ARCHIVE_ENDPOINT_LABEL` | 否 | 端点标签，写入 manifest 与缓存键，默认 `tenderly_mainnet_gateway` |
| `ETHEREUM_REFERENCE_RPC_URLS` | 否 | 交叉核对用的参照端点，逗号分隔，默认 `https://eth.drpc.org`、`https://eth-mainnet.public.blastapi.io` |
| `ETHERSCAN_API_KEY` | 否 | 填入即启用 Etherscan v2 参照端（只服务日志、收据、区块头） |
| `ETHEREUM_REFERENCE_USE_ETHERSCAN` | 否 | 设 `0` 显式停用 Etherscan 参照端 |
| `ETHEREUM_RPC_REQUESTS_PER_SECOND` | 否 | 归档端点配速，默认 `8` |
| `ETHEREUM_ETHERSCAN_REQUESTS_PER_SECOND` | 否 | Etherscan 配速，默认 `3` |
| `EXCHANGE_LABELS_API_URL` / `EXCHANGE_LABELS_API_KEY` | 否 | 带日期的历史交易所标签来源，尚未确定；留空时标签层记 `not_attempted` |
| `OFAC_SDN_BASE_URL` | 否 | OFAC SDN 下载或镜像地址，默认公共下载 |

参照链顺序：`reference:api.etherscan.io` → `reference:eth.drpc.org` → `reference:eth-mainnet.public.blastapi.io`。
端点明确回绝某个方法时，否定结果同样落缓存，重放因此可以完全离线。

### `config/`

| 文件 | 作用 |
| --- | --- |
| `study_scope.json` | 研究期与样本范围决策（`decision_version v1`）：观察期起点 10,274,701、截止区块 26,404,630。核心集与 `--check-scope` 以它为准，命令行边界不一致会拒绝运行 |
| `contracts.json` | 链与代币、6 位小数、代理实现槽位、字节码指纹 → 版本标签（`TetherToken`、`FiatTokenV1`、`FiatTokenV2_2`） |
| `lifecycle_cases.json` | 5 个生命周期案例（`unfreeze`、`destruction`、`tx_scope`、`version_switch`、`unfreeze_usdc`）及其事先声明的预期值 |
| `ofac_advanced.json` | SDN Advanced XML 的解析口径：指定日取实体级 `designation_date`，`precision = day` |
| `exchange_labels.json` | 历史交易所标签注册表。当前 0 个来源、0 条标签，故标签层整体记为不可报告 |

## 目录

```
config/                 研究期决策、合约指纹、案例、标签注册表
data/                   不入库，按 DATASET.md §5 重建
  raw/                  固定日志快照、eth_call 与 RPC 缓存、OFAC 快照
  recon/<batch_id>/     各批次交付表（events / balances / validation / manifest.json …）
  derived/              审计、排除、人工签署等派生表
  samples/              小样本 CSV，供不重跑全量时检查字段
docs/                   定义、数据字典、复现说明、已知问题、ADR
scripts/                采集与重建入口，recon/ 为共用模块
DATASET.md              数据集说明卡
dataset_manifest.json   交付文件的 sha256、行数与批次 id
```

## 运行

`data/` 不在仓库里。全新克隆后先准备输入：

| 输入 | 位置 | 校验 |
| --- | --- | --- |
| 黑名单日志快照（6,060 条，区块 4,634,748–26,404,630） | `data/raw/logs/ethereum_blacklist_logs.json` | `raw_log_sha256 = 10fdb389…c3a1eb` |
| OFAC SDN 快照 | `data/raw/ofac/` | `sdn_manifest.json` 的 `csv_sha256`、`publish_date` |
| 人工核对样本声明（S1–S6） | `data/raw/sample_records.json` | 摘要记入验证 manifest |

其余 RPC 响应按 `query_id` / `probe_id` 落 `data/raw/state_calls/`、`data/raw/chain_probes/`，可离线重放。

按顺序执行主流程：

```bash
python scripts/parse_ofac_advanced.py             # SDN Advanced XML → 地址 / 指定日提取表
python scripts/validate_sample.py --check-scope   # 先验证、后范围决策
python scripts/reconstruct_core.py --workers 10   # 核心事件集：冷跑约 2h15m，缓存齐后约 15 分钟
python scripts/reconstruct_matches.py             # 匹配层与缺失报告（全离线）
python scripts/reconstruct_comparison.py          # 同口径 issuer 比较与敏感性
python scripts/reconstruct_restricted.py          # 受限子样本分析
python scripts/reconstruct_materials.py           # 支撑材料包与措辞审查
python scripts/audit_dataset.py --write           # 67 项契约审计
```

单笔与案例入口，用于核对模板和局部重放：

```bash
python scripts/reconstruct_single_event.py                     # 单笔事件（默认 S1）
python scripts/reconstruct_lifecycle.py --case all             # 5 个案例逐一落到 lc_<case>/
python scripts/validate_sample.py                              # 代表性样本 → vs_<id>/
python scripts/check_cutoff_blacklist.py                       # D2 总体缺口
python scripts/check_signoff.py --validate                     # 26 行人工签署复核
python scripts/build_dataset_manifest.py [--check]             # 生成 / 校验 dataset_manifest.json
```

各入口都支持 `--check`：不联网、不写盘、重算摘要并与交付逐行比对。`--expect-data-digest <hex>`
传入已知摘要可在评审环境直接复核。

| 退出码 | 含义 |
| --- | --- |
| 0 | 通过 |
| 1 | 运行失败（端点不可用、输入缺失、在快照中找不到交易） |
| 2 | 数据摘要与期望不符 |
| 3 | 前置条件未满足或核对表出现 `mismatch`（不写产物） |
| 4 | `--check` 离线复核不一致 |

产物写入 `data/recon/<batch_id>/`，批次 id 由输入摘要派生；换参数或上游摘要变化即换新目录，
脚本从不猜“最新目录”。核心集摘要 `49db21c8…2fd5` / `fdea03ea…7b01`，其余批次见各自 `manifest.json`。

## 复现边界

- `data_digest` 剔除四个抓取时刻字段，同一输入必须同值；`artifact_digest` 是落盘字节摘要，含抓取时刻，
  `--refresh` 后会变。重放不改变记录与余额，只改变取证时刻。
- 只覆盖 Ethereum 的 USDT / USDC。Tron 在 `chain_registry` 中仅登记为 `registered_pending`，未采集。
- 匹配子集只占全部事件的一小部分（193 / 5,970 事件、94 / 3,753 地址），结论不得外推；
  `unmatched` 不等于“未受制裁”。
- 5 项已登记缺口见 `docs/known-issues.md` §2，完整方法见 `docs/reproduction.md`。

## 许可与引用

链上原始数据来自公开账本；整理、字典与脚本由项目组完成。**许可尚未指定**，在项目负责人确定前仅供
内部研究与评审使用，不对外分发。

> PENG Beining, CHANG Juxiao, LI Mingzheng. *Observable Stablecoin Freeze Events: A Reproducible
> Ethereum Dataset with Documented Gaps*. MSBD5017 project, 2026.
