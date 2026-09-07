"""우대사항_대장(구글 시트) → 정규화 레코드. [공용 — 양쪽 진입점이 import]

이 파일의 '상수 블록'만 실제 대장 표기에 맞추면 나머지 모듈은 손댈 필요 없다.

⚠ 과거 버그 재발 방지: get_sheet_client()는 (client, spreadsheet) 튜플을 반환한다.
   반드시  `_, ss = get_sheet_client()`  로 언패킹할 것. (호출은 진입점에서 수행)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator

# ── 상수 블록 ──────────────────────────────────────────────────────────
LEDGER_TAB = "우대사항_대장"      # TODO(확인 2): 실제 탭 이름
COL_QUAL   = "종목명"             # TODO(확인 2): 실제 헤더
COL_LAW    = "법령명"             # TODO(확인 2): 실제 헤더
COL_CAT    = "우대분류"           # TODO(확인 2): 실제 헤더
COL_STATUS = "검토상태"           # TODO(확인 2): 실제 헤더
STATUS_CONFIRMED = "확정"         # §2.1: '검토상태=확정' 건만 내보냄
QUAL_SEPARATORS = (",", "、", ";", "/")   # 한 셀에 여러 종목이 든 경우 분리자
# ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LedgerRecord:
    """원자화 레코드: 종목 1개 단위."""
    qual_name: str
    law_name: str
    raw_cat: str      # 대장값 원본 (의무고용/직무권한부여/…)


def _split_quals(cell: str) -> list[str]:
    text = (cell or "").strip()
    for sep in QUAL_SEPARATORS[1:]:
        text = text.replace(sep, QUAL_SEPARATORS[0])
    return [t.strip() for t in text.split(QUAL_SEPARATORS[0]) if t.strip()]


def iter_confirmed_records(rows: Iterable[dict]) -> Iterator[LedgerRecord]:
    """get_all_records() 결과 → 확정 건만 원자화해서 방출."""
    for row in rows:
        if str(row.get(COL_STATUS, "")).strip() != STATUS_CONFIRMED:
            continue
        law = str(row.get(COL_LAW, "")).strip()
        cat = str(row.get(COL_CAT, "")).strip()
        if not law or not cat:
            continue
        for qual in _split_quals(str(row.get(COL_QUAL, ""))):
            yield LedgerRecord(qual, law, cat)


def fetch_ledger_rows(ss) -> list[dict]:
    """스프레드시트 객체에서 대장 탭 전체를 dict 목록으로 가져온다."""
    return ss.worksheet(LEDGER_TAB).get_all_records()
