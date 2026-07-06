# -*- coding: utf-8 -*-
"""
annex.py — 별표·서식 심층 수집기 (2026-07-06 재발방지 조치)
=============================================================================
배경: 법제처 API의 <별표내용>은 텍스트 변환본이 있는 별표만 채워지고,
HWP/PDF 파일 전용 별표는 링크만 온다. 자격 기준(선임·배치 인력표)은
별표에 몰려 있어, 미수집 시 AI가 "별표가 본문에 없다"는 검토사유를 남발했다.

설계 원칙 — 분석 완전성 우선 (2026-07-06 확정):
  · 파일 수 제한 없음, 별표 본문 절단 없음. 자격 기준이 사는 곳을 자르지 않는다.
  · Gemini Flash 컨텍스트(100만 토큰) 대비 수십 배 여유 — 절단의 실익이 없음.
  · 유일한 예외 = 병리적 문서용 '안전밸브'(기본 120,000자, 환경변수
    ANNEX_TOTAL_CHARS로 확장). 발동 시 자격 우선순위로 채우고,
    제외분은 제목을 사실 표기 + 로그 경고. 정책이 아니라 서킷브레이커다.

안전핀: 모든 단계 try/except — 어떤 실패도 일일 배치를 멈추지 않고
        '미확보' 표기로 강등될 뿐이다. (해외 IP 차단·필드명 변화 대비)
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BASE = "https://www.law.go.kr"
# 안전밸브(서킷브레이커) — 기본값은 실존 별표가 닿지 않는 높이
TOTAL_CHAR = int(os.environ.get("ANNEX_TOTAL_CHARS", "120000"))
MAX_FILES = int(os.environ.get("ANNEX_MAX_FILES", "0"))  # 0 = 무제한
PRIORITY = re.compile(r"자격|인력|기술|선임|배치|기준|검사|교육|안전관리자")

# 링크 필드명 후보 (법제처 XML 변형 대비 방어적 탐색)
_LINK_KEYS = ("별표서식파일링크", "별표HWP파일링크", "별표파일링크")
_TITLE_KEYS = ("별표제목", "별표명")
_NUM_KEYS = ("별표번호",)


def _child_text(unit, names):
    for n in names:
        el = unit.find(n)
        if el is not None and (el.text or "").strip():
            return el.text.strip()
    return ""


def _iter_units(detail_root):
    """별표 단위 노드 수집 — 표준 태그 우선, 변형 태그 방어."""
    units = detail_root.findall(".//별표단위")
    if units:
        return units
    out = []
    for el in detail_root.iter():
        tag = getattr(el, "tag", "")
        if isinstance(tag, str) and tag.startswith("별표") and tag != "별표내용":
            if el.find("별표내용") is not None or any(el.find(k) is not None for k in _LINK_KEYS):
                out.append(el)
    return out


def resolve_hwp5txt():
    """hwp5txt 실행 수단 3단 자가해결 (PATH 미등록 환경 대응, 2026-07-06):
    ① PATH → ② 현재 파이썬의 Scripts/bin 폴더 → ③ 파이썬 내부 모듈 구동(runpy).
    반환: ("cmd", [실행리스트]) | ("runpy",) | (None, 사유)"""
    exe = shutil.which("hwp5txt")
    if exe:
        return ("cmd", [exe])
    sib = Path(sys.executable).parent / ("Scripts" if os.name == "nt" else "bin")
    cand = sib / ("hwp5txt.exe" if os.name == "nt" else "hwp5txt")
    if cand.exists():
        return ("cmd", [str(cand)])
    try:
        from hwp5.hwp5txt import TextTransform  # noqa: F401 — 패키지만 있으면 API 직결
        return ("api",)
    except Exception as e:
        return (None, f"pyhwp 미설치/로딩 실패({str(e)[:40]})")


def _run_hwp5txt(mode, tmp):
    if mode[0] == "cmd":
        r = subprocess.run(mode[1] + [tmp], capture_output=True, timeout=120)
        return r.stdout.decode("utf-8", errors="ignore")
    if mode[0] == "api":
        # pyhwp 파이썬 API 직결 (PATH·콘솔스크립트 완전 무관)
        from contextlib import closing
        from hwp5.hwp5txt import TextTransform
        from hwp5.xmlmodel import Hwp5File
        out = tmp + ".txt"
        tt = TextTransform()
        try:
            with closing(Hwp5File(tmp)) as h, open(out, "wb") as d:
                tt.transform_hwp5_to_text(h, d)
        except TypeError:  # 일부 판은 텍스트 스트림을 기대
            with closing(Hwp5File(tmp)) as h, open(out, "w", encoding="utf-8") as d:
                tt.transform_hwp5_to_text(h, d)
        try:
            return open(out, encoding="utf-8", errors="ignore").read()
        finally:
            if os.path.exists(out):
                os.remove(out)
    return ""


def extract_hwp_text(data: bytes) -> str:
    """HWP 바이트 → 텍스트 (pyhwp의 hwp5txt 사용). 실패 시 ""."""
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".hwp", delete=False) as f:
            f.write(data)
            tmp = f.name
        mode = resolve_hwp5txt()
        if mode[0] is None:
            return ""
        txt = _run_hwp5txt(mode, tmp)
        txt = re.sub(r"<표>", "\n[표]\n", txt)
        txt = re.sub(r"\n{3,}", "\n\n", txt).strip()
        return txt
    except Exception:
        return ""
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass


def build_annex_sections(detail_root, http_get):
    """반환: (추출 텍스트 섹션, 상태 표기 섹션) — 둘 다 "" 가능.
    http_get(url) -> bytes  (scraper의 세션·재시도 재사용을 위해 주입)"""
    try:
        units = _iter_units(detail_root)
    except Exception:
        units = []
    if not units:
        return "", ""

    file_only = []
    for u in units:
        try:
            has_text = bool(_child_text(u, ("별표내용",)))
            link = _child_text(u, _LINK_KEYS)
            if has_text:
                continue
            file_only.append((link or None, _title(u)))
        except Exception:
            continue
    if not file_only:
        return "", ""

    # 자격 관련 제목 우선 정렬 (안전밸브 발동 시에도 중요한 것부터 채워지도록)
    file_only.sort(key=lambda x: 0 if PRIORITY.search(x[1] or "") else 1)

    got, miss, used, tried = [], [], 0, 0
    for link, title in file_only:
        if link is None:
            miss.append((title, "다운로드 링크 없음(PDF 전용 추정)"))
            continue
        if MAX_FILES and tried >= MAX_FILES:
            miss.append((title, f"파일 수 밸브({MAX_FILES}) 초과 — ANNEX_MAX_FILES로 확장 가능"))
            continue
        if used >= TOTAL_CHAR:
            miss.append((title, f"분량 안전밸브({TOTAL_CHAR:,}자) 초과 — ANNEX_TOTAL_CHARS로 확장 가능"))
            continue
        tried += 1
        url = link if link.startswith("http") else BASE + link
        try:
            data = http_get(url)
            txt = extract_hwp_text(data) if data else ""
        except Exception:
            txt = ""
        if txt:
            used += len(txt)
            got.append((title, txt))  # ★무절단 — 별표 본문은 자르지 않는다
        else:
            miss.append((title, "다운로드·추출 실패"))
    if used >= TOTAL_CHAR:
        print(f"    ⚠️ 별표 분량 안전밸브 발동({used:,}자) — 제외분은 상태 섹션에 표기")

    sec_text = ""
    if got:
        parts = [f"[{t or '별표'}]\n{x}" for t, x in got]
        sec_text = "### ⭐ 별표(파일 추출: 자격 기준 등)\n" + "\n\n".join(parts)
    sec_status = ""
    if miss:
        lines = [f"- {t or '별표'}: {why}" for t, why in miss]
        sec_status = ("### ⭐ 별표 상태: 파일 전용(내용 미확보)\n"
                      "아래 별표는 파일로만 제공되거나 확보에 실패하여 본문에 내용이 없음.\n"
                      + "\n".join(lines))
    return sec_text, sec_status


def _title(u):
    num = _child_text(u, _NUM_KEYS)
    t = _child_text(u, _TITLE_KEYS)
    return f"별표{num} {t}".strip() if (num or t) else "별표"
