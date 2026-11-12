#!/usr/bin/env bash
# 智研 一键部署
#
#   ./start.sh            预检 → 生成密钥 → 构建 → 启动 → 健康检查
#   ./start.sh --check    只做预检，不构建也不启动
#   ./start.sh --light    跳过重型运行时（公式识别 / 文献 PPT）的构建
#   ./start.sh --stop     停止服务（数据保留在具名卷里）
#   ./start.sh --logs     跟踪后端日志
#
# 数据库使用具名卷 zhiyan-pgdata，停止或重启不会丢数据。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
cd "$HERE"

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[0;33m'; DIM='\033[2m'; OFF='\033[0m'
ok()   { printf "${GREEN}  ✓${OFF} %s\n" "$*"; }
warn() { printf "${YELLOW}  !${OFF} %s\n" "$*"; }
bad()  { printf "${RED}  ✗${OFF} %s\n" "$*"; }
step() { printf "\n%s\n" "$*"; }
die()  { bad "$*"; exit 1; }

COMPOSE=(docker compose)
MODE=run

for arg in "$@"; do
  case "$arg" in
    --check) MODE=check ;;
    --light) MODE=light ;;
    --stop)  MODE=stop ;;
    --logs)  MODE=logs ;;
    -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "未知参数：$arg（用 --help 查看用法）" ;;
  esac
done

# ───────────────────────────────────────────────────────── 预检
step "一、环境预检"

command -v docker >/dev/null 2>&1 || die "未找到 docker，请先安装 Docker Desktop 并启动"
docker info >/dev/null 2>&1 || die "Docker 未运行，请先启动 Docker Desktop"
ok "Docker 已运行"

if ! docker compose version >/dev/null 2>&1; then
  command -v docker-compose >/dev/null 2>&1 || die "未找到 docker compose"
  COMPOSE=(docker-compose)
  warn "使用独立版 docker-compose"
else
  ok "docker compose $(docker compose version --short 2>/dev/null)"
fi

command -v python3 >/dev/null 2>&1 || die "未找到 python3（用于生成密钥）"
ok "python3 $(python3 -c 'import sys; print(".".join(map(str,sys.version_info[:3])))')"

# 可用磁盘：镜像约 3–4G，加上数据卷留 15G 余量。
avail_kb=$(df -Pk "$ROOT" | awk 'NR==2 {print $4}')
avail_gb=$((avail_kb / 1024 / 1024))
if [ "$avail_gb" -lt 15 ]; then
  warn "可用磁盘 ${avail_gb}G，构建重型运行时建议至少 15G"
else
  ok "可用磁盘 ${avail_gb}G"
fi

step "二、数据源检查"
DUMP="$ROOT/补充/zhiyan_2026-10-09_11-47-01_pgsql_data.sql"
if [ -f "$DUMP" ]; then
  ok "数据库备份存在（$(du -h "$DUMP" | cut -f1)）"
else
  warn "未找到 补充/ 下的数据库备份，将按空库初始化（无历史记录与附件）"
fi
for d in 补充/uploads 补充/generated 补充/metadata; do
  [ -d "$ROOT/$d" ] && ok "${d}（$(du -sh "$ROOT/$d" | cut -f1)）" || warn "${d} 缺失"
done
if [ -d "$ROOT/文献/arxiv" ]; then
  ok "文献/arxiv 论文语料（$(du -sh "$ROOT/文献/arxiv" | cut -f1)，只读挂载，不复制）"
else
  # compose 里是 bind 挂载，源目录不存在会直接启动失败，所以这里补一个空目录。
  warn "文献/arxiv 不存在，创建空目录以便挂载；知识库将没有 PDF 原文"
  [ "$MODE" = check ] || mkdir -p "$ROOT/文献/arxiv"
fi
WEIGHTS="$ROOT/backend/app/tools/formula_recognition/runtime/unimernet/models/unimernet_base/pytorch_model.pth"
if [ -f "$WEIGHTS" ]; then
  ok "公式识别权重存在（$(du -h "$WEIGHTS" | cut -f1)）"
else
  warn "公式识别权重缺失，该功能将报告未就绪"
fi

if [ "$MODE" = check ]; then
  step "预检完成，未做任何改动。"
  exit 0
fi

if [ "$MODE" = stop ]; then
  step "停止服务"
  "${COMPOSE[@]}" down
  ok "已停止。数据保留在 zhiyan-pgdata / zhiyan-data 卷中。"
  printf "${DIM}  彻底清除数据：docker volume rm zhiyan_zhiyan-pgdata zhiyan_zhiyan-data${OFF}\n"
  exit 0
fi

if [ "$MODE" = logs ]; then
  exec "${COMPOSE[@]}" logs -f backend
fi

# ───────────────────────────────────────────────────────── 密钥
step "三、生成密钥"
python3 "$HERE/generate_secrets.py" | sed 's/^/  /'

if [ "$MODE" = light ]; then
  step "已选择 --light：跳过重型运行时构建"
  export INSTALL_FORMULA_RUNTIME=false
  export INSTALL_PPT_RUNTIME=false
fi

# ───────────────────────────────────────────────────────── 构建
step "四、构建镜像"
printf "${DIM}  首次构建需要下载依赖；含重型运行时时约 10–30 分钟，取决于网络。${OFF}\n"
"${COMPOSE[@]}" build

# ───────────────────────────────────────────────────────── 启动
step "五、启动服务"
"${COMPOSE[@]}" up -d

WEB_PORT="$(grep -E '^WEB_PORT=' .env | cut -d= -f2)"
WEB_PORT="${WEB_PORT:-18080}"

printf "  等待服务就绪"
for _ in $(seq 1 120); do
  if curl -fsS -m 3 -o /dev/null "http://127.0.0.1:${WEB_PORT}/" 2>/dev/null; then
    printf "\n"; ok "前端已就绪"
    break
  fi
  printf "."
  sleep 5
done
printf "\n"

if ! curl -fsS -m 5 -o /dev/null "http://127.0.0.1:${WEB_PORT}/" 2>/dev/null; then
  bad "服务未在预期时间内就绪"
  printf "${DIM}  查看日志：./start.sh --logs${OFF}\n"
  "${COMPOSE[@]}" ps
  exit 1
fi

step "六、启动后自检"
"${COMPOSE[@]}" exec -T backend sh -c 'cat /dev/null' >/dev/null 2>&1 || true
api_status=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${WEB_PORT}/api/v1/health/ready" || echo 000)
[ "$api_status" = "200" ] || die "后端 /health/ready 返回 $api_status，部署未通过健康检查"
curl -fsS -m 30 "http://127.0.0.1:${WEB_PORT}/api/v1/health/ready" | python3 -c 'import json,sys; data=json.load(sys.stdin).get("data", {}); sys.exit(0 if data.get("status") == "ready" and data.get("components", {}).get("database") == "up" else 1)' || die "后端未就绪或健康检查结果无法解析；请检查模型及运行时依赖"
ok "后端就绪检查通过"
"${COMPOSE[@]}" logs backend 2>/dev/null | grep -E '^\[zhiyan\]   ' | tail -6 | sed 's/^\[zhiyan\] //'

step "部署完成"
printf "  打开 ${GREEN}http://127.0.0.1:%s${OFF}\n" "$WEB_PORT"
cat <<'NOTE'

  说明
    · 登录使用原有管理员手机号与密码（未写入本仓库任何文件）。
    · 历史模型配置被保留；如更换了加密密钥，各用户需在模型库重新配置。
    · 27B 自研微调模型本版本不部署，接口保留，详见 .env 里的 PLATFORM_MODEL_ENABLED。

  常用命令
    ./start.sh --logs    跟踪后端日志
    ./start.sh --stop    停止服务（数据保留）
NOTE
