"""
hrdk_law_core.hybrid
--------------------
직능연 정리본 기반 하이브리드 검증 엔진.

기존에 합의된 설계를 처음으로 코드로 구현합니다:
  - 기존 정리본에 있는 법령 → 직능연 기준 우선 유지
  - 완전 신규 법령         → AI 판단 그대로 적용
  - 둘의 분류가 다를 경우  → AI 판단 채택 + '💡 [AI 스마트 보정]' 표식

투트랙(Track1/Track2)과 직능연 5종 우대분류의 의미론적 매핑표:
┌──────────────────┬────────────────────────────────────────┐
│ 직능연 우대분류  │ LAW-RADAR Track2 효용코드 (대략 대응)  │
├──────────────────┼────────────────────────────────────────┤
│ 의무고용         │ Ⅰ-a (의무선임형), Ⅱ-a (자격취득의무형) │
│ 직무권한부여     │ Ⅰ-b (독점직무형), Ⅱ-b (진입장벽형)    │
│ 인사우대         │ Ⅲ-a (가산점형), Ⅲ-b (우선채용형)      │
│ 시험면제         │ Ⅲ-c (검정연계형)                       │
│ 기타             │ Ⅳ (효용미미형) 또는 미분류              │
└──────────────────┴────────────────────────────────────────┘

사용법:
    from hrdk_law_core.hybrid import verify_with_krivet
    result = verify_with_krivet(law_info, kb)
"""

from .db import KnowledgeBase
from .certs import resolve_current_name

# 직능연 우대분류 → Track2 효용코드 군(群) 매핑
# 의미적으로 동등하다고 볼 수 있는 코드 집합
_PREF_TO_TRACK2_GROUP: dict[str, set[str]] = {
    "의무고용":     {"Ⅰ-a", "Ⅱ-a", "Ⅰ", "Ⅱ"},
    "직무권한부여": {"Ⅰ-b", "Ⅱ-b", "Ⅰ", "Ⅱ"},
    "인사우대":     {"Ⅲ-a", "Ⅲ-b", "Ⅲ"},
    "시험면제":     {"Ⅲ-c", "Ⅲ"},
    "기타":         {"Ⅳ"},
}

# 하이브리드 상태 레이블
STATUS_REFERENCE = "기준조항_확정"     # 383건 AI 재분류 기준조항과 일치 (최고 신뢰)
STATUS_REF_PATCH = "기준조항_보정"     # 기준조항과 AI 투트랙이 달라 검토 권고
STATUS_KRIVET   = "직능연_검증"       # 직능연 기준과 일치
STATUS_AI_PATCH = "AI_스마트_보정"    # 직능연 기준과 달라 AI 판단으로 보정
STATUS_AI_NEW   = "AI_신규판단"       # 직능연 정리본에 없는 완전 신규 법령


def _is_semantically_same(pref_type: str, track2_code: str) -> bool:
    """
    직능연 우대분류와 Track2 효용코드가 의미론적으로 동등한지 판단합니다.
    Track2 코드에 매핑 그룹의 접두사가 포함되면 일치로 봅니다.
    """
    group = _PREF_TO_TRACK2_GROUP.get(pref_type, set())
    for code in group:
        if track2_code.startswith(code.split("-")[0]):
            return True
    return False


def verify_with_krivet(law_info: dict, kb: KnowledgeBase) -> dict:
    """
    AI 분석 결과(law_info)를 직능연 정리본(kb)과 대조하여 하이브리드 검증을 수행합니다.

    Parameters
    ----------
    law_info : LAW-RADAR run_ai_analysis()가 반환한 법령 분석 딕셔너리
    kb       : KnowledgeBase 인스턴스

    Returns
    -------
    검증 결과가 반영된 law_info 딕셔너리 (in-place 수정 후 반환)
    추가되는 키:
        hybrid_status : STATUS_KRIVET / STATUS_AI_PATCH / STATUS_AI_NEW
        상세 분석결과 : AI_스마트_보정 건은 맨 앞에 경고 표식 삽입
    """
    law_name    = law_info.get("법령명", "")
    certs_str   = law_info.get("관련 종목", "")
    track2_code = law_info.get("Track2_효용코드", "")

    # ═══════════════════════════════════════════════════════
    # 1단계 (최우선): 383건 AI 재분류 기준조항 대조
    # 근거 조문에서 조문번호를 뽑아 기준조항 테이블과 맞춰봅니다.
    # 일치하면 이미 정밀 검토된 투트랙 코드이므로 최고 신뢰로 처리.
    # ═══════════════════════════════════════════════════════
    import re as _re
    evidence = law_info.get("근거 조문", "") or ""
    article_nos = _re.findall(r"제\d+조(?:의\d+)?", evidence)
    ref_rows = []
    for ano in (article_nos or [None]):
        ref_rows = kb.lookup_reference(law_name, ano)
        if ref_rows:
            break
    if ref_rows:
        ref = ref_rows[0]
        ref_t1 = ref.get("track1_unified", "")   # 예: C-M
        ref_t2 = ref.get("track2_code", "")       # 예: Ⅱ-4
        ai_t1 = f"{law_info.get('Track1_취급유형','')}-{law_info.get('Track1_위험도','')}"
        # 기준조항의 코드를 신뢰 정보로 부착
        law_info["기준조항_Track1"] = ref_t1
        law_info["기준조항_Track2"] = ref_t2
        law_info["기준조항_근거"] = ref.get("track1_basis", "")
        # AI 분류가 기준조항과 다르면 보정 표식
        t2_diff = ref_t2 and track2_code and not track2_code.startswith(ref_t2.split("-")[0])
        if t2_diff:
            original = law_info.get("상세 분석결과", "")
            law_info["상세 분석결과"] = (
                f"📌 [기준조항 대조] 2022 재분류 기준({ref_t1}, {ref_t2})과 "
                f"AI 분류(Track2:{track2_code})에 차이가 있어 검토를 권고합니다.\n\n" + original
            )
            law_info["검토 필요"] = "O"
            law_info["hybrid_status"] = STATUS_REF_PATCH
        else:
            law_info["hybrid_status"] = STATUS_REFERENCE
        return law_info

    if not certs_str or certs_str.strip() in ["", "없음", "N/A"]:
        law_info["hybrid_status"] = STATUS_AI_NEW
        return law_info

    cert_list = [c.strip() for c in certs_str.split(",") if c.strip()]

    krivet_hits   = []  # 직능연 DB에서 찾은 (종목, 직능연_분류) 쌍
    mismatch_hits = []  # 직능연 분류와 AI 분류가 다른 쌍

    for cert in cert_list:
        # 🌟 [별칭] 구명칭/표기변형을 2026 현행명으로 변환 후 조회 (매칭률 ↑)
        cert_current = resolve_current_name(cert)
        pref_type = kb.lookup_preference_type(cert_current, law_name)
        if pref_type is None and cert_current != cert:
            # 현행명으로 못 찾으면 원본명으로도 시도 (안전망)
            pref_type = kb.lookup_preference_type(cert, law_name)
        if pref_type is None:
            continue  # 직능연 DB에 없는 종목

        krivet_hits.append((cert_current, pref_type))
        if not _is_semantically_same(pref_type, track2_code):
            mismatch_hits.append((cert_current, pref_type, track2_code))

    # ── 판정 ────────────────────────────────────────────
    if not krivet_hits:
        # 직능연 정리본에 이 법령이 전혀 없음 → 완전 신규
        law_info["hybrid_status"] = STATUS_AI_NEW
        return law_info

    if mismatch_hits:
        # 직능연 기준과 AI 판단이 다른 종목이 있음 → AI 보정 표식 삽입
        mismatch_summary = ", ".join(
            f"{cert}(직능연:{pt}↔AI:{at})" for cert, pt, at in mismatch_hits
        )
        original_detail = law_info.get("상세 분석결과", "")
        law_info["상세 분석결과"] = (
            f"💡 [AI 스마트 보정] 직능연 기준과 분류 차이 발생 ({mismatch_summary}). "
            f"AI 투트랙 분석을 우선 적용하되 담당자 검토를 권고합니다.\n\n"
            + original_detail
        )
        law_info["검토 필요"] = "O"
        law_info["검토 사유"] = (
            law_info.get("검토 사유", "")
            + f" | 직능연 기준 불일치 ({mismatch_summary})"
        ).strip(" |")
        law_info["hybrid_status"] = STATUS_AI_PATCH
    else:
        # 모든 종목의 분류가 직능연과 일치
        law_info["hybrid_status"] = STATUS_KRIVET

    return law_info
