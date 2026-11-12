# 智研 本地一键部署

本目录提供不依赖华为云、不涉及任何付费服务的本机部署方式。PostgreSQL、Redis、
Nginx、Gunicorn 全部运行在本机容器内；AI 能力由使用者自己填写的模型 API Key 驱动。

华为云相关的部署脚本保留在 `../huawei/`，本目录不替代它。

## 一键启动

```bash
cd deploy/docker
./start.sh
```

脚本依次完成：环境预检 → 生成密钥 → 构建镜像 → 启动服务 → 健康检查，
结束后打印访问地址（默认 <http://127.0.0.1:18080>）。

想先看一遍预检结果而不做任何改动：

```bash
./start.sh --check
```

跳过重型运行时（公式识别约 1.8G、文献 PPT），几分钟即可起来：

```bash
./start.sh --light
```

其他命令：

```bash
./start.sh --logs    # 跟踪后端日志
./start.sh --stop    # 停止服务，数据保留在具名卷中
```

## 前置条件

| 项目 | 要求 |
| --- | --- |
| Docker | Docker Desktop 已安装并处于运行状态 |
| python3 | 仅用于生成密钥 |
| 磁盘 | 构建需额外空间；论文、备份、解压后的数据和 Docker 缓存均需另计 |
| 网络 | 首次构建需要下载依赖；之后可离线运行 |

## 数据从哪里来

| 来源 | 用途 | 挂载方式 |
| --- | --- | --- |
| `补充/zhiyan_2026-10-09_11-47-01_pgsql_data.sql` | 100 张表、19964 行历史数据 | 只读，首次启动时恢复 |
| `补充/uploads`、`补充/generated`、`补充/metadata` | 历史附件与论文元数据 | 只读，首次启动时复制进数据卷 |
| `文献/arxiv` | 31G 论文原文 | **只读挂载，不复制**，避免占用翻倍 |
| `backend/.../unimernet_base` | 1.3G 公式识别权重 | 随源码只读挂载 |

缺少数据库备份时按空库建表启动，此时没有历史记录，需要用
`backend/scripts/init_database.py --admin-phone <手机号>` 创建管理员账号。

## 数据持久化

数据库使用具名卷 `zhiyan-pgdata`，上传与生成结果使用 `zhiyan-data`。
停止或重启容器都不会丢数据——这解决了交接记录中"数据库容器使用临时存储，
停止/重启会丢失本机测试库"的问题。

需要彻底清除数据重新开始：

```bash
./start.sh --stop
docker volume rm zhiyan_zhiyan-pgdata zhiyan_zhiyan-data
```

备份数据库：

```bash
docker compose exec -T db pg_dump -U zhiyan zhiyan | gzip > zhiyan-$(date +%F).sql.gz
```

## Redis 用来做什么

后端把两类短期状态放在 Redis 里：短信验证码的发送间隔（同一手机号每分钟一条），
以及智能体就绪状态的缓存。这样重启后端或运行多个后端实例时，限流和缓存仍然一致。
没有配置 `REDIS_URL` 或 Redis 连不上时，这两项自动退回进程内存，服务照常运行；
`/api/v1/health/ready` 返回的 `components.redis` 会显示当前状态
（`up`、`unavailable` 或 `not_configured`）。

本机使用 compose 里的 Redis 容器；华为云生产环境改用 RDS for PostgreSQL 和
DCS Redis，不在云主机上运行这两个容器，见 `../huawei/生产部署说明.txt`。

## 前端开发

改界面时不必每次重建镜像。让后端继续跑在 Docker 里，另开一个带热更新的前端：

```bash
cd frontend
ZHIYAN_DEV_API_TARGET=http://127.0.0.1:18080 npm run dev   # http://127.0.0.1:5173
```

图标在 `frontend/src/components/icons/glyphs.ts`，智能体和工具的名称文案集中在
`frontend/src/catalog/presentation.ts`，色板和字号在 `frontend/src/styles/tokens.css`。

## 密钥与模型

`start.sh` 会调用 `generate_secrets.py` 由 `.env.example` 生成 `.env`（权限 0600），
并随机生成 `SECRET_KEY`、`MODEL_CONFIG_ENCRYPTION_KEY`、`POSTGRES_PASSWORD`。
交接环境中的旧密钥不再使用。重复运行不会覆盖已填写的值。

智能体在没有指定模型时使用用户在模型库里标为「默认」的对话模型；只有一个已测试
通过的对话模型时也会直接用它。自研微调模型未启用时，界面不提供「平台通用模型」
这个选项。

交接数据库可能包含用户的加密模型凭据，启动不会批量清空或重置模型配置。
如果部署时使用了新的加密主密钥，旧密文无法解密，各用户需要在「个人中心 / 模型库」
重新配置自己的 API Key。旧密文属于私有数据，不可放入公开代码包。

标准模型为 DeepSeek 官方接口（`https://api.deepseek.com`，模型 `deepseek-flash`）。
模型库同时支持通义千问、智谱 GLM 以及任意 OpenAI 兼容网关，可在界面上切换。

## 功能可用性

| 功能 | 状态 | 说明 |
| --- | --- | --- |
| 登录、项目、智能体/工具/技能目录、知识库、系统配置 | 可用 | 不依赖外部模型 |
| 引用格式化、表格转换、文本统计、Markdown 转 Word | 可用 | 纯本地计算 |
| 公式图片转 LaTeX | 可用 | 运行时在镜像内为 Linux 重建，权重随包分发 |
| 文献 PPT 绘制 | 可用 | 同上 |
| 对话、文稿辅助、创新点、论文精读、专利撰写、合规检测、学术速递、绘图、翻译、审稿意见、投稿推荐 | 需配置 Key | 依赖使用者自己的模型 API |
| 向量检索与重排 | 未启用 | 需本地 BGE-M3 权重，本包不含，默认走 API 后端 |
| 自研 27B 微调模型 | 未部署 | 显存要求过高；接口保留，见下 |

### 启用自研微调模型

模型未随本包部署。自行部署好 OpenAI 兼容端点后，在 `.env` 中设置：

```ini
PLATFORM_MODEL_ENABLED=true
QWEN_DPO_BASE_URL=https://你的端点/v1
QWEN_DPO_MODEL=你的模型名
QWEN_DPO_API_KEY=你的密钥
```

重启后界面上会出现「平台通用模型」。未启用时相关接口返回
`PLATFORM_MODEL_DISABLED`，界面不展示该模型。

## 目录内容

| 文件 | 作用 |
| --- | --- |
| `start.sh` | 一键部署入口 |
| `docker-compose.yml` | 四个服务与两个具名卷 |
| `Dockerfile.backend` | 后端依赖与两个重型运行时的 Linux 重建 |
| `Dockerfile.web` | 前端构建 + Nginx |
| `nginx.conf` | 静态资源与 `/api` 反向代理 |
| `gunicorn.conf.py` | 单 worker + 多线程，长超时 |
| `entrypoint.sh` | 等待数据库 → 首次恢复 → 清除遗留凭据 → 自检 → 启动 |
| `generate_secrets.py` | 生成 `.env` 与系统密钥 |
| `restore_local.py` | 本机恢复备份，复用 `../huawei/restore_database.py` 的校验 |
| `.env.example` | 配置模板（`.env` 不进版本库） |

## 设计说明

**为什么源码是只读挂载而不是复制进镜像。** 源码树含 1.3G 公式识别权重与 1.4G
vendored 源码，复制进镜像会让镜像多出数 G，且每次构建都要重新传输。镜像只承载
依赖环境，可写数据全部落在数据卷上。

**为什么重型运行时要在镜像里重建。** 仓库里
`backend/app/tools/formula_recognition/runtime/.venv` 与
`补充/literature_ppt_tools/.venv` 都是 macOS arm64 虚拟环境（`pyvenv.cfg` 指向
`/opt/homebrew`，原生扩展是 Mach-O），而容器内是 Linux，**原样打包无法运行**。
重建使用 `requirements.lock.txt` 而非 `requirements-inference.txt`：后者的
`torch>=2.2.2` 没有上界，会解析出要求 `numpy>=2` 的 pyarrow，与清单钉死的
`numpy<2` 冲突，导入即失败。torch 走官方 CPU 索引，Linux 轮子约 160MB。

**为什么 Gunicorn 只有一个 worker。** 现有任务线程与内嵌知识库运行时不支持多进程：
多 worker 会让同一任务被重复调度。并发通过线程提供。

## 复现步骤（供第三方验证）

```bash
# 1. 取得源码与数据目录（补充/、文献/ 为私有数据，不随开源仓库分发）
cd newcode/zhiyan_sub/deploy/docker

# 2. 预检
./start.sh --check

# 3. 部署
./start.sh

# 4. 验证
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18080/
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18080/api/v1/health/ready

# 5. 浏览器打开 http://127.0.0.1:18080 ，使用管理员账号登录，
#    在「个人中心 / 模型库」填写模型 API Key 后运行 AI 任务。
```
