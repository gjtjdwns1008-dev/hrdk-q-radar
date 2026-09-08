"""집계 코어 — 채널 A(CSV)와 채널 B(JSON)가 '같은 숫자'를 쓰게 하는 단일 지점. [공용]

규격 대응:
  §2.1  확정 건만 · 0건 종목 생략 · 검정형만
  §2.2  n = duty+auth+hr+exempt ('기타' 제외) · asof = 스냅샷 생성일
        (데이터 무변동이어도 실행마다 당일로 갱신 — v1.3 개정 2호)
  §2.3  집계 키는 JSON 키, 대표법령 성격은 표시값
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable

from . import catmap, codemap
from .source_adapter import SourceRecord

KST = timezone(timedelta(hours=9), name="KST")

# ── 대표 법령 선정 — 규격 v1.1 §1.3 확정 규칙 (확인 3 종결, 2026-09-07) ──
#  1) 성격 우선순위: 의무고용 > 직무권한 > 인사우대 > 시험면제
#  2) 법령당 1회 — 한 법령이 복수 성격이어도 대표에는 최상위 성격으로 1회만.
#     같은 법령의 복수 개정 행은 최신 시행일로 병합, 동순위는 시행일 최신순.
#  3) 법령명은 공식 명칭 그대로(시행령·시행규칙 구분 포함).
#  성격 값은 v1.3 §2.3 표시값('직무권한' 등)으로 기록한다.
TOP_LIMIT = 3
TOP_CAT_PRIORITY = ("의무고용", "직무권한", "인사우대", "시험면제")  # 표시값 기준
# ──────────────────────────────────────────────────────────────────────


@dataclass
class QualAgg:
    code: str
    name: str
    counts: dict[str, int]              # json 키(duty/auth/hr/exempt) → 건수
    top: list[tuple[str, str]]          # (법령명, 성격 표시값) 최대 3건

    @property
    def n(self) -> int:
        return sum(self.counts.values())


@dataclass
class ExportReport:
    """Summary(깃허브 Job Summary·로컬 콘솔)용 통계 — 정직 신고의 원천."""
    raw_rows: int = 0                    # 대장 확정 레코드 수(원자화 후)
    dup_dropped: int = 0                 # (종목,법령,성격) 완전중복 제거 수
    etc_excluded: int = 0                # '기타' 성격 제외 수
    unknown_cat: list[str] = field(default_factory=list)       # 사전에 없는 성격값
    unmatched_quals: list[str] = field(default_factory=list)   # 코드 미매칭 종목
    course_only_quals: list[str] = field(default_factory=list) # 과정평가형 코드뿐인 종목
    zero_dropped: int = 0                # n=0 생략 종목 수
    exported_quals: int = 0
    cat_totals: dict[str, int] = field(
        default_factory=lambda: {k: 0 for k in catmap.JSON_KEYS})


@dataclass
class PrefSnapshot:
    asof: str                            # yyyy-mm-dd = 스냅샷 생성일
    generated: str                       # ISO8601 초 단위, +09:00
    items: dict[str, QualAgg]            # 종목코드(str) → 집계
    report: ExportReport


def now_kst() -> datetime:
    return datetime.now(KST)


def build_snapshot(records: Iterable[SourceRecord],
                   code_table: dict[str, str],
                   canonical: dict[str, str] | None = None,
                   ts: datetime | None = None) -> PrefSnapshot:
    ts = ts or now_kst()
    rep = ExportReport()

    # 1) 원자화 레코드 수집 + 완전중복 제거 (같은 법령 복수 개정 → 최신 시행일 병합)
    triples: dict[tuple[str, str, str], str] = {}
    for r in records:
        rep.raw_rows += 1
        key = (r.qual_name, r.law_name, r.raw_cat)
        if key in triples:
            rep.dup_dropped += 1
            triples[key] = max(triples[key], r.enforce_date or "")
            continue
        triples[key] = r.enforce_date or ""

    # 2) 종목별 집계 ('기타'·미등록 성격 제외)
    by_qual: dict[str, dict] = {}
    for (qual, law, raw_cat), date in sorted(triples.items()):
        jkey = catmap.to_json_key(raw_cat)
        if jkey is None:
            if raw_cat in catmap.EXCLUDED_CATS:
                rep.etc_excluded += 1
            elif raw_cat not in rep.unknown_cat:
                rep.unknown_cat.append(raw_cat)   # 발견 즉시 게시 차단 대상(진입점에서 처리)
            continue
        slot = by_qual.setdefault(
            qual, {"counts": {k: 0 for k in catmap.JSON_KEYS}, "laws": []})
        slot["counts"][jkey] += 1
        slot["laws"].append((law, catmap.to_display(raw_cat), date))

    # 3) 코드 매칭 → 검정형 필터 → 0건 생략 → items
    items: dict[str, QualAgg] = {}
    for qual, slot in by_qual.items():
        n = sum(slot["counts"].values())
        if n == 0:
            rep.zero_dropped += 1
            continue
        code = codemap.resolve(code_table, qual)
        if code is None:
            rep.unmatched_quals.append(qual)
            continue
        if not codemap.is_certification_code(code):
            rep.course_only_quals.append(qual)     # §2.1: 검정형만 게시
            continue
        std_name = (canonical or {}).get(code, qual)   # v1.1 §1.2: 현행 표준 종목명
        items[code] = QualAgg(code=code, name=std_name,
                              counts=slot["counts"], top=_pick_top(slot["laws"]))
        for k, v in slot["counts"].items():
            rep.cat_totals[k] += v

    rep.exported_quals = len(items)
    return PrefSnapshot(
        asof=ts.date().isoformat(),                 # §2.2: asof = 스냅샷 생성일
        generated=ts.isoformat(timespec="seconds"),
        items=dict(sorted(items.items())),          # 코드순 정렬 — diff 안정화
        report=rep,
    )


def _pick_top(laws: list[tuple[str, str, str]]) -> list[tuple[str, str]]:
    """대표 법령 최대 3건 — v1.1 §1.3 확정 규칙 구현.

    입력: (법령명, 성격 표시값, 시행일 yyyymmdd) 목록.
    법령당 1회로 병합(최상위 성격 채택, 시행일은 그 법령의 최신값),
    (성격 우선순위, 시행일 최신, 법령명) 순으로 정렬해 상위 3건.
    """
    prio = {c: i for i, c in enumerate(TOP_CAT_PRIORITY)}
    per_law: dict[str, tuple[int, str]] = {}      # 법령 → (최상위 성격 순위, 최신 시행일)
    for law, disp, date in laws:
        p = prio.get(disp, len(TOP_CAT_PRIORITY))
        cur = per_law.get(law)
        if cur is None:
            per_law[law] = (p, date)
        else:
            per_law[law] = (min(cur[0], p), max(cur[1], date))
    ranked = sorted(per_law.items(),
                    key=lambda kv: (kv[1][0], -int(kv[1][1] or 0), kv[0]))
    return [(law, TOP_CAT_PRIORITY[pd[0]]) for law, pd in ranked[:TOP_LIMIT]]
