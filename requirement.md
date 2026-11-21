Observable blacklist events and their governance
可观测的⿊名单事件及其治理：构建⼀个可复现但不完整的数据集
C Empirical Policy
WHY NOW
Tether's April 2026 disclosure reports more than US$4.4 billion frozen since launch
(issuer-reported), including a US$344 million action across two Tron addresses
coordinated with OFAC, and a US$182 million action in January 2026; Circle freezes
too, and freeze authority is now being tested in court. The blacklist events themselves
are observable on chain. What is not: the reason for each freeze, the timing of law￾enforcement requests, entity attribution of addresses, cross-chain migration of the
funds, re-issuance and unfreezing. The dataset is reproducible but incomplete, and
the paper must say which questions it cannot answer.
ANCHOR PROJECTS
USDT / USDC blacklist events (Ethereum, Tron) OFAC SDN list timing
T3 Financial Crime Unit (Tether · TRON · TRM)
MiCA and HKMA freeze obligations (guideline §5.10)
hkchain: sanctions list updated by governance message; blacklist reached by three paths
(governance, KYC officer, flag close)
CANDIDATE QUESTIONS
1. Measure freeze latency after an SDN designation, the share of funds that reach
exchanges before the freeze lands, and the value stranded per freeze, across
issuers and chains, at aggregate level.
2. Who should hold the freeze key (the issuer, chain governance, or a court-triggered
oracle), and what appeal path exists for a wrongly frozen holder?
3. Blacklist on the issuer contract versus a chain-level gate: which is easier to bypass
through wrapped tokens, bridges and liquidity positions, and does the answer
change on a chain with holder identification?
DELIVERABLE
An on-chain dataset of freeze events with documented gaps and an empirical
paper; or a governance design with a formal appeals mechanism, evaluated against
the Hong Kong and EU obligations. Per the ground rules, the analysis is ex post and
aggregate, never a real-time guide to moving funds ahead of a freeze.
START WITH
Tether, freeze of more than US$344 m in coordination with OFAC (April 2026).
BlockSec, USDT freeze data guide, a starting point for the event dataset.