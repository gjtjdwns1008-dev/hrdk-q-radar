"""[로컬 도구] 종목마스터(xlsb) → data/qnet_codes.csv 재생성.

부속서 A-5: 종목코드·연도별 종목명의 정본 = Q-Page 종목마스터.
번역 사전(qnet_codes.csv)은 그 파생물 — 마스터 개정 시 연 1회(채널 A 리듬) 재생성.

사용법(레포/작업 폴더 어디서든):
    python build_codemap.py --xlsb "QPAGE_....xlsb 경로"
필요 라이브러리: pip install pyxlsb
출력: 이 파일 옆 data/qnet_codes.csv  (열: 종목코드, 연도, 종목명)
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
YEARS = (2024, 2025, 2026, 2027, 2028)   # 종목마스터의 5개년 명칭 열


def main() -> int:
    ap = argparse.ArgumentParser(description="종목마스터 → 번역 사전 재생성")
    ap.add_argument("--xlsb", required=True, help="Q-Page 종목마스터가 든 xlsb 경로")
    ap.add_argument("--sheet", default="2_종목마스터")
    ap.add_argument("--out", default=str(HERE / "data" / "qnet_codes.csv"))
    args = ap.parse_args()

    try:
        from pyxlsb import open_workbook
    except ImportError:
        sys.exit("❌ pyxlsb 미설치 — 실행에 쓴 파이썬으로:  python -m pip install pyxlsb")

    wb = open_workbook(args.xlsb)
    with wb.get_sheet(args.sheet) as sh:
        raw = [[c.v for c in r] for r in sh.rows()]
    raw = [r for r in raw if any(v not in (None, "") for v in r)]
    hdr = [str(h) for h in raw[0]]
    rows = [dict(zip(hdr, r)) for r in raw[1:]]
    g = lambda r, k: str(r.get(k) or "").strip()

    out_rows, codes = [], set()
    for r in rows:
        code = g(r, "종목코드")
        if not code:
            continue
        codes.add(code)
        for y in YEARS:
            nm = g(r, f"종목명_{y}")
            if nm:
                out_rows.append((code, y, nm))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["종목코드", "연도", "종목명"])
        for row in sorted(out_rows):
            w.writerow(row)
    print(f"✅ 재생성 완료: {out}")
    print(f"   코드 {len(codes)}개 · (코드,연도,명칭) {len(out_rows)}행 · 연도 {YEARS[0]}~{YEARS[-1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
