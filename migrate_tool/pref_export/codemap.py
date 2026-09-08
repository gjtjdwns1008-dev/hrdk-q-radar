"""종목명 ↔ Q-Net 종목코드 번역 사전. [공용]

정본 원칙(부속서 A-5): 종목코드·연도별 종목명의 정본 = Q-Page 종목마스터.
이 모듈이 읽는 data/qnet_codes.csv 는 그 파생물(열: 종목코드, 연도, 종목명)이며
build_codemap.py 로 마스터 개정 시 연 1회 재생성한다.

연도 세대 변환(부속서 A-5 / 회신 2026-09-07 ②):
  동일 명칭이 연도에 따라 다른 코드를 가리키는 실사례가 있으므로
  (설비보전기능사 = 2024년 6837, 2025년부터 7121도 동명 진입)
  명칭→코드 변환은 반드시 기준연도(base year)를 정해 수행한다.

변환 규칙(결정론):
  ① 기준연도에 그 명칭이 있으면 그 연도의 코드.
  ② 없으면 전 연도 통틀어 후보 코드를 모으되, 기준연도 명칭이 존재하는 코드만 인정.
  ③ ①·②에서 후보 복수 시 검정형(숫자 코드) 우선 — 검정형·과정평가형 동명 쌍 해소.
     검정형끼리도 복수면 미매칭(안전) — 예: '컴퓨터시스템기사'@2024 → 미매칭
     (1321·1322의 2026 통합 예정 명칭이라 2024 화면에 없음: 정답 동작).

엑셀 훼손 방어: 4자리 미만 숫자 코드는 앞자리 0 복원(마스터 불변식: 코드는 4자리).
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path

_HERE = Path(__file__).resolve().parent
DEFAULT_CANDIDATES = (
    _HERE / "data" / "qnet_codes.csv",      # ① 이 부품 상자 옆 (로컬 폴더·레포 공통)
    Path("data") / "qnet_codes.csv",        # ② 실행 위치 기준 (예비)
)
COL_CODE, COL_YEAR, COL_NAME = "종목코드", "연도", "종목명"
DEFAULT_BASE_YEAR = 2024                    # 현행 Q-Page 화면 세대


def _norm(name: str) -> str:
    """매칭용 정규화: 공백·가운뎃점(·) 제거."""
    return (name or "").strip().replace(" ", "").replace("·", "")


def is_certification_code(code: str) -> bool:
    """검정형 코드 여부 — 숫자 시작(과정평가형은 B/E/F 등 문자 시작)."""
    return bool(code) and code[0].isdigit()


def _pick(codes: set[str]) -> str | None:
    """후보 집합에서 검정형 우선 결정론 선택. 검정형 복수면 None(미매칭)."""
    certs = sorted(c for c in codes if is_certification_code(c))
    if len(certs) == 1:
        return certs[0]
    if len(certs) > 1:
        return None
    course = sorted(codes)
    return course[0] if len(course) == 1 else None


def load_maps(path: Path | str | None = None,
              year: int = DEFAULT_BASE_YEAR) -> tuple[dict[str, str], dict[str, str]]:
    """(명칭→코드 사전, 코드→기준연도 표준명) 를 돌려준다.

    반환 사전의 키는 정규화된 명칭(_norm)이며, resolve()가 같은 정규화로 조회한다.
    """
    src = Path(path) if path else next(
        (c for c in DEFAULT_CANDIDATES if c.exists()), DEFAULT_CANDIDATES[0])

    name_year_codes: dict[tuple[str, int], set[str]] = defaultdict(set)
    name_codes: dict[str, set[str]] = defaultdict(set)
    code_year_name: dict[tuple[str, int], str] = {}
    restored = 0

    with open(src, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames and COL_YEAR not in reader.fieldnames:
            sys.exit(f"❌ {src} 가 구버전 형식입니다(연도 열 없음). "
                     f"build_codemap.py 로 종목마스터에서 재생성하세요.")
        for row in reader:
            code = str(row.get(COL_CODE, "")).strip()
            name = str(row.get(COL_NAME, "")).strip()
            try:
                y = int(str(row.get(COL_YEAR, "")).strip())
            except ValueError:
                continue
            if not code or not name:
                continue
            if code.isdigit() and len(code) < 4:
                code = code.zfill(4)          # 앞자리 0 복원 (4자리 불변식)
                restored += 1
            n = _norm(name)
            name_year_codes[(n, y)].add(code)
            name_codes[n].add(code)
            code_year_name[(code, y)] = name

    if restored:
        print(f"⚠️ 종목코드 앞자리 0 복원 {restored}건 — 사전이 엑셀에서 저장된 흔적입니다. "
              f"build_codemap.py 재생성을 권장합니다(열람은 무방, 저장 금지).")

    name2code: dict[str, str] = {}
    for n, all_codes in name_codes.items():
        in_year = name_year_codes.get((n, year))
        if in_year:
            code = _pick(in_year)
        else:
            candidates = {c for c in all_codes if (c, year) in code_year_name}
            code = _pick(candidates)
        if code:
            name2code[n] = code

    canonical = {code: nm for (code, y), nm in code_year_name.items() if y == year}
    return name2code, canonical


def resolve(codemap: dict[str, str], qual_name: str) -> str | None:
    """시트 표기(공백·가운뎃점 차이 흡수)로 코드를 찾는다."""
    return codemap.get(_norm(qual_name))
