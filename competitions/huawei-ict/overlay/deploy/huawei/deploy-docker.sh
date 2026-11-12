#!/usr/bin/env bash
# Production Docker preflight and deployment entry point.
set -euo pipefail
CODE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ROOT="$(python3 "$CODE_ROOT/deploy/huawei/runtime_root.py" "$CODE_ROOT")" || exit 1
HERE="$ROOT/deploy/huawei"
ENV_FILE="$HERE/.env.production"
MODE="${1:---check}"
[[ "$MODE" == --check || "$MODE" == --apply ]] || { echo '用法: bash deploy/huawei/deploy-docker.sh [--check|--apply]' >&2; exit 2; }
[[ $# -le 2 ]] || { echo '参数过多' >&2; exit 2; }
if [[ $# -eq 2 ]]; then
  [[ "$MODE" == --apply && "$2" == --accept-initial-changes ]] || { echo '仅初始化失败后可使用 --apply --accept-initial-changes' >&2; exit 2; }
fi
[[ "$(uname -s)" == Linux ]] || { echo '生产部署要求 Linux；本机测试请使用 deploy/docker/start.sh。' >&2; exit 1; }
for cmd in docker python3 openssl curl; do command -v "$cmd" >/dev/null || { echo "缺少 $cmd" >&2; exit 1; }; done
docker info >/dev/null 2>&1 || { echo 'Docker 未运行' >&2; exit 1; }
docker compose version >/dev/null || { echo '需要 Docker Compose 插件' >&2; exit 1; }
DOCKER_ROOT="$(docker info --format '{{.DockerRootDir}}')"
python3 "$CODE_ROOT/deploy/huawei/production_resources.py" --docker-root "$DOCKER_ROOT"
[[ -r "$ENV_FILE" ]] || { echo '缺少 deploy/huawei/.env.production，请先配置；不会自动生成或覆盖生产凭据。' >&2; exit 1; }
[[ -r "$HERE/tls/fullchain.pem" && -r "$HERE/tls/privkey.pem" ]] || { echo '缺少 TLS 证书或私钥。' >&2; exit 1; }
[[ -f "$ROOT/补充/zhiyan_2026-10-09_11-47-01_pgsql_data.sql" && -d "$ROOT/文献/arxiv" ]] || { echo '缺少数据库备份或论文目录，禁止自动初始化空库。' >&2; exit 1; }
[[ -d "$ROOT/补充/docling_hf/hub/models--docling-project--docling-layout-heron" && -d "$ROOT/补充/docling_hf/hub/models--docling-project--docling-models" ]] || { echo '缺少 Docling 离线模型；请先按交付说明解压模型包。' >&2; exit 1; }
[[ -f "$CODE_ROOT/backend/app/tools/formula_recognition/runtime/unimernet/models/unimernet_base/pytorch_model.pth" ]] || { echo '缺少公式识别模型权重。' >&2; exit 1; }
MAX_MAP_COUNT="$(cat /proc/sys/vm/max_map_count)"
[[ "$MAX_MAP_COUNT" =~ ^[0-9]+$ ]] && (( MAX_MAP_COUNT >= 1048576 )) || { echo 'Elasticsearch 8.19 需要宿主机 vm.max_map_count 至少为 1048576；请先由服务器管理员设置。' >&2; exit 1; }
python3 - "$ENV_FILE" <<'PY'
import base64, os, re, stat, sys
from urllib.parse import urlparse
p = sys.argv[1]
if os.stat(p).st_mode & (stat.S_IRWXG | stat.S_IRWXO):
    raise SystemExit('生产环境文件权限过宽；请设置为 0600。')
v = {}
for line in open(p, encoding='utf8'):
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        k, value = line.split('=', 1)
        if k in v:
            raise SystemExit('生产环境文件存在重复配置项。')
        v[k.strip()] = value.strip().strip('"\'')
for key in ('SECRET_KEY', 'MODEL_CONFIG_ENCRYPTION_KEY', 'POSTGRES_PASSWORD', 'PUBLIC_ORIGIN'):
    if not v.get(key): raise SystemExit(f'生产配置缺少 {key}')
try:
    if len(base64.urlsafe_b64decode(v['MODEL_CONFIG_ENCRYPTION_KEY'])) != 32: raise ValueError()
except Exception:
    raise SystemExit('模型配置加密密钥格式错误。')
u = urlparse(v['PUBLIC_ORIGIN'])
if u.scheme != 'https' or not u.hostname or u.username or u.password or u.port not in (None, 443) or u.path not in ('', '/') or u.query or u.fragment:
    raise SystemExit('PUBLIC_ORIGIN 必须是 HTTPS 域名，且不含路径或凭据。')
platform_enabled = v.get('PLATFORM_MODEL_ENABLED', 'false').lower()
if platform_enabled not in ('0', 'false', 'no', '1', 'true', 'yes'):
    raise SystemExit('PLATFORM_MODEL_ENABLED 必须为 true 或 false。')
if platform_enabled in ('1', 'true', 'yes'):
    model_url = urlparse(v.get('QWEN_DPO_BASE_URL', ''))
    if model_url.scheme not in ('http', 'https') or not model_url.hostname or model_url.username or model_url.password or not v.get('QWEN_DPO_MODEL'):
        raise SystemExit('启用外部平台模型时必须填写有效地址和模型名称，地址不得包含账号或密码。')
if not re.fullmatch(r'[A-Za-z0-9_-]+', v['POSTGRES_PASSWORD']):
    raise SystemExit('数据库密码需要 URL 安全字符，避免连接串解析错误。')
# Production keeps its data in managed services, not in containers on the host.
host = v.get('POSTGRES_HOST', '')
if not re.fullmatch(r'[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?', host) or host in ('db', 'localhost'):
    raise SystemExit('请在 POSTGRES_HOST 填写 RDS for PostgreSQL 实例的内网地址（不含端口和协议）。')
if not re.fullmatch(r'[0-9]{1,5}', v.get('POSTGRES_SERVER_PORT', '5432')) or not 0 < int(v.get('POSTGRES_SERVER_PORT', '5432')) < 65536:
    raise SystemExit('POSTGRES_SERVER_PORT 必须是有效端口。')
cache = urlparse(v.get('REDIS_URL', ''))
if cache.scheme not in ('redis', 'rediss') or not cache.hostname or cache.hostname in ('redis', 'localhost'):
    raise SystemExit('请在 REDIS_URL 填写 DCS Redis 的内网连接地址，例如 redis://:密码@地址:6379/0。')
for key in ('KB_MILVUS_ENABLED', 'ELASTICSEARCH_ENABLED'):
    if v.get(key, '').lower() not in ('1', 'true', 'yes'):
        raise SystemExit(f'完整检索要求启用 {key}。')
for key in ('INSTALL_FORMULA_RUNTIME', 'INSTALL_PPT_RUNTIME'):
    if v.get(key, 'true').lower() not in ('1', 'true', 'yes') or os.environ.get(key, 'true').lower() not in ('1', 'true', 'yes'):
        raise SystemExit(f'完整交付要求启用 {key}，不能使用轻量构建。')
for key in ('KB_MILVUS_URI', 'ELASTICSEARCH_URL',
            'KB_EMBEDDING_API_BASE_URL', 'KB_EMBEDDING_API_MODEL',
            'KB_RERANKER_API_BASE_URL', 'KB_RERANKER_API_MODEL'):
    if not v.get(key): raise SystemExit(f'完整检索配置缺少 {key}')
if v.get('ELASTICSEARCH_URL') != 'http://elasticsearch:9200' or v.get('ELASTICSEARCH_USERNAME') != 'elastic':
    raise SystemExit('本次部署使用同机 Elasticsearch，请设置容器内地址和 elastic 用户。')
if not re.fullmatch(r'[A-Za-z0-9_-]{20,}', v.get('ELASTICSEARCH_PASSWORD', '')):
    raise SystemExit('请设置至少 20 位、由字母数字及 _- 组成的 Elasticsearch 独立密码。')
for key in ('KB_EMBEDDING_BACKEND', 'KB_RERANKER_BACKEND'):
    if v.get(key, '').lower() != 'api':
        raise SystemExit(f'当前生产预检仅支持明确配置的远程 API：{key}')
for prefix in ('KB_EMBEDDING', 'KB_RERANKER'):
    endpoint = urlparse(v[f'{prefix}_API_BASE_URL'])
    if endpoint.scheme not in ('http', 'https') or not endpoint.hostname or endpoint.username or endpoint.password:
        raise SystemExit(f'{prefix}_API_BASE_URL 格式错误，不能包含账号或密码。')
    if endpoint.hostname.lower() in ('api.siliconflow.cn', 'api.siliconflow.com') and not v.get(f'{prefix}_API_KEY'):
        raise SystemExit(f'硅基流动接口缺少 {prefix}_API_KEY；不会发起连接。')
PY
python3 "$CODE_ROOT/deploy/huawei/validate_production_environment.py" "$ENV_FILE" \
  "$CODE_ROOT/deploy/docker/docker-compose.yml" "$CODE_ROOT/deploy/huawei/docker-production.yml"
DOMAIN="$(python3 - "$ENV_FILE" <<'PY'
from urllib.parse import urlparse
import sys
for line in open(sys.argv[1], encoding='utf8'):
    if line.startswith('PUBLIC_ORIGIN='):
        print(urlparse(line.split('=',1)[1].strip().strip(chr(34) + chr(39))).hostname)
        break
PY
)"
[[ -n "$DOMAIN" ]] || { echo 'PUBLIC_ORIGIN 格式错误' >&2; exit 1; }
openssl x509 -in "$HERE/tls/fullchain.pem" -noout -checkend 86400 >/dev/null || { echo '证书无效或即将到期。' >&2; exit 1; }
openssl x509 -in "$HERE/tls/fullchain.pem" -noout -checkhost "$DOMAIN" | grep -q 'does match certificate' || { echo '证书与域名不匹配。' >&2; exit 1; }
CERT_HASH="$(openssl x509 -in "$HERE/tls/fullchain.pem" -pubkey -noout | openssl pkey -pubin -outform der | openssl dgst -sha256)"
KEY_HASH="$(openssl pkey -in "$HERE/tls/privkey.pem" -pubout -outform der | openssl dgst -sha256)"
[[ "$CERT_HASH" == "$KEY_HASH" ]] || { echo '证书与私钥不匹配' >&2; exit 1; }
bash "$CODE_ROOT/deploy/huawei/verify_certificate.sh" "$HERE/tls/fullchain.pem"
export ZHIYAN_ENV_FILE="$ENV_FILE" ZHIYAN_DEPLOY_ROOT="$ROOT" ZHIYAN_RELEASE_DIR="$CODE_ROOT" WEB_BIND_ADDRESS=0.0.0.0 WEB_PORT=443
COMPOSE=(docker compose --project-name zhiyan-production --env-file "$ENV_FILE" -f "$CODE_ROOT/deploy/docker/docker-compose.yml" -f "$CODE_ROOT/deploy/huawei/docker-production.yml")
"${COMPOSE[@]}" config -q
echo '生产配置静态预检通过；未确认知识库、模型服务及资源规格。'
if [[ "$MODE" == --check ]]; then exit 0; fi
if [[ $# -eq 2 ]]; then
  exec python3 "$CODE_ROOT/deploy/huawei/deployment_transaction.py" --root "$ROOT" --domain "$DOMAIN" --accept-initial-changes
fi
exec python3 "$CODE_ROOT/deploy/huawei/deployment_transaction.py" --root "$ROOT" --domain "$DOMAIN"
