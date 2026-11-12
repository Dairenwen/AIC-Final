# 验证记录

验证时间：2026-07-28  
工具版本：UniMERNet 上游提交 `5a2c80d96b1d2dba447ff18d873e5fb73ba03c35`  
模型：官方 `wanderkid/unimernet_base`（本地文件名 `pytorch_model.pth`）  
运行环境：macOS / Python 3.12.13 / PyTorch 2.13.0 / Apple MPS

## 已完成检查

1. 上游源码已浅克隆到 `unimernet/`，模型和 tokenizer 已下载到 `unimernet/models/unimernet_base/`。
2. 依赖安装在本目录的 `.venv/`，并确认 MPS 可用。
3. 用上游提供的 `asset/test_imgs/0000001.png` 执行了真实端到端推理，而非只检查导入。

整合后的执行命令（从 `backend` 目录执行）：

```bash
app/tools/formula_recognition/runtime/.venv/bin/python \
  app/tools/formula_recognition/runtime/recognize.py \
  app/tools/formula_recognition/runtime/unimernet/asset/test_imgs/0000001.png
```

识别输出：

```latex
\begin{array} { r l } { \mathrm { M i n i m i s e ~ } } & { { } J ( u . ; s , y ) = \mathbb { E } \left[ \int _ { s } ^ { T } \left( u _ { t } ^ { 2 } + 1 \right) d t - \ln \left( \cosh \left( X _ { T } \right) \right) \right] } \\ { \mathrm { s u b j e c t ~ t o ~ } } & { { } \left\{ \begin{array} { l l } { d X _ { t } = 2 u _ { t } d t + \sqrt { 2 } d W _ { t } , t \in [ s , T ] } \\ { X _ { s } = y } \\ { u _ { t } \in [ - 1 , 1 ] , \quad t \in [ s , T ] } \end{array} \right. } \end{array}
```

该结果与样图的“最小化目标 + 随机微分约束”结构相符，可被 LaTeX 引擎解析。模型在文本字符之间输出额外空格是其常见格式，TeX 会忽略这些空格；接入生产流程时仍建议对关键公式进行人工核对。

## 兼容性修正

上游 `configs/demo.yaml` 保留了旧权重文件名 `unimernet_base.pth`，而当前官方模型仓库提供的是 `pytorch_model.pth`。本工具在 `recognize.py` 内部映射到实际文件，没有修改上游源码。
