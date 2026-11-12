#!/usr/bin/env bash
# 后端容器入口：准备数据目录 → 恢复并核验数据 → 保留模型配置 → 启动服务。
#
# 每一步都是幂等的，容器重启不会重复恢复或覆盖已有数据。
set -euo pipefail

SOURCE_DIR="${ZHIYAN_SOURCE:-/srv/zhiyan}"
BACKEND_DIR="$SOURCE_DIR/backend"
DATA_DIR="${ZHIYAN_DATA_ROOT:-/data}"
RESTORE_DIR="/restore"
STATE_DIR="$DATA_DIR/.state"
DUMP_FILE="$RESTORE_DIR/zhiyan_2026-10-09_11-47-01_pgsql_data.sql"
PY=/opt/zhiyan/venv/bin/python

log() { printf '[zhiyan] %s\n' "$*"; }
fail() { printf '[zhiyan] 错误：%s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- 前置检查
[ -d "$BACKEND_DIR" ] || fail "源码未挂载到 $SOURCE_DIR，请用 deploy/docker/start.sh 启动"
[ -f "$BACKEND_DIR/wsgi.py" ] || fail "$BACKEND_DIR 下没有 wsgi.py，挂载路径不正确"
[ -n "${DATABASE_URL:-}" ] || fail "DATABASE_URL 未设置"
[ -n "${SECRET_KEY:-}" ] || fail "SECRET_KEY 未设置，请运行 start.sh 生成 .env"
[ -n "${MODEL_CONFIG_ENCRYPTION_KEY:-}" ] || fail "MODEL_CONFIG_ENCRYPTION_KEY 未设置"

mkdir -p "$STATE_DIR" \
         "$DATA_DIR/uploads" "$DATA_DIR/generated" \
         "$DATA_DIR/knowledge-base" "$DATA_DIR/cache/uv" "$DATA_DIR/cache/hf"

# Offline Docling weights are supplied separately from private user data.
# Do not overwrite a cache already populated by a previous deployment.
if [ -d "$RESTORE_DIR/docling_hf/hub" ] && [ ! -f "$STATE_DIR/docling-models-restored" ]; then
  log "恢复 Docling 模型文件（不覆盖已有文件）"
  "$PY" /opt/zhiyan/restore_files.py --source "$RESTORE_DIR/docling_hf" --destination "$DATA_DIR/cache/hf" \
    --base "$DATA_DIR" --state-dir "$STATE_DIR" || fail "Docling 模型恢复失败；已有文件未覆盖"
  touch "$STATE_DIR/docling-models-restored"
fi

export PYTHONPATH="$BACKEND_DIR${PYTHONPATH:+:$PYTHONPATH}"
cd "$BACKEND_DIR"

# ------------------------------------------------------- 等待数据库可连接
log "等待数据库..."
for _ in $(seq 1 60); do
  if "$PY" - <<'PY' >/dev/null 2>&1
import os, sys
from sqlalchemy import create_engine, text
engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
with engine.connect() as connection:
    connection.execute(text("SELECT 1"))
PY
  then break; fi
  sleep 2
done
"$PY" - <<'PY' || fail "数据库始终不可连接"
import os
from sqlalchemy import create_engine, text
engine = create_engine(os.environ["DATABASE_URL"])
with engine.connect() as connection:
    version = connection.execute(text("SHOW server_version_num")).scalar_one()
print(f"[zhiyan] 数据库已连接，server_version_num={version}")
if int(version) < 180000:
    raise SystemExit("备份由 PostgreSQL 18 产生，目标库版本过低")
PY

# --------------------------------------------- 检查数据库与数据卷的一致性
[ -f "$DUMP_FILE" ] || fail "缺少交接数据库备份，不能核验历史数据"
"$PY" /opt/zhiyan/database_state.py before --dump "$DUMP_FILE" --state-dir "$STATE_DIR" \
  || fail "数据库与数据卷状态不一致；未更改数据库"

# --------------------------------------------- 首次运行：恢复或初始化数据库
if [ ! -f "$STATE_DIR/db-initialised" ]; then
  schema_count=$("$PY" - <<'PY'
import os
from sqlalchemy import create_engine, text
engine = create_engine(os.environ["DATABASE_URL"])
with engine.connect() as connection:
    print(connection.execute(text(
        "SELECT count(*) FROM pg_namespace "
        "WHERE nspname IN ('zhiyan','zhiyan1','knowledge_base')"
    )).scalar_one())
PY
  )
  if [ "$schema_count" != "0" ]; then
    log "数据库已有业务表结构，跳过初始化"
  elif [ -f "$DUMP_FILE" ]; then
    log "检测到交接备份，开始恢复（单事务，失败即回滚）"
    "$PY" /opt/zhiyan/database_state.py prepare --dump "$DUMP_FILE" --state-dir "$STATE_DIR" \
      || fail "无法安全准备数据库恢复凭据"
    "$PY" /opt/zhiyan/restore_local.py --dump "$DUMP_FILE" --state-dir "$STATE_DIR" --apply \
      || fail "数据库恢复失败"
  else
    log "未找到交接备份，按空库初始化建表"
    "$PY" scripts/init_database.py || fail "数据库初始化失败"
    log "空库已建表。请用 scripts/init_database.py --admin-phone 创建管理员账号。"
  fi
  "$PY" /opt/zhiyan/database_state.py after --dump "$DUMP_FILE" --state-dir "$STATE_DIR" \
    || fail "数据库恢复后检查失败；未标记恢复完成"
  touch "$STATE_DIR/db-initialised"
fi

# A verified first-time restore may bind provided PDFs to exact paper IDs.
# This is a one-time transaction; existing keys and unrelated databases are
# preserved, and a failure prevents the service from claiming readiness.
log "核对已提供论文与新恢复库的对应关系"
"$PY" "$SOURCE_DIR/deploy/docker/bind_restored_pdfs.py" \
  || fail "论文路径核对未完成；请检查恢复凭据和论文目录"

# ------------------------------------- 首次运行：放置历史附件（不覆盖已有文件）
if [ ! -f "$STATE_DIR/files-restored" ] && [ -d "$RESTORE_DIR" ]; then
  for group in uploads generated; do
    if [ -d "$RESTORE_DIR/$group" ]; then
      log "复制历史 $group ..."
      "$PY" /opt/zhiyan/restore_files.py --source "$RESTORE_DIR/$group" --destination "$DATA_DIR/$group" \
        --base "$DATA_DIR" --state-dir "$STATE_DIR" || fail "历史 $group 恢复失败；已有文件未覆盖"
    fi
  done
  if [ -d "$RESTORE_DIR/metadata" ]; then
    log "复制论文元数据 ..."
    "$PY" /opt/zhiyan/restore_files.py --source "$RESTORE_DIR/metadata" --destination "$DATA_DIR/knowledge-base/metadata" \
      --base "$DATA_DIR" --state-dir "$STATE_DIR" || fail "论文元数据恢复失败；已有文件未覆盖"
  fi
  MILVUS_SOURCE="$RESTORE_DIR/demov15_milvus/milvus_lite.db"
  MILVUS_DEST="$DATA_DIR/knowledge-base/milvus_lite.db"
  # The handoff uses the Milvus Lite 3 directory layout; some exports are
  # single files. Preserve either shape and never overwrite an existing index.
  if [ -e "$MILVUS_SOURCE" ] && [ ! -e "$MILVUS_DEST" ]; then
    MILVUS_STAGING="$MILVUS_DEST.copy-incomplete"
    [ ! -e "$MILVUS_STAGING" ] || fail "发现未完成的 Milvus 索引副本 $MILVUS_STAGING，请检查后手动处理"
    cp -R "$MILVUS_SOURCE" "$MILVUS_STAGING" || fail "Milvus 历史索引复制失败，请检查并手动处理 $MILVUS_STAGING 后重试"
    mv "$MILVUS_STAGING" "$MILVUS_DEST" || fail "Milvus 历史索引就位失败，请检查 $MILVUS_STAGING"
  fi
  touch "$STATE_DIR/files-restored"
fi

# 历史凭据属于各个用户，启动时绝不能自动批量重置。
# 使用新的加密主密钥后旧密文可能无法解密，应由用户分别重新配置。
log "保留历史模型配置；若加密主密钥已更换，旧密钥需由用户重新配置"

# Recover an interrupted keyword-alias switch before accepting application
# traffic. A malformed or ambiguous journal fails closed and keeps all indices.
SEARCH_JOURNAL="${KB_DATA_DIR:-$DATA_DIR/knowledge-base}/.state/elasticsearch-rebuild.json"
if [ -e "$SEARCH_JOURNAL" ] || [ -L "$SEARCH_JOURNAL" ]; then
  case "${ELASTICSEARCH_ENABLED:-0}" in
    1|true|True) ;;
    *) fail "发现未完成的搜索索引切换，但 Elasticsearch 未启用；请检查后重试" ;;
  esac
  log "核对上次中断的关键词索引切换"
  "$PY" -c 'from knowledge_base_runtime.backend.service.es_rebuild import recover_pending_switch; from knowledge_base_runtime.backend.service.retrieval_backends import elasticsearch_client; recover_pending_switch(elasticsearch_client())' \
    || fail "关键词索引切换恢复失败；已保留所有索引和恢复记录"
fi

# --------------------------------------------------------- 运行时能力自检
log "运行时自检："
if [ -x /opt/zhiyan/formula-venv/bin/python ] \
   && /opt/zhiyan/formula-venv/bin/python -c "import torch" >/dev/null 2>&1; then
  weights="$BACKEND_DIR/app/tools/formula_recognition/runtime/unimernet/models/unimernet_base/pytorch_model.pth"
  if [ -f "$weights" ]; then
    log "  公式识别：可用（Linux CPU 运行时 + 本地权重）"
  else
    log "  公式识别：运行时就绪，但缺少模型权重，功能将报告未就绪"
  fi
else
  log "  公式识别：未部署（构建时 INSTALL_FORMULA_RUNTIME=false）"
fi
if [ -x /opt/zhiyan/ppt-venv/bin/python ] \
   && /opt/zhiyan/ppt-venv/bin/python -c "import pptx" >/dev/null 2>&1; then
  log "  文献 PPT：可用"
else
  log "  文献 PPT：未部署"
fi
if [ "${PLATFORM_MODEL_ENABLED:-false}" = "true" ]; then
  log "  平台微调模型：已按环境变量启用"
else
  log "  平台微调模型：未部署（接口保留，设置 PLATFORM_MODEL_ENABLED=true 并填写 QWEN_DPO_* 即可启用）"
fi
log "  对话/写作等 AI 任务：需要每位用户在「个人中心 / 模型库」填写自己的模型 API Key"

log "启动 Gunicorn"
exec /opt/zhiyan/venv/bin/gunicorn --config /opt/zhiyan/gunicorn.conf.py wsgi:app
