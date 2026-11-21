# 第三方声明

## patent-disclosure-skill

- **项目**：patent-disclosure-skill
- **仓库**：https://github.com/handsomestWei/patent-disclosure-skill
- **固定提交**：`e4bcb12d02490e8b44e5c9c7cd574644dd70da41`
- **本地路径**：`vendor/patent-disclosure-skill/`
- **许可证**：MIT，copyright (c) 2026 handsomestWei
- **复用范围**：`SKILL.md`、安装与使用说明、Prompt、模板、DOCX/PPTX/Markdown 与 Mermaid/LaTeX 工具、CNIPA Playwright crawler/parser/search 工具、依赖声明、测试和公开/虚构示例。

上游源码以固定副本放入本仓库，最终交付包不需要在运行时再次下载。项目自己的模型调用、检索标准化、解析路由、状态、中断、CLI、安全处理和测试均位于 vendor 目录之外。上游完整许可证保留在 `vendor/patent-disclosure-skill/LICENSE`。

## 工具依赖锁

Vendored 文档工具通过 `vendor/patent-disclosure-skill/tools/package.json` 声明 Node 依赖，并通过 `package-lock.json` 固定依赖图。其中包括 `@mermaid-js/mermaid-cli`、Puppeteer、Chromium 相关工具及其各自按原许可证发布的传递依赖。`node_modules/` 和下载的浏览器二进制属于安装产物，不进入源码交付归档。

Python 运行依赖由 `pyproject.toml` 声明，并在 `requirements.lock` 中固定精确版本和发行包 Hash。各 Python 包继续适用其上游许可证。
