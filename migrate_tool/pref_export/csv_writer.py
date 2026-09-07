"""채널 A — 연 1회 CSV(14열). [주로 로컬 러너에서 사용 — Phase 1 수동 완주용]

⚠ 열 배치의 정본은 규격 v1.1 §1이다. 아래 레이아웃은 v1.3 §1의 단서
   (성격=9·11·13열, 기준일=14열, 성격은 표시값, 기준일=스냅샷 생성일)에서
   역산한 '잠정안'이므로, 첫 산출 전에 v1.1 §1 원문과 반드시 대조할 것.
   TODO(확인 4)
"""

from __future__ import annotations

import csv
import io

from .aggregate import PrefSnapshot

HEADER = [
    "종목코드", "종목명", "법령수",                      # 1–3
    "의무고용", "직무권한", "인사우대", "시험면제",       # 4–7 (성격별 건수)
    "대표법령1", "성격1",                                # 8–9
    "대표법령2", "성격2",                                # 10–11
    "대표법령3", "성격3",                                # 12–13
    "기준일",                                            # 14 = 스냅샷 생성일(asof)
]  # TODO(확인 4): v1.1 §1 원문 대조


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
