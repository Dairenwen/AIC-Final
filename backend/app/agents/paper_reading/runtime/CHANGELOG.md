# Changelog

## 0.6.4（Local Stable Table Evidence Quality）

- 将本地文本与视觉模型统一切换为 `qwen3.7-plus`，并对两个网关增加可选
  `enable_thinking` 请求参数。当前配置显式关闭文本与视觉思考，其他
  OpenAI-compatible provider 未配置该选项时不发送 provider-specific 字段。
- 首轮视觉不确定的表格现在仍可进入独立同页证据复核；只有第二轮实际接受至少
  一条 comparison 或 cell fact 时才升级为 `VISION_VERIFIED`，否则继续
  fail-closed。真实 Qwen3.7 Table 1 复测恢复 5/5 FLOPs cell facts，0 拒绝、
  0 不安全数值结论，Agent Core `176/176 PASS`。
- 默认思考模式一次基础分析读取超时、一次视觉阶段延迟过长；显式关闭思考后，
  有界真实流程在 `88.751s` 内 `COMPLETED`，文本 `3/3`、视觉 `1/1`
  请求均为 HTTP 200，但 Table 1 为 `VISION_NOT_CONFIRMED`，5 条 cell facts
  全部安全拒绝。该首次迁移失败证据保持不变；后续 proof-recovery 运行以
  `3/3` 文本、`2/2` 视觉请求完成核心事实质量恢复。本地质量门关闭后，
  稳定 commit `d4426b4` 已推送并通过 macOS/Python 3.13 与
  Windows/Python 3.12 CI；annotated `v0.6.4` tag 已从同一 commit 推送。
- Human 已接受 `v0.6.4-rc1` 的 P0 diff 与有界真实验收；源码版本晋级为
  `0.6.4`。运行行为与通过验收的候选保持一致，本次晋级只更新版本、状态、
  Release Notes 和交付门禁。
- 稳定 commit 必须先推送并通过远端 CI，之后才能从同一 commit 创建
  `v0.6.4` tag；当前本地晋级不提前创建 tag，也不创建 GitHub Release 或 ZIP。
- 完成一次有界真实 ResNet Table 1 验收：`flow_first / COMPLETED`、无
  degradation，文本/视觉请求分别 `2/2` 成功且均为 HTTP 200；6/6 条表格
  证据审计独立复核通过，接受 5 条单元格事实和 1 条 `NEUTRAL` FLOPs 算术
  比较，未产生 best、因果或性能优越结论。
- 新报告离线 replay、Agent Core `173/173`、冻结 Golden `31/31`、非空
  cell-fact fixture `2/2` 和完整交付门禁通过；状态进入
  本地稳定晋级门，尚未创建 tag，也未改变远端 `v0.6.3` 稳定标签。
- 表格行列标签改为 Unicode 规范化、数字身份保护和中英文分词匹配；`101-layer`
  不再因共享通用词误匹配 `50-layer`，中文指标、范围和方法标签可进入同一保守门。
- 表格证据新增 `table_evidence_audit_v1`：每个 comparison/cell-fact 候选均保留
  `ACCEPTED` 或 `REJECTED` 决策、原因码、候选哈希、核验证明、页图哈希和核验器身份。
- Golden 每篇报告由 10 项扩展为 12 项核心不变量，新增 cell-fact 的视觉确认、
  字段完整性、有限数值、唯一性与同单元格冲突检查；冻结事实仍为 `31/31 PASS`。
- 新增数字、标点、中文、同值错列、结构化审计和 Golden 负例回归；晋级前审阅
  进一步关闭小数标签碰撞、CJK 前缀误收、历史 rejection replay、未确认视觉候选
  漏审计、Markdown 原始拒绝原因和标点变体 cell 冲突。Agent Core 当前
  `173/173 PASS`，非空 cell-fact fixture `2/2 PASS`。本版本已通过有界真实
  验收并晋级本地稳定版；远端 CI 前不创建 tag，`v0.6.3` 保持不可变。

- 为表格候选增加逐项拒绝审计：未接受的数值比较和单元格事实均记录稳定原因码、
  候选索引及受限安全说明；核验器不可用、漏返回、单元格不清晰、数值/标签/scope
  不一致和 `BEST_VALUE` 语义矛盾继续 fail-closed；数值句过滤兼容科学计数法
  上标和千分位格式。
- 增加独立 `TableCellFact` 合同。配置表可以保留经过第二次视觉核验的单个
  `metric + scope + row + column + value`，但不能由此生成必要性、因果性、
  比较优劣或最佳值结论。
- 增加完整结果表、指数/脚注单元格等小型结构回归；冻结 ResNet Table 1 真实
  定向运行保留 5 个 FLOPs 单元格事实，拒绝 1 个矛盾比较，保持
  `0 accepted comparisons / 0 unsafe numeric findings`。
- 后续稳定 tag 改为在目标 commit 的 `main` CI 成功后创建和推送；已经存在的
  `v0.6.3` tag 保持不可变。

## 0.6.3（Local Stable Table Structure Guard）

- 为 Docling 候选增加有界稀疏表头归一化：仅在首行单列表头锚点完整、幽灵列
  数量为 1–2 且后续确有片段时折叠物理列；Table 1 从 `8×9 / 55 cells`
  收敛为 `8×7 / 46 cells`，其余 13 张冻结 ResNet 表格 shape/cell count
  不变，候选继续保持 `acceptance_ready=false`。
- 增加保守反例门：首行存在合法空表头单元格或多列合并表头覆盖时不折叠列，
  防止把真实未命名数据列误判为幽灵列。
- 当前源码晋级为 `v0.6.3` 本地稳定版；`v0.6.2` 标签保持不可变，不创建
  GitHub Release、ZIP 或额外 Windows 增量复验。

## 0.6.2（Local Stable Table Semantic Guard）

- 修复表格 `BEST_VALUE` 语义门：target 必须按声明方向不劣于 baseline，
  `NEUTRAL` 不能声称 best；在线单元格核验与离线 reliability replay 均删除
  自相矛盾的检查和派生 finding。该缺口由冻结 ResNet Table 1 三次相同参数
  复测中的 `2/3` 真实复现触发，不包含论文特例。
- 收紧视觉提示合同，并将结构化数值关系的质量措辞限定为“指标数值更优/更差”，
  避免资源或配置指标被误读成整体模型表现。
- 当前源码晋级为 `v0.6.2` 本地稳定版；`v0.6.1` 标签保持不可变，不创建
  GitHub Release、ZIP 或额外 Windows 增量复验。

## 0.6.1（Local Stable Performance Closeout）

- 正式 CLI 增加 `fast`、`balanced`、`quality` 三档可选速度配置；档位只提供
  深度和可选分析默认值，显式 `--depth`、`--analyze-*` / `--no-analyze-*`
  继续优先，未选择档位时保持原有执行语义。
- 每次成功运行在 stderr 输出 source acquisition、configuration、base
  reading、optional analysis、output 和 total 耗时；可通过
  `--timing-json-output` 保存结构化性能记录，不改变正式报告 JSON 合同。
- 正式 CLI 对独立 Claim–Evidence 语义核验启用最多 4 路有序并发，并让文本/
  视觉模型网关复用进程内 HTTP 连接；直接库调用继续默认串行。
- timing JSON 增加脱敏的文本/视觉模型请求种类、状态、HTTP 状态码和耗时；
  不记录 Prompt、论文正文、模型响应或凭据。
- 使用冻结 ResNet PDF 对当前性能改动完成一次有界
  `quality / DEEP / flow_first / SELECTED` 真实验收：文本请求 `8/8`、
  视觉请求 `4/4` 成功且全部 HTTP 200，Table 2/3 共保留 4 条 accepted
  checks；Table 1 未确认后保持 0 findings / 0 checks，核心不变量 `10/10`
  且离线 replay 通过。
- 当前源码晋级为 `v0.6.1` 本地稳定版，并以本地 Git tag 建立不可变身份；
  不创建 GitHub Release、ZIP 或发布流水线，不要求 Windows 增量复验。

## 0.6.0（Local Stable Promotion）

- 将已通过全部既定门禁的 `0.6.0-rc1` 本地晋级为 `0.6.0`；本次只修改
  版本与发布状态，不改变 Agent、Parser、Splitter、质量门禁或输出行为。
- 当前源码是已验证的团队稳定版，通过远端 `main` 供组员 `git pull` 使用；
  不创建 `v0.6.0` Git tag 或 GitHub Release，公开 Release 标签仍为
  `v0.5.0`。

- 未发布的 `0.5.2-rc1` 候选已并入本版本，不再作为独立发布目标；诊断、
  元数据、定位、复杂表格路径和质量门禁校准统一以 V0.6 交付。
- 完成质量门禁校准后的冻结 ResNet 最终真实验收：
  `flow_first / COMPLETED_WITH_WARNINGS`，PDF preparation、base reading、
  experiments、Table 1/2/3 和 core reliability 均完成；3/3 表格视觉确认，
  4 accepted / 0 rejected。4 条低风险未决内容只进入 review candidate，
  核心不变量 `10/10` 且离线 replay 通过；RC 已可交由 Human 决定晋级。
- 校准质量门禁：表格视觉可读性与数值 accepted-check 分离，拒绝数值项不再
  自动抹掉安全的定性视觉结论；低风险未决概括只作为显式待核验候选保留，
  不进入可靠核心 Claim，高风险事实门保持不变。
- 当前开发门切换为 Mac 本机源码验证；Windows `v0.5.0` 验收作为历史兼容
  证据保留，不要求 `v0.6.0` Windows 增量复验。
- 建立六篇论文分层回归基线：Attention、ResNet、BERT、LoRA 为四篇确定性
  Golden；中文样本个体差异性训练论文与 GNN Survey 为两篇完整运行泛化论文。
- 验证器收敛为 Mac 本机跟踪源码模式，只读取跟踪文件，不读取忽略的 `.env`、运行
  记录或虚拟环境。
- 组内交付收敛为 Git commit/pull，以及 macOS/Python 3.13、
  Windows/Python 3.12 CI；移除
  ZIP/SHA 打包器、逐文件 provenance、双构建比较、CodeQL 和 Dependabot。
- 修正式发布验证器中残留的 RC1/Review 状态检查。
- 增加类型化可选阶段错误、建议动作和不暴露原始异常的安全降级输出。
- 增加首页元数据恢复与逐字段 provenance；模型调用前即可统一标题、作者、年份
  和 arXiv ID。
- 增加只读 Unicode/空白/连字符定位规范化、未命中候选，以及
  `--explain-object-id`。
- 完成 V0.6 混合表格路径：PyMuPDF 负责精确 caption/page/hash 与几何锚定，
  Docling TableFormer `accurate + cell matching` 作为首选结构候选，并把
  TableGrid 加入视觉表格分析上下文。冻结 ResNet 的 14/14 个表格完成绑定，
  候选单元格由 159 增至 330；Table 2/3 形状正确，Table 1 明显改善但仍有
  额外结构列。OCR 不进入本阶段。
- 取消为 Table 1/2/3 建设重型人工金标准和 GriTS 系统；只保留一次轻量形状
  抽查，并继续要求渲染页面独立核对。
- PyMuPDF 与 Docling 进入默认锁定依赖，Docling 模型继续显式准备；记录
  PyMuPDF 的 AGPL/商业许可证义务。
- 表格视觉路径增加目标 bbox 横纵向聚焦、仅注入当前选中表格的结构上下文，
  以及 ROW/COLUMN 行列方向证据核对；常见表头缩写仍需与完整指标语义一致。
- 冻结 ResNet 正式 Agent 冒烟通过：`flow_first / COMPLETED` 且无降级，
  Table 1/2/3 均视觉确认；Table 2 接受 1 条跨列比较，Table 3 接受 2 条跨行
  比较，拒绝数为 0。Table 1 没有被强制生成数值比较。
- Agent-core 回归扩展为 `161/161 PASS`；`v0.5.0` 的 Mac 开发、Windows 接收
  使用路径已验收。当前源码已进入 `main`，但未发布为新的 GitHub Release。
- V0.6 的 RC 停止线限定为单篇文本 PDF 完整流程、当前可靠性边界、现有六篇
  分层回归和一次冻结 ResNet 真实验收；Checkpoint/Resume、成本预算、扩大到
  20 篇论文、OCR、UI/API 与多篇能力均后移，不阻塞本版本。

## 0.5.0

- 将已公开的 `0.5.0-rc2` 晋级为首个稳定正式版。
- 正式版不改变 Agent 运行逻辑，继续保留单篇、单 Agent、文本型 PDF 的范围边界。
- 发布元数据、版本文件、交付清单和公开文档统一为 `0.5.0`。
- 本次晋级按发布决定跳过重新测试，沿用 RC2 已记录的 `136/136` 交付回归、
  `7/7` Windows 补充测试与真实论文验收证据。
- 正式版状态不等于 `WINDOWS_NATIVE_SUPPORTED`；Windows CI、OCR、复杂表格和
  checkpoint 等缺口继续列入后续改进路线图。

## 0.5.0-rc2（Lead Review Candidate）

- 基于原始 `0.5.0-rc1` 接收方工作副本整理独立组长审阅包；原始 RC1 ZIP 保持不变。
- 新增 Windows 原生 UTF-8/LF 原子 Markdown/JSON 输出与显式
  `pdftoppm`、`pdftotext`、ImageMagick 路径参数。
- Windows 上拒绝批处理 Poppler 包装器和系统 `convert.exe` 误识别，并加入
  7 项接收方补充测试。
- `.env.example` 和文档明确记录可选视觉模型配置；本次实测选择
  `qwen3-vl-plus`，但交付包不包含真实 `.env` 或凭据。
- 新增独立 ResNet arXiv 深度验收：12 页 PDF、23 个科学对象全部分析，
  11 个视觉确认、9 个 accepted table checks、21 个候选检查被可靠性门拒绝。
- 加入脱敏的代表性 Markdown/JSON 证据和组长审阅清单；不包含原始 PDF、
  虚拟环境、模型密钥或便携视觉工具。
- 建立公开仓库发布元数据，补齐 MIT License、第三方声明、贡献指南、安全策略、
  RC2 Release Notes 和按验收门槛组织的未来改进路线图。

## 0.5.0-rc1

- 正式 CLI 默认使用 `flow_first`，并保留显式 `strict` 模式。
- `flow_first` 可在精确 lineage 仍有效时继续 Parse Quality `REVIEW`，基础
  `ReadingAnalysis` 格式错误可定向修复一次。
- 实验、科学对象、论文内问答和选中文本解释独立失败；基础报告与成功的兄弟阶段
  不再因单项失败被丢弃。
- `DEEP + flow_first` 默认执行实验和科学对象分析，支持显式 `--no-analyze-*`。
- Markdown/JSON 写入模式、阶段、降级记录，并内嵌本次问答和解释。
- 保持 PDF identity、精确 Chunk/DocumentIR lineage、未知 Chunk 拒绝、核心
  Evidence 和单篇范围的硬阻断。
- 加入锁定的 SOCKS 代理依赖；`best_effort` 部分报告、checkpoint、OCR 和
  Windows 原生支持仍未实现。

## 0.4.0-rc3

- 2026-07-19，Human 接受并冻结单篇论文、单 Agent 的 V0.4 完整流程；RC3 版本号保持不变，不自动创建正式发布或 Git tag。
- 按 Human 新要求撤出 Dockerfile、容器启动脚本和 Docker 交付声明。
- 新增 Windows 原生运行重构方案，记录当前未验证结论、风险、建议重构、支持矩阵和验收门。
- 保留 RC2 已完成的解压包独立自检、`164/126` 测试边界、跨平台 `uv.lock` 和严格内容扫描修复。
- 本版本不实现或宣称 Windows 原生支持，不改变 Agent、Parser、Local Splitter、Reading Plan、Evidence 或输出合同。

## 0.4.0-rc2

- 修复 RC1 交付包无法在解压目录独立执行交付验收的问题，新增接收方可运行的 `validate_delivery.py`。
- 区分源码仓库 `164/164` 全量回归与交付包 `126/126` 聚焦回归，不再让接收方误以为两者可由同一文件集复现。
- Agent Core 与交付包版本统一为 `0.4.0-rc2`，并纳入跨平台 `uv.lock`。
- 新增 Docker Desktop + Linux 容器交付，包括非 root 镜像、Poppler、Windows PowerShell 入口和只读输入/独立输出挂载。
- 内容安全门严格拒绝主机路径、指定局域网地址、明文密钥前缀和静态 `Bearer` 值；运行时认证头的字节语义保持不变。
- Parser、Local Splitter、Reading Plan、Context Router、Claim–Evidence 和最终输出合同均未改变。

## 0.4.0-rc1

- 将三种冻结 Splitter 策略迁入 `agent-core`，通过 `LocalSplitterGateway` 在同一 Python 进程内执行。
- 正式 CLI 移除 Splitter URL、端口、HTTP、Run storage 和轮询依赖；策略仍需显式选择。
- 保留确定性执行身份、Chunk ID、config hash、offset、section/parent 和 DocumentIR lineage 校验。
- `HttpSplitterGateway` 仅作为兼容对照实现保留；V0.3 阅读与可靠性层不变。
- 完整 Agent-core、根合同、后端兼容、Golden Baseline、离线 smoke 与交付包复验均作为 RC1 发布门重新执行。
- 当前权威状态为 `READY_FOR_HUMAN_REVIEW`；本条目是 Review Candidate，不代表 Human 已冻结 V0.4。

## 0.3.0

- 在并发分析前建立不可变 Reading Plan，并通过 Context Router 为各任务选择有界上下文。
- 完成实验、复现信息、Equation、Figure 和 Table 的论文内分析。
- 支持整篇、章节、页面、Chunk 和选中文本范围的论文内问答。
- 将 Claim–Evidence 可靠性统一为 `SUPPORTED`、`PARTIALLY_SUPPORTED` 和 `INSUFFICIENT_EVIDENCE` 三态，并区分作者陈述、Evidence 推导和 Agent 推断。
- 增加 Numeric Relation Guard；指标方向未知时不生成“更优/更差”结论。
- 仅允许独立 accepted table checks 生成数值关系，视觉未确认表格不携带 numeric checks。
- 对缺少 Evidence 的新颖性、最高级和跨范围 state-of-the-art 声明进行降级或移除。
- 完成两篇论文泛化门、完整 Agent-core 回归、Golden Baseline 和最后三项 backlog，冻结 V0.3。

本文件只记录交付版本变化，不复述完整开发历史。
