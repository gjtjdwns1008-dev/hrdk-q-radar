"""게시 전 자체검산 — Q-Page 주입기 검산(§3)의 미러 + 우리 쪽 추가 점검. [공용]

원칙: 여기서 걸리면 Q-Page에서도 걸린다. 우리 손을 떠나기 전에 잡는다.
"""

from __future__ import annotations

import json
import re

from . import catmap

ASOF_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate_export_text(text: str) -> list[str]:
    """문제 목록 반환. 빈 목록 = 통과."""
    problems: list[str] = []

    # §3-검산 1) 파싱 · version 호환 · asof 형식
    try:
        data = json.loads(text)
    except Exception as e:
        return [f"JSON 파싱 실패: {e}"]

    ver = str(data.get("version", ""))
    if not ver.startswith("1."):
        problems.append(f"version 비호환: {ver!r} (1.x 요구)")

    asof = str(data.get("asof", ""))
    if not ASOF_RE.match(asof):
        problems.append(f"asof 형식 오류: {asof!r} (yyyy-mm-dd)")

    gen = str(data.get("generated", ""))
    if asof and not gen.startswith(asof):
        problems.append(f"asof({asof}) ≠ generated 날짜부({gen[:10]}) — §2.2 위반")
    if "+09:00" not in gen:
        problems.append(f"generated 타임존이 KST(+09:00)가 아님: {gen!r}")

    items = data.get("items")
    if not isinstance(items, dict) or not items:
        problems.append("items 비어 있음 또는 형식 오류")
        return problems

    # §3-검산 2) 항목 불변식
    for code, it in items.items():
        where = f"[{code} {it.get('name', '?')}]"
        if not (isinstance(code, str) and code.isdigit()):
            problems.append(f"{where} 종목코드가 숫자 문자열이 아님(검정형만 허용, 앞자리 0 보존)")
        cat = it.get("cat", {})
        if sorted(cat.keys()) != sorted(catmap.JSON_KEYS):
            problems.append(f"{where} cat 키 4종 불일치: {sorted(cat.keys())}")
            continue
        try:
            total = sum(int(cat[k]) for k in catmap.JSON_KEYS)
        except Exception:
            problems.append(f"{where} cat 값이 정수가 아님: {cat}")
            continue
        n = it.get("n")
        if n != total:
            problems.append(f"{where} n({n}) ≠ duty+auth+hr+exempt({total})")
        if not isinstance(n, int) or n <= 0:
            problems.append(f"{where} n>0 위반(0건 종목은 생략)")
        top = it.get("top", [])
        if not isinstance(top, list) or len(top) > 3:
            problems.append(f"{where} top {len(top) if isinstance(top, list) else '?'}건 — 최대 3건")
            continue
        for t in top:
            if t.get("cat") not in catmap.DISPLAY_VALUES:
                problems.append(f"{where} top.cat 표시값 위반: {t.get('cat')!r}")
            if not str(t.get("law", "")).strip():
                problems.append(f"{where} top.law 빈 값")

    # 우리 쪽 추가 점검: 대장값 원형 유출 금지 (§2.3 — 등장 자체가 규격 위반)
    for raw in catmap.FORBIDDEN_RAW_IN_EXPORT:
        if raw in text:
            problems.append(f"대장값 원형 {raw!r} 이 연계 파일에 등장 — §2.3 위반")

    return problems
