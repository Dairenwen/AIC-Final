#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "--help" ]]; then
  echo 'Usage: ./translate_pdf.sh INPUT.pdf [--source en] [--target zh] [--no-figures] [--parallel 1-5] [--timeout 60-3600] [--glossary JSON] [--bilingual]'
  exit 0
fi
input="${1:?Usage: ./translate_pdf.sh /absolute/path/to/paper.pdf [options]}"
shift
source_lang=en
target_lang=zh
translate_figures=true
parallel=2
timeout=600
glossary='{}'
pdf_bilingual=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --source) source_lang="$2"; shift 2 ;;
    --target) target_lang="$2"; shift 2 ;;
    --no-figures) translate_figures=false; shift ;;
    --parallel) parallel="$2"; shift 2 ;;
    --timeout) timeout="$2"; shift 2 ;;
    --glossary) glossary="$2"; shift 2 ;;
    --bilingual) pdf_bilingual=true; shift ;;
    --help)
      echo 'Usage: ./translate_pdf.sh INPUT.pdf [--source en] [--target zh] [--no-figures] [--parallel 1-5] [--timeout 60-3600] [--glossary JSON] [--bilingual]'
      exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
if [[ ! -f "$input" || "${input##*.}" != "pdf" ]]; then
  echo "Input must be an existing PDF: $input" >&2
  exit 2
fi

root="$(cd "$(dirname "$0")" && pwd)"
compose="$root/agent-system/docker/docker-compose.yml"
name="$(basename "$input")"
stem="${name%.pdf}"
host_outputs="$root/agent-core/outputs"
delivery="$root/output/pdf"
mkdir -p "$delivery"

if ! command -v docker >/dev/null; then
  echo "Docker Desktop is required. Start Docker, then run this same command again." >&2
  exit 127
fi
translation_backend="${TRANSLATION_LLM_BACKEND:-api}"
if [[ "$translation_backend" == "ollama" ]]; then
  if ! command -v ollama >/dev/null; then
    echo "Ollama is required for TRANSLATION_LLM_BACKEND=ollama." >&2
    exit 127
  fi
  ollama_model="${OLLAMA_TRANSLATION_MODEL:-translategemma:12b}"
  if ! ollama show "$ollama_model" >/dev/null 2>&1; then
    ollama pull "$ollama_model"
  fi
  if ! curl --silent --fail http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    nohup ollama serve >"${TMPDIR:-/tmp}/academic-translation-ollama.log" 2>&1 &
  fi
elif [[ -z "${TRANSLATION_API_BASE_URL:-}" || -z "${TRANSLATION_API_MODEL:-}" ]]; then
  echo "TRANSLATION_API_BASE_URL and TRANSLATION_API_MODEL are required for API translation." >&2
  exit 2
fi
docker compose -f "$compose" up -d --build >/dev/null
for _ in {1..30}; do
  if curl --silent --fail http://127.0.0.1:8000/health >/dev/null; then break; fi
  sleep 1
done

curl --fail --silent --show-error --max-time 660 \
  -X POST http://127.0.0.1:8000/translate/document \
  -F "file=@$input" -F "source_lang=$source_lang" -F "target_lang=$target_lang" \
  -F preserve_pdf_layout=true -F pdf_only=true -F pdf_layout_mode=batch -F "pdf_bilingual=$pdf_bilingual" \
  -F "translate_figures=$translate_figures" -F "max_parallel_segments=$parallel" \
  -F max_output_bytes=10000000 -F "pdf_timeout_seconds=$timeout" \
  --form-string "glossary_json=$glossary" >"$delivery/${stem}-result.json"

for candidate in "$host_outputs/${stem}-mono-visuals.pdf" "$host_outputs/${stem}-mono-figures.pdf" "$host_outputs/${stem}-mono.pdf"; do
  if [[ -s "$candidate" ]]; then
    cp "$candidate" "$delivery/${stem}-zh.pdf"
    echo "$delivery/${stem}-zh.pdf"
    exit 0
  fi
done
echo "Translation finished without a deliverable PDF; inspect $delivery/${stem}-result.json" >&2
exit 1
