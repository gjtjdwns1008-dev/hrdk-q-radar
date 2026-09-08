"""채널 A — 연 1회 CSV(14열). [주로 로컬 러너에서 사용 — Phase 1 수동 완주용]

열 배치 정본 = 규격 v1.1 §1.2 (14열, 순서 고정) — 확인 4 종결(2026-09-07).
v1.3 §1 반영: 성격(9·11·13열)=표시값, 14열 기준일=스냅샷 생성일.
Q-Page Z33 엔진은 열 이름을 직독하므로 헤더 문자열이 계약의 일부다.
"""

from __future__ import annotations

import csv
import io

from .aggregate import PrefSnapshot

HEADER = [
    "종목코드", "종목명", "건수",                        # 1–3 (건수 = 4~7열 합)
    "의무고용", "직무권한", "인사우대", "시험면제",       # 4–7 (성격별 법령 수)
    "대표1_법령", "대표1_성격",                          # 8–9
    "대표2_법령", "대표2_성격",                          # 10–11
    "대표3_법령", "대표3_성격",                          # 12–13
    "기준일",                                            # 14 = 스냅샷 생성일(asof)
]


def to_csv_text(snap: PrefSnapshot) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(HEADER)
    for code, agg in snap.items.items():
        tops = list(agg.top) + [("", "")] * (3 - len(agg.top))
        w.writerow([
            code, agg.name, agg.n,
            agg.counts["duty"], agg.counts["auth"],
            agg.counts["hr"], agg.counts["exempt"],
            tops[0][0], tops[0][1],
            tops[1][0], tops[1][1],
            tops[2][0], tops[2][1],
            snap.asof,
        ])
    return buf.getvalue()
