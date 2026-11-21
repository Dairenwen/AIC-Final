from __future__ import annotations

import json

from patent_agent.adapters.qwen import ModelResult


class FakeModelAdapter:
    """Deterministic model fixture used only when fake mode is explicit."""

    def complete_json(self, *, system_prompt: str, user_prompt: str, temperature=None, max_tokens=None) -> ModelResult:
        marker = next((line.strip() for line in user_prompt.splitlines() if line.startswith("TASK:")), "")
        if marker == "TASK:CANDIDATE_POINTS":
            data = {
                "patent_points": [
                    {
                        "id": "PP-001",
                        "title": "基于冷热度预测与写放大约束的分层缓存自适应迁移方法",
                        "technical_background": "多层缓存中固定阈值难以同时适应突发热点和写密集对象。",
                        "innovation": "联合短窗访问趋势、写放大预算和迁移冷却时间确定对象层级。",
                        "difference": "区别于单一命中率阈值，形成带资源约束的闭环迁移决策。",
                        "feasibility": "可由访问计数器、趋势估计器和迁移执行器实现。",
                    },
                    {
                        "id": "PP-002",
                        "title": "面向热点突变的双时间窗缓存对象温度估计方法",
                        "technical_background": "单时间窗统计对热点突变响应慢。",
                        "innovation": "融合短窗斜率和长窗基线并设置置信门限。",
                        "difference": "兼顾快速响应与抖动抑制。",
                        "feasibility": "仅需常量级对象状态。",
                    },
                    {
                        "id": "PP-003",
                        "title": "具备迁移冷却和回退机制的多级缓存一致性控制系统",
                        "technical_background": "频繁迁移易造成抖动和一致性开销。",
                        "innovation": "在迁移后设置冷却窗，并依据校验结果触发回退。",
                        "difference": "把迁移决策、校验和回退组织为闭环。",
                        "feasibility": "可复用版本号与校验摘要。",
                    },
                ]
            }
        elif marker == "TASK:PRIOR_ART_QUERIES":
            data = {"queries": ["分层缓存迁移", "缓存冷热度预测", "缓存写放大控制"]}
        elif marker == "TASK:DIFFERENCE_ANALYSIS":
            data = {
                "analysis_scope": {
                    "selected_patent_point_id": "PP-001",
                    "search_mode": "fake",
                    "notice": "该分析仅用于演示 Agent 流程，不构成真实现有技术检索结论。",
                },
                "target_features": [
                    {
                        "feature_id": "TF-001",
                        "feature_text": "采集缓存对象的短时间窗访问频率和长时间窗访问基线",
                        "source_summary": "材料明确记载双时间窗访问统计。",
                    },
                    {
                        "feature_id": "TF-002",
                        "feature_text": "根据短窗相对长窗的变化估计缓存对象的热度趋势",
                        "source_summary": "材料明确记载基于短窗和长窗变化估计趋势。",
                    },
                    {
                        "feature_id": "TF-003",
                        "feature_text": "将热度趋势、预计迁移写入量、当前写入预算和冷却状态合成为迁移判定",
                        "source_summary": "材料明确记载趋势、写入预算和冷却状态的联合判定。",
                    },
                    {
                        "feature_id": "TF-004",
                        "feature_text": "迁移时依次复制对象、核验版本与内容摘要并切换映射",
                        "source_summary": "材料明确记载复制、核验和映射切换步骤。",
                    },
                    {
                        "feature_id": "TF-005",
                        "feature_text": "核验失败时恢复旧映射并设置更长冷却时间",
                        "source_summary": "材料明确记载失败回退和差异化冷却。",
                    },
                ],
                "comparisons": [
                    {
                        "feature_id": "TF-001",
                        "publication_number": "FIXTURE-CN-0002",
                        "prior_art_title": "缓存热度预测方法（测试 Fixture）",
                        "disclosure_status": "disclosed",
                        "analysis": "演示记录描述了两个统计窗口的热度估计。",
                        "recommended_claim_role": "background_only",
                    },
                    {
                        "feature_id": "TF-002",
                        "publication_number": "FIXTURE-CN-0002",
                        "prior_art_title": "缓存热度预测方法（测试 Fixture）",
                        "disclosure_status": "partially_disclosed",
                        "analysis": "演示记录提及双窗口估计，但未给出与迁移约束组合的闭环。",
                        "recommended_claim_role": "independent",
                    },
                    {
                        "feature_id": "TF-003",
                        "publication_number": "FIXTURE-CN-0001",
                        "prior_art_title": "分层缓存对象迁移方法（测试 Fixture）",
                        "disclosure_status": "not_found",
                        "analysis": "当前演示记录仅描述按访问频率迁移，未记载写入预算和冷却状态的联合判定。",
                        "recommended_claim_role": "independent",
                    },
                    {
                        "feature_id": "TF-004",
                        "publication_number": "FIXTURE-CN-0001",
                        "prior_art_title": "分层缓存对象迁移方法（测试 Fixture）",
                        "disclosure_status": "uncertain",
                        "analysis": "当前演示摘要不足以判断是否包含版本与摘要核验后的映射切换。",
                        "recommended_claim_role": "dependent",
                    },
                    {
                        "feature_id": "TF-005",
                        "publication_number": "FIXTURE-CN-0001",
                        "prior_art_title": "分层缓存对象迁移方法（测试 Fixture）",
                        "disclosure_status": "not_found",
                        "analysis": "当前演示记录未记载失败回退和延长冷却时间的组合。",
                        "recommended_claim_role": "dependent",
                    },
                ],
                "candidate_distinguishing_features": [
                    {
                        "feature_id": "TF-002",
                        "reason": "与资源约束迁移结合后构成核心趋势输入。",
                        "recommended_claim_role": "independent",
                    },
                    {
                        "feature_id": "TF-003",
                        "reason": "联合写入预算和冷却状态形成核心约束判定。",
                        "recommended_claim_role": "independent",
                    },
                    {
                        "feature_id": "TF-004",
                        "reason": "用于限定迁移执行的一致性核验顺序。",
                        "recommended_claim_role": "dependent",
                    },
                    {
                        "feature_id": "TF-005",
                        "reason": "用于限定失败回退及冷却增强机制。",
                        "recommended_claim_role": "dependent",
                    },
                ],
                "limitations": ["该分析仅用于演示 Agent 流程，不构成真实现有技术检索结论。"],
            }
        elif marker == "TASK:DISCLOSURE_PREVIEW":
            data = {
                "working_title": "一种基于冷热度预测与写放大约束的分层缓存自适应迁移方法及系统",
                "technical_problems": ["固定阈值不能适应热点突变", "频繁迁移造成写放大和抖动"],
                "core_steps": ["采集访问与写入统计", "估计双时间窗冷热度", "计算约束迁移评分", "执行迁移并校验回退"],
                "closest_difference": "以趋势、写放大预算和冷却状态共同驱动闭环迁移。",
            }
        elif marker == "TASK:DISCLOSURE_SECTIONS":
            data = _fake_disclosure_sections()
        elif marker == "TASK:DISCLOSURE":
            # Legacy test helper only. New Runs never request model-authored Markdown.
            data = {"markdown": _fake_disclosure()}
        elif marker == "TASK:DISCLOSURE_SECTION_RECOVERY":
            requested_line = next(
                (
                    line
                    for line in user_prompt.splitlines()
                    if line.startswith("MISSING SECTION FIELDS: ")
                ),
                "MISSING SECTION FIELDS: []",
            )
            requested = json.loads(requested_line.split(": ", 1)[1])
            sections = {
                "title": "一种基于冷热度预测与写放大约束的分层缓存自适应迁移方法及系统",
                "technical_field": "本方案属于计算机存储与缓存管理技术领域，具体涉及分层缓存对象的自适应迁移。",
                "background": "多层缓存系统需要在访问模式变化时平衡响应速度、写入开销和迁移稳定性。",
                "technical_problem": "需要在写入预算约束下识别热点变化，并避免缓存对象在层级之间反复迁移。",
                "technical_solution": "采集双时间窗统计，结合热度趋势、写入预算和冷却状态判定迁移，并在迁移后执行核验与回退。",
                "beneficial_effects": "该方案能够兼顾热点变化响应、迁移写入约束和迁移过程稳定性。",
                "embodiments": "实施时依次采集统计、计算趋势、检查预算与冷却状态、执行对象复制和映射切换，并在核验失败时恢复原映射。",
                "drawing_description": "附图示出统计采集、迁移判定、迁移执行以及核验回退之间的处理关系。",
            }
            data = {field: sections[field] for field in requested}
        elif marker == "TASK:CLAIM_PLAN":
            data = {
                "title": "一种基于冷热度预测与写放大约束的分层缓存自适应迁移方法及系统",
                "recommended_claim_types": ["method", "system"],
                "independent_claims": [
                    {
                        "claim_type": "method",
                        "technical_subject": "一种分层缓存自适应迁移方法",
                        "essential_features": [
                            {"feature_id": "TF-001", "text": "采集双时间窗访问统计", "reason": "提供趋势估计输入"},
                            {"feature_id": "TF-002", "text": "估计缓存对象热度趋势", "reason": "识别访问模式变化"},
                            {"feature_id": "TF-003", "text": "结合写入预算和冷却状态判定迁移", "reason": "形成核心资源约束决策"},
                        ],
                    },
                    {
                        "claim_type": "system",
                        "technical_subject": "一种分层缓存自适应迁移系统",
                        "essential_features": [
                            {"feature_id": "TF-001", "text": "统计采集模块", "reason": "提供双时间窗统计"},
                            {"feature_id": "TF-002", "text": "热度趋势估计模块", "reason": "形成趋势输入"},
                            {"feature_id": "TF-003", "text": "约束迁移判定模块", "reason": "执行核心联合判定"},
                        ],
                    },
                ],
                "dependent_feature_groups": [
                    {
                        "parent_claim_type": "method",
                        "features": [
                            {"feature_id": "TF-004", "text": "复制后核验版本和内容摘要并切换映射", "reason": "限定迁移执行顺序"},
                            {"feature_id": "TF-005", "text": "失败时恢复旧映射并延长冷却时间", "reason": "限定异常回退机制"},
                        ],
                    },
                    {
                        "parent_claim_type": "system",
                        "features": [
                            {"feature_id": "TF-004", "text": "迁移执行与核验模块", "reason": "限定系统执行组件"},
                            {"feature_id": "TF-005", "text": "回退与冷却控制模块", "reason": "限定系统异常处理组件"},
                        ],
                    },
                ],
                "excluded_or_background_features": [],
                "warnings": ["保护范围和术语仍需专利专业人员审核。"],
            }
        elif marker == "TASK:CLAIM_DRAFTING":
            data = {
                "claims": [
                    {
                        "claim_id": "CL-001",
                        "claim_number": 1,
                        "claim_type": "independent_method",
                        "depends_on": [],
                        "text": "1. 一种分层缓存自适应迁移方法，其特征在于，包括：采集缓存对象的短时间窗访问频率和长时间窗访问基线；根据所述短时间窗访问频率相对于所述长时间窗访问基线的变化估计热度趋势；根据所述热度趋势、预计迁移写入量、当前写入预算和冷却状态确定是否执行缓存层间迁移。",
                        "feature_ids": ["TF-001", "TF-002", "TF-003"],
                    },
                    {
                        "claim_id": "CL-002",
                        "claim_number": 2,
                        "claim_type": "dependent_method",
                        "depends_on": [1],
                        "text": "2. 根据权利要求1所述的方法，其特征在于，执行所述缓存层间迁移包括依次复制缓存对象、核验所述缓存对象的版本与内容摘要，并在核验成功后切换缓存映射。",
                        "feature_ids": ["TF-004"],
                    },
                    {
                        "claim_id": "CL-003",
                        "claim_number": 3,
                        "claim_type": "dependent_method",
                        "depends_on": [2],
                        "text": "3. 根据权利要求2所述的方法，其特征在于，在核验失败时恢复迁移前的缓存映射，并使所述缓存对象进入比核验成功时更长的冷却状态。",
                        "feature_ids": ["TF-005"],
                    },
                    {
                        "claim_id": "CL-004",
                        "claim_number": 4,
                        "claim_type": "independent_system",
                        "depends_on": [],
                        "text": "4. 一种分层缓存自适应迁移系统，其特征在于，包括：统计采集模块，用于采集缓存对象的短时间窗访问频率和长时间窗访问基线；热度趋势估计模块，用于根据所述短时间窗访问频率相对于所述长时间窗访问基线的变化估计热度趋势；约束迁移判定模块，用于根据所述热度趋势、预计迁移写入量、当前写入预算和冷却状态确定是否执行缓存层间迁移。",
                        "feature_ids": ["TF-001", "TF-002", "TF-003"],
                    },
                    {
                        "claim_id": "CL-005",
                        "claim_number": 5,
                        "claim_type": "dependent_system",
                        "depends_on": [4],
                        "text": "5. 根据权利要求4所述的系统，其特征在于，还包括迁移执行与核验模块，用于依次复制缓存对象、核验所述缓存对象的版本与内容摘要，并在核验成功后切换缓存映射。",
                        "feature_ids": ["TF-004"],
                    },
                    {
                        "claim_id": "CL-006",
                        "claim_number": 6,
                        "claim_type": "dependent_system",
                        "depends_on": [5],
                        "text": "6. 根据权利要求5所述的系统，其特征在于，还包括回退与冷却控制模块，用于在核验失败时恢复迁移前的缓存映射，并使所述缓存对象进入比核验成功时更长的冷却状态。",
                        "feature_ids": ["TF-005"],
                    },
                ]
            }
        elif marker == "TASK:SELF_CHECK":
            data = {"passed": True, "issues": [], "revised_markdown": ""}
        elif marker == "TASK:REVISE_DISCLOSURE":
            marker_start = user_prompt.find("DRAFT:\n")
            data = {"revised_markdown": user_prompt[marker_start + len("DRAFT:\n") :] if marker_start >= 0 else _fake_disclosure()}
        else:
            data = {"status": "ok", "purpose": "patent_agent_smoke"}
        return ModelResult(data=data, model="fake-deterministic", request_id="fake-0001")

    def smoke_test(self):
        return {"status": "ok", "model": "fake-deterministic", "request_id": "fake-0001"}


def _fake_disclosure() -> str:
    return r"""# 技术交底书

## 一、发明名称

一种基于冷热度预测与写放大约束的分层缓存自适应迁移方法及系统

**技术联系人**：
- 姓名：待填写
- 电话：待填写
- 邮箱：待填写

**专利类型**：发明

---

## 注意事项

本交底书描述一种可独立实施的分层缓存迁移方案。

## 二、技术领域

本方案属于计算机存储与缓存管理技术领域，具体涉及分层缓存对象的自适应迁移。

## 三、背景技术

### 3.1 现有技术

检索说明：在国家知识产权局专利公布公告系统中，以“分层缓存迁移”“缓存冷热度预测”“缓存写放大控制”为检索词进行检索。

多层缓存系统可基于访问频率对缓存对象进行分层，并在达到配置条件后执行迁移。相关著录项、摘要与来源链接见本次运行的 `prior_art/prior_art.json`；正文仅采用其中具有可核验来源的条目，不补造专利号或链接。

### 3.2 需要处理的问题

- 固定阈值对热点突变响应迟缓。
- 频繁跨层迁移会扩大写入量并引起层级抖动。
- 迁移决策与执行后校验相互割裂，异常时缺少回退闭环。

## 四、要解决的技术问题

本发明需要在不超过预设写放大预算的前提下，及时识别热点变化，并通过迁移冷却、执行校验和异常回退避免对象在多个缓存层之间反复摆动。

## 五、技术方案

### 5.1 方案概述

系统包括高速缓存层和容量缓存层。对象访问模式随时间变化，单一长期平均值无法反映短时突发，而只观察短窗又容易受瞬时噪声影响。

### 5.2 系统框图

```mermaid
flowchart LR
  A[统计采集模块] --> B[冷热度估计模块]
  B --> C[约束评分模块]
  C --> D[迁移执行模块]
  D --> E[校验与回退模块]
  E --> B
```

### 5.3 模块功能说明

- 统计采集模块维护短时间窗与长时间窗的访问、写入和命中统计。
- 冷热度估计模块计算对象访问趋势及其置信度。
- 约束评分模块把趋势、目标层收益、写放大预算和冷却状态联合为迁移评分。
- 迁移执行模块按评分顺序执行对象复制、版本切换和旧副本回收。
- 校验与回退模块校验版本、摘要和命中变化，失败时恢复迁移前映射并延长冷却时间。

### 5.4 系统流程说明

```mermaid
flowchart TB
  S1[采集双时间窗统计] --> S2[计算冷热度与趋势]
  S2 --> S3{满足置信门限?}
  S3 -- 否 --> S1
  S3 -- 是 --> S4[计算约束迁移评分]
  S4 --> S5{评分与预算均满足?}
  S5 -- 否 --> S1
  S5 -- 是 --> S6[执行迁移]
  S6 --> S7{校验通过?}
  S7 -- 是 --> S8[更新映射并进入冷却]
  S7 -- 否 --> S9[回退并延长冷却]
  S8 --> S1
  S9 --> S1
```

### 5.4.1 符号与公式

| 符号 | 含义 | 取值范围或量纲 |
|---|---|---|
| \(i\) | 缓存对象索引 | 正整数 |
| \(h_i^{(s)}\) | 对象 \(i\) 的短窗访问频率 | 次/秒，非负 |
| \(h_i^{(l)}\) | 对象 \(i\) 的长窗访问频率 | 次/秒，非负 |
| \(w_i\) | 迁移对象产生的预计写入量 | 字节，非负 |
| \(B\) | 当前周期可用写入预算 | 字节，正数 |
| \(c_i\) | 冷却状态指示量 | 0 或 1 |
| \(M_i\) | 约束迁移评分 | 无量纲 |

短窗相对长窗的归一化趋势为：

\[ M_i = \alpha \frac{h_i^{(s)}-h_i^{(l)}}{h_i^{(l)}+\varepsilon} - \beta \frac{w_i}{B} - \gamma c_i \tag{1} \]

其中，\(\varepsilon\) 为防止分母为零的正数；\(\alpha\)、\(\beta\) 和 \(\gamma\) 均为正权重。仅当评分超过门限、写入预算足够且对象不处于冷却状态时执行迁移。

### 5.5 关键技术参数

| 参数 | 符号 | 示例范围 |
|---|---|---|
| 短窗长度 | \(T_s\) | 5 秒 |
| 长窗长度 | \(T_l\) | 10 分钟 |
| 写入预算 | \(B\) | 按设备耐久度动态设置 |
| 冷却指示量 | \(c_i\) | 0 或 1 |
| 迁移评分 | \(M_i\) | 与预设门限比较 |

## 六、有益效果

- 双时间窗趋势能够兼顾热点突变响应和长期稳定性。
- 写放大预算直接进入迁移判定，避免只优化命中率而损害介质寿命。
- 迁移、校验、回退与冷却构成闭环，降低跨层反复摆动。

## 七、具体实施方式

在一个虚构的软件缓存实施例中，系统每 5 秒更新短窗统计、每 10 分钟更新长窗基线。对象达到评分门限且周期写入预算充足时，从容量层迁移至高速层；校验失败则恢复旧映射并延长冷却窗口。上述数值仅为实施例，不作为权利要求限制。

## 八、技术关键点和欲保护点

1. 以短窗趋势和长窗基线联合估计缓存对象冷热度的方法。
2. 将写放大预算和冷却状态共同纳入迁移评分的方法。
3. 迁移后执行一致性校验，并依据校验结果更新映射或回退的闭环流程。
4. 实现上述方法的统计采集、冷热度估计、约束评分、迁移执行以及校验回退系统。
"""


def _fake_disclosure_sections() -> dict[str, str]:
    return {
        "title": "一种基于冷热度预测与写放大约束的分层缓存自适应迁移方法及系统",
        "technical_field": "本方案属于计算机存储与缓存管理技术领域，具体涉及分层缓存对象的自适应迁移。",
        "background": (
            "多层缓存系统通常基于访问统计执行对象分层与迁移。单一长期统计难以及时反映热点突变，"
            "仅观察短时间窗又容易受瞬时变化影响；频繁迁移还会增加写入开销并造成层级抖动。"
        ),
        "technical_problem": (
            "需要在写入预算约束下识别热点变化，并通过迁移冷却、执行校验和异常回退，"
            "避免缓存对象在多个缓存层之间反复迁移。"
        ),
        "technical_solution": (
            "采集缓存对象的短时间窗访问频率和长时间窗访问基线，根据二者变化估计热度趋势；"
            "结合预计迁移写入量、当前写入预算和冷却状态确定是否执行缓存层间迁移。"
            "执行迁移时依次复制对象、核验版本与内容摘要并切换映射；核验失败时恢复原映射并延长冷却时间。"
        ),
        "beneficial_effects": (
            "通过双时间窗趋势、写入预算和冷却状态的联合判定，能够兼顾热点变化响应、"
            "迁移写入约束和迁移过程稳定性；通过迁移后的核验与回退形成可恢复的执行闭环。"
        ),
        "embodiments": (
            "在一种实施方式中，统计采集模块持续维护短时间窗和长时间窗访问统计，"
            "热度趋势估计模块计算对象访问变化，约束迁移判定模块检查写入预算与冷却状态。"
            "满足迁移条件后，迁移执行模块复制缓存对象并核验版本和内容摘要；"
            "核验成功时切换缓存映射，核验失败时恢复原映射并设置更长的冷却时间。"
        ),
        "drawing_description": "本阶段不生成附图，系统模块关系和处理流程由技术方案及具体实施方式文字说明。",
    }
