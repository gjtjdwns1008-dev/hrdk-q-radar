"""'국가기술자격 관련법령' 탭(현행 운영 시트) → 내보내기 레코드. [공용 — 양쪽 진입점이 import]

원천 전환 이력
--------------
초기 설계 원천은 '우대사항_대장' 탭이었으나, 그 탭은 구본 시트에 남은
초기 유물(직능연 과거자료 적재분)임을 담당자가 확인(2026-09-07).
현행 통합 시스템의 분석 결과는 '국가기술자격 관련법령' 탭에 쌓이므로
원천을 이 탭으로 확정한다.

규격 v1.3 §2.1 '검토상태=확정만 내보냄'의 취지(신뢰 가능한 건만)는
이 탭의 실제 열로 이렇게 구현한다:
    내보내기 대상 = 우대여부가 'O' 이고, 검토필요가 'O'가 아닌 행
실측(2026-09-07, 현행 시트): 전체 1,892행 → 우대 'O' 295행
→ 그중 검토필요 'O' 6행 제외 → 289행이 내보내기 대상.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator

# ── 상수 블록 (실측 헤더 기준 — 2026-09-07 확정) ─────────────────────
SOURCE_TAB = "국가기술자격 관련법령"   # 현행 원천 탭
COL_LAW    = "법령명"
COL_QUAL   = "관련 종목"
COL_CAT    = "우대분류"
COL_FLAG   = "우대여부"      # 'O' = 우대 판정
FLAG_EXPORT = "O"
COL_REVIEW = "검토필요"      # 'O' = 담당자 검토 대기 → 내보내기 제외
REVIEW_EXCLUDE = "O"
COL_DATE   = "시행일자"      # 대표 법령 규칙 §1.3-2 '최신 개정 시행일 기준'에 사용
QUAL_SEPARATORS = (",", "、", ";", "/", "\n")
# ⚠ 가운뎃점(·)은 분리자가 아님 — '항공전기·전자정비기능사'처럼 종목명 자체에
#    쓰이는 실존 표기(담당자 확인 2026-09-07). 표기 차이는 codemap 정규화가 흡수.
# ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SourceRecord:
    """원자화 레코드: 종목 1개 단위."""
    qual_name: str
    law_name: str
    raw_cat: str          # 대장값 원본 표기 (의무고용/직무권한부여/…)
    enforce_date: str = ""  # 시행일자(숫자만 정규화, yyyymmdd) — 대표 선정 순서용


def _split_quals(cell: str) -> list[str]:
    text = (cell or "").strip()
    for sep in QUAL_SEPARATORS[1:]:
        text = text.replace(sep, QUAL_SEPARATORS[0])
    return [t.strip() for t in text.split(QUAL_SEPARATORS[0]) if t.strip()]


def is_export_row(row: dict) -> bool:
    """내보내기 대상 행인가 — 우대 'O' 이고 검토필요 'O' 아님."""
    if str(row.get(COL_FLAG, "")).strip() != FLAG_EXPORT:
        return False
    if str(row.get(COL_REVIEW, "")).strip() == REVIEW_EXCLUDE:
        return False
    return True


def iter_export_records(rows: Iterable[dict]) -> Iterator[SourceRecord]:
    """get_all_records() 결과 → 내보내기 대상만 원자화해서 방출."""
    for row in rows:
        if not is_export_row(row):
            continue
        law = str(row.get(COL_LAW, "")).strip()
        cat = str(row.get(COL_CAT, "")).strip()
        if not law or not cat:
            continue
        date = "".join(ch for ch in str(row.get(COL_DATE, "")) if ch.isdigit())[:8]
        for qual in _split_quals(str(row.get(COL_QUAL, ""))):
            yield SourceRecord(qual, law, cat, date)


def fetch_source_rows(ss) -> list[dict]:
    """스프레드시트 객체에서 원천 탭 전체를 dict 목록으로 가져온다."""
    return ss.worksheet(SOURCE_TAB).get_all_records()
