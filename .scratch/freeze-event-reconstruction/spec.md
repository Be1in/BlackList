Status: done (2026-11-17; 审计 66/66)

# Spec: Data quality and reproducibility in public freeze events

## Problem Statement

我手上的提案要研究稳定币冻结事件，但公开链上数据只能看到事件本身。历史日志、合约升级、同一交易内的事件顺序、以及"冻结时余额"与"截止日仍冻结余额"的口径差异，都会让看起来像 USDT 与 USDC 之间的差异，其实只是数据处理方式的差异。OFAC 地址匹配和历史交易所标签可能只覆盖很小一部分事件，如果把有限匹配样本当作总体，结论就没有证据支撑。反馈意见要求把这些问题先讲清楚，再谈任何描述性比较。我需要一份能直接照着执行、且每条要求都有对应交付物的计划。

## Solution

把项目重心从"回答冻结行为的问题"改成"证明公开事件数据能被可靠重建，并说明在此覆盖下能回答什么"。

**研究问题**（对应反馈的 Research question）：公开链上事件能在多大程度上可靠重建稳定币冻结生命周期及相关余额？在明确定义的数据覆盖下，哪些描述性的 issuer 比较是受证据支持的？

具体做法：先定义 freezing / unfreezing / destruction 三类事件、两类余额口径、公开制裁时间戳与私有执法请求的边界；用可复现的采集脚本重建 USDT / USDC 的冻结事件与余额（验证样本限 Ethereum，核心分析覆盖研究范围内全部可检索事件），并把原始来源、交易与日志位置、定义、匹配证据、时间戳精度、缺失情况写进数据字典；用一个小型代表性样本（含多事件交易）和人工核对、可查询历史状态做验证，以验证结果反过来确定研究期与样本范围。涉及的链在数据字典中逐条登记，范围依据见 Implementation Decisions。

分析分两条互不混用的路径：核心分析覆盖全部可检索事件；sanctions timing-gap 与 exchange-outflow 分析只在证据充分的子样本上进行。所有 issuer 比较统一观察期、事件定义和缺失处理，并做敏感性分析。报告 event retrieval coverage、balance reconstruction error、unverifiable share、public-record match rates；public sanctions timing gaps、address outflows、frozen balances 一律标为描述性统计，不作因果推断。

交付物为小数据样本、数据字典、可复现采集脚本、验证表，并在其中标出哪些字段一致可得、哪些指标只能对受支持子集报告。

## User Stories

1. As 项目负责人, I want 一份明确排除范围的计划, so that 反馈未要求的治理与跨链迁移/绕过议题不再占用工作量
2. As 项目负责人, I want 验证结果先行、研究期与样本范围后置确定, so that 范围由可获得的证据决定而不是先假定
3. As 数据工程师, I want freezing / unfreezing / destruction 各自独立且互斥的定义, so that 生命周期重建不把不同事件混为一类
4. As 数据工程师, I want 公开制裁时间戳与私有执法请求各自有明确定义与可观测性声明, so that 不会把公开时间差误读为对私有请求的响应速度
5. As 数据工程师, I want "冻结时余额"与"研究截止日仍冻结余额"两套一致口径, so that issuer 差异不来自余额定义
6. As 数据工程师, I want 每个事件记录其合约版本, so that 合约升级不会让日志解析结果前后不一致
7. As 数据工程师, I want 记录同一交易内的日志顺序, so that 含多个事件的交易能被正确重建
8. As 数据工程师, I want 访问重建所需的历史状态, so that 余额不是用当前状态倒推的近似值
9. As 数据工程师, I want 明确观察期与样本范围（验证样本限 Ethereum 上 USDT / USDC，涉及链在数据字典登记）, so that 所有比较有同一时间基准且不隐含未声明的链范围
10. As 数据工程师, I want 一个覆盖 freezes、unfreezes、destruction 和多事件交易的小型代表性样本, so that 各类边界情况都被验证过
11. As 数据工程师, I want 一份可复现的采集脚本, so that 评审能用同样输入得到同样的事件集
12. As 数据工程师, I want 数据字典记录原始来源与交易、日志位置, so that 每条记录都可回溯
13. As 数据工程师, I want 数据字典记录余额定义, so that 后续分析不会另起一套口径
14. As 数据工程师, I want 数据字典记录匹配证据, so that OFAC 与交易所标签的归属可以被复核
15. As 数据工程师, I want 数据字典记录时间戳精度, so that 时间差分析的误差范围可见
16. As 数据工程师, I want 数据字典记录 missingness, so that 缺失不会被静默当作零值
17. As 数据工程师, I want 对不可获得或无法独立核验的数据使用一致的排除规则, so that 排除不因发行方或事件类型而偏斜
18. As 复核人, I want 一个人工核对过的样本, so that 重建结果有独立于脚本的参照
19. As 复核人, I want 可查询历史状态作为验证参照, so that 余额重建能被外部数据交叉检验
20. As 复核人, I want 一张验证表, so that 每个偏差都有可查的记录与原因
21. As 分析者, I want 核心分析覆盖全部可检索事件, so that 主要结论不依赖匹配成功的小样本
22. As 分析者, I want timing-gap 与 exchange-outflow 分析只在证据充分的子样本进行, so that 稀疏匹配不会污染核心结论
23. As 分析者, I want 报告 event retrieval coverage, so that 读者知道事件集有多完整
24. As 分析者, I want 报告 balance reconstruction error, so that 余额指标的可信度可量化
25. As 分析者, I want 报告 unverifiable share, so that 无法核验的部分显式暴露
26. As 分析者, I want 单独报告 public-record match rates、缺失值和标签日期, so that 匹配覆盖的局限不会被掩盖
27. As 分析者, I want 历史交易所标签带日期, so that 不会用后来的标签解释过去的行为
28. As 分析者, I want issuer 比较使用同一观察期、事件定义和缺失处理, so that 比较反映的是数据而非处理差异
29. As 分析者, I want 对 outflow windows、timestamp precision、missing labels 做敏感性分析, so that 结论对参数选择的依赖可见
30. As 分析者, I want 结论措辞被约束为描述性, so that 不推断私有请求响应速度、规避或执法有效性
31. As 分析者, I want 有限匹配样本的结论不外推到全部冻结活动, so that 推广范围与证据覆盖一致
32. As 评审, I want 每个指标标注其数据覆盖范围, so that 能一眼分辨全量结论与子集结论
33. As 评审, I want 交付一个小数据样本, so that 不需重跑全量也能检查字段与口径
34. As 评审, I want 交付数据字典、采集脚本和验证表, so that 复现与核查都有抓手
35. As 评审, I want 支撑材料明确区分"一致可得字段"与"仅受支持子集可报的指标", so that 交付物与结论边界对齐
36. As 读者, I want 描述性统计与因果性判断明确分开, so that 不会把冻结余额与资金流出读成行为动机

## Implementation Decisions

- **单一重建流程**：一个可复现的事件采集与余额重建流程，输出事件表、余额表、以及覆盖与缺失记录。不在流程内部再拆分需要独立验证的组件。
- **事件分类契约**：三类事件（freezing / unfreezing / destruction）各自独立且互斥地定义。
- **时间来源契约**：为公开制裁时间戳与私有执法请求各给出明确定义与可观测性声明——前者是公开记录的时间点，带来源与时间戳精度；后者在本研究的数据中不可获得，因此定义为"不可观测、无对应字段、不参与任何指标"，不推断其发生时间。两者不共用同一时间字段。
- **余额口径契约**：冻结时余额与研究截止日仍冻结余额分别定义并分别记录，任何指标必须声明使用哪一套。
- **状态访问**：历史状态查询（可查询归档状态）是唯一的外部真值来源；合约版本、日志顺序、交易内位置随事件一并记录。
- **范围解释**：反馈只在"小型代表性样本"条款中明确写了 Ethereum，而核心分析条款写的是 all retrievable events，未限定链。本计划按"验证样本限 Ethereum、核心分析在既定研究范围内全量"执行，不把 Ethereum 当作反馈对全部链的显式限定；每条事件在数据字典中登记所属链，是否扩展到其他链列为待确认项。
- **数据字典作为模块间契约**：字段清单包含原始来源、所属链、交易与日志位置、余额定义、匹配证据、时间戳精度、missingness、排除原因，以及私有执法请求不可观测的声明；后续所有分析只消费字典声明的字段。
- **匹配层与核心事件表分离**：OFAC 地址匹配与历史交易所标签作为附加匹配结果保存，带标签日期与匹配证据；匹配失败不影响事件是否进入核心分析。
- **验证先行**：人工核对样本与可查询历史状态先产生验证表，验证结果（覆盖率、误差、不可验证比例）再反过来确定研究期与样本范围。
- **两条分析路径显式分离**：核心路径使用全部可检索事件；受限路径仅使用证据充分的子样本，用于 timing-gap 与 exchange-outflow 描述性分析，两者结果不互相补充或拼接。
- **比较前置条件**：任何 issuer 比较必须同时满足同一观察期、同一事件定义、同一缺失处理，否则不产出比较结论。
- **统一排除规则**：不可获得或无法独立核验的数据按同一规则排除，并在字典中记录原因。
- **敏感性参数化**：outflow window、timestamp precision、missing label 处理作为参数，可重复运行并对照。
- **报告措辞约束**：public sanctions timing gaps、address outflows、frozen balances 一律标为描述性；不产出关于私有请求响应、规避或执法有效性的推断。
- **不新增内部测试接口**：验证边界放在重建输出处，不为解析步骤各建一处 seam。

## Testing Decisions

- **什么算好测试**：只验证外部可观察行为——给定同一区块/交易范围，采集脚本产出的事件集、事件顺序与两类余额是否与独立参照一致。不断言内部解析函数、缓存策略或实现细节；重构内部实现不应使验证失败。
- **唯一 seam（最高层）**：以"重建输出（事件表 + 余额表 + 验证表）"为验证边界，对照人工核对样本与可查询历史状态。这是本次新增的唯一一处 seam，也是反馈意见已经指定的验证参照。
- **被验证的行为**：事件检索覆盖与顺序、合约版本切换后的解析一致性、两类余额口径、多事件交易、匹配证据与缺失记录、核心路径与受限子样本的分流。
- **验证指标**：event retrieval coverage、balance reconstruction error、unverifiable share、public-record match rates。
- **对照维度**：outflow windows、timestamp precision、missing labels 的敏感性，以及跨 issuer 的同口径对照。
- **Prior art**：仓库当前只有需求与反馈文档、agent 配置及 spec，没有应用代码、测试框架或既有 seam 可复用；验证参照直接取自反馈意见中的 "manually checked sample" 与 "queryable historical state"。因此不引入更低层的既有 seam，也不新造多处 seam。

## Out of Scope

- 反馈未采纳、来自 `requirement.md` 的议题：冻结密钥归属、申诉机制、治理设计、EU / HK 义务评估、T3 FCU、MiCA / HKMA、`hkchain`，以及跨链迁移、桥、wrapped token、流动性仓位与绕过分析。
- 其他链（含 Tron）是否纳入：反馈未界定，列为待确认项（见 Further Notes），本计划不预设扩展。
- 对冻结原因、执法请求时间、地址实体归属等不可观测字段的补全或推断。
- 实时或可操作的"抢在冻结前转移资金"指引；分析保持 ex post 且聚合（来源：`requirement.md` 的 ground rules，反馈未重申）。
- 由公开时间差推断私有请求响应速度，由地址流出推断规避，由冻结余额推断执法有效性。
- 把有限匹配样本上的发现推广到全部冻结活动。
- 本次不建立持续集成或单元测试套件，验证以交付的验证表与可复现脚本为准（spec 模板的 Testing Decisions 引申，非反馈要求）。

## Further Notes

- 依据：`MSBD5017 Project Proposal Feedback`，Feedback 05，14 October 2026，主题 "Data quality and reproducibility in public freeze events"。
- 与原始选题的关系：`requirement.md` 是更宽的候选清单。其候选问题 1 在此被收窄为"描述性、且仅用于有充分证据的子样本"；候选问题 2、3、治理设计交付物、以及监管锚点项目均不在本计划内。
- 术语沿用反馈文档（freeze lifecycle、event retrieval coverage、balance reconstruction、study cutoff、missingness 等）。仓库已配置 `AGENTS.md` 与 `docs/agents/`（本地 markdown tracker、单 context 领域文档、默认 triage 标签），但尚无 `GLOSSARY.md` 或 ADR，因此未套用额外领域词汇。
- 执行顺序不可倒置：先重建与验证，再定研究期与样本范围，最后才是描述性比较。
- 发布：`/setup-matt-pocock-skills` 已完成，issue tracker 为本地 markdown；本 spec 位于 `.scratch/freeze-event-reconstruction/spec.md`，`Status: ready-for-agent` 已生效。
- 待确认：链范围。反馈仅对"小型代表性样本"明确 Ethereum，核心分析写的是 all retrievable events；若评审期望核心分析跨链全量，需在定研究期前确认并相应扩展采集范围。
