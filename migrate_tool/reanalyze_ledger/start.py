# -*- coding: utf-8 -*-
"""
Q-RADAR 대장 재분석 도구 — 시작 메뉴  (▶ 실행 버튼으로 실행)
=============================================================
이 파일만 실행하면 아래 메뉴가 뜹니다. 각 항목은 같은 폴더의 scan.py / run.py / apply.py 를
같은 파이썬으로 실행합니다(명령줄 인자 필요 없음 — 필요한 선택은 화면에서 물어봄).

  [1] 대상 선정        scan.py   → work/대상목록_날짜.xlsx           (시트 읽기만)
  [2] 재분석 실행      run.py    → work/재분석_대조_날짜.xlsx         (시트 읽기만, 건수는 화면에서 질문)
  [3] 시트 반영        apply.py  → 미리보기 후 '반영' 입력 시 기록    (시트 쓰기 — 백업 탭 자동 생성)
  [4] 산출물 폴더 열기 work/
  [0] 종료
"""
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable                     # 지금 이 파일을 실행한 파이썬 그대로 사용

MENU = """
┌──────────────────────────────────────────────┐
│  Q-RADAR 대장 재분석 도구                     │
├──────────────────────────────────────────────┤
│  [1] 대상 선정   (scan  · 시트 읽기만)        │
│  [2] 재분석 실행 (run   · 시트 읽기만)        │
│  [3] 시트 반영   (apply · 미리보기 → 확인)    │
│  [4] 산출물 폴더 열기 (work/)                 │
│  [0] 종료                                    │
└──────────────────────────────────────────────┘"""


def run_script(name: str):
    path = HERE / name
    if not path.exists():
        print(f"⚠️ {name} 이 같은 폴더에 없습니다."); return
    print(f"\n▶ {name} 실행 ({PY})\n" + "─" * 50)
    rc = subprocess.call([PY, str(path)], cwd=str(HERE))
    print("─" * 50 + f"\n◀ {name} 종료 (코드 {rc})")


def open_work():
    w = HERE / "work"; w.mkdir(exist_ok=True)
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(w))                     # 탐색기
        elif sys.platform == "darwin":
            subprocess.call(["open", str(w)])
        else:
            subprocess.call(["xdg-open", str(w)])
    except Exception as e:
        print(f"폴더: {w}  ({e})")


def main():
    os.chdir(HERE)
    if not (HERE / ".env").exists():
        print("⚠️ .env 가 없습니다. env.example 을 복사해 .env 로 만들고 값을 채워 주세요.")
    if not (HERE / "gcp-key.json").exists():
        print("⚠️ gcp-key.json 이 없습니다. 이 폴더에 서비스 계정 키 파일을 두세요.")
    while True:
        print(MENU)
        ch = input("번호 선택: ").strip()
        if ch == "1":
            run_script("scan.py")
        elif ch == "2":
            run_script("run.py")
        elif ch == "3":
            run_script("apply.py")
        elif ch == "4":
            open_work()
        elif ch in ("0", "q", "Q", ""):
            print("종료합니다."); break
        else:
            print("1~4 또는 0 을 입력하세요.")


if __name__ == "__main__":
    main()
