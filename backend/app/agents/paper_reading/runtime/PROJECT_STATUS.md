# Project Status

- Project: Zhiyan Paper Reading Agent
- Active phase: `V0_6_4_RELEASE`
- Phase state: `TEAM_SOURCE_READY_TAGGED`
- Current public stable package: `zhiyan-paper-reading-agent-v0.5.0.zip`
- Current stable source: `v0.6.4`
- Promoted candidate: `v0.6.4-rc1`
- Public repository target: `https://github.com/Mau-Q/zhiyan-paper-reading-agent`
- Public release target: `v0.6.4`
- Development release target: `v0.6.4`
- Development host mode: `MAC_LOCAL`
- Current validation gate: `V0_6_4_RELEASE_COMPLETE`
- Windows candidate revalidation: `NOT_REQUIRED_FOR_CURRENT_MAC_DEVELOPMENT`
- Current product mode: `AGENT_FLOW_ONLY`
- Paper-reading Agent flow: `ACCEPTED`
- Automated PDF parse and in-process local Splitter: `COMPLETE`
- Real-PDF Agent flow: `COMPLETE`
- Implemented entry: `RealPdfReadingAgent.read_pdf(pdf_path, reading_goal, depth, splitter_strategy, focus_aspects=...)`
- Intended output: grounded unified deep-reading Markdown plus optional structured JSON with explicit flow status
- Default execution mode: `flow_first`
- Alternate execution mode: `strict`
- Best-effort partial-report mode: `DOCUMENTED_NOT_IMPLEMENTED`
- Development priority: complete the end-to-end Agent capability first; improve reading quality later
- Required verification: focused local-Splitter/preparation tests, affected core regression, one socket-denied local execution, offline smoke, and repository contract checks
- Historical full-contract, backend, and frozen-asset suites are optional unless affected
- Manual metadata and Chunk preparation: removed from the primary entry
- External model: one OpenAI-compatible adapter behind `ModelGateway`
- Frontend, backend expansion, database, persistence, Docker, general Windows productization, cloud/service deployment, and OCR: frozen
- Implemented now: automatic PDF identity/metadata fallback, direct parser/local-Splitter/reading composition, OpenAI-compatible model adapter, Chunk-reference validation, and Markdown rendering
- Targeted PDF preparation and deep-reading tests: `65/65 PASSED`
- Historical V0.3 external-Splitter run: `PASSED`; V0.4 removes that Splitter service from the formal runtime while retaining the Qwen model boundary
- Observed real run: 56 accepted Chunks; Chinese Markdown contains grounded claims with page and Chunk references
- First quality iteration: `COMPLETE` — model-derived title/authors/year, depth-specific generation guidance, and deterministic invalid Claim-ID normalization
- Quality rerun: `PASSED` — recovered `Attention Is All You Need`, eight authors, and year 2017 while preserving grounded citations
- Paper-scoped Q&A: `COMPLETE` — reuses the current reading Chunks, calls the same Qwen adapter, rejects unknown Chunk references, and returns `QAResponse`, Evidence, and Markdown
- Real Q&A run: `PASSED` — answered the scaled dot-product attention question with page and Chunk references
- Formula/figure/table analysis: `COMPLETE` — text-first explanation, variable meanings, deterministic caption-page alignment, optional rendered-page vision verification, and explicit unconfirmed status
- Real visual run: `PASSED` — `qwen-plus` selected the scientific elements and `qwen3-vl-plus` verified automatically rendered pages; Figure 2 and Table 1 resolved to PDF pages 4 and 6
- Structured PDF understanding: `COMPLETE` — paragraph-like blocks, multi-level sections, equations, Figure/Table captions, and references preserve exact source and Splitter lineage
- Experiment and reproducibility analysis: `COMPLETE` — datasets, baselines, metrics, main results, ablations, conclusion support, hyperparameters, hardware, training details, and missing information are evidence-bound
- Unified report: `COMPLETE` — base reading, deep narrative, experiments, and scientific elements are exported as Markdown and JSON
- Scoped explanation: `COMPLETE` — questions can be constrained by page, section, Chunk, or selected text
- Target-aware vision: `COMPLETE` — labeled regions are cropped when confidently located, with full-page fallback
- arXiv input: `COMPLETE` — `1706.03762` passed a live PDF signature and size check
- Concurrent model execution: `COMPLETE` — experiments, scientific elements, scoped Q&A, and selection explanation run together after base reading; visual pages run concurrently with deterministic output ordering
- Real-paper quality iteration: `COMPLETE` — Attention, ResNet, BERT, and LoRA produced valid Markdown/JSON reports through the live Splitter and configured Qwen text/vision models
- Parsing quality fixes: `COMPLETE` — binary controls, caption variants/references, duplicate labels, numeric table/figure noise, and A-H appendix headings are handled; Chunk sections use DocumentIR while Splitter offsets/hashes/lineage remain unchanged
- Model-output compatibility: `COMPLETE` — efficiency aliases and numeric Chunk ordinals are normalized, one evidence-based experiment reference repair is allowed, and scientific page validation occurs after DocumentIR alignment
- V0.2 freeze: `GOLDEN_BASELINE_ESTABLISHED` — four existing real-paper reports pass `31/31` automated Golden Checks and four manual limitation-source reviews; the current offline checker enforces twelve core invariants per report
- Six-paper regression baseline: `ESTABLISHED` — Attention, ResNet, BERT, and
  LoRA form the deterministic Golden layer; the Chinese sample-difference paper
  and GNN Survey form the separately frozen full-run generalization layer. All
  six retain `10/10` core invariants without claiming identical annotation
  granularity.
- Lightweight Reading Plan and Context Router: `COMPLETE` — one immutable plan is built before concurrency; all specialists share deterministic bounded task context with explicit-scope, section-role, object-lineage, lexical, neighbor, and bounded fallback routing.
- Numeric relation consistency: `COMPLETE` — `REASONING_ERROR` in final relation wording is guarded deterministically across Reading claims, experiments, scientific findings/explanations, and unified Markdown/JSON; numeric ordering is separate from known metric quality direction.
- Core reliability consolidation: `COMPLETE` — Claim–Evidence uses three reliability states and explicit source classes; table status is finalized once, and only independently accepted checks can generate numeric relations or quality language.
- Quality-gate calibration: `COMPLETE` — qualitative table visual verification
  no longer depends on accepting a numeric comparison; rejected/unproven checks
  are still removed. Low-risk unresolved paraphrases are retained only as
  explicit review candidates in `core_reliability`, make the flow complete with
  warnings, and never enter reliable core Claim sections. Numeric, causal,
  universal, novelty/superlative, author-attribution, unknown-Chunk, and lineage
  gates remain strict.
- Offline reliability replay: `COMPLETE` — existing Attention and LoRA reports can be reprocessed without Splitter, text-model, or vision-model calls and without overwriting their source JSON.
- V0.3 focused verification: `86/86 PASSED`; one complete Attention run and one complete LoRA run followed by deterministic replay produce valid final JSON with `10/10` core invariants each; V0.2 Golden remains `31/31 PASS` plus four recorded manual reviews.
- Current scope boundary: single-paper deep-reading Agent only; retrieval handoff, generation, submission, and cross-Agent composition are deferred
- Generalization validation: the repeated HIGH Context Router defect and the two
  final bounded reliability gaps are closed; focused/core tests pass `99/99`,
  Paper A and Paper B each pass `10/10` invariants, offline reliability replay
  passes, and V0.2 Golden remains `31/31`.
- Optional reproducibility Evidence: empty or invalid optional hyperparameter,
  hardware/cost, and training-condition subitems are discarded deterministically
  without weakening core Evidence requirements or inventing Chunk IDs.
- Novelty grounding: unsupported `首次`, `首个`, `唯一`, `最全面`, `首创`,
  English novelty/superlative equivalents, and cross-scope state-of-the-art
  assertions are removed; supported neutral facts remain.
- Paper B front-matter metadata: `COMPLETE` — one authorized follow-up run
  recovered the full title, all six authors, and year 2019 by prepending at most
  two real first-page Chunks to the existing base call; parser, Router, schemas,
  and model-call count are unchanged.
- Repository-wide contract validation: `PASS` — current phase/state are checked
  against tracked authority documents and the capability count is bounded by the
  current feature inventory rather than the obsolete V0.2 snapshot.
- KEY scientific-object backlog: `COMPLETE` — result/comparison/ablation tables
  outrank repeated configuration tables, and missing requested object types use
  bounded scientific text context without changing the parser.
- Semantic partial-reduction backlog: `COMPLETE` — incomplete remnants are not
  emitted, and unsupported synthetic knowledge-framework clauses are removed
  while neutral Evidence-supported survey scope remains.
- Historical pre-publication expanded Agent-core regression: `174/174 PASS`; this includes nine focused
  local-Splitter tests plus the frozen reading/reliability coverage.
- Optional scientific Evidence: empty or invalid optional scientific elements
  are discarded with internal diagnostics; valid elements and core Claims remain
  Evidence-strict.
- Generalization backlog: all three explicitly authorized items are closed.
- V0.3 freeze status: `SINGLE_PAPER_READING_AGENT_CORE_V0_3 / FROZEN`.
- V0.4 local Splitter migration: `COMPLETE` — the three strategies execute in process; the formal CLI has no Splitter URL, HTTP, port, Run storage, or polling dependency; legacy HTTP support is compatibility-only.
- V0.4 Human review: `ACCEPTED` — 2026-07-19，Human 接受单篇论文、单 Agent 的完整流程及当前质量边界。
- V0.4 RC2 delivery hardening: `COMPLETE` — extracted-package `126/126` self-validation, tracked lockfile, source and extracted-package `linux/amd64` image builds, non-root/no-port container checks, CLI help, socket-denied smoke, Windows PowerShell parser, and strict content scan pass.
- V0.4 RC3 non-Docker delivery: `COMPLETE` — current source/package whitelist excludes Docker assets, extracted-package `126/126` self-validation passes, and the Windows native refactor plan remains documentation-only at `DOCUMENTED_NOT_IMPLEMENTED`.
- V0.5 flow-first execution: `COMPLETE` — CLI defaults to `flow_first`; valid
  parse review may continue, base JSON gets one bounded repair attempt, optional
  stages fail independently, `DEEP` receives useful defaults, and unified
  Markdown/JSON records mode, stages, degradations, Q&A, and explanation.
- V0.5 strict execution: `COMPLETE` — explicit `strict` preserves the previous
  parse and optional-stage fail-closed behavior.
- Proxy-compatible model transport: `COMPLETE` — locked `httpx[socks]` support
  prevents an inherited SOCKS proxy from failing before the model request.
- V0.5 live default-mode smoke: `PASSED` — a two-page partially extractable PDF
  completed as `flow_first / COMPLETED_WITH_WARNINGS` with
  `PARSE_REVIEW_CONTINUED`, one grounded Claim, and one Evidence.
- V0.5 RC1 delivery candidate: `COMPLETE` — `0.5.0-rc1` preview and extracted
  package pass `136/136` focused tests, content safety, archive integrity,
  mode/state checks, compileall, and SHA-256 verification; final handoff is
  rebuilt from the clean delivery commit.
- V0.5 RC2 team-lead review package: `READY_FOR_HUMAN_REVIEW` — receiver-side
  Windows output/tool-path patches, 7/7 supplemental tests, and independent
  ResNet `DEEP / COMPREHENSIVE` evidence are bundled without credentials, raw
  PDFs, virtual environments, or portable visual tools.
- Public repository preparation: `COMPLETE` — MIT License, third-party notices,
  contribution guide, security policy, release notes, and a prioritized future
  improvements roadmap are included in the review source tree.
- V0.5.0 stable release: `RELEASED` — promoted directly from the public RC2
  baseline by release decision; no runtime behavior changed and no tests were
  rerun during the promotion.
- Receiver-side Windows validation baseline: `V0_5_0_ACCEPTED_FOR_TEAM_USE` — one Windows x64
  host passed frozen dependency sync, `136/136` delivery regression, real
  `STANDARD` / `DEEP` text-model runs, a real `qwen3-vl-plus` visual run with
  equations, Figure 1/2, and Table 1 verified, real Poppler/ImageMagick target
  crops, native UTF-8 output, and seven supplemental platform tests. Table 2/3
  remain explicitly unconfirmed after a selected retry. This is sufficient for
  the V0.5.0 macOS-development / Windows-receiver team workflow; the historical
  general Windows productization plan remains deferred.
- V0.5.1 team source workflow: `SIMPLIFIED_LOCAL_PASS` — ignored local secrets
  are not read by the tracked-source validator; Mac development commits are
  consumed through `git pull` on Windows. GitHub Actions keeps only
  macOS/Python 3.13 and Windows/Python 3.12. ZIP/SHA packaging, broader matrices, per-file
  provenance, CodeQL and Dependabot are not current delivery gates.
- V0.6 typed diagnostics, metadata, and selection location: `COMPLETE` —
  optional failures use stable safe categories/actions, selection mismatches
  return bounded page/object/snippet candidates without fuzzy auto-binding,
  Equation/Figure/Table object IDs can be explained directly, and first-page
  metadata recovery records per-field source/confidence provenance before the
  model call.
- V0.6 complex table path: `DOCLING_PREFERRED_CANDIDATE_PATH_COMPLETE` —
  PyMuPDF 1.28.0 provides caption/page/hash and geometry anchors; Docling
  2.113.0 TableFormer accurate + cell matching provides the preferred structure
  candidate. All 14 frozen ResNet tables bind to DocumentIR, candidate cells
  increase from 159 to 330, Table 2/3 match the lightweight logical shape check,
  and Table 1 improves but retains two extra structure columns. Candidate grids
  are supplied to visual analysis while remaining `acceptance_ready=false`.
  PyMuPDF and Docling are default locked dependencies while Docling models remain
  an explicit local setup. OCR is out of scope; upstream PyMuPDF license
  compliance remains a user responsibility. Windows Docling revalidation is
  historical/future compatibility work, not the current Mac development gate.
  Current Agent-core regression is `161/161 PASS`.
- V0.6 real Agent smoke: `PASS` — the frozen ResNet PDF completed through the
  formal `flow_first` CLI with local Docling models and `qwen3-vl-plus`, no
  degradation, and Table 1/2/3 all `VISION_VERIFIED`. Target-bbox rendering,
  selected-table-only structure context, and independent row/column cell proof
  accepted one Table 2 comparison and two Table 3 comparisons; rejected checks
  are zero. Table 1 remains qualitative because no independently safe numeric
  comparison was proposed.
- V0.6 stable source gate: `PASS` — version, lockfile, tracked-source validator,
  Agent-core `161/161`, Golden `31/31`, and offline Attention/LoRA replay are
  the bounded release checks.
- V0.6 post-quality-gate real acceptance: `PASS_COMPLETED_WITH_WARNINGS` — the
  frozen ResNet PDF completed PDF preparation, base reading, experiments,
  selected Table 1/2/3 analysis, and core reliability. All three tables are
  `VISION_VERIFIED`; four independently proven table checks are accepted and
  zero rejected. Four low-risk unresolved items remain only as review
  candidates, producing one auditable `UNCERTAIN_CONTENT_RETAINED` degradation;
  core invariants pass `10/10` and offline replay passes. Local-PDF metadata is
  explicitly partial (`Unknown` authors, missing year/arXiv ID) while PDF
  identity and lineage remain intact; this bounded quality gap is non-blocking
  under the V0.6 flow-completeness stop line.
- V0.6 stop line: `FROZEN` — Checkpoint/Resume, cost budgets, expansion to a
  20-paper/6-class benchmark, paper-type-aware planning, OCR, UI/API, database,
  multi-paper comparison, cross-Agent composition, and general Windows
  productization are V0.7/P1/P2 work and do not block V0.6.
- Best-effort follow-up: `DOCUMENTED_NOT_IMPLEMENTED` — preparation-only partial
  reports, checkpointing, typed backoff, adaptive parse thresholds, and
  multi-question isolation remain optimization work.
- V0.6.1 performance closeout: `PROMOTED_AND_VALIDATED` — release identity is
  `v0.6.1 / 0.6.1`. The post-V0.6.0 speed profiles, ordered Claim–Evidence
  concurrency, HTTP connection reuse, and request telemetry passed one bounded
  frozen-ResNet `quality / SELECTED` real smoke: text `8/8`, vision `4/4`,
  no non-200 responses, core invariants `10/10`, and offline replay `PASS`.
  Table 1 remained safely unconfirmed with no findings or checks; Table 2/3
  retained four independently proven checks.
- V0.6.1 immutable identity: local Git tag `v0.6.1`; no GitHub Release or ZIP.
- V0.6.2 table semantic guard: `PROMOTED_AND_VALIDATED` — the generic
  `BEST_VALUE × direction × values` invariant rejects the reproduced FLOPs
  contradiction in live verification and offline replay. ResNet Table 1
  structure remains explicitly `8×9` versus logical `8×7`; this patch does not
  claim perfect structure extraction.
- V0.6.2 immutable identity: local Git tag `v0.6.2`; no GitHub Release or ZIP.
- V0.6.3 Table 1 structure normalization: `PROMOTED_AND_VALIDATED` — a bounded
  sparse-header rule changes
  only Table 1 from `8×9 / 55` to `8×7 / 46`; the other 13 frozen ResNet
  tables retain their shape and cell count. The candidate remains
  `acceptance_ready=false`; legitimate blank-header columns and multi-row merged
  headers remain unchanged. The selected live run rejected all three proposed
  numeric checks and retained `0 accepted / 0 unsafe numeric findings`.
- V0.6.3 immutable identity: local Git tag `v0.6.3`; no GitHub Release or ZIP.
- Post-V0.6.3 table evidence audit: `LOCAL_QUALITY_FOLLOW_UP_COMPLETE` —
  rejected comparisons and cell facts now retain per-item reason codes; a
  configuration table can preserve independently verified single-cell facts
  without upgrading them to causal, comparative, or best-value claims. The
  selected Table 1 live run completed without degradation, rejected one
  contradictory comparison, retained five FLOPs cell facts, and kept
  `0 unsafe numeric findings`.
- V0.6.4 table evidence quality: `PROMOTED_AND_VALIDATED` — numeric identifiers are
  fail-closed, CJK labels/scopes are supported, all proposed comparison/cell-fact
  evidence has accepted/rejected structured audit, and Golden enforces `12/12`
  invariants per report. 晋级前负例审阅发现的数字小数碰撞、CJK 前缀误收、历史
  rejection replay、未确认候选漏审计、Markdown 原始拒绝原因和 cell-fact
  规范化缺口均已关闭。Agent Core is `173/173 PASS`，非空 cell-fact fixture
  `2/2 PASS`。随后完成一次有界真实 ResNet Table 1 验收：
  `flow_first / COMPLETED`、无 degradation，文本 `2/2` 与视觉 `2/2`
  请求全部 HTTP 200；6/6 条审计独立复核通过，接受 5 条单元格事实和 1 条
  `NEUTRAL` FLOPs 算术比较，0 条拒绝、0 条不安全质量措辞。离线 replay、
  `173/173` 回归、Golden `31/31` 和交付门禁均通过。Human 已决定将通过
  验收的 RC1 晋级为 `v0.6.4`；本次只更新稳定身份，不改变运行行为。稳定
  release commit 已推送并通过远端 CI；annotated tag 已从同一 commit 创建。
- Qwen3.7 unified model migration: `PASS_CORE_TABLE_EVIDENCE_EQUIVALENT` —
  text and vision are both `qwen3.7-plus` with thinking explicitly disabled.
  The first bounded non-thinking run completed fail-closed but did not confirm
  Table 1. A general proof-recovery change now lets an uncertain first visual
  pass enter independent same-page verification without accepting unproven
  content. The follow-up run completed with one isolated low-risk review
  candidate; text `3/3` and vision `2/2` requests returned HTTP 200, Table 1
  became `VISION_VERIFIED`, and all five FLOPs cell facts were accepted with
  proof. Agent Core is `176/176 PASS`; the missing optional neutral arithmetic
  comparison is recorded as non-blocking because it is not a required invariant.
- Release commit: `d4426b40062d1ef34e287e498794c1309939dbe0`.
- Remote CI: run `29730685468`, macOS/Python 3.13 and Windows/Python 3.12
  both `SUCCESS`.
- Release tag: annotated `v0.6.4`, remotely verified against the release commit.
- Next action: `TEAM_PULL_AND_USE_V0_6_4`.

The implementation target remains deliberately small: read one text PDF, return grounded deep-reading output, analyze experiments and scientific objects, and answer focused follow-up questions. Multi-paper behavior and productization remain deferred.
V0.4 remains frozen; V0.5.0 remains the current public stable release.
V0.6.4 is released as the current remote stable tag. The public GitHub Release
object remains V0.5.0 by policy. Team receivers can fast-forward `main` and use
the source; the released tag must not be moved or rewritten.
