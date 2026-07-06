# -*- coding: utf-8 -*-
"""
annex.py v2.0 — 별표·서식 심층 수집기 (패턴 수확 방식, 2026-07-06)
=============================================================================
v1의 한계: 별표 링크가 담긴 XML '태그 이름'을 추정에 의존 → 실전 0건.
v2 전략: 태그 이름을 믿지 않는다. 상세 XML 전체를 훑어 '값'이 파일 링크
패턴(flDownload / flSeq= / .hwp)인 요소를 전부 수확한다. 태그가 무엇이든
법제처 파일서버 주소의 생김새는 같기 때문이다.
2차 소스: 법령 XML에서 못 찾으면 별표서식 전용 검색 API(target=licbyl)를
같은 패턴 수확으로 시도한다.

설계 원칙(유지): 무절단 — 파일수·본문 절단 없음. 안전밸브만 env
(ANNEX_TOTAL_CHARS 기본 120,000 / ANNEX_MAX_FILES 기본 0=무제한).
안전핀(유지): 모든 실패는 '미확보' 표기로 강등 — 배치를 멈추지 않는다.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BASE = "https://www.law.go.kr"
TOTAL_CHAR = int(os.environ.get("ANNEX_TOTAL_CHARS", "120000"))
MAX_FILES = int(os.environ.get("ANNEX_MAX_FILES", "0"))
PRIORITY = re.compile(r"자격|인력|기술|선임|배치|기준|검사|교육|안전관리자")
LINK_PAT = re.compile(r"flDownload|flSeq=|\.hwp(?:\b|$)", re.I)
TITLE_TAG = re.compile(r"제목|명$")


# ── hwp5txt 3단 자가해결 (v1.3.1 유지) ─────────────────────────────────
def resolve_hwp5txt():
    exe = shutil.which("hwp5txt")
    if exe:
        return ("cmd", [exe])
    sib = Path(sys.executable).parent / ("Scripts" if os.name == "nt" else "bin")
    cand = sib / ("hwp5txt.exe" if os.name == "nt" else "hwp5txt")
    if cand.exists():
        return ("cmd", [str(cand)])
    try:
        from hwp5.hwp5txt import TextTransform  # noqa: F401
        return ("api",)
    except Exception as e:
        return (None, f"pyhwp 미설치/로딩 실패({str(e)[:40]})")


def _run_hwp5txt(mode, tmp):
    if mode[0] == "cmd":
        r = subprocess.run(mode[1] + [tmp], capture_output=True, timeout=120)
        return r.stdout.decode("utf-8", errors="ignore")
    if mode[0] == "api":
        from contextlib import closing
        from hwp5.hwp5txt import TextTransform
        from hwp5.xmlmodel import Hwp5File
        out = tmp + ".txt"
        tt = TextTransform()
        try:
            with closing(Hwp5File(tmp)) as h, open(out, "wb") as d:
                tt.transform_hwp5_to_text(h, d)
        except TypeError:
            with closing(Hwp5File(tmp)) as h, open(out, "w", encoding="utf-8") as d:
                tt.transform_hwp5_to_text(h, d)
        try:
            return open(out, encoding="utf-8", errors="ignore").read()
        finally:
            if os.path.exists(out):
                os.remove(out)
    return ""


def extract_hwp_text(data: bytes) -> str:
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


# ── 패턴 수확 엔진 ───────────────────────────────────────────────────────
def _title_near(el, parent):
    """링크 요소 주변에서 제목 후보 찾기: 같은 부모의 '…제목/…명' 태그 우선."""
    if parent is not None:
        for c in parent:
            t = getattr(c, "tag", "")
            if isinstance(t, str) and TITLE_TAG.search(t) and (c.text or "").strip():
                return c.text.strip()
        num = ""
        for c in parent:
            t = getattr(c, "tag", "")
            if isinstance(t, str) and t.endswith("번호") and (c.text or "").strip():
                num = c.text.strip()
                break
        if num:
            return f"별표{num}"
    return "별표"


def harvest_links(root):
    """XML 전체에서 파일 링크 '값' 패턴 수확 → [(url, title)], 태그명 무관."""
    out, seen = [], set()
    parent_of = {c: p for p in root.iter() for c in p}
    for el in root.iter():
        v = (el.text or "").strip()
        if not v or not LINK_PAT.search(v):
            continue
        if len(v) > 500 or "\n" in v:      # 본문 문장 속 우연 매치 방지
            continue
        url = v if v.startswith("http") else BASE + (v if v.startswith("/") else "/" + v)
        if url in seen:
            continue
        seen.add(url)
        out.append((url, _title_near(el, parent_of.get(el))))
    return out


def licbyl_links(api_key, law_name, http_get_text):
    """2차 소스: 별표서식 검색 API(target=licbyl)에서 같은 방식으로 수확."""
    import xml.etree.ElementTree as ET
    from urllib.parse import quote
    for tgt in ("licbyl", "licByl"):
        try:
            url = (f"{BASE}/DRF/lawSearch.do?OC={api_key}&target={tgt}"
                   f"&type=XML&display=50&query={quote(law_name)}")
            body = http_get_text(url)
            root = ET.fromstring(body)
            got = harvest_links(root)
            if got:
                return got
        except Exception:
            continue
    return []


def census(root, limit=14):
    """진단용: 별표/서식/파일/링크 관련 태그 분포 + 링크 후보 요약 문자열."""
    tags = {}
    for el in root.iter():
        t = getattr(el, "tag", "")
        if isinstance(t, str) and re.search(r"별표|서식|파일|링크", t):
            tags[t] = tags.get(t, 0) + 1
    lines = [f"<{k}> ×{v}" for k, v in sorted(tags.items())[:limit]]
    hits = harvest_links(root)
    lines.append(f"[패턴 수확 링크] {len(hits)}건")
    for u, t in hits[:5]:
        lines.append(f"  · {t} → {u[:90]}")
    return "\n".join(lines) if lines else "(별표/파일 관련 태그 없음)"


# ── 본체 ────────────────────────────────────────────────────────────────
def build_annex_sections(detail_root, http_get, law_name=None, api_key=None,
                         http_get_text=None):
    """반환: (추출 텍스트 섹션, 상태 섹션). http_get(url)->bytes 주입.
    1차: 상세 XML 패턴 수확 → 2차: licbyl API (law_name·api_key 제공 시)."""
    try:
        cands = harvest_links(detail_root)
    except Exception:
        cands = []
    src = "법령XML"
    if not cands and law_name and api_key:
        try:
            getter = http_get_text or (lambda u: http_get(u).decode("utf-8", "ignore"))
            cands = licbyl_links(api_key, law_name, getter)
            src = "별표서식API"
        except Exception:
            cands = []
    if not cands:
        return "", ""

    cands.sort(key=lambda x: 0 if PRIORITY.search(x[1] or "") else 1)
    got, miss, used, tried = [], [], 0, 0
    for url, title in cands:
        if MAX_FILES and tried >= MAX_FILES:
            miss.append((title, f"파일 수 밸브({MAX_FILES}) — ANNEX_MAX_FILES로 확장 가능"))
            continue
        if used >= TOTAL_CHAR:
            miss.append((title, f"분량 안전밸브({TOTAL_CHAR:,}자) — ANNEX_TOTAL_CHARS로 확장 가능"))
            continue
        tried += 1
        try:
            data = http_get(url)
            txt = extract_hwp_text(data) if data else ""
        except Exception:
            txt = ""
        if txt:
            used += len(txt)
            got.append((title, txt))          # ★무절단
        else:
            miss.append((title, "다운로드·추출 실패"))
    if used >= TOTAL_CHAR:
        print(f"    ⚠️ 별표 분량 안전밸브 발동({used:,}자) — 제외분은 상태 섹션에 표기")

    sec_text = ""
    if got:
        parts = [f"[{t or '별표'}]\n{x}" for t, x in got]
        sec_text = (f"### ⭐ 별표(파일 추출: 자격 기준 등 / 출처 {src})\n"
                    + "\n\n".join(parts))
    sec_status = ""
    if miss:
        lines = [f"- {t or '별표'}: {why}" for t, why in miss]
        sec_status = ("### ⭐ 별표 상태: 파일 전용(내용 미확보)\n"
                      "아래 별표는 파일 확보에 실패하여 본문에 내용이 없음.\n"
                      + "\n".join(lines))
    return sec_text, sec_status
