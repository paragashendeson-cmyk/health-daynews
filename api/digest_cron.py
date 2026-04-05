from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

APP_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_RELATIVE_CANDIDATES = [
    Path(".agents/skills/med-it-feishu-daily-brief/scripts/build_digest.py"),
    Path("agents/skills/med-it-feishu-daily-brief/scripts/build_digest.py"),
]

DEFAULT_OUTPUT_DIR = "/tmp/med-it-digest" if os.name != "nt" else "./output/med-it-digest-vercel"


def _json(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _is_authorized(handler: BaseHTTPRequestHandler) -> bool:
    secret = os.getenv("CRON_SECRET", "").strip()
    if not secret:
        return True

    auth_header = handler.headers.get("Authorization", "")
    if auth_header == f"Bearer {secret}":
        return True

    parsed = urlparse(handler.path)
    query_token = parse_qs(parsed.query).get("token", [""])[0]
    return query_token == secret


def _resolve_paths() -> tuple[Path | None, Path, Path | None, list[str]]:
    # Support multiple repo layouts and hidden/non-hidden agents directory names.
    base_candidates = [APP_ROOT, APP_ROOT / "news"]
    tried: list[str] = []

    for base in base_candidates:
        for script_rel in SCRIPT_RELATIVE_CANDIDATES:
            script_candidate = base / script_rel
            tried.append(str(script_candidate))
            if script_candidate.exists():
                registry_rel = script_rel.parent.parent / "references" / "source_registry.example.yaml"
                registry_candidate = base / registry_rel
                return script_candidate, base, registry_candidate, tried

    return None, APP_ROOT, None, tried


def _run_digest() -> tuple[int, dict[str, Any]]:
    script_path, workdir, registry_path, tried_paths = _resolve_paths()
    if script_path is None:
        return 500, {
            "ok": False,
            "error": "Script not found",
            "tried_paths": tried_paths,
        }

    lookback_hours = os.getenv("DIGEST_LOOKBACK_HOURS", "72").strip() or "72"
    timezone_name = os.getenv("DIGEST_TIMEZONE", "Asia/Shanghai").strip() or "Asia/Shanghai"
    output_dir = os.getenv("DIGEST_OUTPUT_DIR", DEFAULT_OUTPUT_DIR).strip() or DEFAULT_OUTPUT_DIR

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["DIGEST_OUTPUT_DIR"] = output_dir
    if registry_path is not None and registry_path.exists() and not env.get("SOURCE_REGISTRY_PATH"):
        env["SOURCE_REGISTRY_PATH"] = str(registry_path)

    cmd = [
        sys.executable,
        str(script_path),
        "--delivery-mode",
        "send",
        "--lookback-hours",
        lookback_hours,
        "--timezone",
        timezone_name,
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=240,
        cwd=str(workdir),
    )

    response = {
        "ok": result.returncode == 0,
        "code": result.returncode,
        "stdout": result.stdout[-4000:],
        "stderr": result.stderr[-4000:],
        "ran_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "output_dir": output_dir,
        "workdir": str(workdir),
        "script_path": str(script_path),
    }

    if result.returncode != 0:
        return 500, response
    return 200, response


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if not _is_authorized(self):
            _json(self, 401, {"ok": False, "error": "Unauthorized"})
            return

        status, payload = _run_digest()
        _json(self, status, payload)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return