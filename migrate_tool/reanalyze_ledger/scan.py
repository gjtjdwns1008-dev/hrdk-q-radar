# -*- coding: utf-8 -*-
"""
[1/3] scan.py — 재분석 대상 선정  (읽기 전용)
=================================================
입력
  --targets 파일.xlsx   이상건 목록(QRADAR_이상건_목록.xlsx, 시트 '이상건_목록', MST_ID·점검유형 열)
  --ids 파일.txt        또는 MST_ID 를 줄마다 적은 텍스트
  --all                 또는 대장 전체 (후속 전수 회귀검증용)
  --local 대장.xlsx      구글 시트 대신 로컬 xlsx 의 대장 탭을 읽음 (= LOCAL_XLSX)

출력  work/대상목록_YYYYMMDD.xlsx
  · 대상목록 : 순번·MST_ID·시행일자·법령명·소관부처·현재 판정(연관도·우대여부·우대분류·검토필요)·점검유형·재분석대상(Y/N)·비고
  · 시행일자별 : 날짜별 대상 수 → run 단계 법제처 호출 횟수 예측
  · 누락 : 입력에는 있는데 대장에 없는 MST_ID (있으면 반드시 확인)

규칙
  · 점검유형이 N(워크넷 서버에러 표기)뿐인 행 → 재분석대상 N (시트 정비 사항, AI 재분석 불필요)
  · 그 외 전부 Y. 이 파일은 run.py 의 입력이 된다.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402

NO_REANALYSIS_KINDS = {"N"}   # 점검유형 머리글자 기준 — 이 유형만 있으면 재분석 불필요


def load_targets(args) -> tuple[dict[str, str], str]:
    """MST_ID → 점검유형(요약 문자열) 매핑과 입력 설명을 돌려준다."""
    if args.all:
        return {}, "대장 전체"
    if args.ids:
        ids = [l.strip() for l in open(args.ids, encoding="utf-8") if l.strip() and not l.startswith("#")]
        return {i: "(지정)" for i in ids}, f"ID 목록 {os.path.basename(args.ids)} ({len(ids)}건)"
    import pandas as pd
    df = pd.read_excel(args.targets, sheet_name=args.sheet, dtype=str).fillna("")
    if "MST_ID" not in df.columns:
        print(f"⚠️ {args.targets} 의 '{args.sheet}' 시트에 MST_ID 열이 없습니다."); sys.exit(1)
    kinds = defaultdict(list)
    for _, r in df.iterrows():
        mid = r["MST_ID"].strip()
        if mid:
            kinds[mid].append(r.get("점검유형", "").strip() or "(유형 없음)")
    out = {mid: " + ".join(sorted(set(k))) for mid, k in kinds.items()}
    return out, f"{os.path.basename(args.targets)} / '{args.sheet}' ({len(df)}행 → 고유 {len(out)}건)"


def kind_letters(kinds_text: str) -> set[str]:
    """'A. 우대여부…  + N. 워크넷…' → {'A','N'}"""
    return {m.group(1) for m in re.finditer(r"(?:^|\+\s*)([A-Z])\.", kinds_text)}


def main():
    ap = argparse.ArgumentParser(description="재분석 대상 선정 (읽기 전용)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--targets", help="이상건 목록 xlsx")
    g.add_argument("--ids", help="MST_ID 텍스트 파일")
    g.add_argument("--all", action="store_true", help="대장 전체")
    ap.add_argument("--sheet", default="이상건_목록", help="--targets 의 시트명")
    ap.add_argument("--local", help="구글 시트 대신 읽을 로컬 대장 xlsx")
    args = ap.parse_args()

    C.load_env()
    if args.local:
        os.environ["LOCAL_XLSX"] = args.local
    elif not C.require(["GOOGLE_SHEET_URL", "GCP_SERVICE_ACCOUNT_JSON"], "구글 시트 읽기"):
        sys.exit(1)

    targets, desc = load_targets(args)
    print(f"· 입력: {desc}")
    header, rows = C.read_ledger()
    by_id = {r["MST_ID"]: r for r in rows if r.get("MST_ID")}
    dup = [k for k, n in Counter(r["MST_ID"] for r in rows if r.get("MST_ID")).items() if n > 1]
    if dup:
        print(f"⚠️ 대장에 MST_ID 중복 {len(dup)}건: {dup[:5]} — 반영 단계가 위험합니다. 먼저 정리하세요.")

    want = list(by_id.keys()) if args.all else list(targets.keys())
    out, missing = [], []
    for i, mid in enumerate(want, 1):
        r = by_id.get(mid)
        if r is None:
            missing.append(mid); continue
        kinds = targets.get(mid, "(전수)")
        letters = kind_letters(kinds)
        reanalyze = "N" if (letters and letters <= NO_REANALYSIS_KINDS) else "Y"
        note = "워크넷 표기만 해당 — 시트 정비(일괄 치환) 사항" if reanalyze == "N" else ""
        if not C.norm_date(r.get("시행일자", "")):
            reanalyze, note = "N", "시행일자 없음 — 원문 판본을 특정할 수 없음(수동 확인)"
        out.append({
            "순번": i, "MST_ID": mid, "시행일자": C.norm_date(r.get("시행일자", "")), "법령명": r.get("법령명", ""),
            "소관부처": r.get("소관부처", ""), "연관도": r.get("연관도", ""), "우대여부": r.get("우대여부", ""),
            "우대분류": r.get("우대분류", ""), "검토필요": r.get("검토필요", ""), "AI신뢰도": r.get("AI신뢰도", ""),
            "점검유형": kinds, "재분석대상": reanalyze, "비고": note, "_시트행": r["_row"],
        })

    import pandas as pd
    df = pd.DataFrame(out)
    y = df[df["재분석대상"] == "Y"]
    by_date = (y.groupby("시행일자").size().reset_index(name="대상 수").sort_values("시행일자")
               if len(y) else pd.DataFrame(columns=["시행일자", "대상 수"]))

    path = C.WORK / f"대상목록_{C.STAMP}.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        df.to_excel(w, sheet_name="대상목록", index=False)
        by_date.to_excel(w, sheet_name="시행일자별", index=False)
        pd.DataFrame({"MST_ID": missing}).to_excel(w, sheet_name="누락", index=False)
    _style(path)

    print(f"\n✅ {path.name}")
    print(f"   대상 {len(df)}건 → 재분석 Y {len(y)}건 / N {int((df['재분석대상'] == 'N').sum())}건 / 대장에 없음 {len(missing)}건")
    print(f"   시행일자 종류 {len(by_date)}개 → run 단계 법제처 목록 조회 약 {len(by_date)}회")
    if len(y):
        print("   재분석 Y 구성 — 연관도:", dict(y["연관도"].value_counts()), "| 우대 O:", int((y["우대여부"] == "O").sum()))
    if missing:
        print(f"⚠️ 대장에 없는 MST_ID {len(missing)}건 → '누락' 시트 확인: {missing[:5]}")


def _style(path):
    from openpyxl import load_workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter
    wb = load_workbook(path)
    widths = {"대상목록": [6, 14, 10, 40, 16, 9, 8, 11, 8, 8, 46, 9, 40, 8], "시행일자별": [12, 8], "누락": [16]}
    for ws in wb.worksheets:
        for c in ws[1]:
            c.font = Font(name="맑은 고딕", bold=True, color="FFFFFF", size=10)
            c.fill = PatternFill("solid", fgColor="1F3864"); c.alignment = Alignment(vertical="center")
        for row in ws.iter_rows(min_row=2):
            for c in row:
                c.font = Font(name="맑은 고딕", size=9); c.alignment = Alignment(vertical="top", wrap_text=True)
        for i, wd in enumerate(widths.get(ws.title, []), 1):
            ws.column_dimensions[get_column_letter(i)].width = wd
        ws.freeze_panes = "A2"
        if ws.max_row > 1:
            ws.auto_filter.ref = ws.dimensions
    wb.save(path)


if __name__ == "__main__":
    main()
