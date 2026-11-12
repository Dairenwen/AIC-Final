from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "agent-system" / "frontend"
CORE_ENTRY = ROOT / "agent-core" / "main.py"
PYTHON = sys.executable


class AppHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB), **kwargs)

    def send_json(self, payload: object, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/status":
            self.send_json(build_status())
            return
        if parsed.path == "/api/file":
            params = parse_qs(parsed.query)
            rel = params.get("path", [""])[0]
            file_path = (ROOT / rel).resolve()
            if not str(file_path).startswith(str(ROOT)) or not file_path.is_file():
                self.send_json({"error": "file not found"}, 404)
                return
            try:
                payload = json.loads(file_path.read_text(encoding="utf-8"))
                if isinstance(payload, list):
                    run_command(
                        [
                            PYTHON,
                            str(ROOT / "paper_lifecycle.py"),
                            "--event",
                            "access",
                            "--input",
                            str(file_path),
                        ],
                        timeout=120,
                    )
                self.send_json(payload)
            except Exception as exc:
                self.send_json({"error": str(exc)}, 500)
            return
        super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {}

        if parsed.path == "/api/run/crawl":
            cmd = [
                PYTHON,
                str(ROOT / "crawl_papers.py"),
                "--config",
                str(ROOT / "config.zhiyan.yaml"),
                "--out",
                str(ROOT / "data" / "raw"),
            ]
            self.send_json(run_command(cmd))
            return

        if parsed.path == "/api/run/daily":
            cmd = [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(ROOT / "run_pwc_daily.ps1"),
            ]
            self.send_json(run_command(cmd, timeout=7200))
            return

        if parsed.path == "/api/run/split":
            cmd = [
                PYTHON,
                str(ROOT / "split_by_venue_year.py"),
                "--input",
                str(ROOT / "data" / "raw"),
                "--out",
                str(ROOT / "data" / "by_venue_year"),
            ]
            self.send_json(run_command(cmd))
            return

        if parsed.path == "/api/run/download":
            cmd = [
                PYTHON,
                str(ROOT / "download_pdfs.py"),
                "--input",
                str(ROOT / "data" / "by_venue_year"),
                "--out",
                str(ROOT / "pdfs"),
            ]
            self.send_json(run_command(cmd, timeout=3600))
            return

        if parsed.path == "/api/run/organize":
            cmd = [
                PYTHON,
                str(ROOT / "organize_knowledge_base.py"),
                "--input",
                str(ROOT / "data" / "raw"),
                "--out",
                str(ROOT / "knowledge_base_zhiyan"),
            ]
            self.send_json(run_command(cmd))
            return

        if parsed.path == "/api/run/import-postgres":
            default_es_ca_cert = Path.home() / "Desktop" / "elasticsearch-9.4.3" / "config" / "certs" / "http_ca.crt"
            cmd = [
                PYTHON,
                str(ROOT / "import_to_postgres.py"),
                "--input",
                str(ROOT / "data" / "raw"),
                "--host",
                str(payload.get("host") or "localhost"),
                "--port",
                str(payload.get("port") or "5432"),
                "--database",
                str(payload.get("database") or "postgres"),
                "--user",
                str(payload.get("user") or "postgres"),
                "--table",
                str(payload.get("table") or "papers"),
                "--es-url",
                str(payload.get("es_url") or "https://localhost:9200"),
                "--es-index",
                str(payload.get("es_index") or "papers_idx"),
            ]
            if default_es_ca_cert.exists():
                cmd.extend(["--es-ca-cert", str(default_es_ca_cert)])
            env_updates = {}
            if payload.get("password"):
                env_updates["PGPASSWORD"] = str(payload["password"])
            if payload.get("es_user"):
                cmd.extend(["--es-user", str(payload["es_user"])])
            if payload.get("es_password"):
                env_updates["ES_PASSWORD"] = str(payload["es_password"])
            self.send_json(run_command(cmd, timeout=1800, env_updates=env_updates))
            return

        if parsed.path == "/api/run/update-metadata":
            cmd = [
                PYTHON,
                str(ROOT / "update_metadata_timestamps.py"),
                "--input",
                str(ROOT / "data" / "raw"),
                "--host",
                str(payload.get("host") or "localhost"),
                "--port",
                str(payload.get("port") or "5432"),
                "--database",
                str(payload.get("database") or "postgres"),
                "--user",
                str(payload.get("user") or "postgres"),
                "--table",
                str(payload.get("table") or "papers"),
            ]
            if payload.get("timestamp"):
                cmd.extend(["--timestamp", str(payload["timestamp"])])
            env_updates = {}
            if payload.get("password"):
                env_updates["PGPASSWORD"] = str(payload["password"])
            self.send_json(run_command(cmd, timeout=1800, env_updates=env_updates))
            return

        if parsed.path == "/api/run/innovation-agent":
            domain = str(payload.get("research_domain") or payload.get("domain") or "").strip()
            if not domain:
                self.send_json({"ok": False, "error": "research_domain is required"}, 400)
                return

            cmd = [
                PYTHON,
                str(CORE_ENTRY),
                "--domain",
                domain,
                "--mode",
                str(payload.get("mode") or "full"),
                "--top-k",
                str(payload.get("top_k") or 5),
                "--corpus",
                str(ROOT / "data" / "raw"),
                "--out",
                str(ROOT / "data" / "innovation_runs"),
            ]
            for keyword in payload_list(payload.get("keywords")):
                cmd.extend(["--keyword", keyword])
            for seed_idea in payload_list(payload.get("seed_ideas")):
                cmd.extend(["--seed-idea", seed_idea])
            if payload.get("time_range"):
                cmd.extend(["--time-range", str(payload["time_range"])])
            if payload.get("additional_context"):
                cmd.extend(["--additional-context", str(payload["additional_context"])])
            constraints = payload.get("constraints")
            if isinstance(constraints, str) and constraints.strip():
                cmd.extend(["--constraints-json", constraints.strip()])
            elif isinstance(constraints, dict) and constraints:
                cmd.extend(["--constraints-json", json.dumps(constraints, ensure_ascii=False)])

            result = run_command(cmd, timeout=1800, env_updates={"PYTHONIOENCODING": "utf-8"})
            attach_innovation_result(result)
            self.send_json(result)
            return

        self.send_json({"error": "not found"}, 404)


def run_command(cmd: list[str], timeout: int = 900, env_updates: dict[str, str] | None = None) -> dict[str, object]:
    env = os.environ.copy()
    if env_updates:
        env.update(env_updates)
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=timeout,
        )
        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "command": " ".join(cmd),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "returncode": None,
            "stdout": exc.stdout or "",
            "stderr": f"Command timed out after {timeout} seconds.",
            "command": " ".join(cmd),
        }


def payload_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[\n,，;；]+", value) if item.strip()]
    return [str(value).strip()] if str(value).strip() else []


def attach_innovation_result(result: dict[str, object]) -> None:
    stdout = str(result.get("stdout") or "")
    result_path = ""
    result_rel_path = ""
    for line in stdout.splitlines():
        if line.startswith("RESULT_JSON_REL="):
            result_rel_path = line.split("=", 1)[1].strip()
        elif line.startswith("RESULT_JSON="):
            result_path = line.split("=", 1)[1].strip()
    if result_rel_path:
        path = (ROOT / result_rel_path).resolve()
    elif result_path:
        path = Path(result_path).resolve()
    else:
        runs_dir = ROOT / "data" / "innovation_runs"
        latest = sorted(runs_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True) if runs_dir.exists() else []
        if not latest:
            return
        path = latest[0].resolve()
    try:
        if not str(path).startswith(str(ROOT)) or not path.is_file():
            return
        result["result_path"] = str(path.relative_to(ROOT)).replace("\\", "/")
        result["result"] = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        result["result_read_error"] = str(exc)


def count_json_items(path: Path) -> int:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    return len(data) if isinstance(data, list) else 0


def list_json_dir(rel_dir: str) -> list[dict[str, object]]:
    directory = ROOT / rel_dir
    if not directory.exists():
        return []
    files = []
    for path in sorted(directory.glob("*.json")):
        if path.name == "_index.json":
            continue
        files.append(
            {
                "name": path.name,
                "path": str(path.relative_to(ROOT)).replace("\\", "/"),
                "count": count_json_items(path),
                "size_kb": round(path.stat().st_size / 1024, 1),
            }
        )
    return files


def build_status() -> dict[str, object]:
    raw_files = list_json_dir("data/raw")
    grouped_files = list_json_dir("data/by_venue_year")
    pdf_dir = ROOT / "pdfs"
    pdf_count = len(list(pdf_dir.glob("*.pdf"))) if pdf_dir.exists() else 0
    kb_dir = ROOT / "knowledge_base_zhiyan"
    kb_metadata_count = len(list(kb_dir.rglob("metadata.json"))) if kb_dir.exists() else 0
    return {
        "root": str(ROOT),
        "raw_files": raw_files,
        "grouped_files": grouped_files,
        "raw_total": sum(int(item["count"]) for item in raw_files),
        "grouped_total": sum(int(item["count"]) for item in grouped_files),
        "pdf_count": pdf_count,
        "kb_metadata_count": kb_metadata_count,
    }


def main() -> None:
    WEB.mkdir(exist_ok=True)
    host = "127.0.0.1"
    port, server = create_server(host, 8765)
    print(f"Open http://{host}:{port}")
    server.serve_forever()


def create_server(host: str, preferred_port: int) -> tuple[int, ThreadingHTTPServer]:
    last_error: OSError | None = None
    for port in range(preferred_port, preferred_port + 20):
        try:
            return port, ThreadingHTTPServer((host, port), AppHandler)
        except OSError as exc:
            last_error = exc
    if last_error:
        raise last_error
    raise OSError("No available port found.")


if __name__ == "__main__":
    main()
