"""
hrdk_law_core.worknet
---------------------
고용24(워크넷) OpenAPI에서 실시간 구인 건수를 조회합니다.

기존 HRDK-LAW-RADAR의 worknet_api.py를 공유 모듈로 이동한 버전입니다.

사용법:
    from hrdk_law_core.worknet import get_worknet_job_count
    result = get_worknet_job_count("건축기사, 건축산업기사", api_key="...")
    # → "건축기사(142건) | 건축산업기사(45건)"

★2026-09-16 개정
  1) 스위치: 환경변수 WORKNET_ENABLED 가 "1"/"true"/"on" 이 아니면 API를 부르지 않고
     "미조회(OFF)" 를 기록한다. (기본 OFF — 실시간 조회 배제 결정. Q-Page 연간 효용성
     분석은 한고원 공문 자료를 쓰므로 실시간 값이 당장 필요하지 않음)
  2) 파싱 결함 수정: ElementTree 요소는 자식이 없으면 falsy 로 평가되어
     `root.find(".//total") or root.find(".//totalCount")` 가 정상 응답까지 None 으로 만들었다
     → 대장에 기록된 "(0건)" 498행이 전부 이 결함의 산출값. `is not None` 패턴으로 교정.
  3) HTTP 200 이지만 본문이 인증/서비스 오류 XML인 경우를 감지해 정직 표기
     ("서버에러(본문: …)"). 상태코드만으로는 인증 실패를 알 수 없다.
  4) 포괄형 토큰 [전종목](certs.SCOPE_TOKEN_ALL) 은 종목별 조회를 하지 않는다("미조회(포괄)").
     — 541종목 × 호출 폭주 방지.
"""

import os
import xml.etree.ElementTree as ET
import requests

from .certs import SCOPE_TOKEN_ALL

# 공공데이터 계열 API가 본문에 오류를 담아 보내는 대표 태그들 (상태코드 200 + 오류 XML 패턴)
_ERROR_TAGS = ("returnAuthMsg", "returnReasonCode", "errMsg", "errorMsg", "resultCode", "message")
_OK_CODES = ("", "00", "0", "200", "NORMAL SERVICE", "NORMAL_SERVICE")


def is_enabled() -> bool:
    """실시간 조회 스위치. 환경변수 WORKNET_ENABLED 가 1/true/on 일 때만 켜진다(기본 OFF)."""
    return os.environ.get("WORKNET_ENABLED", "").strip().lower() in ("1", "true", "on", "yes")


def _find_error_in_body(root) -> str:
    """오류 XML 본문이면 짧은 사유 문자열, 정상이면 빈 문자열."""
    for tag in _ERROR_TAGS:
        el = root.find(f".//{tag}")
        if el is None or not (el.text or "").strip():
            continue
        val = el.text.strip()
        if tag in ("returnReasonCode", "resultCode") and val.upper() in _OK_CODES:
            continue
        if tag in ("returnAuthMsg", "message") and val.upper() in _OK_CODES:
            continue
        return f"{tag}={val[:24]}"
    return ""


def fetch_single_job_count(cert_name: str, api_key: str) -> str:
    """
    자격증 이름 하나를 받아 워크넷 현재 구인 공고 건수를 반환합니다.

    Parameters
    ----------
    cert_name : 자격증 이름 (예: "전기기사")
    api_key   : 고용24 OpenAPI 인증키

    Returns
    -------
    "N건" 형태의 문자열. 실패 시 "조회실패" / "서버에러…" 등 정직 표기.
    """
    if not api_key:
        return "인증키 없음"

    url = "https://www.work24.go.kr/cm/openApi/call/wk/callOpenApiSvcInfo210L01.do"
    params = {
        "authKey": api_key,
        "callTp": "L",
        "returnType": "XML",
        "startPage": 1,
        "display": 10,
        "keyword": cert_name,
    }

    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code != 200:
            return f"서버에러({response.status_code})"
        return parse_total(response.content)
    except Exception:
        return "조회실패"


def parse_total(xml_bytes: bytes) -> str:
    """응답 XML 본문 → "N건" / 오류 표기. (네트워크와 분리해 단위 검증 가능하게 둠)"""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return "서버에러(본문: XML 아님)"
    err = _find_error_in_body(root)
    if err:
        return f"서버에러(본문: {err})"
    if str(root.tag).lower().endswith("html"):
        return "서버에러(본문: HTML 응답 — 점검페이지 추정)"
    total_tag = root.find(".//total")
    if total_tag is None:                      # ★ `or` 대신 is None — 텍스트 전용 태그는 falsy
        total_tag = root.find(".//totalCount")
    if total_tag is None:
        return "서버에러(본문: total 태그 없음)"   # 정상 응답에는 항상 total이 있다 — 없으면 0건이 아니라 오류
    if (total_tag.text or "").strip():
        return f"{total_tag.text.strip()}건"
    return "0건"


def get_worknet_job_count(certs_string: str, api_key: str) -> str:
    """
    쉼표로 구분된 자격증 목록 문자열을 받아 종목별 구인 건수를 매쉬업합니다.

    Parameters
    ----------
    certs_string : 쉼표 구분 자격증 목록 (예: "건축기사, 건축산업기사")
    api_key      : 고용24 OpenAPI 인증키

    Returns
    -------
    "건축기사(142건) | 건축산업기사(45건)" 형태의 문자열
    자격증이 없거나 빈 값이면 "-", 스위치 OFF면 "미조회(OFF)", 포괄형이면 "미조회(포괄)"
    """
    if not is_enabled():
        return "미조회(OFF)"
    if not certs_string or certs_string.strip() in ["", "없음", "N/A"]:
        return "-"
    if SCOPE_TOKEN_ALL in certs_string:
        return "미조회(포괄)"

    cert_list = [c.strip() for c in certs_string.split(",") if c.strip()]
    results = [
        f"{cert}({fetch_single_job_count(cert, api_key)})"
        for cert in cert_list
    ]
    return " | ".join(results)
