# -*- coding: utf-8 -*-
"""
Q-RADAR 대장 재분석 도구 — 공통 모듈  (2026-09-16 신설)
========================================================
scan.py / run.py / apply.py 가 함께 쓰는 것들:
  · .env / gcp-key.json 로드 (기존 migrate_tool 규약 그대로)
  · 대장(국가기술자격 관련법령 탭) 읽기 — 구글 시트 또는 LOCAL_XLSX(오프라인 검토·테스트)
  · 컬럼 정의: 고정 키 4열(MST_ID·시행일자·소관부처·법령명) / 분석 산출 20열
  · 산출물 폴더(work/) 경로

원칙
  · 분석 로직은 여기 없다 — core(scraper·brain·certs)를 그대로 부른다(코드 중복 금지).
  · 이 모듈은 시트를 읽기만 한다. 쓰는 곳은 apply.py 한 곳.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORK = HERE / "work"                      # 산출물·캐시 (깃허브 커밋 금지 — .gitignore 참조)
WORK.mkdir(exist_ok=True)

LEDGER_TAB = "국가기술자격 관련법령"

# 대장 24열 — pipeline/config.py COLUMNS 와 같은 순서. (config 를 import 하지 않는 이유: 이 도구는
#  pipeline 폴더 밖에서 단독 실행되고, 실수로 config 가 로컬 .env 를 읽는 부작용을 피하기 위함)
COLUMNS = [
    "MST_ID", "시행일자", "소관부처", "법령명", "개정유형", "연관도", "우대여부", "관련 종목",
    "주요 제·개정내용", "활용도_구분", "활용도_상세", "조문 요약", "우대분류",
    "Track1_취급유형", "Track1_위험도", "Track2_효용코드", "중처법대상", "상세 분석 결과",
    "근거조문", "AI신뢰도", "검토필요", "검토사유", "조문별 다이렉트 링크", "워크넷 실시간 구인건수",
]
KEY_COLS = COLUMNS[:4]                    # 절대 덮어쓰지 않는 열
ANALYSIS_COLS = COLUMNS[4:]               # 재분석이 갱신할 수 있는 열 (20열)
# 대조표에서 사람이 먼저 보는 핵심 판정 열 (변경 여부 색 표시·요약 통계 대상)
CORE_COLS = ["연관도", "우대여부", "우대분류", "관련 종목", "Track1_취급유형", "Track1_위험도",
             "Track2_효용코드", "활용도_구분", "AI신뢰도", "검토필요"]

STAMP = datetime.now().strftime("%Y%m%d")


# ─────────────────────────────────────────────────────────────
# .env / gcp-key.json  (reanalyze_ghosts.py 와 같은 규약)
# ─────────────────────────────────────────────────────────────
def load_env() -> dict:
    """폴더의 .env 를 읽어 os.environ 에 채운다(이미 있는 값은 덮지 않음). 반환: 읽은 키-값."""
    env: dict = {}
    p = HERE / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    for k, v in env.items():
        if v and not os.environ.get(k):
            os.environ[k] = v
    # 시트 키: QRADAR_SHEET(도구 규약) 또는 GOOGLE_SHEET_URL(파이프라인 규약) 둘 다 허용
    if not os.environ.get("GOOGLE_SHEET_URL") and env.get("QRADAR_SHEET"):
        os.environ["GOOGLE_SHEET_URL"] = env["QRADAR_SHEET"]
    # 서비스 계정: gcp-key.json 파일 → GCP_SERVICE_ACCOUNT_JSON 문자열
    key = HERE / "gcp-key.json"
    if key.exists() and not os.environ.get("GCP_SERVICE_ACCOUNT_JSON"):
        os.environ["GCP_SERVICE_ACCOUNT_JSON"] = key.read_text(encoding="utf-8")
    return env


def sheet_key() -> str:
    """URL 이든 키든 받아서 스프레드시트 키만 돌려준다."""
    v = os.environ.get("GOOGLE_SHEET_URL", "").strip()
    m = re.search(r"/d/([A-Za-z0-9_-]+)", v)
    return m.group(1) if m else v


def require(keys: list[str], purpose: str) -> bool:
    """필수 환경변수 점검. 하나라도 없으면 안내 후 False."""
    ok = True
    for k in keys:
        if not os.environ.get(k):
            print(f"⚠️ .env 에 {k} 가 없습니다 ({purpose}).")
            ok = False
    return ok


# ─────────────────────────────────────────────────────────────
# 대장 읽기
# ─────────────────────────────────────────────────────────────
def open_ledger_ws():
    """gspread 워크시트(대장 탭) 반환. 쓰기는 apply.py 만 한다."""
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        json.loads(os.environ["GCP_SERVICE_ACCOUNT_JSON"], strict=False),
        ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"],
    )
    return gspread.authorize(creds).open_by_key(sheet_key()).worksheet(LEDGER_TAB)


def read_ledger() -> tuple[list[str], list[dict]]:
    """
    대장 전체를 (헤더, 행 dict 목록) 으로 읽는다. 모든 값은 str.
      · LOCAL_XLSX 환경변수가 있으면 그 파일의 대장 탭을 읽는다 (오프라인 검토·테스트용)
      · 없으면 구글 시트
    행 dict 에는 '_row'(시트 실제 행 번호, 헤더=1) 를 함께 넣는다 — apply 가 MST_ID 대조 후 이 번호로 쓴다.
    """
    lx = os.environ.get("LOCAL_XLSX", "").strip()
    if lx:
        import pandas as pd
        df = pd.read_excel(lx, sheet_name=LEDGER_TAB, dtype=str).fillna("")
        header = [str(c).strip() for c in df.columns]
        rows = []
        for i, rec in enumerate(df.to_dict("records")):
            r = {k: str(v).strip() for k, v in rec.items()}
            r["_row"] = i + 2
            rows.append(r)
        src = f"LOCAL_XLSX {Path(lx).name}"
    else:
        ws = open_ledger_ws()
        values = ws.get_all_values()
        header = [str(c).strip() for c in values[0]]
        rows = []
        for i, vals in enumerate(values[1:]):
            vals = list(vals) + [""] * (len(header) - len(vals))
            r = {h: str(v).strip() for h, v in zip(header, vals)}
            r["_row"] = i + 2
            rows.append(r)
        src = "구글 시트"
    missing = [c for c in COLUMNS if c not in header]
    if missing:
        print(f"⚠️ 대장 헤더에 없는 열: {missing} — config.COLUMNS 와 시트가 어긋났습니다. 중단.")
        sys.exit(1)
    print(f"· 대장 읽기: {src} · {len(rows)}행 · {len(header)}열")
    return header, rows


def norm_law_name(name: str) -> str:
    """법령명 비교 키 (core.scraper.norm_law_name 과 동일 규칙)."""
    return re.sub(r"[\s·ㆍ()（）]", "", str(name or ""))


def norm_date(v: str) -> str:
    """시행일자를 YYYYMMDD 8자리로. (시트에 2026-09-16 / 20260916 / 2026.9.16 혼재 대비)"""
    d = re.sub(r"\D", "", str(v or ""))
    return d[:8] if len(d) >= 8 else d


def same_value(a, b) -> bool:
    """대조·변경 감지용 비교. 앞뒤 공백과 쉼표 주변 공백만 무시한다(그 외는 글자 하나라도 다르면 '다름')."""
    n = lambda v: re.sub(r"\s*,\s*", ",", str(v or "").strip())
    return n(a) == n(b)
