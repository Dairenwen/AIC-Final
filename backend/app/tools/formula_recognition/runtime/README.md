# 公式图片转 LaTeX

本目录是已整合到 Zhiyan 后端的本地工具，用于将单张数学公式图片识别为 LaTeX。识别模型采用 [UniMERNet](https://github.com/opendatalab/UniMERNet)（Apache-2.0）。上游源码、配置、模型和 tokenizer 固定在本目录内，Web 服务不会从外部目录加载它们。

## 目录说明

- `recognize.py`：本工具的非交互命令行入口；传入一张图片，标准输出 LaTeX。
- `unimernet/`：上游 UniMERNet 源码，固定到提交 `5a2c80d96b1d2dba447ff18d873e5fb73ba03c35`。
- `unimernet/models/unimernet_base/`：启动脚本首次运行时下载的官方模型及 tokenizer（约 1.2 GB）。
- `.venv/`：启动脚本首次运行时创建的本工具专用 Python 环境。
- `run.sh`、`run.ps1`：Linux/macOS bash 与 Windows PowerShell 的一行式启动脚本。

## 使用

进入本目录，运行相应的一条命令即可。首次运行需要联网，会自动在本目录创建虚拟环境、安装依赖并下载模型；之后直接识别，不会重复安装或下载。请预留至少 4 GB 磁盘空间。

前提：Linux/macOS 需有 Python 3.10+ 和 bash；Windows 需有 Python 3.10+（优先使用 `py -3.10`）。首次模型下载约 1.2 GB。

### macOS

```bash
./run.sh /absolute/path/to/formula.png
```

### Linux / bash

`auto` 会优先使用 CUDA，否则回退到 CPU：

```bash
./run.sh /absolute/path/to/formula.png cuda
```

### Windows PowerShell

```powershell
PowerShell -NoProfile -ExecutionPolicy Bypass -File .\run.ps1 "C:\path\to\formula.png"
```

若 Windows 主机带有可用的 NVIDIA CUDA 环境，可用第二个参数指定：

```powershell
PowerShell -NoProfile -ExecutionPolicy Bypass -File .\run.ps1 "C:\path\to\formula.png" cuda
```

The first run creates `.venv`, installs the inference-only dependencies from
`requirements-inference.txt`, and downloads the UniMERNet checkpoint files
(about 1.2 GB). When `nvidia-smi` is available, the Windows launcher installs
the CUDA 12.4 Torch wheel; otherwise it uses CPU Torch. No system LaTeX
compiler is required for recognition.

输出仅为 LaTeX 字符串，适合被其他脚本、API 或 agent 直接调用。

## 验证

使用上游随附测试图：

```bash
./run.sh unimernet/asset/test_imgs/0000001.png
```

本工具会在首次导入时初始化 PyTorch，启动可能需要数十秒。识别结果仍应人工复核，特别是符号相似、低清晰度或包含大段文字的图片。
