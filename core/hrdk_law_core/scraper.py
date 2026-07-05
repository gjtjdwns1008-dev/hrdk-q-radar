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

import re
import time
import xml.etree.ElementTree as ET

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
    text = re.sub(r"(①|②|③|④|⑤|⑥|⑦|⑧|⑨|⑩)", r"\n- **\\1**", text)
    return f"### 📜 {title}\n{text}\n"


def get_base_laws(api_key: str, target_date: str) -> list | None:
    """
    특정 일자의 시행 법령을 수집·정제합니다.

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

    for target_type in ["law", "histlaw"]:
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
                                is_connection_failed = True
                                break
                            print(f"  ⚠️ [재시도 {d_attempt}/3] '{law_name}' 상세조회 실패. 10초 대기...")
                            time.sleep(10)

                    if is_connection_failed or detail_response is None:
                        break

                    # 조문 파싱 및 마크다운 변환
                    detail_root = ET.fromstring(detail_response.text)
                    reason_text = ""
                    for tag in [".//개정이유", ".//제개정이유"]:
                        r_node = detail_root.find(tag)
                        if r_node is not None and r_node.text:
                            reason_text += r_node.text.strip() + "\n"

                    article_1, changed_articles = "", []
                    for jomun in detail_root.findall(".//조문단위"):
                        if jomun.attrib.get("조문여부") == "조문":
                            title = (jomun.find("조문제목").text or "") if jomun.find("조문제목") is not None else ""
                            content = (jomun.find("조문내용").text or "") if jomun.find("조문내용") is not None else ""
                            if "제1조(" in title or "목적" in title:
                                article_1 = clean_to_markdown(title, content)
                            elif "개정" in content or "신설" in content:
                                changed_articles.append(clean_to_markdown(title, content))

                    stars = "\n".join(
                        s.text.strip() for s in detail_root.findall(".//별표내용") if s.text
                    )
                    full_text = f"### 🏢 개정이유\n{reason_text}\n\n"
                    full_text += f"{article_1}\n" if article_1 else ""
                    if changed_articles:
                        full_text += "### 🚨 이번에 바뀐 핵심 조문\n" + "\n".join(changed_articles)
                    else:
                        body = "\n".join(
                            j.text.strip() for j in detail_root.findall(".//조문내용") if j.text
                        )
                        full_text += f"### 🚨 전체 조문\n{body}"
                    if stars:
                        full_text += f"\n\n### ⭐ 별표(자격 기준 등)\n{stars}"

                    all_laws_dict[law_name] = {
                        "법령명": law_name, "시행일자": enforce_date,
                        "소관부처": ministry, "공포번호": prom_num,
                        "공포일자": prom_date, "원본": full_text[:15000],
                        "링크": base_law_link, "스킵여부": False,
                    }
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
