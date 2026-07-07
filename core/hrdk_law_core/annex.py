# -*- coding: utf-8 -*-
"""
annex.py v2.1 — 별표·서식 심층 수집기 (철저 수집판, 2026-07-07)
=============================================================================
v2.0(패턴 수확) 대비 강화점 — "누락 없이" 원칙:
  ① 이중 소스 상시 병합: 법령 상세 XML 수확 + 별표서식 API(licbyl) 수확을
     항상 합치고 URL 중복 제거. (v2.0은 XML 실패 시에만 licbyl 폴백)
     licbyl 결과는 항목 내 '법령명'이 다른 법령이면 걸러냄(동명 오염 방지).
  ② 3포맷 판독: 파일 머리글로 자동 감지 — HWP5(OLE) / HWPX(zip) / PDF.
     (v2.0은 HWP5만 — PDF 전용·HWPX 별표가 '실패'로 새던 구멍 봉합)
  ③ 파일당 재시도 1회: 순간 네트워크 오류로 별표 하나를 놓치지 않게.
설계 원칙(유지): 무절단 — 파일수·본문 절단 없음. 안전밸브만 env
(ANNEX_TOTAL_CHARS 기본 120,000 / ANNEX_MAX_FILES 기본 0=무제한).
안전핀(유지): 모든 실패는 '미확보' 표기 강등 — 배치 무중단.
"""
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

BASE = "https://www.law.go.kr"
TOTAL_CHAR = int(os.environ.get("ANNEX_TOTAL_CHARS", "120000"))
MAX_FILES = int(os.environ.get("ANNEX_MAX_FILES", "0"))
PRIORITY = re.compile(r"자격|인력|기술|선임|배치|기준|검사|교육|안전관리자")
LINK_PAT = re.compile(r"flDownload|flSeq=|\.hwpx?(?:\b|$)|\.pdf(?:\b|$)", re.I)
TITLE_TAG = re.compile(r"제목|명$")
_NSP = lambda x: re.sub(r"[\s\u318D\u00B7\u30FB\u2027]+", "", str(x or ""))


# ── hwp5txt 3단 자가해결 (v1.3.1 계승) ──────────────────────────────────
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


def _clean(txt):
    txt = re.sub(r"<표>", "\n[표]\n", txt)
    return re.sub(r"\n{3,}", "\n\n", txt).strip()


def _extract_hwp5(data: bytes) -> str:
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".hwp", delete=False) as f:
            f.write(data)
            tmp = f.name
        mode = resolve_hwp5txt()
        if mode[0] is None:
            return ""
        return _clean(_run_hwp5txt(mode, tmp))
    except Exception:
        return ""
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass


def _extract_hwpx(data: bytes) -> str:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        parts = []
        for n in sorted(z.namelist()):
            if n.startswith("Contents/section") and n.endswith(".xml"):
                x = z.read(n).decode("utf-8", "ignore")
                for p in re.findall(r"<hp:p [^>]*>(.*?)</hp:p>", x, re.S):
                    t = "".join(re.findall(r"<hp:t[^>]*>(.*?)</hp:t>", p, re.S))
                    if t.strip():
                        parts.append(t)
        s = "\n".join(parts)
        s = s.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
        return _clean(s)
    except Exception:
        return ""


def _extract_pdf(data: bytes) -> str:
    try:
        from pdfminer.high_level import extract_text
        return _clean(extract_text(io.BytesIO(data)) or "")
    except Exception:
        return ""


def extract_any(data: bytes) -> str:
    """파일 머리글로 포맷 자동 감지 → HWP5/HWPX/PDF 텍스트."""
    if not data or len(data) < 8:
        return ""
    head = data[:8]
    if head[:4] == b"PK\x03\x04":               # HWPX (zip)
        return _extract_hwpx(data)
    if head[:5] == b"%PDF-":                     # PDF
        return _extract_pdf(data)
    if head == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":  # OLE → HWP5
        return _extract_hwp5(data)
    return _extract_hwp5(data)                   # 미상 → HWP5 시도


# 하위호환 별칭 (기존 호출부·도구 보호)
def extract_hwp_text(data: bytes) -> str:
    return extract_any(data)


# ── 패턴 수확 엔진 ───────────────────────────────────────────────────────
def _title_near(el, parent):
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
    """XML 전체에서 파일 링크 '값' 패턴 수확 → [(url, title)] (태그명 무관)."""
    out, seen = [], set()
    parent_of = {c: p for p in root.iter() for c in p}
    for el in root.iter():
        v = (el.text or "").strip()
        if not v or not LINK_PAT.search(v):
            continue
        if len(v) > 500 or "\n" in v:
            continue
        url = v if v.startswith("http") else BASE + (v if v.startswith("/") else "/" + v)
        if url in seen:
            continue
        seen.add(url)
        out.append((url, _title_near(el, parent_of.get(el))))
    return out


def licbyl_links(api_key, law_name, http_get_text):
    """별표서식 검색 API 수확 — 항목의 '법령명'이 다르면 배제(동명 오염 방지)."""
    import xml.etree.ElementTree as ET
    from urllib.parse import quote
    want = _NSP(law_name)
    for tgt in ("licbyl", "licByl"):
        try:
            url = (f"{BASE}/DRF/lawSearch.do?OC={api_key}&target={tgt}"
                   f"&type=XML&display=100&query={quote(law_name)}")
            root = ET.fromstring(http_get_text(url))
            out = []
            for item in list(root):
                if len(list(item)) < 2:
                    continue
                bad = False
                for c in item:
                    t = getattr(c, "tag", "")
                    if isinstance(t, str) and "법령명" in t and (c.text or "").strip():
                        if _NSP(c.text) != want:
                            bad = True
                        break
                if bad:
                    continue
                out.extend(harvest_links(item))
            if out:
                return out
        except Exception:
            continue
    return []


def census(root, limit=14):
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
    """반환: (추출 텍스트 섹션, 상태 섹션).
    ★v2.1: XML 수확 ∪ licbyl 수확 상시 병합(중복 URL 제거) + 파일당 재시도 1회."""
    try:
        xml_c = harvest_links(detail_root)
    except Exception:
        xml_c = []
    lic_c = []
    if law_name and api_key:
        try:
            getter = http_get_text or (lambda u: http_get(u).decode("utf-8", "ignore"))
            lic_c = licbyl_links(api_key, law_name, getter)
        except Exception:
            lic_c = []
    seen, cands = set(), []
    for src, pool in (("법령XML", xml_c), ("별표서식API", lic_c)):
        for url, title in pool:
            if url in seen:
                continue
            seen.add(url)
            cands.append((url, title, src))
    if not cands:
        return "", ""

    cands.sort(key=lambda x: 0 if PRIORITY.search(x[1] or "") else 1)
    got, miss, used, tried, srcs = [], [], 0, 0, set()
    for url, title, src in cands:
        if MAX_FILES and tried >= MAX_FILES:
            miss.append((title, f"파일 수 밸브({MAX_FILES}) — ANNEX_MAX_FILES로 확장 가능"))
            continue
        if used >= TOTAL_CHAR:
            miss.append((title, f"분량 안전밸브({TOTAL_CHAR:,}자) — ANNEX_TOTAL_CHARS로 확장 가능"))
            continue
        tried += 1
        txt = ""
        for _attempt in (1, 2):                    # ★재시도 1회
            try:
                data = http_get(url)
                txt = extract_any(data) if data else ""
            except Exception:
                txt = ""
            if txt:
                break
        if txt:
            used += len(txt)
            got.append((title, txt))               # ★무절단
            srcs.add(src)
        else:
            miss.append((title, "다운로드·추출 실패(재시도 포함)"))
    if used >= TOTAL_CHAR:
        print(f"    ⚠️ 별표 분량 안전밸브 발동({used:,}자) — 제외분은 상태 섹션에 표기")

    sec_text = ""
    if got:
        parts = [f"[{t or '별표'}]\n{x}" for t, x in got]
        label = "+".join(sorted(srcs)) or "법령XML"
        sec_text = f"### ⭐ 별표(파일 추출: 자격 기준 등 / 출처 {label})\n" + "\n\n".join(parts)
    sec_status = ""
    if miss:
        lines = [f"- {t or '별표'}: {why}" for t, why in miss]
        sec_status = ("### ⭐ 별표 상태: 파일 전용(내용 미확보)\n"
                      "아래 별표는 파일 확보에 실패하여 본문에 내용이 없음.\n"
                      + "\n".join(lines))
    return sec_text, sec_status
