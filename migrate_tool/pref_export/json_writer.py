"""채널 B — pref_export.json 페이로드 생성. [공용]

§2.2 스키마를 그대로(Q-Page 화면 소비 형태, 변환 없음) 만든다.
"""

from __future__ import annotations

import json

from .aggregate import PrefSnapshot

SCHEMA_VERSION = "1.0"
SOURCE = "HRDK Q-Radar"


def build_payload(snap: PrefSnapshot) -> dict:
    items: dict[str, dict] = {}
    for code, agg in snap.items.items():
        items[code] = {
            "name": agg.name,
            "n": agg.n,
            "cat": {k: agg.counts[k] for k in ("duty", "auth", "hr", "exempt")},
            "top": [{"law": law, "cat": disp} for law, disp in agg.top],
        }
    return {
        "version": SCHEMA_VERSION,
        "asof": snap.asof,
        "generated": snap.generated,
        "source": SOURCE,
        "items": items,
    }


def to_text(payload: dict) -> str:
    """게시용 직렬화 — 한글 원문 유지(ensure_ascii=False), 사람이 읽을 수 있게 indent=2."""
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
