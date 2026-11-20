"""One read per resource, using only the public demonstration OC from the DRF manual."""

import os
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests

dest = Path(__file__).resolve().parents[1] / "docs"
dest.mkdir(exist_ok=True)
records = []
base = "https://www.law.go.kr/DRF/"
for target, query in [
    ("law", "주택임대차보호법"),
    ("prec", "임대차보증금"),
    ("expc", "주택임대차보호법"),
]:
    records.append(
        (
            target,
            base + "lawSearch.do",
            dict(
                OC=os.getenv("LEGAL_API_KEY") or os.getenv("LEGAL_DEMO_OC"),
                type="JSON",
                target=target,
                query=query,
                display=2,
            ),
        )
    )
records.extend(
    [
        ("holidays", "https://date.nager.at/api/v3/PublicHolidays/2026/KR", {}),
        ("gms_portal", "https://gms.ssafy.io", {}),
    ]
)
report = []

for name, url, params in records:
    row = {"api": name, "url": url, "at": datetime.now(timezone.utc).isoformat()}
    try:
        r = requests.get(url, params=params, timeout=(5, 10))
        row["http_status"] = r.status_code
        row["destination_host"] = urlsplit(r.url).hostname
        r.raise_for_status()
        if name == "gms_portal":
            r.encoding = "utf-8"
            row["result"] = (
                "public page not found"
                if "페이지를 찾을 수 없습니다" in r.text
                else "public HTML only"
            )
            row["public_html_mentions_claude"] = "claude" in r.text.lower()
            import re

            print(
                "portal assets", re.findall(r'<script[^>]* src="([^"]+)"', r.text)[:8]
            )
        else:
            body = r.json()
            if name == "holidays":
                valid = [
                    x for x in body if x.get("date") and x.get("countryCode") == "KR"
                ]
                row["count"] = len(valid)
                if valid:
                    (dest.parent / "fixtures").mkdir(exist_ok=True)
                    (dest.parent / "fixtures/holidays-2026.json").write_text(
                        json.dumps(valid, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
            elif name == "detail":
                row["count"] = len(body["법령"]["조문"]["조문단위"])
            else:
                root = {"law": "LawSearch", "prec": "PrecSearch", "expc": "Expc"}[name]
                if root not in body and "검증에 실패" in str(body.get("result", "")):
                    row["result"] = "AUTH_SERVER_REGISTRATION_REQUIRED"
                    report.append(row)
                    print(name, row["result"], flush=True)
                    continue
                rows = body[root].get(name, [])
                rows = [rows] if isinstance(rows, dict) else rows
                row["count"] = len(rows)
                if name == "law" and rows:
                    records.append(
                        (
                            "detail",
                            base + "lawService.do",
                            dict(
                                OC=os.getenv("LEGAL_API_KEY")
                                or os.getenv("LEGAL_DEMO_OC"),
                                type="JSON",
                                target="law",
                                MST=rows[0]["법령일련번호"],
                            ),
                        )
                    )
            row["result"] = "verified" if row["count"] else "empty"
    except Exception as exc:
        row["result"] = type(exc).__name__
    report.append(row)
    print(name, row["result"], row.get("http_status"), row.get("count"), flush=True)
(dest / "live-probes.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
)
