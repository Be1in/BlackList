# 10: 数据集契约审计与修正视图

**What to build:** 用一套可复跑的检查，把交付表逐条对回定义与字典；发现的契约偏差不能静默修掉，要给出证据、影响面与可用替代视图。

**Blocked by:** 09: 支撑材料打包与措辞审查

**Status:** done

- [x] 66 项检查覆盖文件/清单、事件、余额、匹配、核对、跨批次与字典列
- [x] 两处契约偏差定位到根因与逐行证据（KI-01 29 个 D2 地址、KI-02 16 条销毁状态）
- [x] 修正视图可确定性派生，且不修改主表与摘要
- [x] 影响面写清：哪些指标必须换表、哪些继续用主表
- [x] 修复代码已写入重建流程，重跑步骤单列

## Comments

### 2026-11-17 交付

入口：

- `python scripts/audit_dataset.py`（`--write` 写 `data/derived/dataset_audit.json`）
- `python scripts/check_cutoff_blacklist.py`（29 个地址的状态与余额，写 `data/derived/cutoff_blacklist_gap.json`）
- `python scripts/derive_dataset_corrections.py`（写两张修正视图）

结果：66 项检查，64 通过、2 失败。失败项即 KI-01 / KI-02，详见 `docs/known-issues.md`。
修复代码：`Reconstruction._position_state`、`CoreRun._cutoff_population_rows`。
重跑已完成（工单 11）：主表并入两处修正，审计 66/66 通过；本工单的修正视图保留为对照证据。
