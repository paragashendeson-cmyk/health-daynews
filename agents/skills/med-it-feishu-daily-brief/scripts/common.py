#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlparse, urlunparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


DEFAULT_TOOL_QUERIES = [
    "AI prototyping tool",
    "text to ui tool",
    "wireframe ai",
    "ai design copilot",
    "Figma AI prototype",
]

DEFAULT_HEALTHCARE_QUERIES = [
    "互联网医院 新功能",
    "AI 导诊 医院",
    "AI 随访 平台",
    "线上复诊 慢病管理",
    "医院 小程序 服务",
]

DEFAULT_REGISTRY = {
    "query_hints": {
        "tools": DEFAULT_TOOL_QUERIES,
        "healthcare": DEFAULT_HEALTHCARE_QUERIES,
    }
}

FEISHU_DOC_DEFAULT_PATH = Path(".agents/feishubot.md")


def get_tz(tz_name: str):
    try:
        return ZoneInfo(tz_name)
    except Exception:
        name = (tz_name or "").strip()
        if name in {"Asia/Shanghai", "Asia/Chongqing", "PRC"}:
            return timezone(timedelta(hours=8), name="Asia/Shanghai")
        match = re.fullmatch(r"UTC([+-])(\d{1,2})(?::?(\d{2}))?", name)
        if match:
            sign = 1 if match.group(1) == "+" else -1
            hours = int(match.group(2))
            minutes = int(match.group(3) or "0")
            return timezone(sign * timedelta(hours=hours, minutes=minutes), name=name)
        return timezone.utc


class _HTMLStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        return "".join(self.parts)


def strip_html(raw_html: str | None) -> str:
    if not raw_html:
        return ""
    stripper = _HTMLStripper()
    stripper.feed(raw_html)
    return re.sub(r"\s+", " ", unescape(stripper.text())).strip()


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def canonicalize_url(url: str) -> str:
    parsed = urlparse(url)
    query_parts = []
    if parsed.query:
        for item in parsed.query.split("&"):
            if not item:
                continue
            key = item.split("=", 1)[0].lower()
            if key.startswith("utm_"):
                continue
            query_parts.append(item)
    normalized = parsed._replace(query="&".join(query_parts), fragment="")
    return urlunparse(normalized)


def _load_yaml_if_available(path: Path) -> dict[str, Any] | None:
    try:
        import yaml  # type: ignore
    except Exception:
        return None
    try:
        content = path.read_text(encoding="utf-8")
        data = yaml.safe_load(content)
    except Exception:
        return None
    if isinstance(data, dict):
        return data
    return None


def load_source_registry(path: Path) -> dict[str, Any]:
    if path.exists():
        yaml_data = _load_yaml_if_available(path)
        if yaml_data is not None:
            return yaml_data
        try:
            raw = path.read_text(encoding="utf-8")
            if raw.lstrip().startswith("{"):
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return parsed
        except Exception:
            pass
    return DEFAULT_REGISTRY


def registry_queries(registry: dict[str, Any], topic: str) -> list[str]:
    query_hints = registry.get("query_hints", {})
    values = query_hints.get(topic, []) if isinstance(query_hints, dict) else []
    out: list[str] = []
    if isinstance(values, list):
        for item in values:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
    if out:
        return out
    return DEFAULT_TOOL_QUERIES if topic == "tools" else DEFAULT_HEALTHCARE_QUERIES


@dataclass
class Candidate:
    topic: str
    title: str
    source: str
    author_or_org: str
    published_at: datetime | None
    url: str
    summary: str
    image_url_or_cover: str | None
    evidence_type: str
    score: int = 0

    def to_json(self, tz_name: str) -> dict[str, Any]:
        tz = get_tz(tz_name)
        published_local = self.published_at.astimezone(tz).strftime("%Y-%m-%d %H:%M") if self.published_at else ""
        return {
            "topic": self.topic,
            "title": self.title,
            "source": self.source,
            "author_or_org": self.author_or_org,
            "published_at": published_local,
            "url": self.url,
            "summary": self.summary,
            "image_url_or_cover": self.image_url_or_cover,
            "evidence_type": self.evidence_type,
            "score": self.score,
        }


def _domain_weight(domain: str) -> int:
    lowered = domain.lower()
    if any(item in lowered for item in ["gov", "hospital", "healthcare", "nhc.gov.cn", "open.feishu", "who.int"]):
        return 10
    if any(item in lowered for item in ["news", "cn-healthcare", "vbdata", "36kr", "iyiou"]):
        return 7
    if any(item in lowered for item in ["youtube", "weibo", "x.com", "twitter"]):
        return 6
    return 5


def _evidence_type(domain: str) -> str:
    lowered = domain.lower()
    if "youtube" in lowered:
        return "视频"
    if any(item in lowered for item in ["weibo", "x.com", "twitter"]):
        return "社交媒体"
    if any(item in lowered for item in ["gov", "hospital", "healthcare", ".org", "nhc.gov.cn"]):
        return "官网/机构"
    return "媒体/社区"


def score_candidate(item: Candidate, now_utc: datetime) -> int:
    title = item.title.lower()
    summary = item.summary.lower()
    merged = f"{title} {summary}"

    freshness = 0
    if item.published_at:
        delta_h = max(0.0, (now_utc - item.published_at).total_seconds() / 3600.0)
        freshness = max(0, int(20 - min(delta_h, 168) / 8))

    keywords_tools = ["ai", "prototype", "wireframe", "figma", "ui", "copilot", "design"]
    keywords_health = ["互联网医院", "导诊", "随访", "复诊", "慢病", "病历", "患者", "小程序", "医院"]

    matched = 0
    for keyword in (keywords_tools if item.topic == "tools" else keywords_health):
        if keyword.lower() in merged:
            matched += 1

    relevance = min(25, matched * 4)
    value = 20 if any(item in merged for item in ["发布", "上线", "更新", "new", "launch", "update", "case", "案例"]) else 12
    completeness = 15 if item.summary and item.url and item.title else 8
    case_rich = 10 if item.image_url_or_cover or "视频" in item.evidence_type or "案例" in merged else 6
    trust = _domain_weight(urlparse(item.url).netloc)

    total = freshness + relevance + value + completeness + case_rich + trust
    return max(0, min(100, total))


def fetch_google_news_rss(query: str, lookback_hours: int, lang: str = "zh-CN") -> list[Candidate]:
    days = max(1, min(7, (lookback_hours + 23) // 24))
    rss_query = f"{query} when:{days}d"
    url = (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(rss_query)}&hl={quote_plus(lang)}&gl=CN&ceid=CN:zh-Hans"
    )

    req = Request(url, headers={"User-Agent": "Mozilla/5.0"}, method="GET")
    with urlopen(req, timeout=20) as resp:
        data = resp.read()

    root = ET.fromstring(data)
    channel = root.find("channel")
    if channel is None:
        return []

    out: list[Candidate] = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    for item in channel.findall("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date = parse_dt(item.findtext("pubDate"))
        source = (item.findtext("source") or "").strip()
        desc = strip_html(item.findtext("description"))
        if not title or not link:
            continue
        if pub_date and pub_date < cutoff:
            continue

        clean_url = canonicalize_url(link)
        domain = urlparse(clean_url).netloc or source
        out.append(
            Candidate(
                topic="",
                title=title,
                source=source or domain,
                author_or_org=source or domain,
                published_at=pub_date,
                url=clean_url,
                summary=desc[:220],
                image_url_or_cover=None,
                evidence_type=_evidence_type(domain),
            )
        )
    return out


def dedupe_candidates(items: list[Candidate]) -> list[Candidate]:
    seen_urls: set[str] = set()
    seen_title_keys: set[str] = set()
    out: list[Candidate] = []

    for item in sorted(items, key=lambda value: value.published_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True):
        url_key = item.url
        title_key = re.sub(r"\W+", "", item.title.lower())[:80]
        if url_key in seen_urls or title_key in seen_title_keys:
            continue
        seen_urls.add(url_key)
        seen_title_keys.add(title_key)
        out.append(item)
    return out


def pm_commentary(item: Candidate) -> dict[str, str]:
    return {
        "what": f"这是关于“{item.title}”的近期更新。",
        "why": "信息包含明确产品动作或功能变化，适合用于跟踪行业方向。",
        "borrow": "可借鉴其信息架构、交互入口设计或用户引导方式，用于快速验证院内数字化场景。",
        "landing": "建议优先映射到导诊、复诊、随访或患者服务流程中的关键触点。"
        if item.topic == "healthcare"
        else "建议优先用于需求澄清、原型评审和跨团队沟通提效。",
    }


def build_feishu_payload_from_markdown(title: str, body_markdown: str, fmt: str = "post") -> dict[str, Any]:
    if fmt == "text":
        return {"msg_type": "text", "content": {"text": f"{title}\n\n{body_markdown}"}}

    if fmt == "card":
        return {
            "msg_type": "interactive",
            "card": {
                "header": {"title": {"tag": "plain_text", "content": title}},
                "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": body_markdown}}],
            },
        }

    lines = [line.strip() for line in body_markdown.splitlines() if line.strip()]
    content: list[list[dict[str, str]]] = []
    for line in lines:
        content.append([{"tag": "text", "text": line}])
    return {
        "msg_type": "post",
        "content": {"post": {"zh_cn": {"title": title, "content": content}}},
    }


def feishu_sign(secret: str, timestamp: int) -> str:
    key = f"{timestamp}\n{secret}".encode("utf-8")
    digest = hmac.new(key, msg=b"", digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def send_feishu(webhook_url: str, payload: dict[str, Any], secret: str | None = None, timeout: int = 20) -> dict[str, Any]:
    body = dict(payload)
    if secret:
        timestamp = int(datetime.now(timezone.utc).timestamp())
        body["timestamp"] = str(timestamp)
        body["sign"] = feishu_sign(secret, timestamp)

    data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    req = Request(
        webhook_url,
        data=data,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urlopen(req, timeout=timeout) as resp:
        resp_body = resp.read().decode("utf-8", errors="replace")
        status = getattr(resp, "status", 200)

    try:
        parsed: Any = json.loads(resp_body)
    except Exception:
        parsed = {"raw": resp_body}
    return {"http_status": status, "response": parsed}


def read_feishu_config_from_markdown(path: Path = FEISHU_DOC_DEFAULT_PATH) -> tuple[str | None, str | None]:
    if not path.exists():
        return None, None
    text = path.read_text(encoding="utf-8", errors="replace")

    webhook_match = re.search(r"https://open\.feishu\.cn/open-apis/bot/v2/hook/[A-Za-z0-9\-]+", text)
    webhook = webhook_match.group(0) if webhook_match else None

    secret = None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if "签名" in line or "secret" in line.lower() or "绛惧悕" in line:
            for candidate in lines[index + 1 : index + 4]:
                if re.fullmatch(r"[A-Za-z0-9_\-]{8,128}", candidate):
                    secret = candidate
                    break
            break

    if secret is None:
        for line in lines:
            if line.startswith("http"):
                continue
            if re.fullmatch(r"[A-Za-z0-9_\-]{8,128}", line):
                secret = line
                break

    return webhook, secret


def resolve_feishu_config() -> tuple[str | None, str | None]:
    webhook = os.getenv("FEISHU_WEBHOOK_URL")
    secret = os.getenv("FEISHU_BOT_SECRET")
    if webhook:
        return webhook, secret

    md_webhook, md_secret = read_feishu_config_from_markdown()
    return md_webhook, secret or md_secret


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, data: Any) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=2)
    path.write_text(text + "\n", encoding="utf-8", newline="\n")


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")
