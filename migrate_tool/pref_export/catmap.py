"""§2.3 성격 표기 사전 — 규격 v1.3의 단일 소스. [공용 — 양쪽 진입점이 import]

대장값(원본) → (JSON `cat` 키, 표시값).
- '기타'는 연계 파일에서 제외한다(집계 n에도 불포함 — §2.2).
- 연계 파일(CSV·JSON)에는 표시값만 사용. 대장값 원형('직무권한부여')이 파일에
  등장하면 규격 위반이며 Q-Page 검산이 실패 처리한다.
- 표시값 변경은 규격서 개정(v1.4~)으로만 가능하므로, 이 파일 밖에 정의를 두지 않는다.
"""

from __future__ import annotations

# 대장값 → (json_key, 표시값)
CAT_MAP: dict[str, tuple[str, str]] = {
    "의무고용": ("duty", "의무고용"),
    "직무권한부여": ("auth", "직무권한"),   # ← 표시값 축약 매핑 (v1.3 개정 1호)
    "인사우대": ("hr", "인사우대"),
    "시험면제": ("exempt", "시험면제"),
}

JSON_KEYS: tuple[str, ...] = ("duty", "auth", "hr", "exempt")
DISPLAY_VALUES: tuple[str, ...] = tuple(disp for _, disp in CAT_MAP.values())
EXCLUDED_CATS: tuple[str, ...] = ("기타",)

# 연계 파일 텍스트에 등장하면 안 되는 대장값 원형 (검산에서 전문 스캔)
FORBIDDEN_RAW_IN_EXPORT: tuple[str, ...] = ("직무권한부여",)


def to_json_key(raw: str) -> str | None:
    """대장값 → JSON cat 키. 제외 대상('기타')이나 미등록 값이면 None."""
    hit = CAT_MAP.get((raw or "").strip())
    return hit[0] if hit else None


def to_display(raw: str) -> str | None:
    """대장값 → 표시값. 미등록이면 None."""
    hit = CAT_MAP.get((raw or "").strip())
    return hit[1] if hit else None
