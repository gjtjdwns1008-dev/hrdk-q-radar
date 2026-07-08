# -*- coding: utf-8 -*-
"""
relabel_tracks.py — 통합 대장 Track 3칸에 한글 라벨 재부착 도구
=============================================================================
왜 필요한가?
  이관 도구가 Track 코드의 라벨을 벗겨 'B'처럼 맨 코드로 통일했었는데,
  시트는 사람이 읽는 원장이므로 'B (영업요건형)' 병기가 표준입니다(사용자 확정).
  이 도구는 대장 전체를 훑어 맨 코드 셀에만 라벨을 입힙니다.
  · 이미 라벨된 셀은 그대로 통과 (core 라벨 함수가 원본 반환) → 몇 번을 돌려도 안전
  · Track1_취급유형 / Track1_위험도 / Track2_효용코드 세 컬럼의 해당 셀만 수정

실행:  python relabel_tracks.py  → 메뉴 (1 미리보기 / 2 반영 / 0 종료)
준비물: migrate_tool/.env(QRADAR_SHEET) + gcp-key.json + hrdk-law-core 설치
"""
import re
import sys
import json
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAIN_TAB = "국가기술자격 관련법령"
PREVIEW_PATH = str(HERE / "재라벨_미리보기.xlsx")


def _load_env_file():
    env = {}
    p = HERE / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


_ENV = _load_env_file()


def _preflight():
    ok = True
    if not (HERE / "gcp-key.json").exists():
        print("🔑 gcp-key.json이 이 폴더에 없습니다."); ok = False
    if not _ENV.get("QRADAR_SHEET"):
        print("⚠️ .env에 QRADAR_SHEET가 없습니다."); ok = False
    try:
        from hrdk_law_core.certs import label_track1_type  # noqa
    except Exception:
        print("⚠️ hrdk-law-core가 없거나 구버전입니다.")
        print("   👉 pip install --upgrade --force-reinstall git+https://github.com/gjtjdwns1008-dev/hrdk-law-core.git")
        ok = False
    if not ok:
        print("\n준비물을 채운 뒤 다시:  python relabel_tracks.py")
    return ok


def open_ws():
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials
    key = _ENV["QRADAR_SHEET"]
    m = re.search(r"/d/([A-Za-z0-9_-]+)", key)
    if m:
        key = m.group(1)
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        json.loads((HERE / "gcp-key.json").read_text(encoding="utf-8"), strict=False),
        ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
    return gspread.authorize(creds).open_by_key(key).worksheet(MAIN_TAB)


def run(apply: bool):
    from hrdk_law_core.certs import label_track1_type, label_track1_risk, label_track2_code
    LABELERS = {"Track1_취급유형": label_track1_type,
                "Track1_위험도": label_track1_risk,
                "Track2_효용코드": label_track2_code}

    print("=" * 62)
    print(f"🏷️ Track 라벨 재부착 — {'★실제 반영★' if apply else '미리보기만'}")
    print("=" * 62)
    ws = open_ws()
    values = ws.get_all_values()
    header = [h.strip() for h in values[0]]
    ix = {h: i for i, h in enumerate(header)}
    for col in LABELERS:
        if col not in ix:
            print(f"⛔ 대장 헤더에 '{col}' 칸이 없습니다."); sys.exit(1)

    def col_letter(n):
        s = ""
        while n:
            n, r = divmod(n - 1, 26); s = chr(65 + r) + s
        return s

    changes = []          # (행, 컬럼명, 현재, 변경)
    per_col = {c: 0 for c in LABELERS}
    for rn, row in enumerate(values[1:], start=2):
        for col, fn in LABELERS.items():
            i = ix[col]
            cur = str(row[i]).strip() if i < len(row) else ""
            if not cur:
                continue
            new = fn(cur)
            if new != cur:
                changes.append((rn, col, cur, new))
                per_col[col] += 1

    print(f"  📥 대장 {len(values)-1}행 스캔 — 라벨 부착 대상 {len(changes)}셀")
    for c, n in per_col.items():
        print(f"     · {c}: {n}셀")
    if changes[:5]:
        print("  표본:", ", ".join(f"{c[2]}→{c[3]}" for c in changes[:5]))

    from openpyxl import Workbook
    wb = Workbook(); w = wb.active; w.title = "재라벨_대상"
    w.append(["행", "컬럼", "현재", "변경"])
    for c in changes:
        w.append(list(c))
    try:
        wb.save(PREVIEW_PATH); print(f"  💾 미리보기: {PREVIEW_PATH}")
    except PermissionError:
        alt = str(HERE / f"재라벨_미리보기_{time.strftime('%H%M%S')}.xlsx")
        wb.save(alt); print(f"  💾 미리보기: {alt} (기존 파일 열려 있음)")

    if not apply:
        print("\n✅ 미리보기만 생성. 확인 후 2번(반영)을 실행하세요.")
        return
    if not changes:
        print("  반영할 것이 없습니다 (이미 전부 라벨 상태)."); return
    updates = [{"range": f"{col_letter(ix[col] + 1)}{rn}", "values": [[new]]}
               for rn, col, _cur, new in changes]
    for i in range(0, len(updates), 400):
        ws.batch_update(updates[i:i + 400], value_input_option="RAW")
        print(f"    …반영 {min(i + 400, len(updates))}/{len(updates)}셀")
    print(f"\n🎉 라벨 재부착 완료: {len(changes)}셀")
    print("※ 이후 일일 신규 행은 report_maker가 자동으로 라벨을 입혀 기록합니다.")


if __name__ == "__main__":
    args = [a.lower() for a in sys.argv[1:]]
    if args:
        run(apply=("apply" in args)); sys.exit(0)
    print("=" * 62)
    print("🏷️ Q-RADAR Track 라벨 재부착 — 쉬운 모드")
    print("=" * 62)
    if not _preflight():
        sys.exit(0)
    print("\n무엇을 할까요?")
    print("  1) 미리보기 — 어떤 셀이 어떻게 바뀔지 목록만 (안 바꿈)")
    print("  2) 실제 반영 — Track 3컬럼의 맨 코드 셀에 라벨 부착")
    print("  0) 종료")
    choice = input("\n번호를 입력하고 Enter: ").strip()
    if choice == "1":
        run(apply=False)
    elif choice == "2":
        confirm = input("⚠️ 시트의 Track 셀들을 수정합니다. 진행하려면 '반영' 입력: ").strip()
        if confirm == "반영":
            run(apply=True)
        else:
            print("취소했습니다.")
    else:
        print("종료합니다.")
