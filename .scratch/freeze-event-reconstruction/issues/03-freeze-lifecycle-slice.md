# 03: 生命周期事件闭环

**What to build:** 把同一条端到端路径扩展到完整冻结生命周期：unfreezing、destruction、同一交易内多个事件、合约版本切换，都能被正确重建，并在验证表中解释事件顺序与版本差异对余额的影响。

**Blocked by:** 02: 单笔 freeze 事件的可复现重建

**Status:** done

- [x] 三类事件在同一交易内按日志顺序分别记录，互不混淆
- [x] 含多事件的交易能重建出正确的事件序列与两类余额（路径已实现；本范围 0/6,060 笔多事件交易，以交易作用域多日志交易与跨交易事件序列替代验证，见交付说明）
- [x] 合约版本切换前后解析一致，版本随事件一并记录
- [x] 验证表至少各含一例 unfreeze、destruction、多事件交易，并对照人工核对样本与可查询历史状态（"多事件交易"一栏以 `tx_scope` 多日志交易替代，并在表内写入结构性结论行）
- [x] 每个生命周期案例可独立演示，不依赖后续工单

## Comments

### 2026-11-13 交付

入口：`scripts/reconstruct_lifecycle.py --case {unfreeze|destruction|tx_scope|version_switch|all}`，案例清单与事先声明的预期在 `config/lifecycle_cases.json`，产物在 `data/recon/lc_<case>/`。

代码：

- `scripts/recon/pipeline.py`：作用域由「一条日志」扩展到「一笔交易」。`log_index = None` 时取该交易内全部已登记事件，按 `log_index` 升序逐条成行，各自查询状态、余额与验证行；`log_index` 有值时行为与工单 02 完全一致。`balances` 以 `query_id` 为主键，同一次 `eth_call` 不再重复成行；`at_study_cutoff` 每（代币，地址）只写一行。
- `scripts/recon/lifecycle.py`：案例层只做合并与核对，不重解日志——交易内事件序列对照独立端点收据、与工单 01 人工核对样本逐字段对照、D1/D2 归属自检、代理实现切换点二分定位与两侧代码哈希复核、案例预期比对。
- `scripts/recon/rpc.py`：修正参照端缺陷（见下），历史 `eth_call` 不再由 Etherscan 提供。

四个案例（`--case all`，全部 `match`）：

| 案例 | 事件 | 余额行 | 验证行 | `data_digest` |
| --- | --- | --- | --- | --- |
| `unfreeze` | 3（USDT 冻结 → 解冻 → 重新冻结） | 4（`at_freeze`×2、`at_event`×1、截止日×1） | 32 | `cc4edae2…c466` |
| `destruction` | 3（冻结 → 重复冻结 R9 → 销毁） | 3（`at_freeze`×1、`at_event`×1、截止日×1） | 33 | `a2bf2421…0890` |
| `tx_scope` | 1（3 条日志的交易中只有已登记事件成行） | 2 | 20 | `c84826fa…49ad` |
| `version_switch` | 2（USDC V1 与 V2_2 各一次冻结） | 4 | 33 | `93ac1b88…e4d0` |

数字：

- `unfreeze`：episode 1 起点 18,693,913（D1 8,123 raw）→ 解冻 22,766,755（`at_event` 8,123）→ episode 2 起点 23,141,909（D1 8,123）；截止日仍 `blacklisted`，D2 = 8,123。三行 `blacklist_transition` 均为 `true`。
- `destruction`：16,750,720 冻结（D1 36,950,426,457）→ 18,749,899 重复冻结（状态未变，R9，不产生 D1）→ 24,721,916 销毁，`at_event` = `destruction_amount_raw` = 58,148,619,145（销毁不 emit `Transfer`）；截止日仍 `blacklisted`，D2 = 0。
- `tx_scope`：交易内 3 条日志，事件在 `log_index 60`，前后两条来自另一个合约；事件表 1 行、`tx_event_count = 1`，收据过滤序列与之一致。
- `version_switch`：10,274,701 冻结于 `FiatTokenV1`（代码哈希 `0x767f…`），25,705,174 冻结于 `FiatTokenV2_2`（`0xcdfb…`）；地址均取自 topic，余额与版本都由独立端点复核。二分定位到实现切换在区块 10,743,414（`0x0882…` → `0xb727…`）与 18,963,716（`0xa232…` → `0x4350…`），两侧哈希由 drpc 复核。

复现：`--case all` 连续两次四个 `data_digest` 逐一相同；`tx_scope` 的 `--refresh` 与主端点指向不可达地址的重放都得到同一 `data_digest`；`--expect-data-digest` 可逐案例复核。校验失败的退出码：摘要不符 2，验证 `mismatch` 或案例预期不符 3。

评审后修正（两轴评审 + 独立审阅）：

- `balances.query_id` 主键：截止日查询原本未登记进查重表，`--cutoff-block` 恰好等于 `block_number - 1` 时会与事件前行撞主键；现已把截止日查询一并登记，碰撞时不重复成行并写入 manifest 说明。R9 重复冻结行不再留悬空外键（`events.balance_query_id` 没有对应 balances 行时为空）。
- Etherscan 非 `error` 形态的失败（`{"status":"0","result":"Max rate limit reached"}`）原本被当成功返回，`eth_getLogs` 会被折成 0 条日志并静默污染余额调整量；现在判为该方法不可得，日志结果不是列表也直接报错。
- `--refresh` 遇限流/超时不再把已成功的探针缓存改写成失败（那会让验证行退化为 `unverifiable` 并改变 `data_digest`）。
- 对照工单 01 样本时改用样本自带的 `adjustment`，不再用本流程自己的调整量构造期望值；样本判定截止日有余额而重建未产生 D2 行时，现在会明确报不一致。
- 版本切换点二分后用两侧取值复核，非单调时记 `unverifiable` 而不是写出错误区块；边界行的时间戳取自真实探针。
- 同一地址跨代币的 D2 行按（代币，地址）定位；样本记录的 `study_cutoff_block` 与本次运行不同时不再直接比较。
- 单个案例失败不再中断 `--case all`（退出码 1），`reproduce_command` 带上实际使用的输入路径。

偏差与限制：

1. **多事件交易在本范围结构性不可得**：全量 6,060 条日志按 `tx_hash` 分组，含两条以上黑名单事件的交易数为 0（与工单 01 样本阶段的结论一致）。交易作用域入口已实现并在 `tx_scope` 用多日志交易覆盖「只成已登记事件行 + 顺序以收据为准」；同一地址的跨交易事件序列由 `unfreeze` / `destruction` 覆盖。若后续范围出现多事件交易，需要以人工核对样本重新覆盖该路径，`data-dictionary.md` 不为此新增字段。
2. **参照端缺陷（已修复）**：Etherscan v2 的 `proxy eth_call` 接受历史区块参数但返回 latest 状态（USDT `balanceOf` 在区块 10,000,000 与 latest 同值，归档端点在该区块为 0）。旧实现会把「当前余额」当历史余额；在余额未变的地址上恰好一致（S1），在已销毁地址上直接为 0（本工单首次暴露）。已把历史 tag 的 `eth_call` 判为不可得，历史状态只来自归档端点；受污染的 7 个正缓存已删除，改由修复后的代码写否定缓存。工单 02 的 `data_digest` 因此由 `207f25f3…e646` 变为 `f259582b…e03b3`（仅 `reference_source` 变化，5 条 `match` 与数值不变）。
3. **中间版本标签留空**：USDC 的 V2 / V2_1 实现未登记在 `config/contracts.json` 指纹表，切换边界的标签为 `unregistered`、`contract_version_confidence = low`（R4）；边界本身以代码哈希确认。
4. **跨代币交错**：`0xd850…e10e` 在 23,140,604 收到 USDC `Blacklisted`、在 23,141,909 收到 USDT `AddedBlackList`；`0x4f47…270c` 在 USDT 与 USDC 上各有冻结。episode 与余额按代币各自的状态函数计算，案例的 USDT 序列不含跨代币日志，已在验证表 §5 记录。
5. `reviewer` 仍为 `agent:auto-cross-check`，人工签署留给工单 04；仓库仍不是 git 仓库，无法按流程提交分支。
