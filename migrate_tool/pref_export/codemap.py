"""종목명 → Q-Net 종목코드 매핑 + 검정형 판별. [공용 — 양쪽 진입점이 import]

규격 근거:
  §2.1  검정형 종목만 내보냄(과정평가형은 Q-Page 주입기가 동일 종목명의 검정형 공유).
  §2.2  items 키 = 종목코드 '문자열'(앞자리 0 보존) → 코드는 항상 str로 다룬다.
  §3    과정평가형 = 코드가 문자로 시작하는 종목.

# TODO(확인 1): 매핑 소스 파일 경로·컬럼명을 레포의 2026년 541개 종목 사전에 맞출 것.
#               (종목 사전에 Q-Net 코드가 없으면 코드 매핑표를 별도 1회 구축 — 규격 §5 '지금' 단계)
"""

from __future__ import annotations

import csv
from pathlib import Path

DEFAULT_CODEMAP_PATH = Path("data") / "qnet_codes.csv"   # TODO(확인 1): 실제 경로
COL_CODE = "종목코드"                                     # TODO(확인 1): 실제 헤더
COL_NAME = "종목명"                                       # TODO(확인 1): 실제 헤더


def is_certification_code(code: str) -> bool:
    """검정형 코드 여부 — 전부 숫자면 검정형, 문자로 시작하면 과정평가형(§3)."""
    return code.isdigit()


def _norm(name: str) -> str:
    """매칭용 정규화: 앞뒤·중간 공백 제거."""
    return (name or "").strip().replace(" ", "")


def load_codemap(path: Path | str | None = None) -> dict[str, str]:
    """종목명(정규화) → 종목코드(str, 앞자리 0 보존).

    같은 종목명이 검정형·과정평가형 코드로 중복되면 검정형(숫자) 코드를 우선한다.
    """
    src = Path(path) if path else DEFAULT_CODEMAP_PATH
    table: dict[str, str] = {}
    with open(src, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            code = str(row.get(COL_CODE, "")).strip()
            name = _norm(str(row.get(COL_NAME, "")))
            if not code or not name:
                continue
            prev = table.get(name)
            if prev is None or (not is_certification_code(prev) and is_certification_code(code)):
                table[name] = code
    return table


def resolve(codemap: dict[str, str], qual_name: str) -> str | None:
    """종목명 → 코드. 미매칭이면 None(집계 리포트에 정직 기록)."""
    return codemap.get(_norm(qual_name))
