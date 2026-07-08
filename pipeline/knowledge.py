# -*- coding: utf-8 -*-
"""
knowledge.py — Q-RADAR의 SQLite 창고지기
==========================================================
[역할 — 세 문장 요약]
  1) core의 KnowledgeBase(hrdk_law.db)를 그대로 물려받되, 통합 대장의 새 칸 6개를
     테이블에 추가합니다(ALTER). core는 건드리지 않으므로 라이브 시스템과 충돌 없음.
  2) 매 실행 시작 때 구글시트 '관련법령(통합 대장)' 전체를 daily_analysis로
     재구축합니다. ★시트=원본, SQLite=검색용 사본★ — GitHub Actions는 실행마다
     컴퓨터가 초기화되므로, 영속하는 시트에서 매번 다시 짓는 것이 누적을 지키는 길.
     (aitestbed VM으로 이전하면 디스크가 영속이라 이 재구축은 '보험'이 됩니다)
  3) 그날 분석 결과(law_info)를 즉시 upsert합니다. 이 창고가 곧 MCP 챗봇의
     RAG 검색 대상(query_preference_db)이 됩니다.

[통합 대장 24칸 ↔ SQLite 컬럼 매핑]
  시트 컬럼명(한글)이 원본이고, db 컬럼명(영문)은 검색용 별명입니다.
  기존 core 컬럼은 재사용하고, ★표 6개만 이 파일이 새로 추가합니다.
"""
import os
import sqlite3
_DEF_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "hrdk_law.db")
from hrdk_law_core.db import KnowledgeBase

# ── 통합 대장 ↔ db 컬럼 매핑 (단일 정의처) ─────────────────────────────
#    (시트 헤더명, db 컬럼명) — 순서는 통합 대장 24칸 순서를 따름
UNIFIED_MAP = [
    ("MST_ID",                "mst_id"),
    ("시행일자",               "enforce_date"),
    ("소관부처",               "ministry"),
    ("법령명",                 "law_name"),
    ("개정유형",               "amend_type"),        # ★신규
    ("연관도",                 "relevance"),          # (기존 relevance 재사용: 연관높음/단순)
    ("우대여부",               "is_preferred"),       # ★신규 (O / "")
    ("관련 종목",              "related_certs"),
    ("주요 제·개정내용",        "main_changes"),       # ★신규
    ("활용도_구분",             "usage_level"),        # ★신규
    ("활용도_상세",             "usage_detail"),       # ★신규
    ("조문 요약",              "summary"),
    ("우대분류",               "preference_type"),
    ("Track1_취급유형",        "track1_type"),
    ("Track1_위험도",          "track1_risk"),
    ("Track2_효용코드",        "track2_code"),
    ("중처법대상",             "is_serious_accident"),
    ("상세 분석 결과",          "detail_analysis"),
    ("근거조문",               "evidence_article"),
    ("AI신뢰도",               "ai_confidence"),
    ("검토필요",               "needs_review"),
    ("검토사유",               "review_reason"),
    ("조문별 다이렉트 링크",     "direct_links"),
    ("워크넷 실시간 구인건수",   "worknet_demand"),
]
NEW_COLUMNS = ["amend_type", "is_preferred", "main_changes", "usage_level", "usage_detail"]


class QRadarKB(KnowledgeBase):
    """core KnowledgeBase + 통합 대장 확장. 사용법은 기존과 동일: kb = QRadarKB(DB_PATH)"""

    def __init__(self, db_path: str = _DEF_DB):
        super().__init__(db_path)          # core 스키마 생성 (있으면 통과)
        self._ensure_unified_schema()      # ★새 칸 6개 추가 (없을 때만)

    # ── 1) 스키마 확장 ──────────────────────────────────────────────
    def _ensure_unified_schema(self) -> None:
        """daily_analysis에 통합 대장 신규 컬럼이 없으면 추가합니다(있으면 조용히 통과)."""
        with self._conn() as conn:
            existing = {r[1] for r in conn.execute("PRAGMA table_info(daily_analysis)")}
            for col in NEW_COLUMNS:
                if col not in existing:
                    conn.execute(f"ALTER TABLE daily_analysis ADD COLUMN {col} TEXT")

    # ── 2) 통합 upsert (그날 분석분 즉시 기록) ───────────────────────
    def upsert_unified(self, law_info: dict) -> None:
        """
        통합 brain의 law_info(+MST_ID, 워크넷, hybrid_status)를 daily_analysis에 upsert.
        core의 upsert_daily 대신 이걸 쓰면 새 칸 6개까지 함께 저장됩니다.
        """
        cols   = [db for _, db in UNIFIED_MAP] + ["hybrid_status"]
        values = [law_info.get(sheet, "") for sheet, _ in UNIFIED_MAP] + [law_info.get("hybrid_status", "")]
        placeholders = ", ".join(["?"] * len(cols))
        update_clause = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "mst_id")
        with self._conn() as conn:
            conn.execute(
                f"""
                INSERT INTO daily_analysis ({", ".join(cols)}, updated_at)
                VALUES ({placeholders}, datetime('now','localtime'))
                ON CONFLICT(mst_id) DO UPDATE SET
                    {update_clause},
                    updated_at = datetime('now','localtime')
                """,
                values,
            )

    # ── 3) 시트 → db 전체 재구축 (매 실행 시작) ──────────────────────
    def rebuild_daily_from_sheet_values(self, values: list) -> int:
        """
        구글시트 '관련법령(통합 대장)' 탭의 get_all_values() 결과를 받아
        daily_analysis를 통째로 재구축합니다(기존 내용 삭제 후 전부 적재).
        반환: 적재된 행 수.

        ※ '삭제 후 재적재'인 이유: 시트에서 담당자가 지운 행이 db에 유령으로
          남지 않게, 사본은 언제나 원본(시트)의 거울이어야 하기 때문.
        """
        if not values or len(values) < 2:
            return 0
        header = [str(h).strip() for h in values[0]]
        idx = {h: i for i, h in enumerate(header)}

        rows_to_insert = []
        for row in values[1:]:
            def cell(name):
                i = idx.get(name)
                return (str(row[i]).strip() if (i is not None and i < len(row) and row[i] is not None) else "")
            if not cell("법령명"):          # 빈 행 방지
                continue
            info = {sheet: cell(sheet) for sheet, _ in UNIFIED_MAP}
            rows_to_insert.append(info)

        with self._conn() as conn:
            conn.execute("DELETE FROM daily_analysis")
        for info in rows_to_insert:
            self.upsert_unified(info)
        return len(rows_to_insert)

    # ── 4) (MCP 대비) 통합 검색 — 우대여부·연관도 필터 지원 ─────────────
    def search_unified(
        self,
        cert_name=None,
        law_name=None,
        preference_type=None,
        only_preferred: bool = False,
        relevance=None,
        limit: int = 100,
    ) -> list:
        """통합 대장 검색. MCP query_preference_db의 심장이 될 함수."""
        conditions, params = [], []
        if cert_name:
            conditions.append("related_certs LIKE ?"); params.append(f"%{cert_name}%")
        if law_name:
            conditions.append("law_name LIKE ?"); params.append(f"%{law_name}%")
        if preference_type:
            conditions.append("preference_type LIKE ?"); params.append(f"%{preference_type}%")
        if only_preferred:
            conditions.append("is_preferred = 'O'")
        if relevance:
            conditions.append("relevance = ?"); params.append(relevance)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM daily_analysis {where} ORDER BY enforce_date DESC LIMIT ?",
                params + [int(limit)],
            ).fetchall()
        return [dict(r) for r in rows]
