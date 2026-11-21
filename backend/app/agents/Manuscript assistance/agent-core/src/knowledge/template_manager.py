"""模板管理器 —— 管理各章节的写作模板"""

from typing import Optional, Dict
from pathlib import Path


# 默认模板定义
DEFAULT_TEMPLATES: Dict[str, str] = {
    "abstract": """## Abstract

[Background] [1-2 sentences about the research field]
[Problem] [1 sentence about the specific problem]
[Method] In this paper, we propose [method name], which [key idea].
[Results] Experimental results on [datasets] demonstrate that [key findings].
[Conclusion] [1 sentence about the significance].

Keywords: [keyword1], [keyword2], [keyword3]
""",

    "introduction": """## 1. Introduction

[Paragraph 1: Research field background and importance]

[Paragraph 2: Specific problem and its challenges]

[Paragraph 3: Limitations of existing approaches]

[Paragraph 4: Our solution - key idea and advantages]

[Paragraph 5: Contributions]
The main contributions of this paper are summarized as follows:
- [Contribution 1]
- [Contribution 2]
- [Contribution 3]

[Paragraph 6 (optional): Paper organization]
The remainder of this paper is organized as follows. Section 2 reviews...
""",

    "related_work": """## 2. Related Work

[Opening paragraph: Overview of related research directions]

### 2.1 [Direction A]
[Representative works in this direction]
[Limitations of this direction]

### 2.2 [Direction B]
[Representative works in this direction]
[Limitations of this direction]

### 2.3 [Direction C]
[Representative works in this direction]
[Limitations of this direction]

[Closing paragraph: Summary of gaps and how this paper differs]
""",

    "method": """## 3. Method

### 3.1 Problem Formulation
[Formal problem definition with mathematical notation]

### 3.2 Overview
[High-level description of the proposed approach]
[Reference to the framework figure]

### 3.3 [Module 1 Name]
[Motivation → Approach → Formulation]

### 3.4 [Module 2 Name]
[Motivation → Approach → Formulation]

### 3.5 Training Objective
[Loss function definition and training strategy]
""",

    "experiment": """## 4. Experiments

### 4.1 Experimental Setup
#### 4.1.1 Datasets
[Dataset descriptions with statistics]

#### 4.1.2 Evaluation Metrics
[Metric definitions]

#### 4.1.3 Implementation Details
[Hyperparameters, hardware, training details]

#### 4.1.4 Baselines
[Baseline methods with brief descriptions]

### 4.2 Main Results
[Comparison table + analysis]

### 4.3 Ablation Study
[Component contribution analysis]

### 4.4 Analysis
[Case study / parameter sensitivity / visualization]
""",

    "conclusion": """## 5. Conclusion

[Paragraph 1: Summary of the work - problem, method, and key findings]

[Paragraph 2: Main contributions recap]

[Paragraph 3 (optional): Limitations]

[Paragraph 4: Future work directions]
""",
}


class TemplateManager:
    """模板管理器"""

    def __init__(self, template_dir: Optional[str] = None):
        self.template_dir = Path(template_dir) if template_dir else None
        self._templates: Dict[str, str] = {}
        self._load_templates()

    def _load_templates(self) -> None:
        """加载模板（优先从文件目录，否则使用默认模板）"""
        # 先加载默认模板
        self._templates = DEFAULT_TEMPLATES.copy()

        # 如果有自定义模板目录，覆盖默认模板
        if self.template_dir and self.template_dir.exists():
            for template_file in self.template_dir.glob("*.txt"):
                section_name = template_file.stem.replace("_template", "")
                self._templates[section_name] = template_file.read_text(encoding="utf-8")

    def get_template(self, section_name: str) -> str:
        """获取指定章节的模板

        Args:
            section_name: 章节名 (abstract/introduction/related_work/method/experiment/conclusion)

        Returns:
            模板文本
        """
        template = self._templates.get(section_name)
        if template is None:
            raise ValueError(
                f"未找到章节 '{section_name}' 的模板。"
                f"可用模板: {list(self._templates.keys())}"
            )
        return template

    def list_templates(self) -> list:
        """列出所有可用模板"""
        return list(self._templates.keys())

    def add_template(self, section_name: str, content: str) -> None:
        """添加或更新自定义模板"""
        self._templates[section_name] = content

        # 如果有模板目录，同时持久化
        if self.template_dir:
            self.template_dir.mkdir(parents=True, exist_ok=True)
            file_path = self.template_dir / f"{section_name}_template.txt"
            file_path.write_text(content, encoding="utf-8")
