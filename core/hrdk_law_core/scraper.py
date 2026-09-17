"""
hrdk_law_core.scraper
---------------------
법제처 OpenAPI에서 당일 시행 법령을 수집·정제합니다.

기존 두 레포(law-monitor, HRDK-LAW-RADAR)의 law_scrapper.py를
하나로 통합한 공유 모듈입니다. 3중 방어망은 그대로 유지합니다.

사용법:
    from hrdk_law_core.scraper import get_base_laws
    laws = get_base_laws(api_key="YOUR_KEY", target_date="20260612")
"""

import os  # ★2026-07-06 핫픽스: env 상한 읽기용 (docstring 문구에 가드가 속아 누락됐던 것)
import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ──────────────────────────────────────────────
# 🛡️ 1차 방어: urllib3 레벨 자동 재시도 세션
# ──────────────────────────────────────────────
# 🌟 [핵심] 법제처 OPEN API는 Referer 헤더가 없으면 OC 키가 유효해도
# "사용자 정보 검증에 실패하였습니다(IP/도메인 등록)" 오류를 반환합니다.
# 메시지는 IP 문제로 오인되기 쉬우나 실제 원인은 Referer 누락인 경우가 많습니다.
# 또한 Node/Python 기본 UA는 봇으로 분류되어 거부되므로 브라우저 UA를 사용합니다.
# (참고: korean-law-mcp v4.0.9의 동일 증상 해결 사례)
# 환경변수 LAW_REFERER / LAW_USER_AGENT로 override 가능.
import os as _os
HEADERS = {
    "User-Agent": _os.environ.get(
        "LAW_USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    ),
    "Referer": _os.environ.get("LAW_REFERER", "https://www.law.go.kr/"),
}

def _build_session() -> requests.Session:
    """재시도 로직이 탑재된 requests 세션을 반환합니다."""
    session = requests.Session()
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        backoff_factor=2,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def clean_to_markdown(title: str, content: str) -> str:
    """조문 텍스트를 마크다운으로 정제해 AI 가독성을 높입니다."""
    if not content:
        return ""
    text = content.strip()
    # ★2026-09-16 수정: 종전 r"\n- **\\1**" 은 백슬래시 중복으로 그룹 참조가 아닌 문자 '\1' 을 써 넣어
    #   모든 항 번호(①②③…)가 원문에서 사라지고 '\1' 로 통일되던 결함 → 항 번호를 그대로 살린다.
    text = re.sub(r"(①|②|③|④|⑤|⑥|⑦|⑧|⑨|⑩|⑪|⑫|⑬|⑭|⑮)", r"\n- **\1**", text)
    return f"### 📜 {title}\n{text}\n"


_JOMUN_TEXT_TAGS = ("조문내용", "항내용", "호내용", "목내용")


def jomun_full_text(jomun) -> str:
    """★2026-09-16 조문단위 한 개의 전체 본문.
    법제처 XML 은 조 머리글(<조문내용>)과 항·호·목(<항내용>/<호내용>/<목내용>) 을 형제·자식 태그로 나눠 준다.
    종전 코드는 <조문내용>.text 만 읽어 '다음 각 호의 …' 뒤의 각 호(자격 종목 목록이 대부분 여기 있음) 를
    통째로 버렸다 (실측: 스마트농업 육성법 시행령 항·호·목 129개 중 129개 누락).
    → 문서 순서(iter = 전위 순회)대로 네 태그의 텍스트를 이어 붙인다. 번호 태그(조문번호·항번호·호번호)는
      내용 텍스트에 이미 '①', '1.' 로 들어 있으므로 넣지 않는다."""
    parts = []
    for el in jomun.iter():
        if el.tag in _JOMUN_TEXT_TAGS and el.text and el.text.strip():
            parts.append(el.text.strip())
    return "\n".join(parts)


def is_article_unit(jomun) -> bool:
    """조문단위가 본문 조문인가(부칙·전문 제외). 조문여부는 자식 태그가 정식이고 속성으로 오는 판본도 대비.
    값이 없으면 포함(본문 누락보다 부칙 포함이 덜 해롭다)."""
    v = (jomun.attrib.get("조문여부") or jomun.findtext("조문여부") or "").strip()
    return v in ("", "조문")


def norm_law_name(name: str) -> str:
    """법령명 비교용 정규화: 공백·중점·괄호를 제거한 키."""
    return re.sub(r"[\s·ㆍ()（）]", "", str(name or ""))


def _fetch_law_record(api_key: str, session, target_type: str, law_id: str, law_name: str,
                      enforce_date: str, ministry: str, prom_num: str, prom_date: str) -> dict | None:
    """★2026-09-16 함수 추출: 법령 한 건의 상세조회 → 조문(각 호 포함)·별표 조립 → 레코드.
    get_base_laws(일일 수집)와 get_law_version(연혁 판본 취득, 재분석 도구)이 같은 코드를 쓴다.
    반환 None = 연결 실패(재시도 소진)."""
    base_law_link = f"https://www.law.go.kr/법령/{law_name}"
    # ─────────────────────────────────────
    # 🛡️ 2차 방어: 수동 패자부활전 (상세 조문)
    # ─────────────────────────────────────
    detail_url = (
        f"https://www.law.go.kr/DRF/lawService.do"
        f"?OC={api_key}&target={target_type}&MST={law_id}&type=XML"
    )
    detail_response = None
    for d_attempt in range(1, 4):
        try:
            detail_response = session.get(detail_url, headers=HEADERS, timeout=30)
            if detail_response.status_code == 200 and detail_response.text.strip():
                break
        except Exception as de:
            if d_attempt == 3:
                print(f"  ❌ [최종 실패] '{law_name}' 상세 조문 수집 불가: {de}")
                return None      # 연결 실패 → 호출자가 판단
            print(f"  ⚠️ [재시도 {d_attempt}/3] '{law_name}' 상세조회 실패. 10초 대기...")
            time.sleep(10)

    if detail_response is None:

        return None

    # 조문 파싱 및 마크다운 변환
    detail_root = ET.fromstring(detail_response.text)
    reason_text = ""
    for tag in [".//개정이유", ".//제개정이유"]:
        r_node = detail_root.find(tag)
        if r_node is not None and r_node.text:
            reason_text += r_node.text.strip() + "\n"

    article_1, changed_articles = "", []
    # ★2026-09-16 조문 본문 조립 (두 가지 결함 수정)
    #  ① 각 호 누락: <조문내용>.text 만 읽어 항·호·목이 빠졌음 → jomun_full_text 로 전체 본문
    #  ② '조문여부' 는 속성이 아니라 자식 태그(<조문여부>조문</조문여부>) → 종전 attrib 검사는 항상 불일치.
    #     그래서 '제1조+바뀐 조문만' 분기는 8개월간 한 번도 작동하지 않았고 늘 '전체 조문' 으로 돌았다.
    #     운영 실동작(전체 조문)을 유지하고, 바뀐 조문은 앞에 '표시' 로만 덧붙인다(개정되지 않은 우대 조문 누락 방지).
    articles = [j for j in detail_root.findall(".//조문단위") if is_article_unit(j)]
    body_parts, changed_articles = [], []
    for jomun in articles:
        title = (jomun.findtext("조문제목") or "").strip()
        content = jomun_full_text(jomun)
        if not content:
            continue
        body_parts.append(clean_to_markdown(title, content) if title else content)
        if "<개정" in content or "<신설" in content or "[신설" in content:
            changed_articles.append(title or content[:30])
    body = "\n".join(body_parts)
    stars = "\n".join(
        s.text.strip() for s in detail_root.findall(".//별표내용") if s.text
    )
    # ★2026-09-16 상한 적용 방식 변경: 종전에는 완성된 원문의 뒤를 잘라 '별표'(맨 뒤)가 먼저 사라졌다.
    #   → 머리(개정이유·바뀐 조문 표시)와 꼬리(별표 본문·파일 별표·상태)는 항상 살리고, 조문 본문만 상한 안에서 자른다.
    head_text = f"### 🏢 개정이유\n{reason_text}\n\n"
    if changed_articles:
        head_text += "### 🚨 이번에 바뀐 조문(표시)\n" + ", ".join(changed_articles[:40]) + "\n\n"
    tail_text = f"\n\n### ⭐ 별표(자격 기준 등)\n{stars}" if stars else ""
    # ★재발방지(2026-07-06): 파일 전용 별표 심층 수집 + 상태 사실 표기
    try:
        from .annex import build_annex_sections
        # ★타르핏 방어(2026-07-16): 해외 IP에 '찔끔 응답'을 주는 서버는
        #   timeout=30(바이트 간격 기준)을 영원히 안 건드려 수집이 무한 대기함.
        #   → 파일당 총 소요 예산(벽시계) 초과 시 예외 발생 = annex 안전핀에 합류
        #     (재시도 1회 → 그래도 초과면 그 별표만 '미확보'로 정직 신고, 배치는 계속)
        #   ※ 분석 타임아웃 아님 — 법령은 절대 조용히 버려지지 않음(검토필요로 남음).
        _AX_BUDGET = int(os.environ.get("ANNEX_FETCH_BUDGET", "90"))
        _ax_sess = requests.Session()          # ★연결·SSL 재사용(파일마다 새 핸드셰이크 방지)
        _ax_sess.headers.update({"User-Agent": "Mozilla/5.0"})
        def _ax_get(u, _b=_AX_BUDGET, _s=_ax_sess):
            import time as _t
            _t0 = _t.monotonic()
            _r = _s.get(u, timeout=30, stream=True)
            _buf = bytearray()
            for _part in _r.iter_content(chunk_size=65536):
                if _part:
                    _buf.extend(_part)
                if _t.monotonic() - _t0 > _b:
                    raise TimeoutError(
                        f"별표 다운로드 예산 초과({_b}s·{len(_buf):,}B 수신)")
            return bytes(_buf)
        ax_text, ax_status = build_annex_sections(detail_root, _ax_get, law_name=law_name, api_key=api_key)
        if ax_text:
            tail_text += f"\n\n{ax_text}"
        if ax_status:
            tail_text += f"\n\n{ax_status}"
    except Exception as _ax_e:
        print(f"    ⚠️ 별표 심층수집 건너뜀: {str(_ax_e)[:40]}")

    # 상한(ORIGINAL_MAX_CHARS, 기본 150,000) 은 조문 본문에만 — 별표는 잘리지 않는다.
    _max = int(os.environ.get("ORIGINAL_MAX_CHARS", "150000"))
    _budget = max(_max - len(head_text) - len(tail_text) - 200, 20_000)
    if len(body) > _budget:
        _cut = len(body) - _budget
        body = body[:_budget] + f"\n…(조문 본문 뒷부분 {_cut:,}자 생략 — ORIGINAL_MAX_CHARS 상한. 별표는 아래에 전부 포함)"
        print(f"    ✂️ 조문 본문 {_cut:,}자 생략(상한 {_max:,}) — 별표는 보존")
    full_text = head_text + f"### 📖 전체 조문\n{body}" + tail_text
    return {
        "법령명": law_name, "시행일자": enforce_date,
        "소관부처": ministry, "공포번호": prom_num,
        "공포일자": prom_date, "원본": full_text,   # 상한은 위에서 조문 본문에만 적용됨
        "링크": base_law_link, "스킵여부": False,
    }


def get_base_laws(api_key: str, target_date: str, only_names: set | None = None) -> list | None:
    """
    특정 일자의 시행 법령을 수집·정제합니다.

    only_names : ★2026-09-16 재분석 도구용 선택 인자. 법령명(공백·구두점 제거) 집합을 주면 그 법령만
                 원문을 수집한다(나머지는 목록 단계에서 건너뜀 → 법제처 호출 최소화). None이면 종전과 동일.

    Parameters
    ----------
    api_key     : 법제처 OpenAPI 인증키
    target_date : 조회 일자 (예: "20260612")

    Returns
    -------
    list  : 수집 성공. 법령 딕셔너리 목록 (0건이면 빈 리스트)
    None  : 네트워크 완전 실패 → 가짜 0건 리포트 방지용 None 반환

    법령 딕셔너리 키:
        법령명, 시행일자, 소관부처, 공포번호, 공포일자, 원본, 링크, 스킵여부
    """
    session = _build_session()

    # ──────────────────────────────────────────────────────
    # 🌟 [버리지 않는 체] 보수적 보류 키워드
    # 자격증과 '확실하게' 무관한 것만 보류 대상으로 둡니다.
    # 보류돼도 삭제하지 않고 held_laws에 사유와 함께 기록됩니다(main.py).
    #
    # ⚠️ 제거한 위험 키워드 (오탐으로 자격 관련 법령을 놓칠 수 있어 제외):
    #   - "위원회": 산업안전보건위원회 등 자격 관련 위원회 오탐
    #   - "인사규정": 자격증 가산점이 인사규정에 있을 수 있음
    #   - "선거": 너무 광범위 → "선거관리"로 좁힘
    # ──────────────────────────────────────────────────────
    SKIP_KEYWORDS = [
        # 조직·기구 (자격 무관 확실)
        "직제", "행정기구", "사무분장", "분장규정", "정원", "위임전결",
        # 행정 내부 규정 (자격 무관 확실)
        "여비규정", "표창규칙", "복무규정",
        # 자격증과 무관한 영역
        "조세특례", "관세", "의전", "재외공관", "선거관리",
    ]

    all_laws_dict: dict = {}
    is_connection_failed = False  # 🛡️ 3차 방어용 플래그

    # ★2026-09-16: 종전 ["law", "histlaw"] 중 "histlaw" 는 법제처 API 에 없는 대상(항상 빈 응답)이라 제거.
    #   옛 시행일자 판본(연혁)은 get_law_version() 이 target=eflaw 로 찾는다.
    for target_type in ["law"]:
        page = 1
        while True:
            search_url = (
                f"https://www.law.go.kr/DRF/lawSearch.do"
                f"?OC={api_key}&target={target_type}&type=XML"
                f"&efYd={target_date}~{target_date}&display=100&page={page}"
            )

            # ─────────────────────────────────────
            # 🛡️ 2차 방어: 수동 패자부활전 (목록 조회)
            # ─────────────────────────────────────
            response = None
            for attempt in range(1, 4):
                try:
                    response = session.get(search_url, headers=HEADERS, timeout=30)
                    if response.status_code == 200:
                        break
                except Exception as e:
                    if attempt == 3:
                        print(f"  ❌ [최종 실패] 법령 목록 조회 불능: {e}")
                        is_connection_failed = True
                        break
                    print(f"  ⚠️ [재시도 {attempt}/3] 목록 수집 실패. 20초 대기 후 재시도...")
                    time.sleep(20)

            if is_connection_failed or response is None:
                break
            if not response.text.strip() or response.status_code != 200:
                break

            try:
                root = ET.fromstring(response.text)
                law_nodes = root.findall(".//law")
                if not law_nodes:
                    break

                for law in law_nodes:
                    law_id = law.findtext("법령일련번호", "")
                    law_name = law.findtext("법령명한글", "").strip()
                    enforce_date = law.findtext("시행일자", "")
                    ministry = law.findtext("소관부처명", "알 수 없음").strip()
                    prom_num = re.sub(r"\D", "", law.findtext("공포번호", ""))
                    prom_date = law.findtext("공포일자", "").strip()

                    if not law_id or law_name in all_laws_dict:
                        continue
                    if only_names is not None and norm_law_name(law_name) not in only_names:
                        continue   # 재분석 대상 아님 — 원문 수집 생략

                    base_law_link = f"https://www.law.go.kr/법령/{law_name}"

                    # 스킵 키워드 필터 (버리지 않는 체: 사유를 함께 기록)
                    matched_kw = next((k for k in SKIP_KEYWORDS if k in law_name), None)
                    if matched_kw:
                        all_laws_dict[law_name] = {
                            "법령명": law_name, "시행일자": enforce_date,
                            "소관부처": ministry, "공포번호": prom_num,
                            "공포일자": prom_date,
                            "원본": "조직/기구 관련 법령으로 AI 분석 생략",
                            "링크": base_law_link, "스킵여부": True,
                            "스킵사유": f"보류 키워드 '{matched_kw}' 일치",
                        }
                        continue

                    rec = _fetch_law_record(api_key, session, target_type, law_id, law_name,
                                            enforce_date, ministry, prom_num, prom_date)
                    if rec is None:
                        is_connection_failed = True
                        break
                    all_laws_dict[law_name] = rec
                    time.sleep(0.1)

                if is_connection_failed:
                    break
                if len(law_nodes) < 100:
                    break
                page += 1

            except Exception as e:
                print(f"⚠️ 법령 데이터 파싱 중 크리티컬 에러: {e}")
                is_connection_failed = True
                break

        if is_connection_failed:
            break

    # ─────────────────────────────────────────────────
    # 🛡️ 3차 방어: 네트워크 실패 시 None 반환 (0건 가짜 리포트 차단)
    # ─────────────────────────────────────────────────
    if is_connection_failed and not all_laws_dict:
        return None

    return list(all_laws_dict.values())


def get_law_version(api_key: str, law_name: str, enforce_date: str, prom_num: str = "",
                    session=None):
    """★2026-09-16 옛 시행일자 판본(연혁) 취득 — 재분석 도구 보조 경로.
    lawSearch target=eflaw(시행일 법령) 에 법령명으로 검색해 모든 판본을 받고,
      ① 시행일자 일치 + 공포번호 일치 → ② 시행일자 일치 중 공포일자가 가장 늦은 판본(당시 현행판)
    을 골라 MST 로 본문을 받는다(_fetch_law_record 공용).
    반환: dict(성공) / None(연결 실패) / str(사유: 판본 없음 등)"""
    session = session or _build_session()
    key = norm_law_name(law_name)
    want_date = re.sub(r"\D", "", enforce_date or "")[:8]
    want_num = re.sub(r"\D", "", prom_num or "")
    cands = []
    for page in range(1, 6):                       # 판본이 많은 법령도 5페이지(500건)면 충분
        url = (f"https://www.law.go.kr/DRF/lawSearch.do?OC={api_key}&target=eflaw&type=XML"
               f"&query={quote(law_name)}&display=100&page={page}")
        try:
            r = session.get(url, headers=HEADERS, timeout=30)
        except Exception as e:
            print(f"  ❌ 연혁 판본 검색 실패 '{law_name}': {e}")
            return None
        if r.status_code != 200 or not r.text.strip():
            break
        try:
            nodes = ET.fromstring(r.text).findall(".//law")
        except ET.ParseError:
            break
        for n in nodes:
            if norm_law_name(n.findtext("법령명한글", "")) != key:
                continue
            cands.append({
                "mst": n.findtext("법령일련번호", "").strip(),
                "efYd": re.sub(r"\D", "", n.findtext("시행일자", "")),
                "prom_num": re.sub(r"\D", "", n.findtext("공포번호", "")),
                "prom_date": re.sub(r"\D", "", n.findtext("공포일자", "")),
                "ministry": (n.findtext("소관부처명", "") or "알 수 없음").strip(),
                "status": n.findtext("현행연혁코드", "").strip(),
            })
        if len(nodes) < 100:
            break
    if not cands:
        return "eflaw 검색에 법령명 없음(명칭 변경·폐지 의심)"
    same = [c for c in cands if c["efYd"] == want_date and c["mst"]]
    if not same:
        dates = sorted({c["efYd"] for c in cands}, reverse=True)[:6]
        return f"시행일자 {want_date} 판본 없음 (있는 시행일자: {', '.join(dates)})"
    pick = next((c for c in same if want_num and c["prom_num"].lstrip("0") == want_num.lstrip("0")), None)
    if pick is None:
        pick = max(same, key=lambda c: c["prom_date"])   # 같은 날 여러 판본 → 공포일자 가장 늦은 것
    rec = _fetch_law_record(api_key, session, "law", pick["mst"], law_name, pick["efYd"],
                            pick["ministry"], pick["prom_num"], pick["prom_date"])
    if rec is not None:
        rec["판본출처"] = f"eflaw MST={pick['mst']} [{pick['status']}] 공포 {pick['prom_date']} 제{pick['prom_num']}호"
    return rec
