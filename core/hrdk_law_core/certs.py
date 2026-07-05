"""
hrdk_law_core.certs
-------------------
국가기술자격 종목 단일 출처(Single Source of Truth).

종목 CSV는 이 패키지 안(data/qnet_certs_2026.csv)에 한 부만 존재하며,
law-monitor와 HRDK-LAW-RADAR가 모두 이 모듈을 통해 종목을 읽습니다.
종목이 매년 바뀌면 코어의 CSV 한 개만 교체하면 양쪽에 동시 반영됩니다.

사용법:
    from hrdk_law_core.certs import get_qnet_certs_text, get_qnet_certs_list

    # RADAR 스타일: 종목 전체를 텍스트로 (프롬프트에 통째로 주입)
    text = get_qnet_certs_text()

    # law-monitor 스타일: 직무분야별로 묶은 텍스트 ([건설] 종목a, 종목b ...)
    text = get_qnet_certs_text(group_by_field=True)

    # 리스트로 받기
    certs = get_qnet_certs_list()   # ['공공조달관리사', '공장관리기술사', ...]
"""

import csv
import re
from functools import lru_cache
from importlib import resources

# 패키지에 동봉된 종목 CSV 파일명(기본/폴백용). 연도 미지정 시 이 파일을 씁니다.
_CSV_FILENAME = "qnet_certs_2026.csv"
_CSV_PREFIX = "qnet_certs_"   # 연도별 파일: qnet_certs_2026.csv, qnet_certs_2027.csv ...


def _available_cert_years() -> list:
    """data 폴더에 존재하는 qnet_certs_{YYYY}.csv 의 연도 목록(오름차순)."""
    years = []
    try:
        data_dir = resources.files("hrdk_law_core").joinpath("data")
        for entry in data_dir.iterdir():
            nm = entry.name
            m = re.match(rf"^{_CSV_PREFIX}(\d{{4}})\.csv$", nm)
            if m:
                years.append(int(m.group(1)))
    except Exception:
        pass
    return sorted(years)


def _resolve_cert_filename(year=None) -> str:
    """
    종목 CSV 파일명을 연도 기준으로 고른다.
      · year 지정 → qnet_certs_{year}.csv 가 있으면 그것
      · 그 파일이 없으면 → 존재하는 연도 중 'year 이하의 가장 가까운 연도'로 폴백,
        그것도 없으면 '가장 이른 연도'로 폴백
      · year 미지정 → 기본 상수(_CSV_FILENAME)
    파일이 하나도 없으면 기본 상수를 그대로 반환(기존 동작 유지).
    """
    if year is None:
        return _CSV_FILENAME
    try:
        year = int(year)
    except (TypeError, ValueError):
        return _CSV_FILENAME
    avail = _available_cert_years()
    if not avail:
        return _CSV_FILENAME
    if year in avail:
        return f"{_CSV_PREFIX}{year}.csv"
    # 폴백: year 이하 중 가장 큰 연도(=그 시점에 유효했던 최신 명단), 없으면 가장 이른 연도
    below = [y for y in avail if y <= year]
    chosen = max(below) if below else min(avail)
    return f"{_CSV_PREFIX}{chosen}.csv"


@lru_cache(maxsize=8)
def _load_rows(year=None) -> tuple:
    """
    패키지에 동봉된 종목 CSV를 읽어 (직무분야, 종목명) 튜플 목록을 반환합니다.
    year를 주면 그 연도(폴백 포함)의 파일을, 없으면 기본 파일을 읽습니다.
    pip로 설치된 패키지 안에서도 안전하게 읽기 위해 importlib.resources 사용. 결과는 캐시됨.
    """
    rows = []
    fname = _resolve_cert_filename(year)
    data_pkg = resources.files("hrdk_law_core").joinpath("data", fname)
    with data_pkg.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            field = (r.get("직무분야") or "").strip()
            name = (r.get("종목명") or "").strip()
            if name:
                rows.append((field, name))
    return tuple(rows)


def get_qnet_certs_list(year=None) -> list:
    """종목명만 담은 리스트를 반환합니다. year 지정 시 그 연도(폴백 포함) 명단."""
    return [name for _field, name in _load_rows(year)]


def get_qnet_certs_count(year=None) -> int:
    """등록된 종목 개수를 반환합니다."""
    return len(_load_rows(year))


def get_qnet_certs_text(group_by_field: bool = False, year=None) -> str:
    """
    종목을 프롬프트 주입용 텍스트로 반환합니다.

    Parameters
    ----------
    group_by_field : False(기본) → 종목명을 줄바꿈으로 나열 (RADAR 스타일)
                     True        → 직무분야별로 묶어서 "[분야] 종목a, 종목b" (law-monitor 스타일)
    year           : 종목 명단 기준 연도(법령 시행일자 연도). 미지정 시 기본 파일.
    """
    rows = _load_rows(year)

    if not group_by_field:
        # RADAR 스타일: CSV 헤더 + 종목 나열 (기존 load_qualification_list 출력과 호환)
        lines = ["직무분야,종목명"]
        lines += [f"{field},{name}" for field, name in rows]
        return "\n".join(lines)

    # law-monitor 스타일: 직무분야별 그룹핑
    grouped: dict[str, list[str]] = {}
    for field, name in rows:
        grouped.setdefault(field or "기타", []).append(name)

    blocks = []
    for field, names in grouped.items():
        blocks.append(f"[{field}] " + ", ".join(names))
    return "\n".join(blocks)


# ──────────────────────────────────────────────────────────
# 레버 2: 종목 슬림화 (토큰 절감, 누락 위험 0)
# ──────────────────────────────────────────────────────────
def get_relevant_certs_text(law_text: str, *, group_by_field: bool = False,
                            min_certs: int | None = None, year=None) -> str:
    """
    법령 원문에 '실제로 등장하거나 관련될 가능성이 있는' 종목만 추려서 반환합니다.

    매 호출마다 541개 종목 전체를 프롬프트에 넣던 낭비를 줄입니다.
    단, 누락 위험을 0으로 만들기 위해 다음 안전장치를 둡니다:
      1) 법령 텍스트에 종목명이 직접 등장하면 무조건 포함
      2) 종목명의 핵심 어근(예: '전기', '건축', '안전')이 법령에 등장하면 포함
      3) 추려진 결과가 min_certs개 미만이면 → 안전하게 '전체'를 반환
         (애매할 땐 전부 보낸다는 원칙)

    Parameters
    ----------
    law_text       : 법령 원문 텍스트
    group_by_field : 출력 포맷 (certs_text와 동일 규칙)
    min_certs      : 이 개수 미만으로 추려지면 전체 반환 (기본 30)

    Returns
    -------
    프롬프트 주입용 종목 텍스트 (슬림화 또는 전체)
    """
    rows = _load_rows(year)
    if not law_text:
        # 법령 텍스트가 없으면 안전하게 전체 반환
        return get_qnet_certs_text(group_by_field=group_by_field, year=year)

    # 안전선: 환경변수 CERTS_MIN_MATCH로 운영 중 튜닝 가능 (기본 15)
    if min_certs is None:
        import os
        try:
            min_certs = int(os.environ.get("CERTS_MIN_MATCH", "15"))
        except ValueError:
            min_certs = 15

    matched = []
    for field, name in rows:
        # 1) 종목명 직접 등장
        if name in law_text:
            matched.append((field, name))
            continue
        # 2) 종목명에서 분야 어근 추출 (기사/산업기사/기능사/기술사 등 접미사 제거)
        root = name
        for suffix in ["기술사", "기능장", "산업기사", "기사", "기능사", "관리사", "기술자"]:
            if root.endswith(suffix):
                root = root[: -len(suffix)]
                break
        # 어근이 2글자 이상이고 법령에 등장하면 포함 (과매칭 방지 위해 2글자 이상만)
        if len(root) >= 2 and root in law_text:
            matched.append((field, name))

    # 3) 안전장치: 너무 적게 걸리면 전체 반환 (누락 방지 원칙)
    if len(matched) < min_certs:
        return get_qnet_certs_text(group_by_field=group_by_field, year=year)

    # 슬림화된 결과를 포맷팅
    if not group_by_field:
        lines = ["직무분야,종목명"]
        lines += [f"{field},{name}" for field, name in matched]
        return "\n".join(lines)

    grouped: dict = {}
    for field, name in matched:
        grouped.setdefault(field or "기타", []).append(name)
    return "\n".join(f"[{field}] " + ", ".join(names) for field, names in grouped.items())


# ──────────────────────────────────────────────────────────
# 자격명칭 별칭(변천사) 처리 — 구명칭 ↔ 2026 현행명칭
# ──────────────────────────────────────────────────────────
# 별칭(구명칭→현행명) 소스는 각 시트의 '자격명칭최신화' 탭입니다.
# 배치가 그 탭을 읽어 register_alias_overrides()로 주입하며, resolve_current_name이 이를 사용합니다.
# (과거 cert_aliases.csv는 폐지되었고, 그 265개 이력은 자격명칭최신화 탭으로 이관되었습니다.)


def _normalize_cert(s: str) -> str:
    """종목명 표기 정규화: 가운뎃점·공백·괄호 등 제거 (서비스·경험디자인 = 서비스경험디자인)."""
    import re
    return re.sub(r"[·\s\(\)\[\]ㆍ・,]", "", str(s)).strip()


def _load_alias_map() -> dict:
    """
    구명칭(정규화) → 현행 명칭 매핑을 반환합니다.
    이제 별칭은 '자격명칭최신화' 탭에서 주입되므로(런타임 오버라이드), 여기서는 그 주입분을 돌려줍니다.
    주입 전이면 빈 dict (별칭 없음, 오류 없음).
    """
    return dict(_runtime_alias_overrides)


def resolve_current_name(cert_name: str) -> str:
    """
    어떤 종목명(구명칭/표기변형 포함)을 받아 현행 명칭으로 변환합니다.

    동작 순서:
      1) 현행 종목 목록에 정규화 일치가 있으면 그 현행명 반환
      2) 별칭 사전(변천사)에 구명칭으로 등록돼 있으면 → 그 현행명으로 바꾼 뒤
         다시 1)~2)를 반복(연쇄 추적). 예: A→B, B→C 가 등록돼 있으면 A→C까지 따라감.
      3) 더 못 바꾸면 그 값 반환 (신설/미등록 종목은 원본 유지)

    ※ 순환(A→B→A) 방지: 이미 거쳐간 이름은 다시 적용하지 않고 멈춤.
    """
    cur = cert_name
    seen = set()
    for _ in range(20):  # 안전 상한(현실적으로 명칭변경이 20번 연쇄될 일은 없음)
        norm = _normalize_cert(cur)

        # 1) 현행 종목 목록에 있으면 그게 최종 → 그 표기로 반환
        for _field, name in _load_rows():
            if _normalize_cert(name) == norm:
                return name

        # 순환 방지: 이미 거친 이름이면 중단
        if norm in seen:
            break
        seen.add(norm)

        # 2) 별칭(자격명칭최신화 탭에서 주입된 구명칭→현행명)에 있으면 바꾸고 재시도(연쇄 추적)
        nxt = _runtime_alias_overrides.get(norm)
        if nxt and _normalize_cert(nxt) != norm:
            cur = nxt
            continue
        break

    # 3) 더 못 바꾸면 현재 값 유지
    return cur


def get_alias_count() -> int:
    """등록된 별칭(구명칭) 개수를 반환합니다."""
    return len(_load_alias_map())


# ──────────────────────────────────────────────────────────
# B 알림: 법령에서 '명칭 변경' 의심 감지 (자동 변경 ❌, 알림만)
# ──────────────────────────────────────────────────────────
def detect_name_change_signal(law_name: str, law_text: str) -> bool:
    """
    법령이 국가기술자격 종목 명칭 변경과 관련될 가능성을 가볍게 감지합니다.
    True가 나오면 '변천사 자료 업데이트가 필요할 수 있다'는 신호일 뿐,
    절대 자동으로 명칭을 바꾸지 않습니다. (담당자 확인용 알림)
    """
    if not law_text:
        return False
    # 국가기술자격법 시행규칙 별표 개정 + '명칭' 관련 키워드 동시 등장 시 의심
    is_qualification_law = "국가기술자격법" in law_name
    has_name_change_words = any(
        kw in law_text for kw in ["종목의 명칭", "명칭을", "명칭 변경", "종목을 신설", "종목을 폐지"]
    )
    return is_qualification_law and has_name_change_words


# 런타임 별칭 저장소 — '자격명칭최신화' 탭에서 배치가 읽어 주입하는 {구명칭→현행명} 매핑.
#    resolve_current_name이 이 매핑으로 구명칭을 현행명으로 변환(연쇄 추적 포함).
#    주입 전이면 빈 dict → 변환 없이 원본 유지.
_runtime_alias_overrides: dict = {}


def register_alias_overrides(overrides: dict) -> None:
    """
    '자격명칭최신화' 탭에서 읽은 별칭({구명칭: 현행명})을 런타임에 등록합니다.
    배치(RADAR/monitor)가 실행 시작 시 호출하며, 이후 resolve_current_name이 이를 사용합니다.
    """
    global _runtime_alias_overrides
    cleaned = {}
    for old, new in (overrides or {}).items():
        if old and new:
            cleaned[_normalize_cert(old)] = str(new).strip()
    _runtime_alias_overrides = cleaned


# ══════════════════════════════════════════════════════════
# Track 코드 → 한글 병기 변환 (구글 시트 표기용)
# ⚠️ SQLite에는 순수 코드를 저장하고, 시트에 쓸 때만 이 변환을 적용합니다.
#    (시트=사람이 보는 용도, SQLite=기계 활용 용도)
# ══════════════════════════════════════════════════════════
TRACK1_TYPE_KO = {
    "A": "신분형성형", "B": "영업요건형", "C": "직역독점형",
    "D": "인사가산형", "E": "검정연계형", "Z": "제외",
}
TRACK1_RISK_KO = {
    "C": "임계위험★★", "H": "고위험★", "L": "저위험",
    "M": "중위험", "N": "무관", "X": "해당없음",
}
TRACK2_CODE_KO = {
    "Ⅰ-1": "면허전환형", "Ⅰ-2": "개업창업형",
    "Ⅱ-1": "등록필수형", "Ⅱ-2": "지정인력형", "Ⅱ-3": "전속배치형",
    "Ⅱ-4": "선택배치형", "Ⅱ-5": "현장배치형",
    "Ⅲ-1": "부가우대-시험면제", "Ⅲ-2": "부가우대-인사", "Ⅲ-3": "부가우대-위촉·자문",
    "Ⅳ-0": "제외",
}


def label_track1_type(code: str) -> str:
    """예: 'B' → 'B (영업요건형)'. 매핑에 없거나 빈 값이면 원본 그대로."""
    code = (code or "").strip()
    ko = TRACK1_TYPE_KO.get(code)
    return f"{code} ({ko})" if ko else code


def label_track1_risk(code: str) -> str:
    """예: 'M' → 'M (중위험)'."""
    code = (code or "").strip()
    ko = TRACK1_RISK_KO.get(code)
    return f"{code} ({ko})" if ko else code


def label_track2_code(code: str) -> str:
    """예: 'Ⅱ-1' → 'Ⅱ-1 (등록필수형)'."""
    code = (code or "").strip()
    ko = TRACK2_CODE_KO.get(code)
    return f"{code} ({ko})" if ko else code


# 정규화 매칭용 사전 캐시 (정규화키 → 정식 종목명)
# 정규화 매칭용 사전 캐시 (연도별)
_DICT_NORM_CACHE = {}

def _dict_norm_map(year=None):
    if year not in _DICT_NORM_CACHE:
        _DICT_NORM_CACHE[year] = {_normalize_cert(n): n for n in get_qnet_certs_list(year)}
    return _DICT_NORM_CACHE[year]


def normalize_cert_string(raw, year=None):
    """
    AI가 반환한 '관련 종목' 문자열을 자격증 사전(541종목)에 있는 정식 종목명만 남깁니다.

    - 괄호 안 쉼표는 보호('소방설비기사(기계분야)'가 안 깨지게)
    - 구명칭·표기변형은 resolve_current_name으로 현행명 변환 후 사전 대조
    - ★포괄명칭 확장: 법령이 '미용사'처럼 포괄 명칭만 쓰면, 사전의 '미용사(일반)'
      '미용사(피부)' 등 「토큰(」으로 시작하는 종목 전부로 확장 (실사용자 피드백 반영)
    - 사전에 없는 범주형/임의 표현('~전체', '~등 직무분야 종목' 등)은 제외

    반환: (정식종목 콤마문자열, 제외된_표현_리스트)
    """
    import re as _re
    s = str(raw or "")
    s = _re.sub(r"\(([^)]*)\)", lambda m: "(" + m.group(1).replace(",", "§") + ")", s)
    toks = [t.strip().replace("§", ",") for t in _re.split(r"[,/]", s) if t.strip()]
    dnorm = _dict_norm_map(year)
    kept, dropped, seen = [], [], set()

    def _add(std_name):
        if std_name not in seen:
            seen.add(std_name); kept.append(std_name)

    for t in toks:
        name = resolve_current_name(t)
        std = dnorm.get(_normalize_cert(name))
        if std:
            _add(std)
            continue
        # ★ 포괄명칭 확장: 정확 일치가 없으면 「토큰(」 접두의 사전 종목 전부로
        #   (예: 미용사 → 미용사(일반)/(피부)/(네일)/(메이크업), 소방설비기사 → (기계분야)/(전기분야))
        #   ※ _normalize_cert는 괄호를 지우므로, 괄호를 보존하는 별도 정규화로 비교
        def _np(x):  # 공백·가운뎃점만 제거, 괄호는 보존
            return _re.sub(r"[·\sㆍ・,\[\]]", "", str(x))
        prefix = _np(name) + "("
        expanded = sorted({v for v in dnorm.values() if _np(v).startswith(prefix)})
        if expanded:
            for v in expanded:
                _add(v)
        else:
            dropped.append(t)
    return ", ".join(kept), dropped
