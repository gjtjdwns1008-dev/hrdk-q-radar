# -*- coding: utf-8 -*-
"""
[3/3] apply.py — 승인 행만 시트에 반영  (이 도구에서 시트에 쓰는 유일한 파일)
==========================================================================
대조 엑셀(run 산출) 의 '승인' 열이 O 인 행만 골라 대장에 기록한다.

절차
  1. 대조 엑셀 읽기 → 승인=O 이고 상태=성공 인 행 (X·빈칸은 미반영)
  2. 대장 새로 읽기 → MST_ID 로 행 다시 찾기 (행 번호를 믿지 않음 — 시트가 정렬돼도 안전)
     · 대장에 MST_ID 중복 → 중단
     · 대장의 현재값이 대조 엑셀의 '구_' 값과 다르면(run 이후 누가 시트를 고쳤다는 뜻) 그 행은 건너뛰고 보고 (--force 로 강행)
  3. 미리보기: 바뀔 칸을 전부 work/반영미리보기_시각.xlsx 로 저장하고 화면에 요약  ← 기본 동작, 시트에 쓰지 않음
  4. --commit: 대장 탭을 '백업_국가기술자격 관련법령_시각' 탭으로 복제한 뒤, 분석 20열만 기록
     (MST_ID·시행일자·소관부처·법령명 4열은 절대 쓰지 않음). 기록 후 다시 읽어 검산. work/반영로그.csv 추가.

사용
  python apply.py --file work/재분석_대조_20260916.xlsx             # 미리보기(기본)
  python apply.py --file work/재분석_대조_20260916.xlsx --commit    # 실제 반영
  python apply.py --file ... --local 대장.xlsx                       # 로컬 xlsx 로 미리보기만(쓰기 불가)
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import common as C  # noqa: E402

NOW = datetime.now().strftime("%Y%m%d_%H%M")


def col_letter(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26); s = chr(65 + r) + s
    return s


# ─────────────────────────────────────────────────────────────
def load_approved(path: Path) -> tuple[list[dict], dict]:
    import pandas as pd
    df = pd.read_excel(path, sheet_name="대조표", dtype=str).fillna("")
    stat = {"전체": len(df), "승인 O": int((df["승인"].str.strip().str.upper() == "O").sum()),
            "승인 X": int((df["승인"].str.strip().str.upper() == "X").sum()),
            "승인 빈칸": int((df["승인"].str.strip() == "").sum())}
    ok = df[(df["승인"].str.strip().str.upper() == "O")]
    bad = ok[ok["상태"] != "성공"]
    if len(bad):
        print(f"⚠️ 승인 O 인데 상태≠성공 {len(bad)}건 — 반영하지 않음: {bad['MST_ID'].tolist()[:5]}")
    ok = ok[ok["상태"] == "성공"]
    return ok.to_dict("records"), stat


def plan_changes(approved: list[dict], header: list[str], rows: list[dict], force: bool):
    """(반영 계획 목록, 건너뛴 목록). 계획 항목: {MST_ID, 법령명, _row, cells:[(열명, 구, 신)]}"""
    by_id: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("MST_ID"):
            by_id.setdefault(r["MST_ID"], []).append(r)
    plans, skipped = [], []
    for a in approved:
        mid = a["MST_ID"]
        hits = by_id.get(mid, [])
        if not hits:
            skipped.append((mid, a["법령명"], "대장에 MST_ID 없음")); continue
        if len(hits) > 1:
            skipped.append((mid, a["법령명"], f"대장에 MST_ID 중복 {len(hits)}행 — 정리 후 재시도")); continue
        cur = hits[0]
        # run 이후 시트가 바뀌었는지: 대장 현재값 vs 대조 엑셀의 구_ 값
        drift = [c for c in C.ANALYSIS_COLS if not C.same_value(cur.get(c, ""), a.get(f"구_{c}", ""))]
        if drift and not force:
            skipped.append((mid, a["법령명"], f"대조 시점 이후 시트 값 변경 감지({', '.join(drift[:3])}{'…' if len(drift) > 3 else ''}) — 재확인 후 --force"))
            continue
        cells = [(c, str(cur.get(c, "")), str(a.get(f"신_{c}", ""))) for c in C.ANALYSIS_COLS
                 if not C.same_value(cur.get(c, ""), a.get(f"신_{c}", ""))]
        if not cells:
            skipped.append((mid, a["법령명"], "바뀌는 칸 없음(이미 동일)")); continue
        plans.append({"MST_ID": mid, "법령명": a["법령명"], "_row": cur["_row"], "cells": cells})
    return plans, skipped


def write_preview(plans, skipped, stat, src: Path) -> Path:
    import pandas as pd
    cells = [{"MST_ID": p["MST_ID"], "법령명": p["법령명"], "시트행": p["_row"], "열": c, "구": o, "신": n}
             for p in plans for (c, o, n) in p["cells"]]
    out = C.WORK / f"반영미리보기_{NOW}.xlsx"
    with pd.ExcelWriter(out, engine="openpyxl") as w:
        pd.DataFrame([{"항목": k, "값": v} for k, v in stat.items()] +
                     [{"항목": "반영 예정 행", "값": len(plans)}, {"항목": "반영 예정 칸", "값": len(cells)},
                      {"항목": "건너뜀", "값": len(skipped)}, {"항목": "원본 대조 파일", "값": src.name}]).to_excel(w, sheet_name="요약", index=False)
        pd.DataFrame(cells).to_excel(w, sheet_name="바뀌는 칸", index=False)
        pd.DataFrame(skipped, columns=["MST_ID", "법령명", "사유"]).to_excel(w, sheet_name="건너뜀", index=False)
    return out


def do_commit(ws, header: list[str], plans: list[dict], backup: bool) -> tuple[str, list]:
    """백업 탭 복제 → 행별 분석 20열 기록(RAW) → 재읽기 검산. 반환 (백업탭명, 검산 불일치 목록)"""
    bk_name = ""
    if backup:
        bk_name = f"백업_{C.LEDGER_TAB}_{NOW}"
        ws.spreadsheet.duplicate_sheet(ws.id, new_sheet_name=bk_name)
        print(f"· 백업 탭 생성: {bk_name}")
    hidx = {h: i + 1 for i, h in enumerate(header)}
    # 바뀌는 칸만 개별 A1 범위로 기록 (RAW: 시트가 날짜·수식으로 해석하지 않게). 4열 고정 키는 cells 에 들어올 수 없음.
    reqs = [{"range": f"{col_letter(hidx[c])}{p['_row']}", "values": [[n]]} for p in plans for (c, _o, n) in p["cells"]]
    # 100개 단위 배치 (구글 API 요청 크기 제한 대비)
    for i in range(0, len(reqs), 100):
        ws.batch_update(reqs[i:i + 100], value_input_option="RAW")
    # 검산: 다시 읽어 의도값과 비교
    values = ws.get_all_values()
    mism = []
    for p in plans:
        row = values[p["_row"] - 1] if p["_row"] - 1 < len(values) else []
        for (c, _o, n) in p["cells"]:
            got = row[hidx[c] - 1] if hidx[c] - 1 < len(row) else ""
            if not C.same_value(got, n):
                mism.append((p["MST_ID"], c, n, got))
    return bk_name, mism


def append_log(src: Path, plans, bk_name: str, mism_n: int):
    lp = C.WORK / "반영로그.csv"
    new = not lp.exists()
    with open(lp, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["시각", "대조파일", "백업탭", "MST_ID", "법령명", "바뀐열", "검산불일치(전체)"])
        for p in plans:
            w.writerow([NOW, src.name, bk_name, p["MST_ID"], p["법령명"], "|".join(c for c, _o, _n in p["cells"]), mism_n])


# ─────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="승인 행만 시트 반영 (기본 미리보기)")
    ap.add_argument("--file", help="run 산출 대조 xlsx (기본: work/ 의 최신 비-MOCK)")
    ap.add_argument("--commit", action="store_true", help="실제로 시트에 쓴다 (없으면 미리보기만)")
    ap.add_argument("--force", action="store_true", help="대조 이후 시트 값이 바뀐 행도 덮어씀(비권장)")
    ap.add_argument("--no-backup", action="store_true", help="백업 탭 생략(비권장)")
    ap.add_argument("--local", help="로컬 대장 xlsx 로 미리보기만")
    args = ap.parse_args()

    C.load_env()
    if args.local:
        os.environ["LOCAL_XLSX"] = args.local
        if args.commit:
            print("⚠️ --local 은 미리보기 전용입니다. --commit 무시."); args.commit = False
    elif not C.require(["GOOGLE_SHEET_URL", "GCP_SERVICE_ACCOUNT_JSON"], "시트 접속"):
        sys.exit(1)

    src = Path(args.file) if args.file else sorted(p for p in C.WORK.glob("재분석_대조_*.xlsx") if "MOCK" not in p.name)[-1]
    if "MOCK" in src.name and args.commit:
        print("⛔ MOCK 파일은 시트에 반영할 수 없습니다."); sys.exit(1)
    approved, stat = load_approved(src)
    print(f"· 대조 파일: {src.name} → {stat}")
    if not approved:
        print("반영할 승인 O 행이 없습니다."); sys.exit(0)

    header, rows = C.read_ledger()
    plans, skipped = plan_changes(approved, header, rows, args.force)
    ncell = sum(len(p["cells"]) for p in plans)
    print(f"· 반영 예정: {len(plans)}행 / {ncell}칸 · 건너뜀 {len(skipped)}행")
    for mid, name, why in skipped[:8]:
        print(f"   - 건너뜀 {mid} {name[:24]} — {why}")
    pv = write_preview(plans, skipped, stat, src)
    print(f"· 미리보기 저장: {pv.name}")
    for p in plans[:5]:
        print(f"   {p['MST_ID']} {p['법령명'][:24]} (행 {p['_row']}): " + ", ".join(f"{c} {o[:10]!r}→{n[:10]!r}" for c, o, n in p["cells"][:3]) + (" …" if len(p["cells"]) > 3 else ""))

    if not args.commit:
        print("\n※ 미리보기만 했습니다. 실제 반영은 같은 명령에 --commit 을 붙여 실행하세요.")
        return
    if not plans:
        print("반영할 것이 없습니다."); return
    ws = C.open_ledger_ws()
    bk, mism = do_commit(ws, header, plans, backup=not args.no_backup)
    append_log(src, plans, bk, len(mism))
    print(f"\n✅ 반영 완료: {len(plans)}행 / {ncell}칸 · 백업 탭 {bk or '(생략)'} · 검산 불일치 {len(mism)}건")
    for m in mism[:5]:
        print(f"   ⚠️ 검산 불일치 {m[0]} [{m[1]}] 의도={m[2][:20]!r} 실제={m[3][:20]!r}")
    print("· 다음 네비게이터 빌드(일 4회)와 pref_export 게시에서 반영됩니다. 반영 전 Q-Page팀 통지 여부 확인.")


if __name__ == "__main__":
    main()
