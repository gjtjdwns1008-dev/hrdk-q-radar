"""구글시트 읽기 전용 접속 부품. [공용 — 로컬·깃허브 진입점이 공유]

로컬(.env → 환경변수)과 깃허브(Secrets → 환경변수)가 같은 재료 이름
(GCP_SERVICE_ACCOUNT_JSON, GOOGLE_SHEET_URL)·같은 코드로 접속한다 —
접속 방식 차이로 인한 동작 차이를 원천 차단.

권한은 읽기 전용(spreadsheets.readonly) — 이 연계 도구는 시트를 절대 수정하지 못한다.
확인 5 종결(2026-09-08): 기존 core 자리표시를 본 모듈로 대체(네비게이터와 동일한
경량 gspread 방식 — 워크플로 추가 의존성 없음).
"""

from __future__ import annotations

import json
import re
import sys

READONLY_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"


def extract_id(value: str) -> str | None:
    """값이 시트 전체 URL이든 ID(키) 단독이든 ID만 뽑아낸다."""
    value = (value or "").strip()
    m = re.search(r"/d/([A-Za-z0-9_-]{20,})", value)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{25,}", value):
        return value
    return None


def connect_readonly(sa_json: str, sheet_ref: str):
    """서비스계정 JSON 문자열 + 시트 참조(URL/키) → 읽기 전용 스프레드시트 객체."""
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        sys.exit("❌ 라이브러리 미설치 — 실행에 쓴 파이썬으로 아래 한 줄:\n"
                 "   python -m pip install gspread google-auth")
    sid = extract_id(sheet_ref)
    if not sid:
        raise ValueError(f"시트 ID를 읽지 못했습니다: {sheet_ref[:40]!r}")
    info = json.loads(sa_json)
    creds = Credentials.from_service_account_info(info, scopes=[READONLY_SCOPE])
    return gspread.authorize(creds).open_by_key(sid)
