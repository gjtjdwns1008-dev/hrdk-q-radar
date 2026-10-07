# -*- coding: utf-8 -*-
"""
annex.py v2.2 — 별표 수집기 (XML 본문 우선·파일 폴백판, 2026-10-07)
=============================================================================
배경(실측, 2026-10-06~07 프로브):
  · 법제처 상세 XML의 <별표단위>에는 별표·서식 모두 <별표내용> 본문이 들어 있었다
    (표본 56개 별표 중 파일 전용 0). v2.1은 그 본문을 두고 HWP·PDF·이미지 파일을
    전부 다시 받았고(법령당 수백~수천 회), 10/3~10/5 일일 배치가 150분을 넘겨 침몰했다.
  · <별표HWP파일명>·<별표PDF파일명>은 파일 '이름' 필드인데 링크로 오인해 404를 두 번씩 두드렸다.
  · 러너의 HWP 추출은 표 내용 없이 '[표]' 표식만 남겨 3~13자 — 실질 본문은 PDF에서 왔다.
  · 7월 "별표 누락"의 실제 원인은 원본 15,000자 상한이 맨 뒤 별표 섹션을 자른 것(7/6 상한 상향으로 해소).
설계 원칙 v2.2 — "누락 최소화 + 수집 효율 최대화":
  ① <별표단위> 단위로 읽는다. <별표구분>이 '서식'이면 다운로드 생략(개수만 상태 표기).
  ② <별표내용> 본문이 있는 별표는 파일을 받지 않는다(본문은 scraper 가 ⭐ 별표 섹션에 이미 수록).
  ③ 본문이 빈 '파일 전용' 별표만 PDF 1회 → 20자 미만이면 HWP 1회. 이미지·파일명은 후보에서 제외.
  ④ 재시도는 통신 예외일 때만 1회. 20자 미만 결과는 성공으로 세지 않는다.
  ⑤ 상태 섹션은 별표 단위 — 받은 별표를 '미확보'로 중복 표기하지 않는다.
  ⑥ <별표단위>가 하나도 없는 옛 구조 XML 에서만 패턴 수확(진짜 경로만) + 별표서식 API 폴백.
안전밸브(유지): ANNEX_MAX_FILES(기본 0=무제한, 운영 200) · ANNEX_TOTAL_CHARS(기본 120,000) · 파일당 예산은 호출자.
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
MIN_TEXT = int(os.environ.get("ANNEX_MIN_TEXT", "20"))        # ★v2.2 이 미만은 '쓰레기 성공'(예: '[표]' 3자) → 실패 취급
PRIORITY = re.compile(r"자격|인력|기술|선임|배치|기준|검사|교육|안전관리자")
LINK_PAT = re.compile(r"flDownload|flSeq=|\.hwpx?(?:\b|$)|\.pdf(?:\b|$)", re.I)
TITLE_TAG = re.compile(r"제목|명$")
FORM_PAT = re.compile(r"서식|별지|신청서|수수료|과태료|대장$|증$")   # 비(非)자격기준류(옛 구조 폴백·표기용)
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


# ── 별표 단위 파서 (★v2.2) ──────────────────────────────────────────────
def is_form_kind(kind: str, title: str = "") -> bool:
    """<별표구분> 공식 값으로 서식 판정. 구분이 비어 있을 때만 제목 패턴으로 보조."""
    k = (kind or "").strip()
    if k:
        return ("서식" in k) or ("별지" in k)
    return bool(FORM_PAT.search(title or ""))


def parse_annex_units(root):
    """<별표단위> → [{kind,num,title,content,hwp,pdf,nimg}] (문서 순서 유지)."""
    out = []
    for u in root.findall(".//별표단위"):
        kind = (u.findtext("별표구분") or "").strip()
        title = (u.findtext("별표제목") or "").strip()
        num = (u.findtext("별표번호") or "").strip().lstrip("0") or "0"
        sub = (u.findtext("별표가지번호") or "").strip().lstrip("0")
        label = f"별표 {num}" + (f"의{sub}" if sub else "")
        imgs = [c for c in u if isinstance(getattr(c, "tag", ""), str)
                and "이미지파일링크" in c.tag and (c.text or "").strip()]
        out.append({
            "kind": kind or ("서식" if is_form_kind("", title) else "별표"),
            "num": label,
            "title": title or label,
            "content": (u.findtext("별표내용") or "").strip(),
            "hwp": (u.findtext("별표서식파일링크") or "").strip(),      # HWP 다운로드 주소
            "pdf": (u.findtext("별표서식PDF파일링크") or "").strip(),   # PDF 다운로드 주소
            "nimg": len(imgs),
        })
    return out


def _abs(link: str) -> str:
    return link if link.startswith("http") else BASE + (link if link.startswith("/") else "/" + link)


def _fetch_text(http_get, link: str):
    """파일 1개 다운로드+추출. 통신 예외만 1회 재시도. 반환 (텍스트, 수신바이트)."""
    for _attempt in (1, 2):
        try:
            data = http_get(_abs(link))
        except Exception:
            if _attempt == 1:
                continue
            return "", 0
        txt = extract_any(data) if data else ""
        return (txt if len(txt) >= MIN_TEXT else ""), (len(data) if data else 0)
    return "", 0


# ── 패턴 수확 엔진 (옛 구조 XML 폴백 전용) ──────────────────────────────
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
    """XML 전체에서 파일 링크 '값' 패턴 수확 → [(url, title)].
    ★v2.2: 진짜 경로(/ 또는 http 로 시작)만 — '○○.hwp' 같은 파일명 필드는 제외, 이미지 링크 제외."""
    out, seen = [], set()
    parent_of = {c: p for p in root.iter() for c in p}
    for el in root.iter():
        v = (el.text or "").strip()
        if not v or not LINK_PAT.search(v):
            continue
        if len(v) > 500 or "\n" in v:
            continue
        if not (v.startswith("/") or v.startswith("http")):
            continue                                          # 파일명 필드(별표HWP파일명 등) 제외
        tag = getattr(el, "tag", "")
        if isinstance(tag, str) and "이미지" in tag:
            continue                                          # 이미지는 추출 불가 → 시도 안 함
        url = _abs(v)
        if url in seen:
            continue
        seen.add(url)
        out.append((url, _title_near(el, parent_of.get(el))))
    return out


def licbyl_links(api_key, law_name, http_get_text):
    """별표서식 검색 API 수확 — 항목의 '법령명'이 다르면 배제(동명 오염 방지). 옛 구조 폴백 전용."""
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
    """반환: (파일에서 추출한 별표 텍스트 섹션, 상태 섹션).
    ★v2.2: 본문이 있는 별표는 받지 않고, 파일 전용 별표만 PDF→HWP 순으로 받는다."""
    import time as _pt
    _t_law = _pt.monotonic()
    try:
        units = parse_annex_units(detail_root)
    except Exception:
        units = []

    if units:
        forms = [u for u in units if is_form_kind(u["kind"], u["title"])]
        byul = [u for u in units if not is_form_kind(u["kind"], u["title"])]
        with_text = [u for u in byul if u["content"]]
        file_only = [u for u in byul if not u["content"]]
        # 자격 기준류 제목을 앞에 — 밸브가 걸려도 자격 기준 별표가 먼저 확보되게
        file_only.sort(key=lambda u: 0 if PRIORITY.search(u["title"]) else 1)
        print(f"    📎 별표 {len(byul)}개 — XML 본문 {len(with_text)} · 파일 전용 {len(file_only)}"
              f" · 서식 {len(forms)}개 수집 생략")
        got, miss, used, tried = [], [], 0, 0
        for u in file_only:
            links = [l for l in (u["pdf"], u["hwp"]) if l]         # PDF 우선, HWP 폴백
            if not links:
                miss.append((u["title"], f"HWP·PDF 링크 없음(이미지 {u['nimg']}장) — 현 기술로 판독 불가"))
                continue
            if MAX_FILES and tried >= MAX_FILES:
                miss.append((u["title"], f"파일 수 밸브({MAX_FILES}) — ANNEX_MAX_FILES로 확장 가능"))
                continue
            if used >= TOTAL_CHAR:
                miss.append((u["title"], f"분량 안전밸브({TOTAL_CHAR:,}자) — ANNEX_TOTAL_CHARS로 확장 가능"))
                continue
            txt = ""
            _t_f = _pt.monotonic()
            for link in links:
                tried += 1
                txt, nbytes = _fetch_text(http_get, link)
                if txt:
                    break
            if txt:
                used += len(txt)
                got.append((u["title"], txt))
                print(f"      ⬇ {u['num']} {u['title'][:26]} ✓ {len(txt):,}자 ({_pt.monotonic()-_t_f:.1f}s)")
            else:
                miss.append((u["title"], "다운로드·추출 실패(PDF→HWP, 재시도 포함)"))
                print(f"      ⬇ {u['num']} {u['title'][:26]} ✗ 미확보 ({_pt.monotonic()-_t_f:.1f}s)")
        if file_only:
            print(f"    📎 파일 전용 별표 수집 종료 — 확보 {len(got)} · 미확보 {len(miss)} · {used:,}자"
                  f" · {_pt.monotonic()-_t_law:.0f}s")

        sec_text = ""
        if got:
            parts = [f"[{t or '별표'}]\n{x}" for t, x in got]
            sec_text = "### ⭐ 별표(파일 추출: 자격 기준 등 / 출처 법령XML 파일)\n" + "\n\n".join(parts)
        lines = [f"- 별표 {len(byul)}개: 본문 수록 {len(with_text) + len(got)}"
                 + (f"(XML {len(with_text)} + 파일 {len(got)})" if got else "(모두 XML 본문)")
                 + (f" · 미확보 {len(miss)}" if miss else "")]
        if forms:
            lines.append(f"- 서식 {len(forms)}개: 수집 생략 (행정 양식 — 자격 기준 판정과 무관)")
        if miss:
            lines.append("- 아래 별표는 파일 전용(내용 미확보) — 본문에 내용이 없음:")
            for t, why in miss:
                lines.append(f"  · {t or '별표'}: {why}")
        sec_status = "### ⭐ 별표 상태\n" + "\n".join(lines)
        return sec_text, sec_status

    # ── 옛 구조 XML(별표단위 없음) 폴백: 패턴 수확(진짜 경로만) ∪ 별표서식 API ──
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
    print(f"    📎 (옛 구조 XML) 별표 파일 후보 {len(cands)}개 — 다운로드 시작")
    got, miss, used, tried, done_titles = [], [], 0, 0, set()
    for url, title, src in cands:
        key = _NSP(title)
        if key in done_titles:
            continue                                            # 같은 별표의 다른 포맷은 생략
        if MAX_FILES and tried >= MAX_FILES:
            miss.append((title, f"파일 수 밸브({MAX_FILES}) — ANNEX_MAX_FILES로 확장 가능"))
            continue
        if used >= TOTAL_CHAR:
            miss.append((title, f"분량 안전밸브({TOTAL_CHAR:,}자) — ANNEX_TOTAL_CHARS로 확장 가능"))
            continue
        tried += 1
        _t_f = _pt.monotonic()
        txt, _ = _fetch_text(http_get, url)
        if txt:
            used += len(txt)
            got.append((title, txt))
            done_titles.add(key)
            print(f"      ⬇ [{tried}] {src} {str(title or '')[:26]} ✓ {len(txt):,}자 ({_pt.monotonic()-_t_f:.1f}s)")
        else:
            print(f"      ⬇ [{tried}] {src} {str(title or '')[:26]} ✗ 실패 ({_pt.monotonic()-_t_f:.1f}s)")
    miss_titles = {_NSP(t) for t, _ in miss}
    for url, title, src in cands:                               # 어떤 포맷으로도 못 받은 별표만 미확보
        k = _NSP(title)
        if k not in done_titles and k not in miss_titles:
            miss.append((title, "다운로드·추출 실패(재시도 포함)")); miss_titles.add(k)
    print(f"    📎 별표 수집 종료 — 성공 {len(got)} · 미확보 {len(miss)} · {used:,}자 · {_pt.monotonic()-_t_law:.0f}s")
    sec_text = ""
    if got:
        parts = [f"[{t or '별표'}]\n{x}" for t, x in got]
        sec_text = "### ⭐ 별표(파일 추출: 자격 기준 등 / 출처 법령XML)\n" + "\n\n".join(parts)
    sec_status = ""
    if miss:
        lines = []
        for t, why in miss:
            tag = " (서식류 — 자격 기준 아님)" if FORM_PAT.search(t or "") else ""
            lines.append(f"- {t or '별표'}: {why}{tag}")
        sec_status = ("### ⭐ 별표 상태: 파일 전용(내용 미확보)\n"
                      "아래 별표는 파일 확보에 실패하여 본문에 내용이 없음.\n"
                      "(참고: '서식류' 표시 항목은 행정 양식 — 자격 기준 판정과 무관)\n"
                      + "\n".join(lines))
    return sec_text, sec_status
