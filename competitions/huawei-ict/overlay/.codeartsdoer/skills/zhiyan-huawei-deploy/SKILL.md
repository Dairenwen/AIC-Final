---
name: zhiyan-huawei-deploy
description: 在已获授权的华为云 Linux 服务器上检查和部署智研 Docker 项目，配合 CodeArts 官方部署 Skills 执行并保存真实过程记录。
---

# 智研华为云部署

本技能是项目适配指引。赛题要求的现有智能体和 Skills 应实际使用华为提供的能力；不要把本文件本身或模拟对话当作云端操作证明。

1. 核实待部署代码版本、私有数据、生产配置、域名与证书，以及目标 ECS 和云服务。配置与数据包分开保管；不要输出或上传密钥、个人数据及未脱敏日志。
2. 确认服务器规格、地域、计费方式、代金券适用范围、关机后的存储和公网费用，以及预算。购买、开通、删除资源或发起可能收费的模型请求前，展示具体项目和费用并等待负责人批准。
3. 使用华为现有 `codearts-deploy-env-management` 等能力时先核对项目、权限、地域和已批准的目标主机。官方 `codearts-deploy-orchestration` 示例面向 JAR，可能自动创建 ECS、重装系统或跳过确认，不能原样用于本项目。禁止默认购买、创建、重装或删除资源，禁止关闭 TLS 验证或执行超出授权范围的命令。
3a. 云服务关联：本项目的生产编排要求数据库使用 RDS for PostgreSQL 18、缓存使用 DCS Redis，两者与目标 ECS 位于同一 VPC。通过华为现有智能体和 Skills 开通或关联这两项服务时，先向负责人展示规格、计费方式和预计费用并取得批准；创建后把内网地址、端口、库名和账号写入目标主机上的 `deploy/huawei/.env.production`（`POSTGRES_HOST`、`POSTGRES_SERVER_PORT`、`POSTGRES_DB`、`POSTGRES_USER`、`POSTGRES_PASSWORD`、`REDIS_URL`），不要把密码写进对话、日志或截图。建库语句和安全组要求见 `deploy/huawei/生产部署说明.txt` 第 2a、2b 条。保存实际的 Skill 调用回执和脱敏后的实例信息作为证据。
4. 候选方案：按华为官方 CodeArts Deploy 文档，在已批准的主机环境中使用空白模板与“执行 shell 命令”步骤，依次运行本包中的 `--check`、`--apply`，关闭失败后继续；不要将官方 JAR 模板替换成临时生成的部署流程。主机上的包路径和执行账户应由负责人确认，账户仅取得运行 Docker、读取该项目私有配置和证书所需权限，不默认使用 sudo。私有部署包应通过负责人批准的安全通道传入主机，不得擅自上传制品仓库；命令和日志不得输出密钥或历史用户数据。
   两条命令在目标 Linux 主机项目根目录执行：`bash deploy/huawei/deploy.sh --check` 和 `bash deploy/huawei/deploy.sh --apply`。`--check` 通过后才能执行 `--apply`；需在目标环境实测退出状态、日志、容器持续运行和证据留存。该方案尚未完成云端验证，脚本本身不会购买云资源。
5. 保留命令退出状态和脱敏后的实际 CodeArts Skill 调用、云资源关联及部署记录。基础健康检查通过不等于全部业务、模型或云上演示已通过；分别记录真实验收结果和未验证项。
6. 本次不部署 27B 权重或 GPU；外部模型接口按用户后续配置启用。保留历史数据，禁止自动重跑任务、清空用户配置或释放云资源。结束云端测试时按已确认的策略停止服务器，并核查仍在计费的云硬盘和公网资源。

官方说明：
- 执行 shell 命令：https://support.huaweicloud.com/usermanual-deployman/deployman_hlp_1002.html
- 主机部署：https://support.huaweicloud.com/usermanual-deployman/deployman_hlp_00004.html
