#!/usr/bin/env python3
"""
Kimi API connectivity checker (URL + API key + chat completion).

Usage:
  python verify_kimi_api.py --api-key <YOUR_KEY>
  python verify_kimi_api.py --api-key <YOUR_KEY> --model moonshot-v1-8k

Or use env vars:
  KIMI_API_KEY=xxx
  KIMI_BASE_URL=https://api.moonshot.cn/v1
  KIMI_MODEL=moonshot-v1-8k
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple


DEFAULT_BASE_URL = "https://api.moonshot.cn/v1"
DEFAULT_MODEL = "moonshot-v1-8k"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify Kimi API URL and key by calling /models and /chat/completions."
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("KIMI_BASE_URL", DEFAULT_BASE_URL),
        help=f"Kimi API base URL (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("KIMI_API_KEY", ""),
        help="Kimi API key. If omitted, read from env KIMI_API_KEY.",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("KIMI_MODEL", DEFAULT_MODEL),
        help=f"Model name to test (default: {DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=20,
        help="HTTP timeout in seconds (default: 20).",
    )
    return parser


def _request_json(
    *,
    method: str,
    url: str,
    timeout: int,
    api_key: Optional[str] = None,
    body: Optional[Dict[str, Any]] = None,
) -> Tuple[int, Dict[str, Any] | list[Any] | str]:
    headers = {
        "Accept": "application/json; charset=utf-8",
    }
    data = None
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        data = payload
        headers["Content-Type"] = "application/json; charset=utf-8"
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(url=url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            text = raw.decode("utf-8", errors="replace")
            ctype = resp.headers.get("Content-Type", "")
            if "application/json" in ctype.lower():
                try:
                    return resp.status, json.loads(text)
                except json.JSONDecodeError:
                    return resp.status, text
            return resp.status, text
    except urllib.error.HTTPError as e:
        raw = e.read()
        text = raw.decode("utf-8", errors="replace")
        try:
            parsed: Any = json.loads(text)
        except json.JSONDecodeError:
            parsed = text
        return e.code, parsed


def print_step(title: str) -> None:
    print(f"\n=== {title} ===")


def short(obj: Any, limit: int = 240) -> str:
    s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    return s if len(s) <= limit else s[:limit] + "..."


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    api_key = args.api_key.strip()
    model = args.model.strip()
    timeout = args.timeout

    if not api_key:
        print("ERROR: 缺少 API Key。请通过 --api-key 或环境变量 KIMI_API_KEY 提供。")
        return 2

    print("Kimi API 检测开始")
    print(f"- Base URL: {base_url}")
    print(f"- Model:    {model}")
    print(f"- Timeout:  {timeout}s")

    models_url = f"{base_url}/models"
    chat_url = f"{base_url}/chat/completions"

    print_step("1) URL 可达性检查（不带鉴权）")
    status, body = _request_json(method="GET", url=models_url, timeout=timeout)
    print(f"HTTP {status}")
    if status >= 500:
        print(f"FAIL: 服务端异常或 URL 不正确。响应: {short(body)}")
        return 1
    print("PASS: URL 可访问（401/403 也表示服务可达）。")

    print_step("2) API Key 鉴权检查（GET /models）")
    status, body = _request_json(
        method="GET",
        url=models_url,
        timeout=timeout,
        api_key=api_key,
    )
    print(f"HTTP {status}")
    if status != 200:
        print(f"FAIL: 鉴权失败或权限不足。响应: {short(body)}")
        return 1

    available_models: list[str] = []
    if isinstance(body, dict):
        data = body.get("data")
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    mid = item.get("id")
                    if isinstance(mid, str):
                        available_models.append(mid)
    print(f"PASS: 鉴权成功。可用模型数: {len(available_models)}")
    if available_models:
        print(f"示例模型: {', '.join(available_models[:5])}")

    print_step("3) 聊天接口检查（POST /chat/completions）")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是一个简洁的助手。"},
            {"role": "user", "content": "请只回复：OK"},
        ],
        "temperature": 0,
        "max_tokens": 16,
    }
    status, body = _request_json(
        method="POST",
        url=chat_url,
        timeout=timeout,
        api_key=api_key,
        body=payload,
    )
    print(f"HTTP {status}")

    if status != 200 and available_models:
        fallback_model = available_models[0]
        print(f"提示: 模型 {model} 可能不可用，尝试回退到 {fallback_model}")
        payload["model"] = fallback_model
        status, body = _request_json(
            method="POST",
            url=chat_url,
            timeout=timeout,
            api_key=api_key,
            body=payload,
        )
        print(f"回退请求 HTTP {status}")

    if status != 200:
        print(f"FAIL: 对话接口调用失败。响应: {short(body)}")
        return 1

    answer = ""
    if isinstance(body, dict):
        choices = body.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                msg = first.get("message")
                if isinstance(msg, dict):
                    content = msg.get("content")
                    if isinstance(content, str):
                        answer = content.strip()
    print(f"PASS: 对话成功，模型回复: {answer or '(空)'}")

    print_step("最终结果")
    print("SUCCESS: URL、API Key、聊天接口均已打通。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
