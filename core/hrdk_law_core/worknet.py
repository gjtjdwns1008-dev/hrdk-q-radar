"""
hrdk_law_core.worknet
---------------------
고용24(워크넷) OpenAPI에서 실시간 구인 건수를 조회합니다.

기존 HRDK-LAW-RADAR의 worknet_api.py를 공유 모듈로 이동한 버전입니다.

사용법:
    from hrdk_law_core.worknet import get_worknet_job_count
    result = get_worknet_job_count("건축기사, 건축산업기사", api_key="...")
    # → "건축기사(142건) | 건축산업기사(45건)"
"""

import xml.etree.ElementTree as ET
import requests


def fetch_single_job_count(cert_name: str, api_key: str) -> str:
    """
    자격증 이름 하나를 받아 워크넷 현재 구인 공고 건수를 반환합니다.

    Parameters
    ----------
    cert_name : 자격증 이름 (예: "전기기사")
    api_key   : 고용24 OpenAPI 인증키

    Returns
    -------
    "N건" 형태의 문자열. 실패 시 "조회실패" 반환.
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
            return "서버에러"

        root = ET.fromstring(response.content)
        total_tag = root.find(".//total") or root.find(".//totalCount")
        if total_tag is not None and total_tag.text:
            return f"{total_tag.text}건"
        return "0건"

    except Exception:
        return "조회실패"


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
    자격증이 없거나 빈 값이면 "-" 반환
    """
    if not certs_string or certs_string.strip() in ["", "없음", "N/A"]:
        return "-"

    cert_list = [c.strip() for c in certs_string.split(",") if c.strip()]
    results = [
        f"{cert}({fetch_single_job_count(cert, api_key)})"
        for cert in cert_list
    ]
    return " | ".join(results)
