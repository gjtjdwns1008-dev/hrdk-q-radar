# -*- coding: utf-8 -*-
"""
Q-RADAR 통합 brain — law-monitor(연관도·활용도) + LAW-RADAR(우대·투트랙) 프롬프트 병합
=====================================================================================
호출 1회로 두 관점을 모두 분석해 통합 대장 1행 분량의 dict를 반환합니다.
  반환 계약: (성공여부, 연관도, law_info)   ← RADAR의 기존 계약과 동일 형태
  · 연관도: "연관높음" | "단순관련" | "해당없음"  (해당없음이면 main이 저장 생략)
  · law_info: 통합 대장 컬럼명 키 (report_maker가 그대로 시트에 씀)

설계 원칙:
  1) 두 시스템의 검증된 프롬프트 문구를 최대한 원형 보존 (battle-tested)
  2) 세 텍스트의 역할 분담 + 상호 반복 금지 (이슈브리핑 원칙 계승)
       주요 제·개정내용 = 법령이 무엇을 바꿨나 (객관 팩트)
       활용도_상세     = 자격 수요·노동시장 관점 (광역)
       상세 분석 결과   = 정책 모순(경력이음) + 구직자 기회 (정밀, 우대법령만)
  3) 우대여부=X면 우대 블록을 기본값·빈칸으로 → 출력 토큰 절약 + 후처리로 정합 강제
"""
import json
import re
from hrdk_law_core.llm_client import get_llm_client

# ★2026-10-14 프롬프트 버전 — 분석 프롬프트(run_ai_analysis)나 후처리 방어망의 판정 규칙을 바꿀 때마다 올린다.
#   재분석 도구(migrate_tool/reanalyze_ledger/run.py)가 결과 캐시에 이 값을 적어 두고, 다르면 자동으로 다시 분석한다.
#   V27: P1 근거조문 '제N조(제목)' 형식 고정 · P2 종목 전수 자기점검 · P4 타 법령 자격·인력 인용 규칙 · R6 등급 통칭 안전장치
#   V27.1: P3(등급 전부 나열 강조) 철회 — 회귀검증에서 과잉 확장·흔들림 유발, 모델은 여전히 6/84 → R6 보강안으로 대체 ·
#          R6 분야 한정어 확장('관한'·'관련' 등, 160자 창) · R7 원문 종목명 글자 대조 안전장치(+조리 자격 문언) · 종목 보강안(_종목보강안) 산출
#   V27.2: R6는 나열 종목 10개 이하일 때만(별표 많은 법령 오탐 차단) · R7 글자 경계(앞 비한글·뒤 조사/비한글, '재이용사업'의 '이용사' 오탐 차단) ·
#          R8 정보용: 나열 종목 중 원문에 글자 그대로 없는 것(_원문미출현, 플래그 아님)
#   V27.3: R7·R8 띄어쓰기 무시 대조 — 문구조사에서 '자동차정비 산업기사'처럼 띄어 쓴 종목명 발견(통칭이 아니라 종목명)
#   V27.4: 방침 전환 '신호 → 교정' — 매일 배치는 사람 승인 없이 시트에 기록되므로 기계가 아는 것은 고치고 검토필요는 모델 자신의 불확실 신고로만.
#          R7: 원문에 글자로 있는 종목은 자동 추가(연관도 해당없음일 때만 검토필요 O) · R6: 문구조사로 확인된 두 꼴(전문직 나열 속 등급 / 「국가기술자격법」에 따른 등급+자격, 분야 없음)만
#          그 등급 전체로 자동 확장, 나머지는 손대지 않음(표시도 없음) · 자동 보강 내역은 상세 분석 결과 끝에 '※ 원문 대조로 추가' 메모(검토사유에 쓰면 R2로 O가 되므로)
#   V27.5: 꼴②는 등급 뒤쪽(같은 줄 100자)에도 분야 한정어가 없을 때만 — 자동차관리법 시행규칙 별표19 '산업기사(… 직무분야만 해당한다) 이상의 자격'(괄호 한정) 폭발 사례
PROMPT_VERSION = "V27.5-20261016"


def _normalize_relevance(raw: str) -> str:
    """연관도 값 교정. 정식값: 연관높음/단순관련/해당없음 (monitor의 '일반'도 해당없음으로 흡수)"""
    if not raw:
        return "해당없음"
    s = str(raw).strip().replace(" ", "")
    if s in ("연관높음", "단순관련", "해당없음"):
        return s
    if s == "일반":
        return "해당없음"
    if "연관" in s or "높" in s:
        return "연관높음"
    if "단순" in s or "관련" in s:
        return "단순관련"
    return "해당없음"


def _normalize_usage(raw: str) -> str:
    """활용도_구분 교정. 정식값: 대폭 증가/소폭 증가/현상 유지/소폭 감소/대폭 감소 (빈칸 허용)"""
    if not raw:
        return ""
    s = str(raw).strip().replace(" ", "")
    table = {"대폭증가": "대폭 증가", "소폭증가": "소폭 증가", "현상유지": "현상 유지",
             "소폭감소": "소폭 감소", "대폭감소": "대폭 감소"}
    if s in table:
        return table[s]
    if "대폭" in s and "증" in s: return "대폭 증가"
    if "소폭" in s and "증" in s: return "소폭 증가"
    if "대폭" in s and "감" in s: return "대폭 감소"
    if "소폭" in s and "감" in s: return "소폭 감소"
    if "유지" in s or "현상" in s: return "현상 유지"
    if "증가" in s: return "소폭 증가"
    if "감소" in s: return "소폭 감소"
    return ""


# 🌟 [모델 추상화] 통역 창구 (LLM_PROVIDER로 교체 가능)
_llm = None
def _client():
    global _llm
    if _llm is None:
        _llm = get_llm_client()
    return _llm


# 🌟 링크 조립 공장 (RESTful 포맷 생성기) — RADAR와 동일, 변경 없음
def generate_new_law_link(law_name, enforce_date, prom_num, prom_date, article_name):
    """별표/서식인지 일반 조항인지 구분해서 법제처 RESTful 링크를 완성합니다."""
    star_match = re.search(r'(별표|서식)\s*(\d+)', article_name)
    if star_match:
        target_id = f"{star_match.group(1)}{star_match.group(2)}"
        return f"https://www.law.go.kr/법령별표서식/({law_name},{enforce_date},{target_id})"

    jo_match = re.search(r'(제\d+조(?:의\d+)?)', article_name)
    if jo_match:
        target_id = jo_match.group(1)
        return f"https://www.law.go.kr/법령/{law_name}/({enforce_date},{prom_num},{prom_date})/{target_id}"

    return f"https://www.law.go.kr/법령/{law_name}"




def _sanitize_review(reason, related_certs, source_text, qnet_certs_text):
    """★재발방지(2026-07-06): 검토사유 환각 자동검증.
    사유에 등장한 '별표 번호'나 '자격종목명'이 본 법령 원문·관련종목에 없으면
    오염(타 법령 사례 인용)으로 보고 사실 기반 문구로 교체한다."""
    r = (reason or "").strip()
    if not r:
        return r
    nsp = lambda x: re.sub(r"[\s\u318D\u00B7]+", "", str(x or ""))
    src, rel = nsp(source_text), nsp(related_certs)
    # ① 별표 번호 검증
    for m in set(re.findall(r"별표\s*\d+(?:의\s*\d+)?", r)):
        if nsp(m) not in src:
            print(f"    ⚠️ 검토사유 자동검증: 원문에 없는 '{m}' 인용 감지 → 사유 교체")
            return "자동검증에서 본 법령 원문에 없는 별표·사례 인용이 감지되어 사유를 무효화함. 원문 및 별표 파일 직접 확인 필요."
    # ② 종목명 검증 (사전에 있는 종목이 사유엔 있는데 원문·관련종목엔 없음)
    reason_n = nsp(r)
    for name in re.findall(r"[가-힣()]{4,}", qnet_certs_text or ""):
        n = nsp(name)
        if len(n) >= 4 and n in reason_n and n not in rel and n not in src:
            print(f"    ⚠️ 검토사유 자동검증: 원문 외 종목 '{name}' 언급 감지 → 사유 교체")
            return "자동검증에서 본 법령 원문에 없는 자격종목 언급이 감지되어 사유를 무효화함. 원문 및 별표 파일 직접 확인 필요."
    return r

try:  # ★라벨 주석화(2026-07-07): 신규 행도 "B (영업요건형)" 규약으로 기록
    from hrdk_law_core.certs import (label_track1_type, label_track1_risk,
                                     label_track2_code)
except Exception:  # core 미설치 등 극단 상황 — 원시 코드 유지(무해)
    label_track1_type = label_track1_risk = label_track2_code = (lambda v: v)


# ★2026-09-16 후처리 방어망 — 값끼리 모순이면 사람에게 넘긴다(자동 교정 없음)
_REAL_PREF = ("의무고용", "직무권한부여", "인사우대", "시험면제")
_PREF_TRACK = {   # 우대분류 ↔ (허용 Track1 첫 글자, 허용 Track2 접두)  — 프롬프트 4-1 연동표와 동일
    "의무고용":     (("B",),      ("Ⅱ-",)),
    "직무권한부여": (("A", "C"),  ("Ⅰ-1", "Ⅰ-2", "Ⅱ-2", "Ⅱ-3")),
    "인사우대":     (("D",),      ("Ⅲ-2",)),
    "시험면제":     (("E",),      ("Ⅲ-1",)),
    "기타":         (("Z",),      ("Ⅲ-3",)),
}

def _append_reason(info, note):
    cur = str(info.get("검토사유", "")).strip()
    info["검토사유"] = (cur + " / " + note).strip(" /") if cur else note
    info["검토필요"] = "O"

# ★2026-10-14 R6 등급 통칭 안전장치 — '기술사의 자격이 있는 사람', '기사 이상의 자격'처럼 등급 이름만으로
#   자격을 특정한 문구가 원문에 있는데(바로 앞에 '분야'가 없을 때 = 그 등급 전체를 뜻함) 종목에 그 등급이
#   절반도 안 들어 있으면 검토필요 O. 신뢰도 '높음'인 오답(20건 시범: 기술사 84종 중 6종만 나열)을 기계가 잡는 장치.
_GRADE_RE = re.compile(r"(?<![가-힣])(기술사|기능장|산업기사|기사|기능사)(?:의)?\s*(?:자격|이상)")
_GRADE_FALLBACK = {"기술사": 84, "기능장": 28, "산업기사": 116, "기사": 116, "기능사": 154}   # 2026 사전 기준
_grade_sizes_cache = {}
# ★2026-10-15 V27.1 분야 한정어 — 이 말이 앞 160자(같은 줄) 안에 있으면 '그 분야의 등급'이지 등급 전체가 아니다
#   (자동차관리법 시행규칙 제135조 '자동차정비 또는 자동차검사에 관한 산업기사 이상' 오탐에서 추가)
_FIELD_LIMITERS = ("분야", "관한", "관련", "직종", "부문", "계열", "종류의", "해당하는")
_cert_names_cache = {}
_CUISINE_RE = re.compile(r"(한식|양식|중식|일식|복어)\s*조리\s*(?:자격|기능사|산업기사)")


def _field_limited(text: str, start: int) -> bool:
    scope = text[max(0, start - 160):start]
    nl = scope.rfind("\n")
    if nl >= 0:
        scope = scope[nl + 1:]
    return any(w in scope for w in _FIELD_LIMITERS)


def _cert_names(year=None) -> list:
    """사전 종목명 전체(글자 대조용). 못 읽으면 빈 목록."""
    key = year or 0
    if key not in _cert_names_cache:
        names = []
        try:
            from hrdk_law_core.certs import get_qnet_certs_list
            for n in get_qnet_certs_list(year):
                names.append(n if isinstance(n, str) else (n.get("종목명") or str(n)))
        except Exception:
            pass
        _cert_names_cache[key] = names
    return _cert_names_cache[key]


_PARTICLES = set("의를을은는이가와과로에도만등")


def _word_bounded(text: str, start: int, end: int) -> bool:
    """종목명이 낱말로 서 있는가: 앞 글자는 한글이 아니고(또는 문장 처음), 뒤 글자는 한글이 아니거나 조사."""
    prev = text[start - 1] if start > 0 else ""
    nxt = text[end] if end < len(text) else ""
    if prev and "가" <= prev <= "힣":
        return False
    if nxt and "가" <= nxt <= "힣" and nxt not in _PARTICLES:
        return False
    return True


def _unseen_certs(info, law_text: str) -> list:
    """R8 ★V27.2 정보용: 나열 종목 중 원문에 글자 그대로(괄호 꼬리 제외) 없는 것. 등급·분야 문언에서 끌어낸 종목이면 정상이므로
    플래그는 걸지 않고 재분석 도구 대조 엑셀에만 보여 사람이 과잉 추론 여부를 판단한다."""
    if not law_text:
        return []
    certs_raw = str(info.get("관련 종목", "")).strip()
    if "[전종목]" in certs_raw.replace(" ", ""):
        return []
    body = re.sub(r"[ \t]+", "", law_text)                       # ★V27.3 띄어쓰기 무시
    out = []
    for c in [x.strip() for x in certs_raw.split(",") if x.strip()]:
        stem = re.sub(r"\(.*?\)$", "", c).replace(" ", "")
        if stem and stem not in body:
            out.append(c)
    return out


def _literal_cert_guard(info, law_text: str) -> tuple:
    """R7 ★2026-10-15. 사전 종목명(괄호 꼬리 제외)이 원문에 글자 그대로 있는데 종목에 없으면 그 이름들을 돌려준다.
    '복어 조리 자격'처럼 조리 자격 문언은 조리기능사·산업기사로 환산. 반환: (누락 종목 목록, 사유 문구)"""
    if not law_text:
        return [], ""
    certs_raw = str(info.get("관련 종목", "")).strip()
    if "[전종목]" in certs_raw.replace(" ", ""):
        return [], ""
    names = _cert_names()
    if not names:
        return [], ""
    listed = {c.strip() for c in certs_raw.split(",") if c.strip()}
    stems = {}
    for n in names:
        stems.setdefault(re.sub(r"\(.*?\)$", "", n), []).append(n)
    pat = re.compile("|".join(re.escape(s) for s in sorted(stems, key=len, reverse=True)))
    found = {}
    for body in (law_text, re.sub(r"[ \t]+", "", law_text)):     # ★V27.3 원문 + 띄어쓰기 제거본('자동차정비 산업기사' → '자동차정비산업기사')
        for m in pat.finditer(body):
            if not _word_bounded(body, m.start(), m.end()):   # ★V27.2 '재이용사업' 속 '이용사' 같은 조각 매칭 제외
                continue
            found.setdefault(m.group(0), m.start())
    for m in _CUISINE_RE.finditer(law_text):
        for full in (f"{m.group(1)}조리기능사", f"{m.group(1)}조리산업기사"):
            if full in stems:
                found.setdefault(full, m.start())
    missing = []
    for stem in found:
        fulls = stems.get(stem, [])
        if fulls and not any(f in listed for f in fulls):
            missing.extend(fulls)
    missing = sorted(dict.fromkeys(missing))
    if not missing:
        return [], ""
    shown = ", ".join(missing[:8]) + (f" 외 {len(missing) - 8}종" if len(missing) > 8 else "")
    return missing, f"원문에 종목명이 글자 그대로 있는데 종목에 없음({len(missing)}종): {shown}(R7)"


def _grade_of(name: str) -> str:
    """종목명 → 등급('기사'는 '산업기사'와 구분). 등급 접미사가 없으면 ''."""
    base = re.sub(r"\(.*?\)$", "", str(name).strip())
    for g in ("기술사", "기능장", "산업기사", "기능사"):
        if base.endswith(g):
            return g
    return "기사" if base.endswith("기사") else ""


def _grade_sizes(year=None) -> dict:
    """등급별 종목 수 — 사전(certs)에서 세고, 못 읽으면 2026년 고정값."""
    key = year or 0
    if key in _grade_sizes_cache:
        return _grade_sizes_cache[key]
    sizes = dict(_GRADE_FALLBACK)
    try:
        from hrdk_law_core.certs import get_qnet_certs_list
        cnt = {}
        for n in get_qnet_certs_list(year):
            nm = n if isinstance(n, str) else (n.get("종목명") or str(n))
            g = _grade_of(nm)
            if g:
                cnt[g] = cnt.get(g, 0) + 1
        if cnt:
            sizes.update(cnt)
    except Exception:
        pass
    _grade_sizes_cache[key] = sizes
    return sizes


_GRADE_ORDER = ["기능사", "산업기사", "기사", "기능장", "기술사"]
_PROFESSIONS = ("변호사", "변리사", "공인회계사", "건축사", "감정평가사", "세무사", "법무사", "공인노무사", "노무사", "기술지도사", "경영지도사", "관세사", "행정사")
# 꼴② 「국가기술자격법」(제N조)에 따른/따라 + 등급(들) + (이상의) 자격
_LAWREF_GRADE_RE = re.compile(
    r"국가기술자격법」?(?:\s*제\d+조(?:의\d+)?)?에\s*(?:따른|따라)\s*"
    r"((?:기술사|기능장|산업기사|기사|기능사)(?:\s*(?:또는|및|ㆍ|·|,)\s*(?:기술사|기능장|산업기사|기사|기능사))*)"
    r"\s*(이상)?(?:의)?\s*자격")


def _grades_from(text_grades: str, above: bool) -> list:
    """'기술사 또는 기능장' → ['기술사','기능장']; '기사' + 이상 → ['기사','기능장','기술사']"""
    found = re.findall(r"기술사|기능장|산업기사|기사|기능사", text_grades)
    out = []
    for g in found:
        if above:
            out += _GRADE_ORDER[_GRADE_ORDER.index(g):]
        else:
            out.append(g)
    return list(dict.fromkeys(out))


def _grade_generic_expand(info, law_text: str) -> tuple:
    """R6 ★V27.4 교정형. 문구조사(2026-10-15, 118개 법령)에서 '분야 없는 진짜 등급 통칭'으로 확인된 두 꼴만 자동 확장한다.
      꼴① 전문직 나열 속 등급: '변호사, 변리사, 공인회계사, 건축사 또는 기술사의 자격이 있는 사람' (상생협력법·기술이전법 시행령)
      꼴② 법 이름 바로 뒤 등급: '「국가기술자격법」에 따른 기사 이상의 자격', '제10조에 따라 기술사 또는 기능장의 자격' (원자력안전법·이공계지원법 시행령)
    두 꼴 모두 같은 문장(같은 줄 앞 160자)에 분야 한정어가 없을 때만. 그 밖의 등급 문구(별표 표·중첩 목록)는 손대지 않는다.
    반환: (추가할 종목 목록, 메모)"""
    if not law_text:
        return [], ""
    certs_raw = str(info.get("관련 종목", "")).strip()
    if "[전종목]" in certs_raw.replace(" ", ""):
        return [], ""
    grades, how = [], []
    for m in _GRADE_RE.finditer(law_text):                                   # 꼴①
        if _field_limited(law_text, m.start()):
            continue
        scope = law_text[max(0, m.start() - 120):m.start()]
        nl = scope.rfind("\n")
        scope = scope[nl + 1:] if nl >= 0 else scope
        if any(p in scope for p in _PROFESSIONS) and "자격" in m.group(0):      # '…의 자격'·'… 자격'만('이상'만 붙은 것은 제외)
            prev_tok = re.search(r"([가-힣]+)\s+$", law_text[max(0, m.start() - 16):m.start()])
            stems_all = {re.sub(r"\(.*?\)$", "", n) for n in _cert_names()}
            if prev_tok and (prev_tok.group(1) + m.group(1)) in stems_all:
                continue
            for g in _grades_from(m.group(1), False):
                if g not in grades:
                    grades.append(g); how.append(f"전문직 나열 속 '{g}'")
    for m in _LAWREF_GRADE_RE.finditer(law_text):                            # 꼴②
        if _field_limited(law_text, m.start(1)):
            continue
        after = law_text[m.end(1):m.end(1) + 100]                            # ★V27.5 등급 뒤 괄호·후속 구절의 분야 한정
        nl = after.find("\n")
        after = after[:nl] if nl >= 0 else after
        if any(w in after for w in _FIELD_LIMITERS):
            continue
        for g in _grades_from(m.group(1), bool(m.group(2))):
            if g not in grades:
                grades.append(g); how.append(f"「국가기술자격법」에 따른 '{m.group(1)}{' 이상' if m.group(2) else ''}'")
    if not grades:
        return [], ""
    listed = {c.strip() for c in certs_raw.split(",") if c.strip()}
    add = [n for n in _cert_names() if _grade_of(n) in grades and n not in listed]
    if not add:
        return [], ""
    return add, f"등급 통칭 확장(R6): {' / '.join(dict.fromkeys(how))} → {', '.join(f'{g} 전체' for g in grades)} {len(add)}종 추가"


_PARTICLES = set("의를을은는이가와과로에도만등")


def _word_bounded(text: str, start: int, end: int) -> bool:
    """종목명이 낱말로 서 있는가: 앞 글자는 한글이 아니고(또는 문장 처음), 뒤 글자는 한글이 아니거나 조사."""
    prev = text[start - 1] if start > 0 else ""
    nxt = text[end] if end < len(text) else ""
    if prev and "가" <= prev <= "힣":
        return False
    if nxt and "가" <= nxt <= "힣" and nxt not in _PARTICLES:
        return False
    return True


def _unseen_certs(info, law_text: str) -> list:
    """R8 ★V27.2 정보용: 나열 종목 중 원문에 글자 그대로(괄호 꼬리 제외) 없는 것. 등급·분야 문언에서 끌어낸 종목이면 정상이므로
    플래그는 걸지 않고 재분석 도구 대조 엑셀에만 보여 사람이 과잉 추론 여부를 판단한다."""
    if not law_text:
        return []
    certs_raw = str(info.get("관련 종목", "")).strip()
    if "[전종목]" in certs_raw.replace(" ", ""):
        return []
    body = re.sub(r"[ \t]+", "", law_text)                       # ★V27.3 띄어쓰기 무시
    out = []
    for c in [x.strip() for x in certs_raw.split(",") if x.strip()]:
        stem = re.sub(r"\(.*?\)$", "", c).replace(" ", "")
        if stem and stem not in body:
            out.append(c)
    return out


def _literal_cert_guard(info, law_text: str) -> tuple:
    """R7 ★2026-10-15. 사전 종목명(괄호 꼬리 제외)이 원문에 글자 그대로 있는데 종목에 없으면 그 이름들을 돌려준다.
    '복어 조리 자격'처럼 조리 자격 문언은 조리기능사·산업기사로 환산. 반환: (누락 종목 목록, 사유 문구)"""
    if not law_text:
        return [], ""
    certs_raw = str(info.get("관련 종목", "")).strip()
    if "[전종목]" in certs_raw.replace(" ", ""):
        return [], ""
    names = _cert_names()
    if not names:
        return [], ""
    listed = {c.strip() for c in certs_raw.split(",") if c.strip()}
    stems = {}
    for n in names:
        stems.setdefault(re.sub(r"\(.*?\)$", "", n), []).append(n)
    pat = re.compile("|".join(re.escape(s) for s in sorted(stems, key=len, reverse=True)))
    found = {}
    for body in (law_text, re.sub(r"[ \t]+", "", law_text)):     # ★V27.3 원문 + 띄어쓰기 제거본('자동차정비 산업기사' → '자동차정비산업기사')
        for m in pat.finditer(body):
            if not _word_bounded(body, m.start(), m.end()):   # ★V27.2 '재이용사업' 속 '이용사' 같은 조각 매칭 제외
                continue
            found.setdefault(m.group(0), m.start())
    for m in _CUISINE_RE.finditer(law_text):
        for full in (f"{m.group(1)}조리기능사", f"{m.group(1)}조리산업기사"):
            if full in stems:
                found.setdefault(full, m.start())
    missing = []
    for stem in found:
        fulls = stems.get(stem, [])
        if fulls and not any(f in listed for f in fulls):
            missing.extend(fulls)
    missing = sorted(dict.fromkeys(missing))
    if not missing:
        return [], ""
    shown = ", ".join(missing[:8]) + (f" 외 {len(missing) - 8}종" if len(missing) > 8 else "")
    return missing, f"원문에 종목명이 글자 그대로 있는데 종목에 없음({len(missing)}종): {shown}(R7)"


def _grade_of(name: str) -> str:
    """종목명 → 등급('기사'는 '산업기사'와 구분). 등급 접미사가 없으면 ''."""
    base = re.sub(r"\(.*?\)$", "", str(name).strip())
    for g in ("기술사", "기능장", "산업기사", "기능사"):
        if base.endswith(g):
            return g
    return "기사" if base.endswith("기사") else ""


def _grade_sizes(year=None) -> dict:
    """등급별 종목 수 — 사전(certs)에서 세고, 못 읽으면 2026년 고정값."""
    key = year or 0
    if key in _grade_sizes_cache:
        return _grade_sizes_cache[key]
    sizes = dict(_GRADE_FALLBACK)
    try:
        from hrdk_law_core.certs import get_qnet_certs_list
        cnt = {}
        for n in get_qnet_certs_list(year):
            nm = n if isinstance(n, str) else (n.get("종목명") or str(n))
            g = _grade_of(nm)
            if g:
                cnt[g] = cnt.get(g, 0) + 1
        if cnt:
            sizes.update(cnt)
    except Exception:
        pass
    _grade_sizes_cache[key] = sizes
    return sizes


def _grade_generic_guard(info, law_text: str):
    """R6. 반환: 검토사유에 붙일 문구 또는 ''."""
    if not law_text:
        return ""
    certs_raw = str(info.get("관련 종목", "")).strip()
    if "[전종목]" in certs_raw.replace(" ", ""):
        return ""
    grades = []
    stems_all = {re.sub(r"\(.*?\)$", "", n) for n in _cert_names()}
    for m in _GRADE_RE.finditer(law_text):
        if _field_limited(law_text, m.start()):   # '해당 분야의 기사 이상', '자동차검사에 관한 산업기사' = 한정 → 부분 집합이 맞음
            continue
        prev_tok = re.search(r"([가-힣]+)\s+$", law_text[max(0, m.start() - 16):m.start()])
        if prev_tok and (prev_tok.group(1) + m.group(1)) in stems_all:   # ★V27.3 '자동차정비 산업기사' = 띄어 쓴 종목명, 통칭 아님
            continue
        if m.group(1) not in grades:
            grades.append(m.group(1))
    if not grades:
        return ""
    listed = [c.strip() for c in certs_raw.split(",") if c.strip()]
    if len(listed) > 10:                      # ★V27.2 이미 종목을 길게 나열한 법령(별표 인력표)은 등급 통칭 오탐이 많아 제외
        return ""
    sizes = _grade_sizes()
    notes, short = [], []
    for g in grades:
        have = sum(1 for c in listed if _grade_of(c) == g)
        total = sizes.get(g, _GRADE_FALLBACK.get(g, 0))
        if total and have < total * 0.5:
            notes.append(f"등급 통칭 문구('{g}의 자격/이상') 감지 — {g} 종목 {have}/{total}종만 나열, 전부 나열 여부 확인(R6)")
            short.append(g)
    info["_R6등급"] = short                        # 보강안 계산용(시트 열 아님)
    return " / ".join(notes)


def _apply_review_guards(info, law_text: str = ""):
    """R1 해당없음+사유빈칸→X / R2 사유있음→O / R3 O+사유빈칸→자동문구 /
    R4 우대O 연동 위반→O / R5 우대O+신뢰도 보통·낮음→O / ★V27.4 R7 원문 글자 종목 자동 추가 · R6 두 꼴 등급 통칭 자동 확장(교정, 신호 아님)"""
    # ★V27.4 교정형 방어망 — 기계가 글자로 확인한 것은 고치고, 사람 신호(검토필요 O)는 모델 자신의 불확실(R1~R5)로만 남긴다
    notes = []
    missing, r7 = _literal_cert_guard(info, law_text)
    if missing:
        if str(info.get("연관도", "")).strip() == "해당없음":
            _append_reason(info, "해당없음 판정인데 원문에 종목명이 글자 그대로 있음: " + ", ".join(missing[:6]) + "(R7) — 판정 재확인")
        else:
            cur = [c.strip() for c in str(info.get("관련 종목", "")).split(",") if c.strip()]
            info["관련 종목"] = ", ".join(dict.fromkeys(cur + missing))
            notes.append(f"원문 대조로 추가(R7): {', '.join(missing)}")
    add6, note6 = _grade_generic_expand(info, law_text)
    if add6:
        cur = [c.strip() for c in str(info.get("관련 종목", "")).split(",") if c.strip()]
        info["관련 종목"] = ", ".join(dict.fromkeys(cur + add6))
        notes.append(note6)
    if notes:
        memo = "※ " + " / ".join(notes)
        detail = str(info.get("상세 분석 결과", "")).rstrip()
        info["상세 분석 결과"] = (detail + "\n" + memo) if detail else memo
        info["_자동보강"] = " / ".join(notes)            # 재분석 도구 대조 엑셀 표시용(시트 열 아님)
    unseen = _unseen_certs(info, law_text)             # ★V27.2 R8 정보용(시트 열 아님)
    if unseen:
        info["_원문미출현"] = ", ".join(unseen)
    rel   = str(info.get("연관도", "")).strip()
    pref  = str(info.get("우대여부", "")).strip()
    cls   = str(info.get("우대분류", "")).strip()
    t1    = str(info.get("Track1_취급유형", "")).strip()[:1]
    t2    = str(info.get("Track2_효용코드", "")).strip()
    conf  = str(info.get("AI신뢰도", "")).strip()
    need  = str(info.get("검토필요", "")).strip().upper()
    why   = str(info.get("검토사유", "")).strip()

    if pref == "O":
        # R4: 우대분류-Track 연동표 위반 (우대분류 재판정은 사람이)
        if cls == "기타" and not t2.startswith("Ⅲ-3"):
            _append_reason(info, f"우대분류-Track 연동 불일치(기타인데 Track2={t2 or '빈칸'})")
        if t2.startswith("Ⅳ"):
            _append_reason(info, "우대분류-Track 연동 불일치(우대 O인데 Track2=Ⅳ-0)")
        if t1 == "Z" and cls != "기타":
            _append_reason(info, f"우대분류-Track 연동 불일치({cls}인데 Track1=Z)")
        allow = _PREF_TRACK.get(cls)
        if allow and cls in _REAL_PREF:
            ok_t1 = (t1 in allow[0]) if t1 else True
            ok_t2 = any(t2.startswith(k) for k in allow[1]) if t2 else True
            if not (ok_t1 and ok_t2):
                _append_reason(info, f"우대분류-Track 연동 불일치({cls} / T1={t1 or '빈칸'} / T2={t2 or '빈칸'})")
        # R5: 근거 문언 없는 우대 판정
        if conf in ("보통", "낮음"):
            _append_reason(info, "종목명·자격요건 문언 미확인 상태의 우대 판정(신뢰도 " + conf + ") — 원문 확인")

    need = str(info.get("검토필요", "")).strip().upper()
    why  = str(info.get("검토사유", "")).strip()
    if rel == "해당없음":
        info["관련 종목"] = ""                       # R1' 빈칸 원칙 — 해당없음 행에 종목·'없음' 문구 잔존 차단
    if rel == "해당없음" and not why:
        info["검토필요"] = "X"                       # R1
    elif why and need != "O":
        info["검토필요"] = "O"                       # R2
    elif need == "O" and not why:
        info["검토사유"] = "AI 사유 미기재(자동 표기) — 원문 확인 필요"   # R3
    if str(info.get("검토필요", "")).strip().upper() != "O":
        info["검토필요"] = "X"
        info["검토사유"] = ""
    return info


def _is_too_long(err) -> bool:
    try:
        from hrdk_law_core.llm_client import is_input_too_long
        return is_input_too_long(err)
    except Exception:
        m = str(err).lower()
        if any(k in m for k in ("429", "quota", "rate limit", "per minute", "resource_exhausted")):
            return False
        return "token" in m and ("too long" in m or "exceeds the maximum" in m or "context length" in m)


def _shrink_article_body(text: str):
    """원문에서 '### 📖 전체 조문' 구간(별표 꼬리 앞까지)만 절반으로 줄인다. 반환 (새 원문, 줄인 글자 수)."""
    head_mark, tail_mark = "### 📖 전체 조문\n", "\n\n### ⭐ 별표"
    bs = text.find(head_mark)
    if bs < 0:
        return text, 0
    bs += len(head_mark)
    be = text.find(tail_mark, bs)
    if be < 0:
        be = len(text)
    body = text[bs:be]
    half = max(len(body) // 2, 20_000)
    if len(body) <= half:
        return text, 0
    cut = len(body) - half
    new_body = body[:half] + f"\n…(모델 입력 한도 초과로 조문 본문 뒷부분 {cut:,}자 추가 생략 — 별표는 아래에 전부 포함)"
    return text[:bs] + new_body + text[be:], cut


def run_ai_analysis(law, qnet_certs_text, attempt_count=5):
    # 🌟 [Q-RADAR 통합 프롬프트 · 2026-09-16 개정] — 연관도 문언 기준·우대 근거 문언·연동표·포괄 토큰 반영
    prompt = f"""
    당신은 한국산업인력공단(HRDK)의 '국가기술자격 법령 레이더(Q-RADAR)'를 담당하는 수석 연구원(AI)입니다.
    당신의 임무는 매일 수집되는 제·개정 법령 조문을 한 번에 입체 분석하여,
    (1) 국가기술자격과의 연관도와 노동시장 활용도, (2) 자격 취득자 우대 여부와 투트랙 분류를
    모두 담은 정형화된 JSON 하나로 출력하는 것입니다.

    ⚠️ 가장 중요한 규칙: 아래 [분석 대상 법령]의 실제 조문 내용만을 근거로 분석하십시오.
    법령 본문에 없는 내용을 추측하거나 지어내지 마십시오. 법령명과 조문 내용이 논리적으로
    맞지 않으면(예: 국방부 소관 계엄법인데 소방 자격이 도출됨) "해당없음"으로 판별하십시오.
    억지로 연관성을 찾지 마십시오. 직접 연관되거나 실무상 명백히 영향을 받는 자격증만 추출하십시오.

    ### 📌 [분석 대상 법령]
    - 법령명: {law.get('법령명', '')}
    - 소관부처: {law.get('소관부처', '')}
    - 시행일자: {law.get('시행일자', '')}
    [법령 원문 (마크다운)]
    {law.get('원본', '(본문 없음)')}

    ---
    위 법령이 다음 국가기술자격 종목 중 어느 것과 연관되는지 파악하십시오.
    [국가기술자격 종목 리스트]
    {qnet_certs_text}

    ---
    ### 🧭 [STEP 1. 연관도 판별 — 가장 먼저]
    - "연관높음": 다음 (a) 또는 (b)에 해당할 때만.
      (a) 국가기술자격 종목명이 조문·별표에 텍스트로 명시됨
      (b) 종목명은 없어도 조문이 '자격'을 요건·대상으로 명시하여(예: '국가기술자격 취득자',
          '기술자격을 가진 사람', '해당 분야 기사 이상의 자격') 자격자의 선임·배치·우대·업무 권한을 규율함
      (c) ★타 법령 자격·인력 인용 규칙: 조문이 「산림기술 진흥법」의 산림기술자, 「건설기술 진흥법」의 건설기술인,
          「전력기술관리법」의 전력기술인, 타 법령의 면허·기술자처럼 '다른 법령이 정한 자격·인력'만 인용하고
          국가기술자격 종목·등급을 직접 쓰지 않으면 (a)(b)에 해당하지 않습니다 → 연관도 "단순관련",
          종목 "", 우대여부 "X", 검토필요 "X". 그 면허·기술자의 취득 요건에 국가기술자격이 포함되더라도
          그것은 본 법령의 문언이 아니므로 연동을 추론하지 마십시오.
    - "단순관련": (a)(b)가 모두 없고, 직무 내용의 유사성이나 산업 연관으로만 자격에 실무상 영향이
      추정되는 경우(위 (c)의 경우 포함). ※ 직무 유사성만으로 "연관높음"을 주지 마십시오.
    - "해당없음": 자격과 무관. 이 경우 나머지 항목은 다음 기본값으로 즉시 결론:
      종목 "", 활용도_구분 "", 활용도_상세 "", 우대여부 "X", 검토필요 "X", 검토사유 "".

    ### 📋 [STEP 2. 공통 추출 (모든 연관 법령)]
    - 개정유형: 제정 / 일부개정 / 전부개정 / 폐지 등
    - 주요_제개정내용: 실제 개정된 조항과 객관적 팩트만 글머리 기호('-')로 나열 (법령이 '무엇을' 바꿨는지)
    - 종목: 다음 세 유형 중 하나로만 작성 (아래 치명적 에러 방지 규칙 4·5 준수)
      ① 열거형: 조문·별표에 실제 열거된 종목(또는 문언이 가리키는 종목)만, [국가기술자격 종목 리스트]의
         표기를 글자 그대로 복사하여 쉼표로 나열. 조문에 실제 열거되어 있으면 수백 개라도 전부.
         리스트에 없는 이름은 절대 만들지 말 것 (유사 명칭이면 리스트의 정확한 표기를 쓸 것).
      ② 포괄형(전 종목 통칭): 조문이 개별 종목·등급·분야를 특정하지 않고 '국가기술자격 취득자(소지자)'
         처럼 전체를 통칭하면, 종목 칸에 정확히 [전종목] 한 단어만 기재 (개별 종목 나열 금지).
     ※ 조문이 '다음 각 호의 기술사' 처럼 목록을 두었는데 그 목록이 원문에서 누락·미확보된 경우는
        통칭이 아니다 → [전종목]을 쓰지 말고 종목 빈칸("") + 검토필요 "O" + 검토사유에 누락 사실을 적을 것.
      ③ 부분 포괄(등급·직무분야 한정): '기사 이상', '건설 분야 국가기술자격'처럼 범위만 정하면,
         리스트에서 그 문언에 해당하는 종목만 골라 ①과 같이 나열.
         (등급 서열: 기술사 > 기능장 > 기사 > 산업기사 > 기능사. '기사 이상' = 기사·기능장·기술사)
         ※ '운전기사', '신문 기사', '기능사 양성'처럼 '자격'·'이상'이 붙지 않고 등급 단어가 다른 뜻으로
            쓰인 문구는 ③에 해당하지 않습니다.
      연관 종목이 하나도 없으면 반드시 빈 문자열("")로 둘 것 — '없음', '-', '해당없음' 같은 문자는
      절대 쓰지 말 것 (빈칸이 곧 '매칭 없음'의 공식 표기).
    - 조문리스트: 근거 조문이 여러 개면 **반드시 모두 추출**
        - 제O조 형태: {{"조문명": "제23조의2(기술자료 임치제도)", "숫자": "23.2"}} — 원문에 조문 제목이 있으면
          '제23조의2(제목)'처럼 괄호 안에 제목까지 반드시 적을 것(제목 생략 금지). 제목이 없는 조문만 번호로.
        - 별표 형태: "별표1"이 아닌 "별표 1"처럼 반드시 띄어쓰기 (숫자는 빈칸 "")

    ### 📈 [STEP 3. 노동시장 활용도 (연관높음·단순관련 모두 작성, 해당없음일 때만 빈칸)]
    - 활용도_구분: [대폭 증가 / 소폭 증가 / 현상 유지 / 소폭 감소 / 대폭 감소] 중 택1
    - 활용도_상세: 자격 수요·활용도 변화 분석 3문장 이내. ① 개정 배경 ② 방향성 ③ 파급효과 중심.
    - 단순관련이면 간접 영향 관점에서 판단하되, 증감 근거가 뚜렷하지 않으면 '현상 유지'.
      ※ 주요_제개정내용과 내용 반복 금지 — 여기는 '자격 수요가 어떻게 변하나'만.

    ### ⭐ [STEP 4. 우대 판별 (핵심)]
    우대여부: 조문·별표에 (a) 종목명 또는 (b) 자격 요건 문언이 실제로 존재하고, 그 문언이 국가기술자격
    취득자에게 실질적 우대·요구(의무고용, 직무권한, 인사우대, 시험면제, 위촉자격 등)를 부여하면 "O".
    관련은 있으나 우대 조항이 없으면 "X". 직무 유사성 추론만으로는 "O"를 주지 말 것
    (그 경우 연관도는 "단순관련", 우대여부는 "X"). 타 법령의 면허·기술자만 인용한 조항도 STEP 1 (c)에 따라 "X".
    ★ 우대여부가 "X"이면: 우대분류="기타", Track1_취급유형="Z", Track1_위험도="X",
      Track2_효용코드="Ⅳ-0", 중처법대상="비대상", 조문_요약과 상세_분석은 빈칸("")으로 두고 즉시 종료.
    ★ 우대여부가 "O"이면 아래 4-1 ~ 4-5를 모두 작성:

    #### 4-1. 우대분류 (메인 분류)
    다음 5가지 중 1개. 여러 우대가 섞이면 우선순위: 의무고용 > 직무권한부여 > 인사우대 > 시험면제
      - 의무고용: 사업자가 사업 등록·허가·운영을 위해 자격자를 반드시 고용·선임·배치해야 함
      - 직무권한부여: 특정 직무·검사·업무를 자격자만 수행할 수 있도록 권한을 부여
        (자격이 면허·등록의 취득 요건이 되어 자격자만 그 업무를 할 수 있게 되는 경우 포함 — 예: 면허 발급 자격)
      - 인사우대: 채용·보수·승진·평정 등에서 가산점·우대 (의무는 아님)
      - 시험면제: 다른 자격·면허·임용시험에서 시험과목 면제 또는 응시자격 부여
      - 기타: 위 4가지에 해당하지 않는 우대(위원 위촉, 자문 자격 등)에 한정.
        ※ '기타'는 우대가 있는데 4종 밖일 때만 쓰는 값이며, '우대 없음'의 표기가 아님.

    ★ 우대분류 ↔ Track 연동표 (반드시 이 짝을 지킬 것)
      | 우대분류     | Track1 취급유형 | Track2 효용코드 허용 범위 |
      | 의무고용     | B              | Ⅱ-1 ~ Ⅱ-5                |
      | 직무권한부여 | A 또는 C        | Ⅰ-1, Ⅰ-2, Ⅱ-2, Ⅱ-3       |
      | 인사우대     | D              | Ⅲ-2                      |
      | 시험면제     | E              | Ⅲ-1                      |
      | 기타         | Z (위험도 X)    | Ⅲ-3                      |
      우대분류와 Track이 표에서 어긋나면 우대분류를 먼저 다시 판단하고, 그래도 어긋나면 검토필요 "O"와
      함께 검토사유에 '연동 불일치'라고 적으십시오. 우대여부가 "O"인 조항에 Ⅳ-0은 쓰지 마십시오.

    #### 4-2. Track 1. 정책 담당자 관점 : 「경력이음형 자격제도」와의 정합성
    * 1차 축 (법령의 취급):
      - A. 신분형성형 (예: 자격 취득자만 특정 명칭/신분 사용, 자격이 면허 발급 요건)
      - B. 영업요건형 (예: 기업이 사업을 등록/지정받기 위해 자격자 고용)
      - C. 직역독점형 (예: 특정 업무/행위는 자격자만 수행 가능)
      - D. 인사가산형 (예: 채용, 보수, 승진 시 가점 부여)
      - E. 검정연계형 (예: 타 시험 응시자격 부여 또는 과목 면제)
      - Z. 제외 (A~E 어디에도 해당하지 않음 — 우대분류 '기타'일 때만)
    * 2차 축 (위험도 — 선경력을 요구하는 경력이음형과 충돌하는 정도):
      - C (임계위험): 오직 단일 자격만 인정하고 대체 경로가 전혀 없음
      - H (고위험): '자격 + 경력 N년'을 동시에 요구함
      - M (중위험): 복수의 자격을 OR 조건으로 대체 가능함
      - L (저위험): 자격이 없어도 '관련 학과 졸업 + 경력' 등으로 진입 우회 가능
      - N (무관): 직역 진입 자체를 막지 않는 단순 부가우대 (D, E 유형)
      - X (해당없음): 취급유형이 Z인 경우. ★취급유형 Z ↔ 위험도 X는 항상 짝★

    #### 4-3. Track 2. 국민(구직자) 관점 : 노동시장 효용
    한 조항은 반드시 한 칸. 우선순위: 직업창출(Ⅰ) > 취업관문(Ⅱ) > 부가우대(Ⅲ)
    * Ⅰ-1 (면허전환형): 자격 취득 → 행정청 면허 발급으로 평생 직업·신분 부여
    * Ⅰ-2 (개업창업형): 자격자 본인이 단독으로 직무 수행·서명 가능 → 1인 사업 가능
    * Ⅱ-1 (등록필수형): 사업체 등록·허가 시 자격자 보유 의무
    * Ⅱ-2 (지정인력형): 국가 지정·위탁·대행 기관의 인력 요건 (검사·검정·인증·진단기관 등)
    * Ⅱ-3 (전속배치형): 단일 자격자만 선임 가능(대체 불가). ※매우 드묾
    * Ⅱ-4 (선택배치형): 법령이 인정하는 복수 자격 중 택일 선임 (「또는」, 「어느 하나」)
    * Ⅱ-5 (현장배치형): 공사·사업장 규모·종별에 따라 배치 의무 (프로젝트 단위)
    * Ⅲ-1 (부가우대-시험면제) / Ⅲ-2 (부가우대-인사) / Ⅲ-3 (부가우대-위촉·자문)
    * Ⅳ-0 (제외): 타 조문 통합·삭제·이관, 정의 조항, 우대 외 사항 — ★우대여부 "O"이면 선택 금지★

    #### 4-4. 중대재해처벌법 대상 여부
    중처법과 연계되는 안전관리 의무를 자격자에게 부여하면 "대상":
      - 중대산업재해(안전관리자·보건관리자 선임 등), 중대시민재해(시설물·환경·에너지 안전관리),
        고압가스 안전관리자, 검사대상기기 조종자·에너지관리자
    판단 원칙: 단순 인사우대·시험면제(D·E)는 "비대상". 의무 선임·배치로 사고 시 책임이 따르는
    경우만 "대상". 애매하면 "비대상" + 검토필요 표시.

    #### 4-5. 우대 텍스트 (우대여부 O일 때만)
    - 조문_요약: 우대 조문의 핵심을 3문장 이내로 요약 (구직자 친화적 톤).
      종목이 포괄형([전종목])이면 첫 문장을 "[전 종목 공통 우대] "로 시작.
    - 상세_분석: 정책적(경력이음)으로 어떤 모순 위험이 있고, 구직자에게 어떤 취업 기회를
      여는지 5문장 이내. ※ 활용도_상세와 내용 반복 금지 — 여기는 '우대·정책' 관점만.

    ---
    ### 🧪 [공통 판정 항목]
    - AI_신뢰도:
      '높음' = 종목명이 조문·별표에 텍스트로 명시됨, 또는 자격 요건 문언(전 종목 통칭·등급·분야)으로
               대상 종목 집합이 확정됨
      '보통' = 종목명·자격 요건 문언 없이 직무 내용상 강한 추론
      '낮음' = 논리적 비약 필요
    - 검토필요: 다음 중 하나면 "O", 그 외 "X".
      ① AI_신뢰도가 '낮음'
      ② 활용도_구분이 '대폭 증가/대폭 감소'
      ③ 중처법·Track 판단이 애매함
      ④ 자격·인력·선임·검사·교육 '기준'을 담은 별표가 '파일 전용(내용 미확보)'라 그 기준을 확인할 수 없음
      ⑤ 우대여부가 "O"인데 AI_신뢰도가 '보통' 또는 '낮음' (근거 문언 없는 우대 판정은 사람이 확인)
      ⑥ 우대분류 ↔ Track 연동표와 어긋남
      ※ 우대여부가 "X"인 법령에서 신뢰도가 '보통'이라는 이유만으로 O를 달지 말 것 (검토필요 남발 금지).
      ※ 미확보 별표가 서식·별지(신청서·증·대장 등 행정 양식), 수수료·과태료 기준,
        인·허가 의제 목록 같은 비(非)자격기준류뿐이면 ④에 해당하지 않음 — 확보된
        본문·별표만으로 판정하고 "X". ('별표 상태' 섹션의 '서식류' 표시가 그 예)
    - 검토사유: 검토필요가 "O"이면 반드시 구체적으로 기재(빈칸 금지), "X"이면 반드시 빈칸("").
    - 검토사유 작성 규칙: 본 법령 원문에 실제 등장한 조문 번호·별표 번호·자격종목명만
      언급할 것. 다른 법령의 사례, 기억 속 일반 사례(예: 타 법령의 별표 기준) 인용 절대
      금지. 자격 기준류 별표가 '파일 전용(미확보)' 상태면 그 사실을 그대로 기재
      (서식류 미확보는 검토사유로 쓰지 말 것).

    ### 🚨 [치명적 에러 방지 규칙 — 반드시 지킬 것]
    1. **JSON 형태 유지**: Key는 반드시 큰따옴표(")를 사용해야 합니다.
    2. **내부 텍스트 큰따옴표 금지**: 한국어 내용 안에서 강조할 때는 절대 큰따옴표(")를 쓰지 말고 작은따옴표(')를 쓰세요.
    3. **엔터키(줄바꿈) 절대 금지**: 모든 텍스트는 중간 줄바꿈 없이 한 줄로 이어서 작성하세요.
    4. **순수 종목명만 추출**: '[안전관리]', 'ㅇ 직무분야:' 같은 분류명이나 기호를 절대 쓰지 마십시오.
       오직 리스트의 표기 그대로 '자격증이름1, 자격증이름2' 처럼 쉼표로 연결하세요. (예외: 포괄형 토큰 [전종목])
    5. **열거된 종목은 무제한, 통칭은 토큰**: 조문·별표에 실제 열거된 종목은 수백 개라도 단 하나도 누락하지
       말고 끝까지 다 작성하세요. 반대로 전 종목을 통칭하는 조문은 개별 나열 대신 [전종목] 하나만 쓰세요.
    6. **종목 전수 자기점검(출력 직전)**: JSON을 쓰기 전에 원문 전체(조문·별표·개정이유)를 다시 한 번 훑어
       (가) '기술사·기능장·기사·산업기사·기능사'가 들어간 모든 문구, (나) '○○ 자격', '자격을 취득한',
       '자격이 있는', '자격자' 문구를 전부 찾아 그 종목이 ①~③에 빠짐없이 들어갔는지 확인하세요.
       눈에 띄는 조항 몇 개만 보고 일부 종목을 골라 적는 것은 오답입니다. 별표의 기술인력·검사원·관리자
       기준표에 적힌 종목(예: '기계분야 및 전기분야의 소방설비기사')과 '복어 조리 자격'처럼 문언이 가리키는
       종목도 모두 포함합니다.

    ### 📤 [출력 JSON 포맷 (Strict Rule)]
    반드시 아래 JSON 형식만 출력하고, 설명이나 마크다운 백틱(```json)을 포함하지 마십시오.

    {{
      "연관도": "연관높음" | "단순관련" | "해당없음",
      "개정유형": "제정/일부개정/전부개정/폐지 중 택1",
      "주요_제개정내용": "- 팩트1 - 팩트2 (글머리 '-' 나열, 한 줄)",
      "종목": "리스트 표기 그대로 쉼표 나열 | 전 종목 통칭이면 \\"[전종목]\\" | 없으면 \\"\\"",
      "활용도_구분": "대폭 증가/소폭 증가/현상 유지/소폭 감소/대폭 감소 중 택1 (해당없음일 땐 \\"\\")",
      "활용도_상세": "자격 수요·활용도 분석 3문장 이내 (해당없음일 땐 \\"\\")",
      "우대여부": "O" | "X",
      "우대분류": "의무고용" | "직무권한부여" | "인사우대" | "시험면제" | "기타",
      "Track1_취급유형": "A" | "B" | "C" | "D" | "E" | "Z",
      "Track1_위험도": "C" | "H" | "M" | "L" | "N" | "X",
      "Track2_효용코드": "Ⅰ-1" | "Ⅰ-2" | "Ⅱ-1" | "Ⅱ-2" | "Ⅱ-3" | "Ⅱ-4" | "Ⅱ-5" | "Ⅲ-1" | "Ⅲ-2" | "Ⅲ-3" | "Ⅳ-0",
      "중처법대상": "대상" | "비대상",
      "조문_요약": "우대 조문 3문장 요약 (우대여부 X면 \\"\\")",
      "상세_분석": "정책 모순 + 취업 기회 5문장 (우대여부 X면 \\"\\")",
      "AI_신뢰도": "높음" | "보통" | "낮음",
      "검토필요": "O" | "X",
      "검토사유": "O인 경우만 사유 (아니면 \\"\\")",
      "조문리스트": [
        {{"조문명": "제1조(목적)", "숫자": "1"}},
        {{"조문명": "별표 1", "숫자": ""}}
      ]
    }}

    ### ⚠️ [코드 작성 필수 규칙]
    - Track1_취급유형 "Z" ↔ Track1_위험도 "X"는 항상 짝입니다. "Z"는 우대분류가 "기타"일 때만 씁니다.
    - 우대여부 "O"이면 Track2_효용코드에 "Ⅳ-0"을 쓰지 않습니다.
    - "N/A", "없음", 빈칸 등은 코드 칸에 절대 사용하지 마십시오. 반드시 제시된 코드 중 하나를 고르십시오.
    """

    # 🌟 [모델 추상화] 호출/재시도는 통역 창구에 위임
    try:
        raw_text = _client().generate_with_retry(
            prompt,
            attempt_count=attempt_count,
            max_output_tokens=32768,
            temperature=0.1,          # 3.x 모델에서는 어댑터가 전송하지 않음(호환용)
            json_mode=True,           # ★2026-09-16: JSON 형식 강제 (분석 전용)
        )
    except Exception as e:
        # ★2026-10-07 입력 한도 초과 폴백: 머리(개정이유)·꼬리(별표)는 두고 '조문 본문'만 절반으로 줄여 딱 1회 재시도.
        #   (상한 ORIGINAL_MAX_CHARS 를 올린 뒤에도 100만 자 넘는 법령이 모델 한도에 걸릴 때의 안전장치)
        if _is_too_long(e) and not law.get("_입력축소") and "### 📖 전체 조문" in str(law.get("원본", "")):
            shrunk = dict(law)
            shrunk["_입력축소"] = True
            shrunk["원본"], _cut = _shrink_article_body(str(law.get("원본", "")))
            print(f"\n    ✂️ [입력 한도 초과] 조문 본문 {_cut:,}자 추가 생략 후 1회 재시도", end="", flush=True)
            return run_ai_analysis(shrunk, qnet_certs_text, attempt_count=2)
        return False, "", {"error": str(e)}

    # ── 응답 파싱 (두 시스템 공통 로직 계승) ──
    try:
        match = re.search(r'```json\s*(.*?)\s*```', raw_text, re.DOTALL | re.IGNORECASE)
        if match:
            json_str = match.group(1)
        else:
            json_str = raw_text.replace("```json", "").replace("```JSON", "").replace("```", "").strip()

        json_str = json_str.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')

        try:
            data = json.loads(json_str, strict=False)
        except json.JSONDecodeError as je:
            print(f"\n    🚨 [AI 문법 파괴 발생! 범인 색출 블랙박스 로그]")
            print(f"    >> AI가 뱉은 날것의 텍스트:\n{json_str}\n")
            return False, "", {"error": f"JSON 문법 오류: {je}"}

        # ── 조문 링크 조립 (RADAR 로직 계승) ──
        jomun_list = data.get("조문리스트", [])
        if not jomun_list or not isinstance(jomun_list, list):
            jomun_list = [{"조문명": "내용 확인", "숫자": ""}]

        links_str_list, names_str_list = [], []
        for j in jomun_list:
            j_name = j.get("조문명", "확인불가") if isinstance(j, dict) else str(j)
            if "별표" in j_name:
                j_name = re.sub(r'별표\s*(\d+)', r'별표 \1', j_name)
            if j_name == "내용 확인":
                names_str_list.append("전체 (세부 조문 미지정)")
                links_str_list.append(f"▶ {law['법령명']}\n{law.get('링크', '')}")
            else:
                names_str_list.append(j_name)
                new_link = generate_new_law_link(
                    law_name=law.get('법령명', ''),
                    enforce_date=law.get('시행일자', ''),
                    prom_num=law.get('공포번호', ''),
                    prom_date=law.get('공포일자', ''),
                    article_name=j_name,
                )
                links_str_list.append(f"▶ {law['법령명']} {j_name}\n{new_link}")
        links_str = "\n\n".join(links_str_list)
        names_str = ", ".join(names_str_list)

        # ── 정합성 강제 (후처리 방어망) ──
        연관도 = _normalize_relevance(data.get("연관도") or data.get("분류") or data.get("연관성_판별"))
        우대여부 = "O" if str(data.get("우대여부", "")).strip().upper() == "O" else "X"
        if 연관도 == "해당없음":
            우대여부 = "X"   # 해당없음인데 우대일 수 없음 (모순 방지)

        # Track1 코드 교정 (RADAR 방어망 계승) + 우대여부=X 정합 강제
        _t1_type = (data.get("Track1_취급유형", "") or "").strip()
        _t1_risk = (data.get("Track1_위험도", "") or "").strip()
        _INVALID = {"N/A", "n/a", "NA", "없음", "-", ""}
        if 우대여부 == "X":
            _t1_type, _t1_risk = "Z", "X"
            data["우대분류"] = "기타"
            data["Track2_효용코드"] = "Ⅳ-0"
            data["중처법대상"] = "비대상"
            data["조문_요약"] = data.get("조문_요약", "") if str(data.get("조문_요약", "")).strip() else ""
            data["상세_분석"] = data.get("상세_분석", "") if str(data.get("상세_분석", "")).strip() else ""
        else:
            if _t1_type in _INVALID:
                _t1_type = "Z"
            if _t1_type == "Z":
                _t1_risk = "X"
            elif _t1_risk in _INVALID:
                _t1_risk = "N"

        # ★정책 통일(2026-07-05): 활용도는 연관높음·단순관련 모두 산출 (해당없음만 빈칸)
        활용도_구분 = _normalize_usage(data.get("활용도_구분", "")) if 연관도 in ("연관높음", "단순관련") else ""

        law_info = {
            "시행일자": law["시행일자"],
            "소관부처": law.get("소관부처", "") or data.get("소관부처", ""),
            "법령명": law["법령명"],
            "개정유형": data.get("개정유형", ""),
            "연관도": 연관도,
            "우대여부": 우대여부 if 우대여부 == "O" else "",
            "관련 종목": data.get("종목", ""),
            "주요 제·개정내용": data.get("주요_제개정내용", ""),
            "활용도_구분": 활용도_구분,
            "활용도_상세": data.get("활용도_상세", "") if 연관도 in ("연관높음", "단순관련") else "",
            "조문 요약": data.get("조문_요약", ""),
            "우대분류": data.get("우대분류", "기타"),
            "Track1_취급유형": label_track1_type(_t1_type),
            "Track1_위험도": label_track1_risk(_t1_risk),
            "Track2_효용코드": label_track2_code(data.get("Track2_효용코드", "Ⅳ-0")),
            "중처법대상": data.get("중처법대상", "비대상"),
            "상세 분석 결과": data.get("상세_분석", ""),
            "근거조문": names_str,
            "AI신뢰도": data.get("AI_신뢰도", ""),
            "검토필요": data.get("검토필요", "X"),
            "검토사유": _sanitize_review(data.get("검토사유", ""), data.get("관련_종목", ""),
                                        law.get("원본", ""), qnet_certs_text),
            "조문별 다이렉트 링크": links_str,
        }

        # ── ★2026-09-16 후처리 방어망 R1~R5 (자동 교정이 아니라 '검토필요'로 넘김) ──
        _apply_review_guards(law_info, str(law.get("원본", "")))   # ★2026-10-14 R6 는 원문이 필요

        return True, 연관도, law_info

    except Exception as e:
        return False, "", {"error": str(e)}
