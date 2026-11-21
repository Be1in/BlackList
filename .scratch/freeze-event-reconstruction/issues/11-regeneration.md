# 11: 重跑并把修正并入主表

**What to build:** 在端点与缓存条件允许时，用已修好的重建逻辑重跑核心集与下游四个批次，让审计 66/66 通过、修正视图退役。

**Blocked by:** 10: 数据集契约审计与修正视图

**Status:** done

- [x] `Reconstruction._position_state` 在完整重跑中生效：16 条 destruction 的 before/after 均为 `blacklisted`、transition 为 false
- [x] `CoreRun._cutoff_population_rows` 在完整重跑中生效：D2 行数 3,573（USDT 2,867 / USDC 706）
- [x] 核心集、匹配层、比较、受限分析、支撑材料五个批次全部重跑并更新摘要
- [x] `audit_dataset.py` 66/66 通过
- [x] `docs/core-event-set.md`、`docs/issuer-comparison.md`、`docs/restricted-subset-analyses.md`、`docs/supporting-materials.md` 的行数与摘要同步
- [x] `DATASET.md` 与 `docs/known-issues.md` 的口径同步更新

## Comments

### 2026-11-17 交付

- 核心集：15 分钟完成（检索缓存补齐后），5,970 事件 / 9,466 余额 / 21,492 核对；`data_digest = 49db21c8…2fd5`。
- 匹配层：`match_d1eef47976c369477ed62528e153b8c5`（`63fc9525…e2d4`）。
- 比较：`compare_c1f94b30f0fcacdb30368c1427c695d1`（`4768622b…4ec8`）；D2 USDT 2,867。
- 受限分析：`restrict_8ecac83eb54bc07329c52170d22a35b1`（`11b28a49…63c4`）。
- 支撑材料：`materials_ee94e7bd9495ab5e709b347146825f7d`（`a564d2dd…77f8`），32 个组件。
- `audit_dataset.py`：66 项全部通过；五个入口 `--check` 均退出 0。
