"""
scrape_store.py
----------------
스크랩(법제처 수집) 결과를 디스크에 JSON으로 저장하고 읽어옵니다.

설계 의도:
  - 법제처는 '리스트 받기'까지만 두드림 → 한 번 성공하면 JSON으로 저장
  - 분석 단계는 이 JSON을 읽어 AI 분석만 수행 (법제처 재호출 불필요)
  - 스크랩과 분석을 분리하여, 법제처가 막혀도 분석은 진행 가능하게 함

저장 위치: scraped_data/{YYYYMMDD}.json
  - 같은 날짜가 이미 있으면 스크랩을 건너뜀 (한 번 받으면 사라지지 않음)
"""

import os
import json
from pathlib import Path

# 저장 디렉터리 (레포 루트 기준)
SCRAPE_DIR = os.environ.get("SCRAPE_DIR", "scraped_data")


def _path(date_str: str) -> Path:
    Path(SCRAPE_DIR).mkdir(parents=True, exist_ok=True)
    return Path(SCRAPE_DIR) / f"{date_str}.json"


def is_scraped(date_str: str) -> bool:
    """해당 일자의 스크랩 결과가 이미 저장돼 있는지."""
    p = _path(date_str)
    return p.exists() and p.stat().st_size > 0


def save_scraped(date_str: str, laws: list) -> None:
    """스크랩한 법령 리스트를 JSON으로 저장.
    laws가 None(연결 실패)이면 저장하지 않음 (가짜 0건 방지)."""
    if laws is None:
        return
    p = _path(date_str)
    payload = {
        "date": date_str,
        "count": len(laws),
        "laws": laws,
    }
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def load_scraped(date_str: str) -> list | None:
    """저장된 법령 리스트를 읽어 반환. 없으면 None."""
    p = _path(date_str)
    if not p.exists():
        return None
    try:
        with open(p, encoding="utf-8") as f:
            payload = json.load(f)
        return payload.get("laws", [])
    except Exception as e:
        print(f"  ⚠️ 스크랩 데이터 읽기 실패 ({date_str}): {e}")
        return None


def list_scraped_dates() -> list[str]:
    """저장된 모든 스크랩 일자 목록 (오름차순)."""
    d = Path(SCRAPE_DIR)
    if not d.exists():
        return []
    dates = []
    for p in d.glob("*.json"):
        name = p.stem  # YYYYMMDD
        if name.isdigit() and len(name) == 8:
            dates.append(name)
    return sorted(dates)


def delete_scraped(date_str: str) -> None:
    """분석 완료 후 스크랩 파일을 정리하고 싶을 때 (선택)."""
    p = _path(date_str)
    if p.exists():
        p.unlink()
