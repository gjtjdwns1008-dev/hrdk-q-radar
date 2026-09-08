"""런 요약 서식 — 깃허브 Job Summary와 로컬 콘솔이 '같은 문서'를 쓴다. [공용]

여기서 만든 마크다운이 그대로 워크플로 런 Summary 화면이 되고,
그 화면 캡처가 Q-Page 측이 최종 요구한 인수 증빙이 된다.
"""

from __future__ import annotations

from . import SPEC_VERSION

LIVE_URL = "https://gjtjdwns1008-dev.github.io/hrdk-q-radar/pref_export.json"

CHECK_ITEMS = (
    "JSON 파싱 · version 1.x · asof 형식(yyyy-mm-dd)",
    "전 종목 n = duty+auth+hr+exempt, n > 0",
    "top ≤ 3건 · top.cat ∈ 표시값 4종(의무고용/직무권한/인사우대/시험면제)",
    "대장값 원형('직무권한부여') 미검출",
    "asof = generated 날짜부 · 타임존 +09:00",
)


def build_summary_md(status: str, snap, problems: list[str],
                     out_path: str, out_bytes: int | None,
                     kept_last_good: bool,
                     base_year: int | None = None) -> str:
    L: list[str] = []
    L.append(f"## Q-Page 연계 — pref_export.json (규격 {SPEC_VERSION})")
    L.append("")
    L.append(f"**상태: {status}**")
    if snap is not None:
        year_part = f" · 기준연도(명찰 세대) `{base_year}`" if base_year else ""
        L.append(f"asof `{snap.asof}` · generated `{snap.generated}`{year_part}")
        r = snap.report
        L += ["", "| 항목 | 값 |", "|---|---|"]
        L.append(f"| 대장 확정 레코드(원자화) | {r.raw_rows} (완전중복 제거 {r.dup_dropped}) |")
        L.append(f"| 내보낸 종목 수 | {r.exported_quals} |")
        c = r.cat_totals
        L.append(f"| 성격 합계 duty/auth/hr/exempt | {c['duty']} / {c['auth']} / {c['hr']} / {c['exempt']} |")
        L.append(f"| '기타' 제외 | {r.etc_excluded} |")
        L.append(f"| 0건 종목 생략 | {r.zero_dropped} |")
        L.append(f"| 과정평가형 전용 제외 | {len(r.course_only_quals)} |")
        L.append(f"| 종목코드 미매칭 | {len(r.unmatched_quals)} |")
        if r.unmatched_quals:
            L.append("")
            L.append("미매칭 종목: " + ", ".join(r.unmatched_quals))
        if r.course_only_quals:
            L.append("")
            L.append("과정평가형 전용(제외): " + ", ".join(r.course_only_quals))
    L.append("")
    L.append("### 게시 전 자체검산 (Q-Page §3 검산 미러)")
    if not problems:
        for item in CHECK_ITEMS:
            L.append(f"- ✅ {item}")
    else:
        for p in problems[:20]:
            L.append(f"- ❌ {p}")
        if len(problems) > 20:
            L.append(f"- … 외 {len(problems) - 20}건")
    if kept_last_good:
        L.append("")
        L.append("🟡 신규 스냅샷 실패 → 직전 게시본을 무수정 재게시(asof 미갱신 = 정직 표기). "
                 "게시가 장기 정지되면 Q-Page 35일 신선도 경고가 설계대로 작동한다.")
    L.append("")
    if "미게시" in status:
        L.append("산출물: (미게시 — 파일을 만들지 않음)")
    else:
        size = f" ({out_bytes:,} bytes)" if out_bytes is not None else ""
        L.append(f"산출물: `{out_path}`{size}")
    L.append(f"고정 URL: {LIVE_URL}")
    return "\n".join(L) + "\n"
