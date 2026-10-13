# -*- coding: utf-8 -*-
"""
probe_annex.py v2.1 — 별표 수집 설계 판정 프로브 (1회용, 국내 IP 로컬 실행)
=============================================================================
v1(2026-07)은 "별표가 어떤 태그로 오나"를 확인하는 프로브였다.
v2(2026-10-06)는 "별표 누락이 어디서 생길 수 있나"를 네 경로로 실측해 설계 판정표를 뽑는다.

  [경로1] 파일 전용 별표  : <별표내용>이 비어 있어 파일을 받아야만 본문이 생기는 별표 (7월 누락의 원인)
  [경로2] XML 본문의 충실도: <별표내용>이 있어도 파일과 내용이 다르면(반쪽이면) 그것도 누락 → 표본 대조
  [경로3] 이미지 전용 별표 : HWP/PDF 링크 없이 이미지만 있는 별표 (현 기술로 못 읽음 → 정직 신고 대상)
  [경로4] API 전용 별표   : 법령 XML엔 없고 별표서식 검색 API(licbyl)에만 있는 별표

덤으로 현행 수집기(annex v2.1)가 이 법령에서 몇 번 다운로드를 시도하는지와,
설계안 v2.2(파일 전용 별표만 HWP→PDF 순 1~2회)의 시도 수를 나란히 센다.

실행:
  python probe_annex.py                                  ← 기본 3개 법령(별표 부자·서식 부자·10/2 문제 법령 추정)
  python probe_annex.py "법령명" ["법령명2" ...]           ← 원하는 법령들
  python probe_annex.py --no-download "법령명"             ← 파일 다운로드(표본 대조) 생략, XML만
  python probe_annex.py --samples 5 "법령명"               ← 표본 대조 개수(기본 3)
준비물: 레포 루트 local_run/local.env에 LAW_API_KEY (backfill 도구들과 동일). 레포 core/ 폴더를 설치본보다 먼저 쓴다.
"""
import sys
import re
import shutil
import difflib
import time
from pathlib import Path
import xml.etree.ElementTree as ET
import requests

try:                                   # Windows 콘솔(cp949)에서 이모지 출력 깨짐 방지
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "core"))   # ★레포 core를 설치본(구판일 수 있음)보다 우선
import backfill_usage as bu  # .env 로딩 재사용

H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
BASE = "https://www.law.go.kr"
KEY = bu._ENV.get("LAW_API_KEY", "")

# ── 인자 ──────────────────────────────────────────────────────────────────
args = sys.argv[1:]
NO_DL = "--no-download" in args
SAMPLES = 3
if "--samples" in args:
    i = args.index("--samples")
    try:
        SAMPLES = int(args[i + 1]); del args[i:i + 2]
    except Exception:
        del args[i]
args = [a for a in args if not a.startswith("--")]
NAMES = args or ["위험물안전관리법 시행규칙",      # 별표 부자(79개, 본문 채움)
                 "국가기술자격법 시행규칙",        # 서식 부자 → <별표구분>의 서식 표기 실물 확인
                 "통신비밀보호법 시행령"]          # 10/2 문제 법령 추정(시행규칙 검색 0건 → 시행령일 가능성)

# 현행 annex v2.1 과 동일한 규칙(시뮬레이션용).
# ★v2.1(10/07): 설치본(구판)이 끼어드는 문제 → 레포 core/annex.py 를 '파일 경로로 직접' 읽는다.
#   레포 폴더에 core/ 가 없으면 설치본을 쓰고, 그것도 annex 가 없으면 규칙 사본 + 자체 PDF 추출기로 진행.
import importlib.util
def _load_repo_module(name):
    f = REPO / "core" / "hrdk_law_core" / f"{name}.py"
    if not f.exists():
        return None
    spec = importlib.util.spec_from_file_location(f"repo_{name}", f)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod
AX = _load_repo_module("annex")
CORE_SRC = "레포 core/ (파일 직접 로드)" if AX else None
if AX is None:
    try:
        from hrdk_law_core import annex as AX
        CORE_SRC = f"설치본 {Path(AX.__file__)}"
    except Exception:
        AX = None
if AX is not None:
    LINK_PAT, FORM_PAT, PRIORITY = AX.LINK_PAT, AX.FORM_PAT, AX.PRIORITY
    harvest_links, extract_any = AX.harvest_links, AX.extract_any
    CORE_INFO = f"annex ✓  {CORE_SRC}"
else:
    LINK_PAT = re.compile(r"flDownload|flSeq=|\.hwpx?(?:\b|$)|\.pdf(?:\b|$)", re.I)
    FORM_PAT = re.compile(r"서식|별지|신청서|수수료|과태료|대장$|증$")
    PRIORITY = re.compile(r"자격|인력|기술|선임|배치|기준|검사|교육|안전관리자")
    harvest_links = None
    def extract_any(data: bytes) -> str:      # 자체 PDF 추출기(표본 대조는 PDF 우선이라 이것으로 충분)
        try:
            import io
            from pdfminer.high_level import extract_text
            if data[:5] == b"%PDF-":
                return (extract_text(io.BytesIO(data)) or "").strip()
        except Exception:
            pass
        return ""
    CORE_INFO = ("🚨 레포 폴더에 core/ 가 없고 설치본도 구판 — 규칙 사본 + 자체 PDF 추출기로 진행 "
                 "(로컬 폴더가 GitHub 레포와 다르다는 뜻: 아래 '환경' 참고)")

QUAL_PAT = re.compile(r"기술사|기능장|산업기사|기사|기능사|국가기술자격|자격")   # 자격 신호(경로2 대조용)


def _norm(s):   # 공백·구두점 제거(비교용)
    return re.sub(r"[\s\u318D\u00B7\u30FB\u2027\W_]+", "", str(s or ""))


def search_law(name):
    """법령명 → (법령명, MST, 시행일) / 없으면 후보 목록 출력 후 None"""
    r = requests.get(f"{BASE}/DRF/lawSearch.do", headers=H, timeout=30,
                     params={"OC": KEY, "target": "law", "type": "XML", "query": name, "display": 20})
    root = ET.fromstring(r.text)
    laws = root.findall(".//law")
    exact = [l for l in laws if _norm(l.findtext("법령명한글", "")) == _norm(name)]
    pick = exact[0] if exact else (laws[0] if laws else None)
    if pick is None:
        # 폴백: 뒷단어(시행규칙/시행령 등) 떼고 재검색 → 후보만 보여줌
        stem = re.sub(r"\s*(시행규칙|시행령|규칙|령)\s*$", "", name).strip()
        r2 = requests.get(f"{BASE}/DRF/lawSearch.do", headers=H, timeout=30,
                          params={"OC": KEY, "target": "law", "type": "XML", "query": stem, "display": 20})
        cands = ET.fromstring(r2.text).findall(".//law")
        print(f"❌ '{name}' 검색 결과 없음. '{stem}' 후보:")
        for c in cands[:12]:
            print(f"     · {c.findtext('법령명한글','')}  [{c.findtext('법령구분명','')}]  시행 {c.findtext('시행일자','')}")
        if not cands:
            print("     (후보 없음)")
        return None
    if not exact:
        print(f"ℹ️ 정확히 일치하는 이름이 없어 첫 결과를 씀: {pick.findtext('법령명한글','')}")
    return pick.findtext("법령명한글", ""), pick.findtext("법령일련번호", ""), pick.findtext("시행일자", "")


def fetch_detail(mst):
    r = requests.get(f"{BASE}/DRF/lawService.do", headers=H, timeout=60,
                     params={"OC": KEY, "target": "law", "MST": mst, "type": "XML"})
    return r.text


def licbyl_items(name):
    """별표서식 검색 API 항목(법령명 일치분만) → [(제목, 링크들)]"""
    for tgt in ("licbyl", "licByl"):
        try:
            r = requests.get(f"{BASE}/DRF/lawSearch.do", headers=H, timeout=30,
                             params={"OC": KEY, "target": tgt, "type": "XML", "query": name, "display": 100})
            root = ET.fromstring(r.text)
            out = []
            for it in list(root):
                if len(list(it)) < 2:
                    continue
                lname = next((c.text for c in it if isinstance(c.tag, str) and "법령명" in c.tag and (c.text or "").strip()), "")
                if lname and _norm(lname) != _norm(name):
                    continue
                title = next((c.text for c in it if isinstance(c.tag, str) and "법령" not in c.tag
                              and re.search(r"(별표|서식).*(제목|명)$|제목", c.tag) and (c.text or "").strip()), "")
                links = [(c.text or "").strip() for c in it if isinstance(c.tag, str) and "링크" in c.tag and (c.text or "").strip()]
                out.append((title or "", links))
            if out:
                return out
        except Exception:
            continue
    return []


def download_text(link):
    """상대 링크 → 다운로드 → 텍스트 추출. (텍스트, 바이트수, 소요초)"""
    url = link if link.startswith("http") else BASE + (link if link.startswith("/") else "/" + link)
    t0 = time.monotonic()
    try:
        r = requests.get(url, headers=H, timeout=60)
        data = r.content if r.status_code == 200 else b""
    except Exception:
        data = b""
    txt = ""
    if data and extract_any:
        try:
            txt = extract_any(data) or ""
        except Exception:
            txt = ""
    return txt, len(data), time.monotonic() - t0


def probe(name):
    print("\n" + "=" * 70)
    found = search_law(name)
    if not found:
        return None
    lname, mst, efyd = found
    xml = fetch_detail(mst)
    root = ET.fromstring(xml)
    print(f"대상: {lname} (MST {mst}, 시행 {efyd}) · 상세 XML {len(xml):,}자")

    units = root.findall(".//별표단위")
    rows = []
    for u in units:
        g = (u.findtext("별표구분") or "").strip()
        title = (u.findtext("별표제목") or "").strip()
        num = f"{(u.findtext('별표번호') or '').strip()}-{(u.findtext('별표가지번호') or '').strip()}"
        content = (u.findtext("별표내용") or "").strip()
        hwp = (u.findtext("별표서식파일링크") or "").strip()
        pdf = (u.findtext("별표서식PDF파일링크") or "").strip()
        imgs = [(c.text or "").strip() for c in u if isinstance(c.tag, str) and "이미지파일링크" in c.tag and (c.text or "").strip()]
        rows.append(dict(g=g, title=title, num=num, clen=len(content), content=content,
                         hwp=hwp, pdf=pdf, nimg=len(imgs)))

    # [A] 구분 분포
    gdist = {}
    for r_ in rows:
        gdist[r_["g"] or "(빈값)"] = gdist.get(r_["g"] or "(빈값)", 0) + 1
    print(f"\n[A] 별표단위 {len(units)}개 · <별표구분> 분포: " +
          (" / ".join(f"{k} {v}" for k, v in sorted(gdist.items())) if gdist else "(별표단위 없음)"))
    is_form = lambda r_: ("서식" in r_["g"] or "별지" in r_["g"]) or (r_["g"] == "" and bool(FORM_PAT.search(r_["title"])))
    byul = [r_ for r_ in rows if not is_form(r_)]
    forms = [r_ for r_ in rows if is_form(r_)]
    print(f"    → 별표 취급 {len(byul)} · 서식 취급 {len(forms)}  (판정: 구분에 서식/별지가 있으면 서식, 구분이 비면 제목 패턴으로)")

    # [B] 구분별 별표내용 채움
    def fill_stats(group):
        filled = [r_ for r_ in group if r_["clen"] > 0]
        short = [r_ for r_ in filled if r_["clen"] < 100]
        lens = sorted(r_["clen"] for r_ in filled)
        med = lens[len(lens) // 2] if lens else 0
        return filled, short, med, (lens[-1] if lens else 0)
    bf, bs, bmed, bmax = fill_stats(byul)
    ff, fs, fmed, fmax = fill_stats(forms)
    file_only = [r_ for r_ in byul if r_["clen"] == 0]
    print(f"[B] 별표: 본문 채움 {len(bf)}/{len(byul)} (100자 미만 {len(bs)}, 중앙값 {bmed:,}자, 최대 {bmax:,}자) · "
          f"★파일 전용(본문 없음) {len(file_only)}")
    for r_ in bs[:6]:
        print(f"    · 짧은 본문: {r_['num']} {r_['title'][:34]} = {r_['clen']}자 「{r_['content'][:40].replace(chr(10),' ')}」")
    print(f"    서식: 본문 채움 {len(ff)}/{len(forms)} (중앙값 {fmed:,}자)")

    # [C] 링크 보유
    img_only = [r_ for r_ in byul if not r_["hwp"] and not r_["pdf"] and r_["nimg"] > 0]
    no_link = [r_ for r_ in byul if not r_["hwp"] and not r_["pdf"] and r_["nimg"] == 0]
    print(f"[C] 별표 링크: HWP {sum(1 for r_ in byul if r_['hwp'])} · PDF {sum(1 for r_ in byul if r_['pdf'])} · "
          f"이미지 합계 {sum(r_['nimg'] for r_ in byul)} · ★이미지만 {len(img_only)} · 링크 없음 {len(no_link)}")
    unrecoverable = [r_ for r_ in file_only if not r_["hwp"] and not r_["pdf"]]
    if unrecoverable:
        print(f"    ⚠️ 본문도 없고 HWP/PDF도 없는 별표 {len(unrecoverable)}개 → 현 기술로 못 읽음(정직 신고 대상)")

    # [D] 다운로드 시도 수: 현행 v2.1 vs 설계 v2.2
    lic = licbyl_items(lname)
    if harvest_links:
        v21_xml = harvest_links(root)
        v21_urls = {u for u, _ in v21_xml}
        lic_links = [l for _, ls in lic for l in ls if LINK_PAT.search(l)]
        v21_total = len(v21_urls | {(l if l.startswith("http") else BASE + (l if l.startswith("/") else "/" + l)) for l in lic_links})
    else:
        vals = [(el.text or "").strip() for el in root.iter() if (el.text or "").strip()]
        v21_total = len({v for v in vals if LINK_PAT.search(v) and len(v) <= 500 and "\n" not in v})
    bogus = sum(1 for el in root.iter() if isinstance(el.tag, str) and el.tag.endswith("파일명")
                and LINK_PAT.search((el.text or "").strip()))
    v22_min = sum(1 for r_ in file_only if r_["hwp"] or r_["pdf"])
    v22_max = sum((1 if r_["hwp"] else 0) + (1 if r_["pdf"] else 0) for r_ in file_only)
    print(f"[D] 다운로드 시도 — 현행 v2.1: 약 {v21_total}회 (그중 파일명을 링크로 오인한 가짜 {bogus}회, 이미지 포함) "
          f"→ 설계 v2.2: {v22_min}~{v22_max}회 (파일 전용 별표만, HWP→PDF 폴백)")
    if v21_total:
        print(f"    예상 소요(회당 0.5초 가정): {v21_total*0.5:,.0f}초 → {v22_max*0.5:,.0f}초")

    # [E] 파일 전용 별표 목록
    if file_only:
        print(f"[E] 파일 전용 별표 {len(file_only)}개 (상위 10):")
        for r_ in file_only[:10]:
            flag = "★우선단어(자격·인력·기준 등)" if PRIORITY.search(r_["title"]) else ""
            print(f"    · {r_['num']} {r_['title'][:40]}  [HWP {'O' if r_['hwp'] else 'X'} PDF {'O' if r_['pdf'] else 'X'} 이미지 {r_['nimg']}] {flag}")

    # [F] 표본 대조: XML 본문 vs 파일 추출 (경로2) + 파일 전용 별표 추출 가능성(경로1)
    if not NO_DL:
        hwp_ok = bool(shutil.which("hwp5txt"))
        print(f"[F] 표본 대조 (PDF 우선 — 운영 로그상 HWP 추출은 '[표]' 표식만 남아 3~13자, hwp5txt {'있음' if hwp_ok else '없음'}):")
        picks = [r_ for r_ in bf if (r_["hwp"] or r_["pdf"])]
        picks = sorted(picks, key=lambda r_: 0 if PRIORITY.search(r_["title"]) else 1)[:SAMPLES]
        for r_ in picks:
            link = r_["pdf"] or r_["hwp"]
            txt, nbytes, sec = download_text(link)
            if not txt:
                print(f"    · {r_['num']} {r_['title'][:30]}: XML {r_['clen']:,}자 / 파일 {nbytes:,}B 추출 실패({sec:.1f}s) — 환경 문제 가능(hwp5txt/pdfminer)")
                continue
            a, b = _norm(r_["content"])[:6000], _norm(txt)[:6000]
            sim = difflib.SequenceMatcher(None, a, b).ratio()
            qa, qb = len(QUAL_PAT.findall(r_["content"])), len(QUAL_PAT.findall(txt))
            verdict = "동등" if (sim >= 0.8 and qb <= qa + 1) else ("파일이 더 풍부(의심)" if (qb > qa + 1 or len(txt) > r_["clen"] * 1.5) else "다름(확인)")
            print(f"    · {r_['num']} {r_['title'][:30]}: XML {r_['clen']:,}자 / 파일 {len(txt):,}자 · 유사도 {sim:.2f} · 자격단어 XML {qa} vs 파일 {qb} → {verdict}")
        for r_ in [r_ for r_ in file_only if (r_["hwp"] or r_["pdf"])][:SAMPLES]:
            link = r_["pdf"] or r_["hwp"]
            txt, nbytes, sec = download_text(link)
            q = len(QUAL_PAT.findall(txt))
            print(f"    · [파일 전용] {r_['num']} {r_['title'][:30]}: 파일 {nbytes:,}B → 추출 {len(txt):,}자 ({sec:.1f}s) · 자격단어 {q}")
    else:
        print("[F] 표본 대조 생략(--no-download)")

    # [G] 별표서식 API 대조 (경로4)
    xml_titles = {_norm(r_["title"]) for r_ in rows if r_["title"]}
    api_only = [t for t, _ in lic if t and _norm(t) not in xml_titles]
    print(f"[G] 별표서식 API(licbyl): 이 법령 이름과 일치하는 항목 {len(lic)} vs XML 단위 {len(rows)} · ★API에만 있는 제목 {len(api_only)}" +
          (": " + " / ".join(t[:25] for t in api_only[:5]) if api_only else ""))

    return dict(law=lname, units=len(rows), byul=len(byul), forms=len(forms), file_only=len(file_only),
                img_only=len(img_only), unrecoverable=len(unrecoverable), v21=v21_total, v22=v22_max,
                api_only=len(api_only), gdist=gdist)


# ── 실행 ──────────────────────────────────────────────────────────────────
print("=" * 70)
print("  별표 수집 설계 판정 프로브 v2.1")
print("=" * 70)
print(f"core: {CORE_INFO}")
_sc = _load_repo_module("scraper")
_sc_src = "레포 core/" if _sc else None
if _sc is None:
    try:
        from hrdk_law_core import scraper as _sc
        _sc_src = f"설치본 {Path(_sc.__file__)}"
    except Exception as e:
        _sc, _sc_src = None, f"불러오기 실패 {str(e)[:60]}"
if _sc is not None:
    print(f"scraper: {'9/16 항·호·목 수정판 ✓' if hasattr(_sc, 'jomun_full_text') else '🚨 구판(항·호·목 수정 없음)'}  ({_sc_src})")
else:
    print(f"scraper: {_sc_src}")
print(f"환경: 레포 폴더 core/ {'있음' if (REPO/'core'/'hrdk_law_core').exists() else '★없음 — GitHub 최신 ZIP으로 로컬 폴더를 맞춰야 재분석·재백필 도구가 현행 코드로 돈다'}")
print(f"hwp5txt: {'✓ ' + str(shutil.which('hwp5txt')) if shutil.which('hwp5txt') else '🚨 미설치 (HWP 추출 불가 → PDF로 대조)'}")
try:
    import pdfminer  # noqa
    print("pdfminer: ✓")
except Exception:
    print("pdfminer: 🚨 미설치 (PDF 추출 불가)")
if not KEY:
    print("❌ LAW_API_KEY 없음 — local_run/local.env 확인"); sys.exit(1)

results = []
for nm in NAMES:
    try:
        res = probe(nm)
        if res:
            results.append(res)
    except Exception as e:
        print(f"💥 '{nm}' 처리 중 오류: {str(e)[:120]}")

print("\n" + "=" * 70)
print("  설계 판정표 (이 표를 복사해서 붙여넣어 주세요)")
print("=" * 70)
print(f"{'법령':<28}{'단위':>5}{'별표':>5}{'서식':>5}{'파일전용':>7}{'이미지만':>7}{'못읽음':>6}{'v2.1시도':>9}{'v2.2시도':>9}{'API전용':>7}")
for r_ in results:
    print(f"{r_['law'][:27]:<28}{r_['units']:>5}{r_['byul']:>5}{r_['forms']:>5}{r_['file_only']:>7}{r_['img_only']:>7}"
          f"{r_['unrecoverable']:>6}{r_['v21']:>9}{r_['v22']:>9}{r_['api_only']:>7}")
if results:
    labels = sorted({k for r_ in results for k in r_["gdist"]})
    print(f"\n<별표구분> 실측 표기값: {labels}")
print("""
읽는 법
 · 파일전용 = 본문(<별표내용>)이 비어 파일을 받아야 하는 별표 → v2.2가 받는 유일한 대상
 · 못읽음  = 본문도 없고 HWP/PDF도 없는 별표 → 어떤 설계로도 못 읽음(정직 신고로 처리)
 · v2.1시도 vs v2.2시도 = 다운로드 횟수(현행 ↔ 설계안). 차이가 곧 절약되는 시간
 · API전용 = 법령 XML에 없고 별표서식 API에만 있는 제목 → 0이면 API 병합은 폴백으로만 두어도 됨
 · [F] 유사도가 0.8 이상이고 자격단어 수가 같으면 XML 본문만으로 충분(파일 중복 수집 불필요)
""")
