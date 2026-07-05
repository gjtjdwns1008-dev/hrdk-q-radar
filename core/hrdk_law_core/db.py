"""
hrdk_law_core.db
----------------
직능연 우대법령 정리본을 SQLite로 관리하는 지식베이스 모듈.

구글 시트는 "사람이 보는 보고용 화면"으로 역할이 재정의되며,
실제 빠른 검색이 필요한 대화형(MCP) 질의는 이 SQLite DB에서 처리합니다.

스키마:
  preference_laws  - 직능연 정리본 22,772행 (종목명 × 우대법령)
  daily_analysis   - 매일 AI 분석 결과 누적

사용법:
    from hrdk_law_core.db import KnowledgeBase
    kb = KnowledgeBase("hrdk_law.db")
    rows = kb.search_by_cert("전기기사")
"""

import sqlite3
from pathlib import Path


# ─────────────────────────────────────────────
# DDL: 테이블 정의
# ─────────────────────────────────────────────
_DDL = """
CREATE TABLE IF NOT EXISTS preference_laws (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    cert_name        TEXT    NOT NULL,   -- 종목명
    law_name         TEXT    NOT NULL,   -- 우대법령 (정규화 키)
    law_name_raw     TEXT,               -- 우대법령 (원본)
    article_text     TEXT,               -- 조문내역
    usage_content    TEXT,               -- 활용내용
    preference_type  TEXT,               -- 우대분류 (의무고용/직무권한부여/인사우대/시험면제/기타)
    is_hazard_law    INTEGER DEFAULT 0,  -- 중처법대상 여부 (1=대상)
    UNIQUE(cert_name, law_name)
);

CREATE TABLE IF NOT EXISTS daily_analysis (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    mst_id              TEXT    UNIQUE,           -- HRDK-L-YYYY-NNNN
    law_name            TEXT    NOT NULL,
    enforce_date        TEXT,
    ministry            TEXT,
    related_certs       TEXT,                     -- 쉼표 구분 종목 목록
    relevance           TEXT,                     -- 연관성_판별
    preference_type     TEXT,                     -- 우대분류 (의무고용/직무권한부여/인사우대/시험면제/기타)
    track1_type         TEXT,                     -- Track1_취급유형
    track1_risk         TEXT,                     -- Track1_위험도
    track2_code         TEXT,                     -- Track2_효용코드
    is_serious_accident TEXT,                     -- 중처법대상 (대상/비대상)
    summary             TEXT,                     -- 조문 요약
    detail_analysis     TEXT,                     -- 상세 분석결과
    evidence_article    TEXT,                     -- 근거 조문
    ai_confidence       TEXT,                     -- AI 신뢰도
    needs_review        TEXT,                     -- 검토 필요
    review_reason       TEXT,                     -- 검토 사유
    direct_links        TEXT,                     -- 조문별 다이렉트 링크
    worknet_demand      TEXT,                     -- 워크넷_실시간_구인건수
    hybrid_status       TEXT,                     -- 직능연_검증 / AI_스마트_보정 / AI_신규판단
    created_at          TEXT    DEFAULT (datetime('now','localtime')),
    updated_at          TEXT    DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_pref_law_name  ON preference_laws(law_name);
CREATE INDEX IF NOT EXISTS idx_pref_cert_name ON preference_laws(cert_name);
CREATE INDEX IF NOT EXISTS idx_daily_law_name ON daily_analysis(law_name);
CREATE INDEX IF NOT EXISTS idx_daily_mst_id   ON daily_analysis(mst_id);

CREATE TABLE IF NOT EXISTS held_laws (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    law_name      TEXT    NOT NULL,
    enforce_date  TEXT,
    ministry      TEXT,
    hold_reason   TEXT,                          -- 보류 사유 (어떤 키워드/규칙에 걸렸는지)
    law_link      TEXT,
    reviewed      INTEGER DEFAULT 0,             -- 담당자 검토 여부 (0=미검토)
    created_at    TEXT    DEFAULT (datetime('now','localtime')),
    UNIQUE(law_name, enforce_date)
);

CREATE INDEX IF NOT EXISTS idx_held_reviewed ON held_laws(reviewed);

CREATE TABLE IF NOT EXISTS reference_articles (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    seq           INTEGER,                        -- 원자료 연번
    law_name      TEXT    NOT NULL,               -- 법령명 (정규화 키)
    law_name_raw  TEXT,                           -- 법령명 (원본)
    article       TEXT,                           -- 조문내역
    article_no    TEXT,                           -- 대표 조문번호 (제N조)
    -- 정책 관점 (Track 1)
    track1_code   TEXT,                           -- 1차축 코드 (A~E)
    track1_type   TEXT,                           -- 1차축 유형명
    track1_risk_code TEXT,                        -- 2차축 코드 (N/L/M/H/C)
    track1_risk   TEXT,                            -- 2차축 강도명
    track1_unified TEXT,                          -- 통합코드 (예: C-M)
    track1_basis  TEXT,                           -- 분류 근거
    -- 국민 취업 관점 (Track 2)
    track2_code   TEXT,                           -- 신규코드 (예: Ⅱ-4)
    track2_type   TEXT,                           -- 세부유형명
    track2_basis  TEXT,                           -- 분류 근거
    -- 메타
    source_year   TEXT    DEFAULT '2022',         -- 기준 연도
    UNIQUE(law_name, article_no, seq)
);

CREATE INDEX IF NOT EXISTS idx_ref_law      ON reference_articles(law_name);
CREATE INDEX IF NOT EXISTS idx_ref_unified  ON reference_articles(track1_unified);
CREATE INDEX IF NOT EXISTS idx_ref_t2code   ON reference_articles(track2_code);

CREATE TABLE IF NOT EXISTS sync_state (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def _normalize_law_name(name: str) -> str:
    """
    법령명 정규화: 공백·특수문자 제거 후 소문자화.
    직능연 정리본은 공백 없이 붙여 써져 있어 조회 키 매칭에 사용합니다.
    예: "국가기술자격법 시행규칙" → "국가기술자격법시행규칙"
    """
    import re
    return re.sub(r"[\s·\-\(\)\[\]]", "", name).lower()


class KnowledgeBase:
    """
    HRDK 우대법령 지식베이스 (SQLite 래퍼)

    Parameters
    ----------
    db_path : SQLite 파일 경로 (기본: 현재 디렉토리의 hrdk_law.db)
    """

    def __init__(self, db_path: str = "hrdk_law.db"):
        self.db_path = str(db_path)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """DB 파일이 없으면 생성하고 스키마를 초기화합니다."""
        with self._conn() as conn:
            conn.executescript(_DDL)

    # ── preference_laws ──────────────────────────────────

    def upsert_preference(self, row: dict) -> None:
        """직능연 정리본 행 한 건을 Upsert합니다."""
        law_name_norm = _normalize_law_name(row.get("law_name_raw", ""))
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO preference_laws
                    (cert_name, law_name, law_name_raw, article_text, usage_content,
                     preference_type, is_hazard_law)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cert_name, law_name) DO UPDATE SET
                    article_text    = excluded.article_text,
                    usage_content   = excluded.usage_content,
                    preference_type = excluded.preference_type,
                    is_hazard_law   = excluded.is_hazard_law
                """,
                (
                    row.get("cert_name", ""),
                    law_name_norm,
                    row.get("law_name_raw", ""),
                    row.get("article_text", ""),
                    row.get("usage_content", ""),
                    row.get("preference_type", ""),
                    int(bool(row.get("is_hazard_law", False))),
                ),
            )

    def search_by_cert(self, cert_name: str) -> list[dict]:
        """특정 종목과 관련된 모든 우대법령을 반환합니다."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM preference_laws WHERE cert_name = ? ORDER BY preference_type",
                (cert_name,),
            ).fetchall()
        return [dict(r) for r in rows]

    def search_by_law(self, law_name: str) -> list[dict]:
        """특정 법령과 관련된 모든 종목을 반환합니다."""
        norm = _normalize_law_name(law_name)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM preference_laws WHERE law_name = ? ORDER BY cert_name",
                (norm,),
            ).fetchall()
        return [dict(r) for r in rows]

    def lookup_preference_type(self, cert_name: str, law_name: str) -> str | None:
        """
        (종목명, 법령명) 쌍으로 직능연 우대분류를 반환합니다.
        없으면 None 반환.
        """
        norm = _normalize_law_name(law_name)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT preference_type FROM preference_laws WHERE cert_name = ? AND law_name = ?",
                (cert_name, norm),
            ).fetchone()
        return row["preference_type"] if row else None

    def count_preference_laws(self) -> dict:
        """우대분류별 건수를 딕셔너리로 반환합니다."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT preference_type, COUNT(*) as cnt FROM preference_laws GROUP BY preference_type"
            ).fetchall()
        return {r["preference_type"]: r["cnt"] for r in rows}

    # ── daily_analysis ───────────────────────────────────

    def upsert_daily(self, law_info: dict) -> None:
        """LAW-RADAR 일일 분석 결과를 Upsert합니다."""
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO daily_analysis
                    (mst_id, law_name, enforce_date, ministry, related_certs,
                     relevance, preference_type, track1_type, track1_risk, track2_code,
                     is_serious_accident, summary,
                     detail_analysis, evidence_article, ai_confidence,
                     needs_review, review_reason, direct_links, worknet_demand,
                     hybrid_status, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        datetime('now','localtime'))
                ON CONFLICT(mst_id) DO UPDATE SET
                    law_name         = excluded.law_name,
                    enforce_date     = excluded.enforce_date,
                    ministry         = excluded.ministry,
                    related_certs    = excluded.related_certs,
                    relevance        = excluded.relevance,
                    preference_type  = excluded.preference_type,
                    track1_type      = excluded.track1_type,
                    track1_risk      = excluded.track1_risk,
                    track2_code      = excluded.track2_code,
                    is_serious_accident = excluded.is_serious_accident,
                    summary          = excluded.summary,
                    detail_analysis  = excluded.detail_analysis,
                    evidence_article = excluded.evidence_article,
                    ai_confidence    = excluded.ai_confidence,
                    needs_review     = excluded.needs_review,
                    review_reason    = excluded.review_reason,
                    direct_links     = excluded.direct_links,
                    worknet_demand   = excluded.worknet_demand,
                    hybrid_status    = excluded.hybrid_status,
                    updated_at       = datetime('now','localtime')
                """,
                (
                    law_info.get("MST_ID"),
                    law_info.get("법령명"),
                    law_info.get("시행일자"),
                    law_info.get("소관부처"),
                    law_info.get("관련 종목"),
                    law_info.get("연관성_판별"),
                    law_info.get("우대분류"),
                    law_info.get("Track1_취급유형"),
                    law_info.get("Track1_위험도"),
                    law_info.get("Track2_효용코드"),
                    law_info.get("중처법대상"),
                    law_info.get("조문 요약"),
                    law_info.get("상세 분석 결과"),
                    law_info.get("근거조문"),
                    law_info.get("AI신뢰도"),
                    law_info.get("검토필요"),
                    law_info.get("검토사유"),
                    law_info.get("조문별 다이렉트 링크"),
                    law_info.get("워크넷 실시간 구인건수"),
                    law_info.get("hybrid_status"),
                ),
            )

    def search_daily(
        self,
        cert_name: str | None = None,
        law_name: str | None = None,
        preference_type: str | None = None,
    ) -> list[dict]:
        """일일 분석 결과를 조건부 검색합니다. (MCP query_preference_db 도구에서 사용)"""
        conditions, params = [], []
        if cert_name:
            conditions.append("related_certs LIKE ?")
            params.append(f"%{cert_name}%")
        if law_name:
            conditions.append("law_name LIKE ?")
            params.append(f"%{law_name}%")
        if preference_type:
            conditions.append("track2_code LIKE ?")
            params.append(f"%{preference_type}%")

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM daily_analysis {where} ORDER BY enforce_date DESC LIMIT 100",
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    # ── held_laws (버리지 않는 체: 보류 로그) ──────────────

    def add_held_law(self, law_name: str, enforce_date: str = "",
                     ministry: str = "", hold_reason: str = "",
                     law_link: str = "") -> None:
        """
        AI 분석을 건너뛴 법령을 '버리지 않고' 사유와 함께 기록합니다.
        나중에 담당자가 보류 목록을 검토하거나, 필요 시 다시 분석할 수 있습니다.
        """
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO held_laws (law_name, enforce_date, ministry, hold_reason, law_link)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(law_name, enforce_date) DO UPDATE SET
                    hold_reason = excluded.hold_reason,
                    ministry    = excluded.ministry,
                    law_link    = excluded.law_link
                """,
                (law_name, enforce_date, ministry, hold_reason, law_link),
            )

    def get_held_laws(self, only_unreviewed: bool = True, limit: int = 200) -> list[dict]:
        """보류된 법령 목록을 반환합니다. (담당자 사후 점검용)"""
        where = "WHERE reviewed = 0" if only_unreviewed else ""
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM held_laws {where} ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_held_reviewed(self, held_id: int) -> None:
        """보류 법령을 '검토 완료'로 표시합니다."""
        with self._conn() as conn:
            conn.execute("UPDATE held_laws SET reviewed = 1 WHERE id = ?", (held_id,))

    def count_held_laws(self, only_unreviewed: bool = True) -> int:
        """보류 법령 개수를 반환합니다."""
        where = "WHERE reviewed = 0" if only_unreviewed else ""
        with self._conn() as conn:
            row = conn.execute(f"SELECT COUNT(*) AS c FROM held_laws {where}").fetchone()
        return row["c"]

    # ── reference_articles (383건 AI 재분류 기준조항) ──────

    def upsert_reference_article(self, row: dict) -> None:
        """383건 재분류 기준조항 한 건을 Upsert합니다."""
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO reference_articles
                    (seq, law_name, law_name_raw, article, article_no,
                     track1_code, track1_type, track1_risk_code, track1_risk,
                     track1_unified, track1_basis, track2_code, track2_type,
                     track2_basis, source_year)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(law_name, article_no, seq) DO UPDATE SET
                    track1_code=excluded.track1_code, track1_type=excluded.track1_type,
                    track1_risk_code=excluded.track1_risk_code, track1_risk=excluded.track1_risk,
                    track1_unified=excluded.track1_unified, track1_basis=excluded.track1_basis,
                    track2_code=excluded.track2_code, track2_type=excluded.track2_type,
                    track2_basis=excluded.track2_basis
                """,
                (row.get("seq"), row.get("law_name"), row.get("law_name_raw"),
                 row.get("article"), row.get("article_no"),
                 row.get("track1_code"), row.get("track1_type"),
                 row.get("track1_risk_code"), row.get("track1_risk"),
                 row.get("track1_unified"), row.get("track1_basis"),
                 row.get("track2_code"), row.get("track2_type"),
                 row.get("track2_basis"), row.get("source_year", "2022")),
            )

    def lookup_reference(self, law_name: str, article_no: str = None) -> list[dict]:
        """
        법령명(+조문번호)으로 기준조항 재분류를 조회합니다.
        하이브리드 검증에서 '이미 확정된 투트랙 코드'를 가져오는 데 사용.
        """
        import re
        norm = re.sub(r"[\s·\(\)\[\]ㆍ・,]", "", str(law_name)).strip().lower()
        with self._conn() as conn:
            if article_no:
                rows = conn.execute(
                    "SELECT * FROM reference_articles WHERE law_name=? AND article_no=?",
                    (norm, article_no),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM reference_articles WHERE law_name=?", (norm,),
                ).fetchall()
        return [dict(r) for r in rows]

    def count_reference_articles(self) -> int:
        with self._conn() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM reference_articles").fetchone()
        return row["c"]

    # ── 우대사항 대장 (법령+조문 단위 현황) ────────────────

    def build_ledger_rows(self, resolve_fn=None) -> list[dict]:
        """
        법령+조문 단위로 우대사항 대장 행을 생성합니다.
        각 행에 해당 자격종목 목록을 현행명으로 붙입니다.

        resolve_fn: 종목명을 현행명으로 변환하는 함수(없으면 원본 사용).
                    보통 certs.resolve_current_name 을 넘깁니다.
        """
        with self._conn() as conn:
            # 직능연 정리본을 법령 단위로 집계 (조문내역 + 종목목록)
            rows = conn.execute("""
                SELECT law_name, law_name_raw, article_text, preference_type,
                       GROUP_CONCAT(DISTINCT cert_name) AS certs,
                       MAX(is_hazard_law) AS is_hazard
                FROM preference_laws
                GROUP BY law_name, article_text, preference_type
                ORDER BY law_name_raw
            """).fetchall()

        ledger = []
        for r in rows:
            certs_raw = (r["certs"] or "").split(",")
            # 종목명을 현행명으로 변환 (중복 제거)
            if resolve_fn:
                certs_cur = []
                seen = set()
                for c in certs_raw:
                    cur = resolve_fn(c.strip())
                    if cur and cur not in seen:
                        seen.add(cur); certs_cur.append(cur)
            else:
                certs_cur = [c.strip() for c in certs_raw if c.strip()]

            # 기준조항(383건)에서 투트랙 코드 조회
            ref = self.lookup_reference(r["law_name_raw"] or "")
            t1_type = ref[0]["track1_code"] if ref else ""        # 취급유형 A~E
            t1_risk = ref[0]["track1_risk_code"] if ref else ""   # 위험도 N/L/M/H/C
            t2 = ref[0]["track2_code"] if ref else ""             # 효용코드 Ⅰ~Ⅳ

            ledger.append({
                "법령명": r["law_name_raw"],
                "조문": r["article_text"] or "",
                "우대분류": r["preference_type"] or "",
                "해당 자격종목": ", ".join(certs_cur),
                "Track1_취급유형": t1_type,
                "Track1_위험도": t1_risk,
                "Track2_효용코드": t2,
                "중처법대상": "대상" if r["is_hazard"] else "",
                "상태": "기존",
                "최근변경일": "",
                "비고": "",
            })
        return ledger

    def rename_cert_everywhere(self, old_name: str, new_name: str) -> int:
        """
        명칭 변경: SQLite의 모든 종목명(old → new)을 일괄 갱신합니다.
        preference_laws의 cert_name을 현행명으로 UPDATE.
        반환: 변경된 행 수.
        """
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE preference_laws SET cert_name = ? WHERE cert_name = ?",
                (new_name, old_name),
            )
            return cur.rowcount

    # ── sync_state (백필용: 마지막 성공일 추적) ────────────

    def get_state(self, key: str, default: str = "") -> str:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_state(self, key: str, value: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO sync_state (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
