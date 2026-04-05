#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path

from common import (
    Candidate,
    build_feishu_payload_from_markdown,
    dedupe_candidates,
    ensure_dir,
    fetch_google_news_rss,
    get_tz,
    load_source_registry,
    pm_commentary,
    registry_queries,
    resolve_feishu_config,
    score_candidate,
    send_feishu,
    write_json,
    write_text,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build medical IT daily digest and optionally send to Feishu.")
    parser.add_argument("--delivery-mode", choices=["dry-run", "send"], default="dry-run")
    parser.add_argument("--lookback-hours", type=int, default=72)
    parser.add_argument("--timezone", default="Asia/Shanghai")
    parser.add_argument("--target-audience", default="医疗 IT 产品经理")
    parser.add_argument("--max-items-tools", type=int, default=5)
    parser.add_argument("--max-items-healthcare", type=int, default=5)
    parser.add_argument("--feishu-format", choices=["text", "post", "card"], default="post")
    parser.add_argument("--include-links", action="store_true", default=True)
    parser.add_argument("--include-reasoning-for-pm", action="store_true", default=True)
    parser.add_argument(
        "--source-registry",
        default=os.getenv(
            "SOURCE_REGISTRY_PATH",
            "./.agents/skills/med-it-feishu-daily-brief/references/source_registry.example.yaml",
        ),
    )
    parser.add_argument("--output-dir", default=os.getenv("DIGEST_OUTPUT_DIR", "./output/med-it-digest"))
    parser.add_argument("--min-score", type=int, default=70)
    parser.add_argument("--force-send", action="store_true", help="Send even when delivery-mode is dry-run")
    return parser.parse_args()


def collect_candidates(topic: str, queries: list[str], lookback_hours: int) -> list[Candidate]:
    all_items: list[Candidate] = []
    for query in queries:
        try:
            items = fetch_google_news_rss(query, lookback_hours)
        except Exception:
            continue
        for item in items:
            item.topic = topic
            all_items.append(item)
    return dedupe_candidates(all_items)


def select_items(items: list[Candidate], max_items: int, now_utc: datetime, min_score: int) -> list[Candidate]:
    for item in items:
        item.score = score_candidate(item, now_utc)

    shortlisted = [item for item in items if item.score >= min_score]
    if len(shortlisted) < max_items:
        return sorted(items, key=lambda item: item.score, reverse=True)[:max_items]
    return sorted(shortlisted, key=lambda item: item.score, reverse=True)[:max_items]


def build_markdown(tools: list[Candidate], healthcare: list[Candidate], tz_name: str) -> str:
    tz = get_tz(tz_name)
    now_local = datetime.now(tz)
    lines: list[str] = [
        "# 今日医疗 AI / 医疗 IT 情报简报",
        "",
        f"生成时间：{now_local.strftime('%Y-%m-%d %H:%M')} ({tz_name})",
        "",
        "## 一、AI 原型与界面生成工具",
        "",
    ]

    if not tools:
        lines.append("本期高价值更新较少。")
    for idx, item in enumerate(tools, 1):
        ts = item.published_at.astimezone(tz).strftime("%Y-%m-%d %H:%M") if item.published_at else "未知"
        commentary = pm_commentary(item)
        lines.extend(
            [
                f"{idx}. {item.title}",
                f"- 来源：{item.source}",
                f"- 发布时间：{ts}",
                f"- 一句话摘要：{item.summary or commentary['what']}",
                f"- PM 借鉴点：{commentary['borrow']}",
                f"- 链接：{item.url}",
                "",
            ]
        )

    lines.extend(["## 二、医疗 IT / 互联网医院动态", ""])
    if not healthcare:
        lines.append("本期高价值更新较少。")
    for idx, item in enumerate(healthcare, 1):
        ts = item.published_at.astimezone(tz).strftime("%Y-%m-%d %H:%M") if item.published_at else "未知"
        commentary = pm_commentary(item)
        has_case = "是" if item.image_url_or_cover or "视频" in item.evidence_type or "案例" in item.summary else "否"
        lines.extend(
            [
                f"{idx}. {item.title}",
                f"- 机构/企业：{item.author_or_org}",
                f"- 来源：{item.source}",
                f"- 发布时间：{ts}",
                f"- 功能亮点：{item.summary or commentary['what']}",
                f"- 为什么值得关注：{commentary['why']}",
                f"- 借鉴场景：{commentary['landing']}",
                f"- 是否包含图文/案例：{has_case}",
                f"- 链接：{item.url}",
                "",
            ]
        )

    lines.extend(
        [
            "## 今日结论",
            "",
            "1. AI 原型工具更新节奏在加快，PM 应优先试用可直接产出页面草图与流程稿的能力。",
            "2. 医疗 IT 创新重心仍在患者服务闭环，导诊、复诊、随访与支付协同是一体化方向。",
            "3. 评估新能力时建议优先看可量化指标：服务触达、使用转化、运营效率和医患体验。",
            "4. 对外宣传信息需要二次核验，优先采用机构官网与完整案例页作为内部讨论依据。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()

    now_utc = datetime.now(timezone.utc)
    tz = get_tz(args.timezone)
    date_key = datetime.now(tz).strftime("%Y-%m-%d")

    out_dir = Path(args.output_dir) / date_key
    ensure_dir(out_dir)

    registry = load_source_registry(Path(args.source_registry))
    tool_queries = registry_queries(registry, "tools")
    health_queries = registry_queries(registry, "healthcare")

    tools_candidates = collect_candidates("tools", tool_queries, args.lookback_hours)
    healthcare_candidates = collect_candidates("healthcare", health_queries, args.lookback_hours)

    if len(tools_candidates) + len(healthcare_candidates) < 6 and args.lookback_hours < 168:
        expanded_hours = 168
        tools_candidates = collect_candidates("tools", tool_queries, expanded_hours)
        healthcare_candidates = collect_candidates("healthcare", health_queries, expanded_hours)

    selected_tools = select_items(tools_candidates, args.max_items_tools, now_utc, args.min_score)
    selected_health = select_items(healthcare_candidates, args.max_items_healthcare, now_utc, args.min_score)

    digest_md = build_markdown(selected_tools, selected_health, args.timezone)
    digest_json = {
        "meta": {
            "generated_at": datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S"),
            "timezone": args.timezone,
            "target_audience": args.target_audience,
            "lookback_hours": args.lookback_hours,
            "delivery_mode": args.delivery_mode,
        },
        "tools": [item.to_json(args.timezone) for item in selected_tools],
        "healthcare": [item.to_json(args.timezone) for item in selected_health],
    }

    feishu_payload = build_feishu_payload_from_markdown(
        title="今日医疗 AI / 医疗 IT 情报简报",
        body_markdown=digest_md,
        fmt=args.feishu_format,
    )

    digest_json_path = out_dir / "digest.json"
    digest_md_path = out_dir / "digest.md"
    payload_path = out_dir / "feishu_payload.json"

    write_json(digest_json_path, digest_json)
    write_text(digest_md_path, digest_md)
    write_json(payload_path, feishu_payload)

    print(f"[OK] digest.json -> {digest_json_path}")
    print(f"[OK] digest.md -> {digest_md_path}")
    print(f"[OK] feishu_payload.json -> {payload_path}")

    should_send = args.delivery_mode == "send" or args.force_send
    if not should_send:
        print("[INFO] dry-run mode: skip Feishu send")
        return 0

    webhook, secret = resolve_feishu_config()
    if not webhook:
        print("[ERROR] FEISHU_WEBHOOK_URL not found in env or .agents/feishubot.md")
        return 2

    try:
        result = send_feishu(webhook, feishu_payload, secret=secret)
    except Exception as exc:
        err = {
            "ok": False,
            "error": str(exc),
            "sent_at": datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S"),
        }
        write_json(out_dir / "send_result.json", err)
        print(f"[ERROR] Feishu send failed: {exc}")
        return 3

    send_result = {
        "ok": True,
        "sent_at": datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S"),
        "result": result,
    }
    write_json(out_dir / "send_result.json", send_result)
    print(f"[OK] send_result.json -> {out_dir / 'send_result.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
