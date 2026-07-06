# -*- coding: utf-8 -*-
"""
build_site.py — 자격증 법령 네비게이터 (통합 1파일 빌더 · v2: Q-RADAR 단일 원장 · B안 노선도)
================================================================
두 화면을 '한 개의 index.html'로 굽는다. 상단 탭을 누르면 페이지 이동 없이
보이는 화면만 바뀐다(SPA식).
  · 탭1 「법령 제개정에 따른 자격증 활용도 모니터링」 — 법령 카드(월별 기간선택 + 검색)
  · 탭2 「자격증별 채용시장 우대사항 모니터링」      — 자격증 카드(빈도순 배지 + 2차 팝업)
서버 불필요(GitHub Actions → Pages).

[실행]
  운영(기본):
    클라우드 → QRADAR_SA_JSON, QRADAR_SHEET_ID, QRADAR_WORKSHEET(기본 "국가기술자격 관련법령")
    ※ v2(2026-07-05): 시트 2개(monitor+RADAR) → Q-RADAR 통합 대장 1개로 단일화. 스킨=B안 「법령 노선도」.
  로컬 테스트 → LOCAL_XLSX, LOCAL_SHEET
  옵션: M_MAX(기본 5000), R_MAX(기본 9999), OUT_DIR(기본 dist)
"""
import os, re, json, html, hashlib, datetime
from collections import defaultdict, Counter
from urllib.parse import quote

OUT_DIR = os.environ.get("OUT_DIR", "dist")
M_MAX = int(os.environ.get("M_MAX", "5000"))
R_MAX = int(os.environ.get("R_MAX", "9999"))

MCOL = {"law":"법령명","ministry":"소관부처","date":"시행일자","kind":"개정유형",
        "summary1":"활용도_상세","summary2":"주요 제·개정내용",
        "certs":"관련 종목","article":"근거조문","link":"조문별 다이렉트 링크"}
RCOL = {"law":"법령명","article":"근거조문","pref":"우대분류","certs":"관련 종목",
        "t1type":"Track1_취급유형","t1risk":"Track1_위험도","t2":"Track2_효용코드",
        "sjb":"중처법대상","detail":"상세 분석 결과","rel":"연관도",
        "eff":"시행일자","reason":"검토사유","links":"조문별 다이렉트 링크"}
PREF_ORDER = ["의무고용","직무권한부여","인사우대","시험면제","기타"]
PREF_COLOR = {"의무고용":"#C0492F","직무권한부여":"#1F6FB2","인사우대":"#0F6E56","시험면제":"#5B4BB0","기타":"#8A8F98"}

def py_pref_badge(p):
    """파이썬 측 우대분류 뱃지 HTML (JS의 pfBadge와 동일 모양)."""
    p = str(p or "기타").strip() or "기타"
    return f'<span class="pf" style="--c:{PREF_COLOR.get(p,"#8A8F98")}">{esc(p)}</span>'

TRACK1_TYPE = {
 "A":["신분형성형","자격 취득이 행정청 면허로 이어져 평생 직업·신분을 부여하는 유형."],
 "B":["영업요건형","사업 등록·허가·지정 시 자격자 보유가 의무인 유형."],
 "C":["직역독점형","특정 직무(선임·배치·서명·확인)를 자격자만 수행할 수 있는 유형."],
 "D":["인사가산형","공무원·근로자의 채용·승진·평정·보수에 부가로 우대되는 유형."],
 "E":["검정연계형","다른 자격·시험의 응시자격·시험면제와 연계되는 유형."]}
TRACK1_RISK = {
 "N":["무관","자격이 직역 진입 조건이 아니라 부가우대만 주는 경우."],
 "L":["저위험","자격이 진입 조건이나 학력·경력·유사자격으로 우회 가능."],
 "M":["중위험","법령이 인정하는 복수 자격 중 하나로 대체 가능(우회로 존재)."],
 "H":["고위험","자격과 경력을 동시에 요구해 경력 선행 조건이 되는 경우."],
 "C":["임계위험","단일 자격만 인정되어 대체 경로가 없는 경우."]}
TRACK2 = {
 "Ⅰ-1":["면허전환형","Ⅰ 직업창출형","자격 취득이 행정청 면허로 이어져 평생 직업·신분을 부여."],
 "Ⅰ-2":["개업창업형","Ⅰ 직업창출형","자격자 본인이 단독으로 직무를 수행·서명할 수 있어 1인 사업이 가능."],
 "Ⅱ-1":["등록필수형","Ⅱ 취업관문형","사업체 등록·허가 시 자격자를 일정 인원 이상 보유해야 하는 유형."],
 "Ⅱ-2":["지정인력형","Ⅱ 취업관문형","국가 지정·위탁·대행 기관(검사·검정·인증·진단 등)의 인력 요건."],
 "Ⅱ-3":["전속배치형","Ⅱ 취업관문형","사업장에 단일 자격자만 선임 가능(대체 불가). 매우 드문 유형."],
 "Ⅱ-4":["선택배치형","Ⅱ 취업관문형","법령이 인정하는 복수 자격 중 택일하여 선임하는 유형."],
 "Ⅱ-5":["현장배치형","Ⅱ 취업관문형","공사·사업장 규모·종별에 따라 자격자를 배치하는 유형."],
 "Ⅲ-1":["부가우대(시험면제)","Ⅲ 부가우대형","다른 자격·면허·임용시험에서 시험과목을 면제받는 유형."],
 "Ⅲ-2":["부가우대(인사)","Ⅲ 부가우대형","채용·보수·평정·승진 등 인사에서 우대받는 유형."],
 "Ⅲ-3":["부가우대(위촉·자문)","Ⅲ 부가우대형","위원회·심의위원·시험위원 등 자문성 위촉 자격."],
 "Ⅳ-0":["제외","분류 외","중복·삭제·이관 등 분류 대상에서 제외된 조항."]}


# ───────── 공통 유틸 ─────────
def _sheet_key(v):
    m = re.search(r"/d/([A-Za-z0-9_-]+)", str(v or "")); return m.group(1) if m else str(v or "").strip()
def _client(raw): 
    import gspread; return gspread.service_account_from_dict(json.loads(raw.strip(), strict=False))
def digits(v): return re.sub(r"\D", "", str(v or ""))[:8]
def fmt_date(v):
    d = digits(v); return f"{d[:4]}.{d[4:6]}.{d[6:]}" if len(d) == 8 else str(v or "")
def esc(v): return html.escape(str(v or "").strip())
def law_url_name(name): return f"https://www.law.go.kr/법령/{quote(str(name or '').strip())}"

# 사전에 '·'(가운뎃점)가 포함된 정식 종목명 (분리 시 보호해야 함)
DOT_CERTS = ["항공전기·전자정비기능사"]

def split_certs(raw):
    """관련 종목 문자열 분리. 괄호 안 쉼표 + 사전의 가운뎃점 종목명을 보호한 뒤
    쉼표/슬래시/가운뎃점/줄바꿈으로 분리한다."""
    s = str(raw or "")
    # 1) 사전의 '·' 종목명 보호 (가운뎃점을 임시기호로)
    for dc in DOT_CERTS:
        s = s.replace(dc, dc.replace("·", "㉿"))
    # 2) 괄호 안 쉼표 보호
    s = re.sub(r"\(([^)]*)\)", lambda m: "(" + m.group(1).replace(",", "§") + ")", s)
    # 3) 분리 후 복원
    parts = [c.strip().replace("§", ",").replace("㉿", "·") for c in re.split(r"[,/·\n]", s) if c.strip()]
    return parts

def fmt_eff(s):
    """시행일자 표기: '20220103' → '2022.01.03'. 형식 다르면 원문 그대로."""
    d = re.sub(r"\D", "", str(s or ""))
    if len(d) == 8:
        return f"{d[:4]}.{d[4:6]}.{d[6:]}"
    return str(s or "").strip()

def parse_links(raw):
    """'▶ 법령명 제71조\\nhttps://...\\n▶ ...\\nhttps://...' → [{'t':제목,'u':url}].
    제목 줄(▶) 다음에 오는 http(s) 줄을 짝지어 묶는다."""
    s = str(raw or "").strip()
    if not s:
        return []
    lines = [ln.strip() for ln in s.splitlines() if ln.strip()]
    out, pend = [], None
    for ln in lines:
        if ln.startswith("http"):
            label = pend or "법령 원문"
            out.append({"t": re.sub(r"^▶\s*", "", label), "u": ln})
            pend = None
        else:
            pend = ln
    # 제목 없이 URL만 있거나, ▶만 있고 URL 없는 경우도 안전 처리
    return out
def tok(v): return str(v or "").split(" ")[0].strip()  # "B (영업요건형)" -> "B"


# ───────── 로드 (v2: Q-RADAR 단일 원장) ─────────
# 통합 대장 탭 하나를 한 번만 읽어(캐시) 두 화면이 나눠 쓴다.
_LEDGER = None
def load_ledger():
    global _LEDGER
    if _LEDGER is not None:
        return _LEDGER
    ws_name = os.environ.get("QRADAR_WORKSHEET", "국가기술자격 관련법령")
    lx = os.environ.get("LOCAL_XLSX", "").strip()
    if lx:
        import pandas as pd
        sh = os.environ.get("LOCAL_SHEET", ws_name)
        _LEDGER = pd.read_excel(lx, sheet_name=sh).fillna("").to_dict("records")
        return _LEDGER
    gc = _client(os.environ["QRADAR_SA_JSON"])
    _LEDGER = gc.open_by_key(_sheet_key(os.environ["QRADAR_SHEET_ID"])).worksheet(ws_name).get_all_records()
    return _LEDGER

def _nospace(s): return re.sub(r"\s+", "", str(s or ""))

def load_monitor():
    """화면1(활용도 모니터링): 연관도 = 연관높음·단순관련 — 구 monitor 탭과 동일 범위."""
    keep = {"연관높음", "단순관련"}
    return [r for r in load_ledger() if str(r.get("연관도") or "").strip() in keep]

def load_radar():
    """화면2(우대사항): 기본(R_SCOPE=pref)은 진짜 우대만(연관도=우대 또는 실우대분류 4종).
    R_SCOPE=all 이면 v1처럼 관련 법령 전체 포함(비우대는 '기타' 배지)."""
    if os.environ.get("R_SCOPE", "pref").strip().lower() == "all":
        return load_ledger()
    REAL_PREF = {"의무고용", "직무권한부여", "인사우대", "시험면제"}
    out = []
    for r in load_ledger():
        rel = str(r.get("연관도") or "").strip()
        pf = str(r.get(RCOL["pref"]) or "").strip()
        if rel == "우대" or pf in REAL_PREF:
            out.append(r)
    return out


# ───────── monitor 데이터/카드 ─────────
def m_fields(row):
    certs = split_certs(row.get(MCOL["certs"]))
    arts = [a.strip() for a in re.split(r"[,\n;·]", str(row.get(MCOL["article"]) or "")) if a.strip()]
    mn = str(row.get(MCOL["ministry"]) or "").strip(); kd = str(row.get(MCOL["kind"]) or "").strip()
    dt = fmt_date(row.get(MCOL["date"]))
    lk_raw = str(row.get(MCOL["link"]) or "")
    art_links = parse_links(lk_raw)   # [{t,u}] — 조문별 하이퍼링크
    # 원문 대표 URL: 링크칸이 http로 시작하면 첫 URL, 아니면 법령명 검색 URL
    if art_links:
        base_url = art_links[0]["u"]
    elif lk_raw.strip().startswith("http"):
        base_url = lk_raw.strip()
    else:
        base_url = law_url_name(row.get(MCOL["law"]))
    return {"law":str(row.get(MCOL["law"]) or "").strip(), "month":digits(row.get(MCOL["date"]))[:6],
            "meta":" · ".join(x for x in [mn,dt,kd] if x), "certs":certs,
            "summary_main":str(row.get(MCOL["summary2"]) or "").strip(),
            "summary_use":str(row.get(MCOL["summary1"]) or "").strip(),
            "articles":arts, "artlinks":art_links, "url":base_url}

def m_card(d, i):
    shown = d["certs"][:4]; extra = len(d["certs"]) - len(shown)
    chips = "".join(f'<span class="chip">{esc(c)}</span>' for c in shown) + (f'<span class="chip chip-more">+{extra}</span>' if extra>0 else "")
    summ = esc(d["summary_use"] or d["summary_main"] or "요약 준비 중입니다.")
    # meta("부처 · 날짜 · 유형")에서 날짜를 대장 왼쪽 열로 분리
    parts = [p.strip() for p in str(d["meta"] or "").split("·")]
    date_p = next((p for p in parts if re.match(r"\d{4}\.", p)), "")
    rest = " · ".join(p for p in parts if p and p != date_p)
    kind = parts[-1] if len(parts) >= 3 else ""
    return f"""
    <article class="card" data-i="{i}" data-month="{d['month']}">
      <div class="c-date">{esc(date_p) or '—'}<small>{esc(kind) if (kind and kind != date_p) else '시행'}</small></div>
      <h3 class="card-title"><button type="button" class="title-btn">{esc(d['law'])}</button></h3>
      <div class="card-head">{esc(rest)}</div>
      <p class="summary">{summ}</p>
      <div class="chips">{chips}</div>
      <div class="card-foot"><button type="button" class="detail-link">분석 상세 →</button>
        <a class="ext" href="{esc(d['url'])}" target="_blank" rel="noopener">법제처 원문</a></div>
    </article>"""


# ───────── radar 데이터/카드 ─────────
def r_pref_idx(p): return PREF_ORDER.index(p) if p in PREF_ORDER else len(PREF_ORDER)

def r_build(rows):
    entries = []                 # 고유 우대조항(법령·조문 단위)
    cert_map = defaultdict(list) # 자격증 -> entries 인덱스 참조
    nocert = []                  # 종목 미상(우대는 있으나 종목 특정 불가) 목록
    SKIP_REL = {"해당없음", "일반", ""}   # 공개 화면 제외 (연관높음/단순관련만 표시)
    for r in rows:
        rel = str(r.get(RCOL["rel"]) or "").strip()
        if rel in SKIP_REL:
            continue
        # 종목 분리: 괄호 안 쉼표는 보호('소방설비기사(기계분야)'가 안 깨지게)
        certs = split_certs(r.get(RCOL["certs"]))
        law = str(r.get(RCOL["law"]) or "").strip()
        sjb = str(r.get(RCOL["sjb"]) or "").strip() not in ("","비대상","해당없음")
        if not certs:
            # 종목 미상: 우대는 있으나 종목을 특정 못한 법령 → 별도 섹션용으로 수집
            nocert.append({
                "law": law,
                "p": str(r.get(RCOL["pref"]) or "").strip() or "기타",
                "a": str(r.get(RCOL["article"]) or "").strip(),
                "e": fmt_eff(str(r.get(RCOL["eff"]) or "").strip()),
                "r": str(r.get(RCOL["reason"]) or "").strip(),
                "u": law_url_name(law),
            })
            continue
        e = {"law":law, "a":str(r.get(RCOL["article"]) or "").strip(),
             "p":str(r.get(RCOL["pref"]) or "").strip() or "기타",
             "t1":tok(r.get(RCOL["t1type"])), "t1r":tok(r.get(RCOL["t1risk"])), "t2":tok(r.get(RCOL["t2"])),
             "s":1 if sjb else 0}
        eff = str(r.get(RCOL["eff"]) or "").strip()       # 시행일자(제·개정일) 표시용
        if eff: e["e"] = fmt_eff(eff)
        det = str(r.get(RCOL["detail"]) or "").strip()    # 관련법령 탭의 상세 분석 결과(직접 보유)
        if det: e["d"] = det
        rsn = str(r.get(RCOL["reason"]) or "").strip()    # 검토사유(있을 때만 팝업에 노출)
        if rsn: e["r"] = rsn
        lk = parse_links(r.get(RCOL["links"]))             # 조문별 다이렉트 링크 [{t,u}]
        if lk: e["lk"] = lk
        ei = len(entries); entries.append(e)
        for c in certs: cert_map[c].append(ei)
    items = sorted(cert_map.items(), key=lambda kv: len({entries[ei]["law"] for ei in kv[1]}), reverse=True)[:R_MAX]
    certs_out = []
    for cert, idxs in items:
        prefs = [p for p,_ in Counter(entries[ei]["p"] for ei in idxs).most_common()]
        idxs_sorted = sorted(idxs, key=lambda ei:(r_pref_idx(entries[ei]["p"]), entries[ei]["law"]))
        certs_out.append({"cert":cert, "prefs":prefs,
                          "law_count":len({entries[ei]["law"] for ei in idxs}),
                          "sjb":any(entries[ei]["s"] for ei in idxs), "idx":idxs_sorted})
    # 종목 미상: 법령명 기준 중복 제거(여러 조문이 같은 법령이면 하나로)
    seen_nc, nocert_uniq = set(), []
    for x in sorted(nocert, key=lambda z: z["law"]):
        if x["law"] in seen_nc: continue
        seen_nc.add(x["law"]); nocert_uniq.append(x)
    return certs_out, entries, len(cert_map), nocert_uniq

def r_card(d, i):
    sjb = '<span class="sjb-badge">⚠ 중대재해처벌법 관련</span>' if d["sjb"] else ""
    return f"""
    <article class="card rcard" data-i="{i}">
      <h3 class="cert"><button type="button" class="title-btn">{esc(d['cert'])}</button></h3>
      <div class="card-foot">
        <div class="foot-meta"><span class="lc">우대 법령 {d['law_count']}개</span>{sjb}</div>
        <div class="foot-action"><button type="button" class="detail-link">우대 근거 상세 →</button></div>
      </div>
    </article>"""


# ───────── 조립 ─────────
def build():
    # monitor
    mrows = [r for r in load_monitor() if len(digits(r.get(MCOL["date"]))) == 8]
    mrows.sort(key=lambda r: digits(r.get(MCOL["date"])), reverse=True)
    mrows = mrows[:M_MAX]
    mdata = [m_fields(r) for r in mrows]
    months = sorted({d["month"] for d in mdata if d["month"]})
    def_to = months[-1] if months else ""; def_from = months[-2] if len(months)>=2 else def_to
    m_total_certs = len({c for d in mdata for c in d["certs"]})
    m_opts = "".join(f'<option value="{m}">{m[:4]}.{m[4:6]}</option>' for m in reversed(months))
    m_cards = "\n".join(m_card(d,i) for i,d in enumerate(mdata)) or '<p class="empty">표시할 법령이 없습니다.</p>'

    # radar
    rrows = load_radar()
    rcerts, rentries, r_total, nocert = r_build(rrows)
    r_cards = "\n".join(r_card(d,i) for i,d in enumerate(rcerts)) or '<p class="empty">자료가 없습니다.</p>'
    # 종목 미상 우대법령 섹션 (자격증 그리드 맨 아래 접이식)
    if nocert:
        nocert_banner = (
            '<button type="button" class="nocert-banner" id="nocert-open">'
            '<span class="nc-ic">🔎</span>'
            '<span class="nc-btxt"><b>종목 미상 우대법령</b> '
            '<span class="nc-cnt">' + str(len(nocert)) + '건</span></span>'
            '<span class="nc-bsub">우대는 있으나 종목 특정이 어려운 법령 · 눌러서 보기 →</span>'
            '</button>')
        nocert_json = json.dumps(nocert, ensure_ascii=False).replace("</", "<\\/")
    else:
        nocert_banner = ""
        nocert_json = "[]"

    out = PAGE
    repl = {
      "@@M_OPTS@@":m_opts, "@@M_DEF_FROM@@":def_from, "@@M_DEF_TO@@":def_to,
      "@@M_TOTAL_CERTS@@":str(m_total_certs), "@@M_CARDS@@":m_cards,
      "@@R_CARDS@@":r_cards, "@@R_TOTAL@@":str(r_total), "@@NOCERT@@":nocert_banner, "@@NOCERT_JSON@@":nocert_json,
      "@@BUILT_AT@@":datetime.datetime.now().strftime("%Y.%m.%d"),
      "@@MLAWS@@":json.dumps(mdata, ensure_ascii=False).replace("</","<\\/"),
      "@@RCERTS@@":json.dumps(rcerts, ensure_ascii=False).replace("</","<\\/"),
      "@@RENTRIES@@":json.dumps(rentries, ensure_ascii=False).replace("</","<\\/"),
      "@@T1TYPE@@":json.dumps(TRACK1_TYPE, ensure_ascii=False),
      "@@T1RISK@@":json.dumps(TRACK1_RISK, ensure_ascii=False),
      "@@T2@@":json.dumps(TRACK2, ensure_ascii=False),
      "@@PFC@@":json.dumps(PREF_COLOR, ensure_ascii=False),
    }
    for k,v in repl.items(): out = out.replace(k,v)
    return out, len(mdata), len(rcerts), r_total

def main():
    out, nm, nr, total = build()
    os.makedirs(OUT_DIR, exist_ok=True)
    p = os.path.join(OUT_DIR, "index.html")
    open(p,"w",encoding="utf-8").write(out)
    print(f"✅ 생성: {p}  (법령 {nm}건 / 자격증 {nr}개[전체 {total}])")


PAGE = r"""<!DOCTYPE html>
<html lang="ko"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>자격증 법령 네비게이터 · HRDK</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Noto+Serif+KR:wght@600;700;900&display=swap" rel="stylesheet">
<style>
/* ═══ B안 「법령 노선도」 — 교통 사인 시스템: 노선색·역명판·정거장 ═══ */
:root{--bg:#FAFBFD;--ink:#101828;--navy:#1F3864;--mut:#5D6B7E;--line:#E4E8EF;
--l1:#C0492F;--l2:#1F6FB2;--l3:#0F6E56;--l4:#5B4BB0;--l5:#8A8F98;--go:#00A86B;
--sans:'Pretendard',-apple-system,sans-serif;--accent:#1F6FB2;--hrdk:#005EB8}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:var(--sans);background:var(--bg);color:var(--ink);font-size:15px;line-height:1.65}
.wrap{max-width:1080px;margin:0 auto;padding:0 22px}
button{font-family:inherit;cursor:pointer}

/* 노선 스트립(시그니처): 우대분류 5색 노선 */
.gov-bar{background:#fff;border-bottom:1px solid var(--line)}
.gov-bar .wrap{display:flex;justify-content:space-between;gap:14px;padding:8px 22px;font-size:12px;color:var(--mut)}
.gov-bar b{color:var(--navy);font-weight:700}
header.site{background:#fff;position:relative}
header.site::after{content:"";display:block;height:6px;background:linear-gradient(90deg,var(--l1) 0 20%,var(--l2) 20% 40%,var(--l3) 40% 60%,var(--l4) 60% 80%,var(--l5) 80% 100%)}
header.site .wrap{padding-top:26px}
.doc-head{display:flex;align-items:center;justify-content:space-between;gap:16px;padding-bottom:18px}
.logo{font-weight:800;font-size:clamp(24px,3.6vw,34px);letter-spacing:-.02em;line-height:1.25}
.logo em{font-style:normal;color:var(--navy)}
.logo .doc-sub{display:flex;align-items:center;gap:7px;font-size:12.5px;font-weight:600;color:var(--mut);letter-spacing:.06em;margin-bottom:7px}
.logo .doc-sub::before{content:"";width:10px;height:10px;border-radius:50%;background:#fff;border:3px solid var(--navy)}
.seal-stamp{flex:none;display:flex;align-items:center;gap:8px;font-size:12.5px;font-weight:700;color:var(--go);
background:#EBF9F2;border:1.5px solid #BFEBD6;border-radius:999px;padding:8px 15px;line-height:1.2}
.seal-stamp br{display:none}
.seal-stamp::before{content:"";width:8px;height:8px;border-radius:50%;background:var(--go);animation:blink 1.8s ease-in-out infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.25}}
@media (prefers-reduced-motion:reduce){.seal-stamp::before{animation:none}}
.tabs{display:flex;gap:10px;padding-bottom:16px}
.tab{appearance:none;border:2px solid var(--line);background:#fff;border-radius:999px;padding:13px 26px;font-size:15.5px;font-weight:800;color:var(--mut);display:inline-flex;align-items:center;gap:9px;cursor:pointer;transition:border-color .15s,color .15s,box-shadow .15s}
.tab::before{content:"";width:9px;height:9px;border-radius:50%;background:var(--line);flex:none;transition:background .15s,box-shadow .15s}
.tab:hover{border-color:var(--navy);color:var(--navy)}
.tab.active{background:var(--navy);border-color:var(--navy);color:#fff;box-shadow:0 8px 20px rgba(27,42,74,.30)}
.tab.active::before{background:var(--l1);box-shadow:0 0 0 3px rgba(255,255,255,.35)}
.tab:focus-visible{outline:2.5px solid var(--l2);outline-offset:2px}

/* hero: 정거장 안내판 */
.hero{background:#fff;border-bottom:1px solid var(--line)}
.hero .wrap{padding:40px 22px 34px}
.eyebrow{display:inline-flex;align-items:center;gap:8px;font-size:12.5px;font-weight:700;color:var(--navy);background:#EEF3FB;border-radius:999px;padding:6px 14px;margin-bottom:16px}
.hero h1{font-weight:800;font-size:clamp(22px,3.4vw,32px);line-height:1.42;letter-spacing:-.015em;max-width:740px}
.hero h1 strong{color:#fff;background:var(--navy);border-radius:10px;padding:1px 12px;font-variant-numeric:tabular-nums}
.lead{margin-top:14px;color:var(--mut);font-size:14.5px;max-width:640px}
.lead::after{content:"";display:block;margin-top:22px;height:4px;max-width:420px;border-radius:99px;
background:linear-gradient(90deg,var(--l2),var(--l3));position:relative}

/* toolbar */
.toolbar{background:var(--hrdk);border-bottom:none;position:sticky;top:0;z-index:10;box-shadow:0 2px 12px rgba(6,45,96,.22)}
.toolbar .wrap{display:flex;flex-wrap:wrap;align-items:center;gap:12px 24px;padding:12px 22px}
.trow{display:flex;align-items:center;gap:9px;flex-wrap:wrap}
.trow>span{font-size:13.5px;font-weight:700;color:#fff}
select{appearance:none;border:1.5px solid rgba(255,255,255,.55);background:rgba(255,255,255,.13);border-radius:12px;padding:9px 32px 9px 13px;font-size:13.5px;font-family:inherit;font-weight:600;color:#fff;
background-image:url("data:image/svg+xml,%3Csvg width='9' height='6' viewBox='0 0 9 6' xmlns='http://www.w3.org/2000/svg'%3E%3Cpath d='M1 1l3.5 3.5L8 1' stroke='%23FFFFFF' fill='none' stroke-width='1.8'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 12px center}
select:focus{outline:none;border-color:#fff;background-color:rgba(255,255,255,.22)}
select option{color:var(--ink);background:#fff}
.search{display:flex;align-items:center;gap:10px;border:2px solid rgba(255,255,255,.85);background:rgba(255,255,255,.15);border-radius:999px;padding:12px 19px;min-width:min(520px,78vw);color:#fff;transition:border-color .15s,background .15s,box-shadow .15s}
.search:focus-within{border-color:#fff;background:rgba(255,255,255,.26);box-shadow:0 0 0 4px rgba(255,255,255,.24)}
.search svg{flex:none}
.search input{border:none;outline:none;background:none;font-size:15.5px;font-family:inherit;flex:1;color:#fff}
.search input::placeholder{color:rgba(255,255,255,.80);font-weight:500}
.search input::-webkit-search-cancel-button{filter:invert(1)}
.count{font-size:13px;color:#fff;font-weight:600}.cnt-note{font-size:11.5px;color:rgba(255,255,255,.75);font-weight:600}.count b{color:#fff;font-weight:800;font-variant-numeric:tabular-nums}

/* 화면1: 노선 카드 — 좌측 노선 라인 + 정거장 도트 */
main .wrap{padding:26px 22px 60px}
#grid-m{display:grid;gap:13px}
#grid-m .card{position:relative;background:#fff;border:1.5px solid var(--line);border-radius:16px;padding:18px 20px 16px 46px;transition:border-color .15s, transform .15s}
#grid-m .card:hover{border-color:var(--navy);transform:translateX(3px)}
#grid-m .card::before{content:"";position:absolute;left:22px;top:16px;bottom:16px;width:5px;border-radius:99px;background:var(--l2)}
#grid-m .card::after{content:"";position:absolute;left:19.5px;top:22px;width:10px;height:10px;border-radius:50%;background:#fff;border:3px solid var(--l2)}
.c-date{display:inline-flex;align-items:baseline;gap:6px;font-size:12.5px;font-weight:800;color:var(--navy);background:#EEF3FB;border-radius:8px;padding:3px 10px;font-variant-numeric:tabular-nums;margin-bottom:7px}
.c-date small{font-weight:600;color:var(--mut)}
.card-title{font-size:17.5px;font-weight:800;line-height:1.4;letter-spacing:-.01em}
.title-btn{appearance:none;border:none;background:none;font:inherit;color:var(--ink);text-align:left;padding:0}
.title-btn:hover{color:var(--l2)}
.card-head{font-size:12.5px;color:var(--mut);margin-top:3px}
.summary{font-size:13.5px;color:#4A5567;margin-top:8px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.chip{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:600;background:#F2F5F9;border-radius:999px;padding:4px 11px;color:#3C475A}
.chip::before{content:"";width:6px;height:6px;border-radius:50%;background:var(--navy)}
.chip-more{background:none;border:1.5px dashed var(--line)}.chip-more::before{display:none}
#grid-m .card-foot{display:flex;align-items:center;gap:14px;margin-top:13px}
.detail-link{appearance:none;background:var(--navy);border:none;color:#fff;font-size:12.5px;font-weight:700;padding:8px 16px;border-radius:999px}
.detail-link:hover{background:var(--l2)}
.ext{font-size:12.5px;font-weight:600;color:var(--mut);text-decoration:none}
.ext:hover{color:var(--l2)}.ext::after{content:" ↗"}

/* 화면2: 역명판 카드 */
.rgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(228px,1fr));gap:13px}
.rcard{background:#fff;border:1.5px solid var(--line);border-radius:16px;padding:16px;display:flex;flex-direction:column;gap:10px;transition:border-color .15s,transform .15s}
.rcard:hover{border-color:var(--navy);transform:translateY(-2px)}
.rcard .cert{font-size:16px;font-weight:800;line-height:1.4;letter-spacing:-.01em;display:flex;gap:9px}
.rcard .cert::before{content:"";flex:none;margin-top:5px;width:11px;height:11px;border-radius:50%;background:#fff;border:3.5px solid var(--navy)}
.rcard .card-foot{margin-top:auto;display:flex;flex-direction:column;gap:9px}
.foot-meta{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.lc{font-size:12px;font-weight:700;color:var(--navy);background:#EEF3FB;border-radius:999px;padding:3px 10px}
.sjb-badge{font-size:11px;font-weight:700;color:var(--l1);background:#FBEDEA;border-radius:999px;padding:3px 9px}
.rcard .detail-link{background:none;border:1.5px solid var(--line);color:var(--navy);width:100%;border-radius:12px}
.rcard .detail-link:hover{border-color:var(--navy);background:#EEF3FB}
.noresult{display:none;text-align:center;color:var(--mut);padding:60px 0}
.noresult.show{display:block}

/* 종목미상 배너 */
.nocert-banner{display:flex;align-items:center;gap:12px;width:100%;text-align:left;background:#FFF9EC;
border:1.5px solid #F2DFAE;border-radius:16px;padding:14px 18px;margin-bottom:16px}
.nocert-banner:hover{border-color:#E3C36B}
.nc-ic{font-size:18px}.nc-btxt b{font-size:14.5px;font-weight:800}
.nc-cnt{color:#B37D10;font-weight:800;font-variant-numeric:tabular-nums}
.nc-bsub{margin-left:auto;font-size:12.5px;color:var(--mut)}

/* 분류 안내 */
.clsguide{border:1.5px solid var(--line);border-radius:16px;background:#fff;margin:22px 0 6px;overflow:hidden}
.clsguide summary{list-style:none;display:flex;align-items:center;gap:9px;padding:14px 18px;font-weight:800;font-size:14.5px;cursor:pointer}
.clsguide summary::-webkit-details-marker{display:none}
.cg-ic{font-size:16px}.cg-sub{font-weight:500;font-size:12.5px;color:var(--mut)}
.cg-arrow{margin-left:auto;color:var(--mut);transition:transform .18s}
.clsguide[open] .cg-arrow{transform:rotate(180deg)}
.cg-body{padding:2px 18px 18px;border-top:1.5px solid var(--line)}
.cg-block{margin-top:16px;border:2.5px solid #C9D3E4;border-radius:14px;background:#FBFCFE;padding:14px 16px 12px}
.cg-head{font-weight:800;font-size:13.5px;color:var(--navy);margin-bottom:10px;padding-bottom:9px;border-bottom:1.5px solid #E7ECF4}
.cg-head span{font-weight:500;font-size:12px;color:var(--mut);margin-left:7px}
.cg-tbl{width:100%;border-collapse:collapse;font-size:13px}
.cg-tbl th{background:#F4F6FA;text-align:left;padding:8px 11px;font-size:12px;color:var(--mut);font-weight:700}
.cg-tbl th:first-child{border-radius:9px 0 0 9px}.cg-tbl th:last-child{border-radius:0 9px 9px 0}
.cg-tbl td{padding:8px 11px;border-bottom:1px solid #EEF1F6;vertical-align:top}
.cg-tbl tr:last-child td{border-bottom:none}
.cg-tag{display:inline-block;color:#fff;background:var(--c,#8A8F98);font-weight:700;font-size:12px;padding:3px 11px;border-radius:999px;white-space:nowrap}
.cg-code{display:inline-block;min-width:36px;text-align:center;background:#EEF3FB;color:var(--navy);font-weight:800;font-size:12px;padding:2.5px 7px;border-radius:8px;font-variant-numeric:tabular-nums}
.cg-code.warn{background:#FBF3E2;color:#B37D10}.cg-code.danger{background:#FBEDEA;color:var(--l1)}
.cg-code.dim,.cg-area.dim{background:#F2F3F5;color:var(--mut)}
.cg-area{font-weight:800;color:var(--c,var(--navy));font-size:12.5px}
.cg-area small{font-weight:500;color:var(--mut)}
.cg-row2{display:grid;grid-template-columns:1fr 1fr;gap:18px}
.cg-note{margin-top:15px;font-size:12px;color:var(--mut);line-height:1.9;border-top:1.5px solid var(--line);padding-top:12px}

/* 모달 */
.modal{position:fixed;inset:0;display:none;z-index:50}
.modal.open{display:block}
.modal-backdrop{position:absolute;inset:0;background:rgba(16,24,40,.45)}
.modal-panel{position:absolute;top:4vh;left:50%;transform:translateX(-50%);width:min(760px,94vw);max-height:92vh;overflow-y:auto;
background:#fff;border-radius:22px;padding:32px 34px 28px;scrollbar-width:thin;scrollbar-color:#AEBBD0 transparent}
.modal-panel::-webkit-scrollbar{width:11px}
.modal-panel::-webkit-scrollbar-track{background:transparent;margin:40px 0}
.modal-panel::-webkit-scrollbar-thumb{background:#C3CDDD;border-radius:99px;border:3.5px solid #fff}
.modal-panel::-webkit-scrollbar-thumb:hover{background:var(--navy)}
.modal-panel::before{content:"";position:sticky;top:-32px;display:block;height:6px;margin:-32px -34px 26px;border-radius:22px 22px 0 0;
background:linear-gradient(90deg,var(--l1) 0 20%,var(--l2) 20% 40%,var(--l3) 40% 60%,var(--l4) 60% 80%,var(--l5) 80% 100%)}
.modal-close{position:sticky;top:0;float:right;appearance:none;border:none;background:#F2F5F9;width:36px;height:36px;border-radius:50%;font-size:19px;color:var(--mut);z-index:2}
.modal-close:hover{background:#E4E8EF;color:var(--ink)}
.m-title,.m-cert{font-weight:800;font-size:22px;line-height:1.35;letter-spacing:-.015em;padding-right:44px}
.m-meta{font-size:13px;color:var(--mut);margin-top:7px;padding-bottom:14px;border-bottom:1.5px solid var(--line)}
.m-sec{margin-top:16px;border:2px solid #D3DCEA;border-radius:14px;background:#FBFCFE;padding:14px 16px}
.m-sec h4{font-size:13px;font-weight:800;color:var(--navy);margin-bottom:10px;padding-bottom:9px;border-bottom:2px solid #E7ECF4;display:flex;align-items:center;gap:7px}
.m-sec h4::before{content:"";width:8px;height:8px;border-radius:50%;background:#fff;border:2.5px solid var(--navy)}
.m-sec p{font-size:14px;color:#3C475A}
.m-chips{display:flex;flex-wrap:wrap;gap:6px}
.m-arts{margin-left:19px;font-size:13.5px;color:#3C475A}
.m-arts li{margin:3px 0}
.m-none{font-size:13px;color:var(--mut)}
.m-ext{display:inline-block;margin-top:22px;background:var(--navy);color:#fff;text-decoration:none;font-size:13px;font-weight:700;padding:10px 18px;border-radius:999px}
.m-ext:hover{background:var(--l2)}
.m-pfs{display:flex;gap:7px;flex-wrap:wrap;margin:11px 0 4px}
.pf{display:inline-block;color:#fff;background:var(--c,#8A8F98);font-weight:700;font-size:12px;padding:3.5px 12px;border-radius:999px}
.law{border:1.5px solid var(--line);border-radius:14px;background:#fff;padding:12px 14px;margin-top:9px;cursor:pointer;transition:border-color .15s}
.law:hover{border-color:var(--navy)}
.law-h{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.law-name{font-weight:800;font-size:14.5px}
.law-go{margin-left:auto;font-size:12px;color:var(--navy);font-weight:700}
.law-m{font-size:12.5px;color:var(--mut);margin-top:4px}
.law-eff{margin-left:9px;color:var(--navy);font-weight:600}
.tag-t2{font-size:11px;font-weight:700;background:#EEF3FB;color:var(--navy);padding:2px 8px;border-radius:999px}
.tag-sjb{font-size:11px;font-weight:700;background:#FBEDEA;color:var(--l1);padding:2px 8px;border-radius:999px}
.trk{display:grid;grid-template-columns:118px 1fr;gap:2px 13px;border-bottom:1px solid #EEF1F6;padding:9px 2px}
.trk .k{font-size:12px;color:var(--mut);font-weight:700;padding-top:2px}
.trk .v{font-size:14px;font-weight:800}
.trk .v .sub{font-weight:500;color:var(--mut);font-size:12.5px}
.trk .d{grid-column:2;font-size:12.5px;color:var(--mut)}
.artlinks{display:flex;flex-wrap:wrap;gap:8px}
.artlink{font-size:12.5px;font-weight:600;text-decoration:none;color:var(--navy);background:#EEF3FB;padding:6px 13px;border-radius:999px}
.artlink:hover{background:var(--navy);color:#fff}
.m2-law{font-size:13px;color:var(--mut)}
.m2-art{font-weight:800}
.m2-ext{display:inline-block;margin-top:22px;background:var(--navy);color:#fff;text-decoration:none;font-size:13px;font-weight:700;padding:10px 18px;border-radius:999px}
.m2-ext:hover{background:var(--l2)}
.note,.note-sec{font-size:12.5px;color:var(--mut)}
.nc-item{border:1.5px solid var(--line);border-radius:14px;background:#fff;padding:13px 15px;margin-top:10px}
.nc-h{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.nc-law{font-weight:800;font-size:14.5px}
.nc-r{font-size:12px;color:var(--mut);margin-top:5px}
.nc-art{font-size:12.5px;color:#3C475A;margin-top:4px}
.nc-desc{font-size:13px;color:var(--mut);margin:8px 0 2px;line-height:1.8}
.nc-ext{font-size:12.5px;font-weight:700;color:var(--l2);text-decoration:none}
footer{border-top:1.5px solid var(--line);margin-top:20px;background:#fff}
footer .wrap{padding:20px 22px 34px;font-size:12.5px;color:var(--mut);line-height:1.9}
footer b{color:var(--navy)}
@media (max-width:760px){
  .doc-head{flex-direction:column;align-items:flex-start;gap:12px}
  .tabs{width:100%;overflow-x:auto}
  .tab{white-space:nowrap;font-size:13.5px;padding:11px 17px}
  #grid-m .card{padding-left:40px}
  .cg-row2{grid-template-columns:1fr}
  .modal-panel{padding:24px 18px}.modal-panel::before{margin:-24px -18px 20px}
  .nc-bsub{display:none}
}
[hidden]{display:none !important;}
</style>
</head><body>
<div class="gov-bar"><div class="wrap"><b>한국산업인력공단</b><span>국가기술자격 × 국가법령정보센터</span><span>@@BUILT_AT@@ 발행 · 매일 새벽 자동 갱신</span></div></div>
<header class="site"><div class="wrap">
  <div class="doc-head">
    <span class="logo"><span class="doc-sub">법령 → 자격증 → 채용, 한 노선으로</span>자격증 <em>법령 네비게이터</em></span>
    <span class="seal-stamp" aria-hidden="true">매일 새벽 자동 분석 운행 중</span>
  </div>
  <nav class="tabs">
    <button type="button" class="tab active" data-view="monitor">법령 제개정에 따른 자격증 활용도 모니터링</button>
    <button type="button" class="tab" data-view="radar">자격증별 채용시장 우대사항 모니터링</button>
  </nav>
</div></header>

<!-- ===== 화면1: 활용도 모니터링 ===== -->
<section id="view-monitor">
  <div class="hero"><div class="wrap">
    <div class="eyebrow" id="heroPeriod"></div>
    <h1>선택한 기간, 자격증과 관련해<br><strong id="heroN">0</strong>건의 법령이 바뀌었습니다.</h1>
    <p class="lead">매일 새벽 국가법령정보센터를 살펴 국가기술자격과 관련된 제·개정 법령만 골라, 알기 쉽게 정리합니다. 기간을 고르거나 검색해 보세요.</p>
  </div></div>
  <div class="toolbar"><div class="wrap">
    <div class="trow period"><span>기간</span>
      <select id="mfrom">@@M_OPTS@@</select><span>~</span><select id="mto">@@M_OPTS@@</select>
      <span class="count"><b id="cnt">0</b>건 표시 중</span></div>
    <div class="trow">
      <select id="scope" aria-label="검색 범위"><option value="all">전체검색</option><option value="law">법령명</option><option value="cert">자격명칭</option><option value="detail">상세내용</option></select>
      <div class="search"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
        <input id="qm" type="search" placeholder="법령명, 자격명칭, 상세내용 검색을 통해 관심내용을 확인하세요!" aria-label="검색"></div>
    </div>
  </div></div>
  <main><div class="wrap"><div class="grid" id="grid-m">@@M_CARDS@@</div><p class="noresult" id="nores-m">조건에 맞는 법령이 없습니다.</p></div></main>
</section>

<!-- ===== 화면2: 자격증 우대사항 ===== -->
<section id="view-radar" hidden>
  <div class="hero"><div class="wrap">
    <div class="eyebrow">🚉 자격증 정거장에서 출발하기</div>
    <h1>내 자격증, 어떤 법에서 우대받나요?</h1>
    <p class="lead">자격증을 고르면 그 자격을 우대(의무고용·직무권한·인사우대·시험면제)하는 법령과 근거 조문을 한눈에 봅니다.</p>
  </div></div>
  <div class="wrap">
    <details class="clsguide" open>
      <summary><span class="cg-ic">📊</span> 분류 체계 안내 <span class="cg-sub">— 상세 화면의 분류 표기는 이렇게 읽어요</span><span class="cg-arrow">▾</span></summary>
      <div class="cg-body">
        <div class="cg-block">
          <div class="cg-head">우대분류 <span>법령이 자격에 부여하는 우대의 성격</span></div>
          <table class="cg-tbl">
            <tr><th>분류</th><th>활용 유형</th></tr>
            <tr><td><span class="cg-tag" style="--c:#C0492F">의무고용</span></td><td>사업체를 <b>등록·허가</b>하기 위해 자격 취득자를 고용(배치)해야 하는 경우. 조사·검사·검정·관리 업무의 민간 위탁 대상 기관 지정도 포함.</td></tr>
            <tr><td><span class="cg-tag" style="--c:#1F6FB2">직무권한부여</span></td><td>고용을 전제하지 않고 <b>자격자만 수행</b>할 수 있는 직무(확인·측정, 서류 작성·검토, 능력 산정, 업무 책임 등). 위원 위촉·선발·임명도 포함.</td></tr>
            <tr><td><span class="cg-tag" style="--c:#0F6E56">인사우대</span></td><td><b>채용</b>(임용 특전·시험과목 면제·점수 가산·경력경쟁채용·전직시험), <b>보수</b>(특수업무수당·노임단가 가산), <b>평정·승진</b>(가산점) 우대.</td></tr>
            <tr><td><span class="cg-tag" style="--c:#5B4BB0">시험면제</span></td><td>자격 <b>취득을 위한 시험(검정)에서의 면제</b>. (채용 관련 시험면제는 인사우대로 분류)</td></tr>
            <tr><td><span class="cg-tag" style="--c:#8A8F98">기타</span></td><td>직접적인 자격 우대에 해당하지 않는 경우.</td></tr>
          </table>
        </div>

        <div class="cg-row2">
          <div class="cg-block">
            <div class="cg-head">정책 관점 · Track 1 <span>① 자격을 다루는 방식 (취급유형)</span></div>
            <table class="cg-tbl">
              <tr><th>코드</th><th>유형 · 정의</th></tr>
              <tr><td><span class="cg-code">A</span></td><td><b>신분형성형</b> — 자격이 면허로 전환돼 평생 직업·신분 부여 (건설기계조종사, 이용사·미용사)</td></tr>
              <tr><td><span class="cg-code">B</span></td><td><b>영업요건형</b> — 사업 등록·허가·지정 시 자격자 보유 의무 (건설업·측량업 등록)</td></tr>
              <tr><td><span class="cg-code">C</span></td><td><b>직역독점형</b> — 특정 직무를 자격자만 수행(선임·배치·서명) (안전관리자, 환경기술인)</td></tr>
              <tr><td><span class="cg-code">D</span></td><td><b>인사가산형</b> — 채용·승진·평정·보수 부가 우대 (공무원 가점, 노임 가산)</td></tr>
              <tr><td><span class="cg-code">E</span></td><td><b>검정연계형</b> — 타 자격·시험의 응시자격·면제 연계 (시험 면제)</td></tr>
              <tr><td><span class="cg-code dim">Z</span></td><td><b>제외</b> — 자격을 직접 다루지 않는 조항</td></tr>
            </table>
          </div>
          <div class="cg-block">
            <div class="cg-head">정책 관점 · Track 1 <span>② 경력이음 위험도 (모순 강도)</span></div>
            <table class="cg-tbl">
              <tr><th>코드</th><th>강도 · 정의</th></tr>
              <tr><td><span class="cg-code">N</span></td><td><b>무관</b> — 자격이 직역 진입 조건 아님(부가우대만)</td></tr>
              <tr><td><span class="cg-code">L</span></td><td><b>저위험</b> — 학력·경력·유사자격으로 우회 가능</td></tr>
              <tr><td><span class="cg-code">M</span></td><td><b>중위험</b> — 복수 자격 중 택일로 대체 가능</td></tr>
              <tr><td><span class="cg-code warn">H</span></td><td><b>고위험 ★</b> — 자격+경력 동시 요구(경력이음 도입 시 모순)</td></tr>
              <tr><td><span class="cg-code danger">C</span></td><td><b>임계위험 ★★</b> — 단일 자격만 인정(대체 경로 없음)</td></tr>
              <tr><td><span class="cg-code dim">X</span></td><td><b>해당없음</b> — 취급유형이 Z일 때 (Z↔X 짝)</td></tr>
            </table>
          </div>
        </div>

        <div class="cg-block">
          <div class="cg-head">국민 취업 정보 관점 · Track 2 <span>구직자에게 주는 노동시장 효용 (11종)</span></div>
          <table class="cg-tbl cg-t2">
            <tr><th>영역</th><th>코드</th><th>세부유형 · 정의</th></tr>
            <tr><td rowspan="2" class="cg-area" style="--c:#1F6FB2">Ⅰ 직업창출형<br><small>자격 자체가 직업</small></td><td><span class="cg-code">Ⅰ-1</span></td><td><b>면허전환형</b> — 자격→면허 발급으로 평생 직업·신분</td></tr>
            <tr><td><span class="cg-code">Ⅰ-2</span></td><td><b>개업창업형</b> — 자격자가 단독 수행(확인·서명·진단) → 1인 사업 가능</td></tr>
            <tr><td rowspan="5" class="cg-area" style="--c:#2E8B6F">Ⅱ 취업관문형<br><small>자격이 채용 요건</small></td><td><span class="cg-code">Ⅱ-1</span></td><td><b>등록필수형</b> — 사업체 등록·허가 시 자격자 보유 의무</td></tr>
            <tr><td><span class="cg-code">Ⅱ-2</span></td><td><b>지정인력형</b> — 지정·위탁·대행 기관 인력 요건(검사·인증)</td></tr>
            <tr><td><span class="cg-code">Ⅱ-3</span></td><td><b>전속배치형</b> — 단일 자격자만 선임(대체 불가, 매우 드묾)</td></tr>
            <tr><td><span class="cg-code">Ⅱ-4</span></td><td><b>선택배치형</b> — 복수 자격 중 택일 선임(안전관리자 등)</td></tr>
            <tr><td><span class="cg-code">Ⅱ-5</span></td><td><b>현장배치형</b> — 공사·사업장 규모별 배치 의무</td></tr>
            <tr><td rowspan="3" class="cg-area" style="--c:#C28A2B">Ⅲ 부가우대형<br><small>입직 후 효용</small></td><td><span class="cg-code">Ⅲ-1</span></td><td><b>시험면제</b> — 다른 자격·임용시험 과목 면제</td></tr>
            <tr><td><span class="cg-code">Ⅲ-2</span></td><td><b>인사</b> — 채용·보수·평정·승진 우대(가산점 등)</td></tr>
            <tr><td><span class="cg-code">Ⅲ-3</span></td><td><b>위촉·자문</b> — 위원·심의위원 등 자문성 위촉</td></tr>
            <tr><td class="cg-area dim">Ⅳ 제외</td><td><span class="cg-code dim">Ⅳ-0</span></td><td><b>제외</b> — 중복·삭제·이관·정의 조항 등</td></tr>
          </table>
        </div>
        <p class="cg-note">
          <b>출처</b><br>
          · <b>우대분류</b> — 한국직업능력연구원 「국가기술자격 우대 법령 검토안」(2022.1.3. 기준, 168개 법률 / 383개 조항)<br>
          · <b>정책 관점(Track 1) · 국민 취업 정보 관점(Track 2)</b> — 위 검토안을 토대로 AI 분석을 통해 재구성한 매트릭스 분류 체계 (한국산업인력공단 자격품질관리국 자격품질기획부 검토)
        </p>
      </div>
    </details>
  </div>
  <div class="toolbar"><div class="wrap"><div class="trow">
    <div class="search"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
      <input id="qr" type="search" placeholder="자격증 이름으로 검색 (예: 전기기사)" aria-label="검색"></div>
    <span class="count">자격증 <b id="cntr">0</b>개 <span class="cnt-note">(자격 통폐합·명칭변경 등이 포함된 수치)</span></span>
  </div></div></div>
  <main><div class="wrap">@@NOCERT@@<div class="grid rgrid" id="grid-r">@@R_CARDS@@</div><p class="noresult" id="nores-r">해당 자격증이 없습니다.</p></div></main>
</section>

<footer><div class="wrap"><b>안내</b> · 이 페이지는 AI가 법령 원문을 분석하고 정리하였습니다. 정확한 법적 효력은 반드시 <a href="https://www.law.go.kr" target="_blank" rel="noopener" style="color:var(--accent)">국가법령정보센터</a> 원문을 확인하세요. 출처: 국가법령정보센터 | 생성일 @@BUILT_AT@@ | 한국산업인력공단 실증(PoC)</div></footer>

<div class="modal" id="modal" aria-hidden="true" role="dialog" aria-modal="true"><div class="modal-backdrop"></div><div class="modal-panel"><button class="modal-close" aria-label="닫기">&times;</button><div id="m-body"></div></div></div>
<div class="modal" id="modal2" aria-hidden="true" role="dialog" aria-modal="true"><div class="modal-backdrop"></div><div class="modal-panel"><button class="modal-close" aria-label="닫기">&times;</button><div id="m2-body"></div></div></div>

<script>
var MLAWS=@@MLAWS@@, RCERTS=@@RCERTS@@, RENTRIES=@@RENTRIES@@, T1TYPE=@@T1TYPE@@, T1RISK=@@T1RISK@@, T2=@@T2@@, PFC=@@PFC@@;
var NOCERT=@@NOCERT_JSON@@;
function escq(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c];});}
function tok(v){return String(v||'').split(' ')[0].trim();}
function lawUrl(n){return 'https://www.law.go.kr/법령/'+encodeURIComponent(String(n||'').trim());}
function pfBadge(p){return '<span class="pf" style="--c:'+(PFC[p]||'#8A8F98')+'">'+escq(p)+'</span>';}

// ── 탭 전환 ──
var tabs=[].slice.call(document.querySelectorAll('.tab'));
var views={monitor:document.getElementById('view-monitor'),radar:document.getElementById('view-radar')};
tabs.forEach(function(t){t.addEventListener('click',function(){
  tabs.forEach(function(x){x.classList.remove('active');}); t.classList.add('active');
  for(var k in views) views[k].hidden=(k!==t.dataset.view);
  window.scrollTo(0,0);
});});

// ── monitor 검색/기간 ──
var gm=document.getElementById('grid-m'), mcards=[].slice.call(gm.querySelectorAll('.card'));
var qm=document.getElementById('qm'), scope=document.getElementById('scope');
var mfrom=document.getElementById('mfrom'), mto=document.getElementById('mto'), nresM=document.getElementById('nores-m');
mfrom.value="@@M_DEF_FROM@@"; mto.value="@@M_DEF_TO@@";
MLAWS.forEach(function(o){var cs=(o.certs||[]).join(' ');
  o._law=(o.law||'').toLowerCase(); o._cert=cs.toLowerCase();
  o._det=((o.summary_use||'')+' '+(o.summary_main||'')+' '+(o.meta||'')).toLowerCase();
  o._all=((o.law||'')+' '+(o.meta||'')+' '+cs+' '+(o.summary_use||'')+' '+(o.summary_main||'')).toLowerCase();});
function hay(c){var o=MLAWS[+c.dataset.i];var s=scope.value;return s==='law'?o._law:s==='cert'?o._cert:s==='detail'?o._det:o._all;}
function fmtM(m){return m?m.slice(0,4)+'.'+m.slice(4,6):'';}
function filterM(){var term=(qm.value||'').trim().toLowerCase();var a=mfrom.value,b=mto.value;if(a>b){var t=a;a=b;b=t;}var s=0;
  mcards.forEach(function(c){var on=(c.dataset.month>=a&&c.dataset.month<=b)&&(!term||(hay(c)||'').indexOf(term)!==-1);c.style.display=on?'':'none';if(on)s++;});
  document.getElementById('cnt').textContent=s;document.getElementById('heroN').textContent=s;
  document.getElementById('heroPeriod').textContent=fmtM(a)+' ~ '+fmtM(b)+' 기간';nresM.style.display=s?'none':'block';}
[qm,scope,mfrom,mto].forEach(function(el){el.addEventListener('input',filterM);el.addEventListener('change',filterM);});
filterM();

// ── radar 검색 ──
var gr=document.getElementById('grid-r'), rcards=[].slice.call(gr.querySelectorAll('.card'));
var qr=document.getElementById('qr'), nresR=document.getElementById('nores-r');
function filterR(){var t=(qr.value||'').trim().toLowerCase(),s=0;
  rcards.forEach(function(c){var on=!t||RCERTS[+c.dataset.i].cert.toLowerCase().indexOf(t)!==-1;c.style.display=on?'':'none';if(on)s++;});
  document.getElementById('cntr').textContent=s;nresR.style.display=s?'none':'block';}
qr.addEventListener('input',filterR);filterR();

// ── 모달 ──
var modal=document.getElementById('modal'),mb=document.getElementById('m-body');
var modal2=document.getElementById('modal2'),mb2=document.getElementById('m2-body');
function sec(t,inner){return inner?'<div class="m-sec"><h4>'+t+'</h4>'+inner+'</div>':'';}
function openM(modalEl){modalEl.classList.add('open');modalEl.setAttribute('aria-hidden','false');document.body.style.overflow='hidden';var p=modalEl.querySelector('.modal-panel');if(p)p.scrollTop=0;}
function closeModal(){modal.classList.remove('open');modal.setAttribute('aria-hidden','true');if(!modal2.classList.contains('open'))document.body.style.overflow='';}
function closeModal2(){modal2.classList.remove('open');modal2.setAttribute('aria-hidden','true');if(!modal.classList.contains('open'))document.body.style.overflow='';}

// 종목 미상 우대법령 팝업
function openNocert(){
  if(!NOCERT||!NOCERT.length)return;
  var items=NOCERT.map(function(x){
    var h='<div class="nc-item"><div class="nc-h">'+pfBadge(x.p||'기타')+'<span class="nc-law">'+escq(x.law)+'</span>';
    if(x.e)h+='<span class="law-eff">시행 '+escq(x.e)+'</span>';
    h+='</div>';
    if(x.a)h+='<div class="nc-art">'+escq(x.a)+'</div>';
    if(x.r)h+='<div class="nc-r">📌 '+escq(x.r)+'</div>';
    h+='<a class="nc-ext" href="'+escq(x.u)+'" target="_blank" rel="noopener">법제처에서 원문 확인 →</a></div>';
    return h;
  }).join('');
  mb2.innerHTML='<h2 class="m2-law">🔎 종목 미상 우대법령 <span class="nc-cnt">'+NOCERT.length+'건</span></h2>'
    +'<p class="nc-desc">아래 법령은 국가기술자격 취득자에 대한 <b>우대 조항은 확인되었으나</b>, '
    +'구체적인 자격 종목이 별표·하위 규정·채용공고 등에 위임되어 있어 <b>개별 종목을 특정하지 못한</b> 경우입니다. '
    +'실제 적용 종목은 각 법령 원문(특히 별표)을 직접 확인해 주세요.</p>'+items;
  openM(modal2);
}
var ncBtn=document.getElementById('nocert-open');
if(ncBtn)ncBtn.addEventListener('click',openNocert);

// monitor 법령 상세
function openMonitor(i){var d=MLAWS[i];if(!d)return;
  var certs=(d.certs||[]).map(function(c){return '<span class="chip">'+escq(c)+'</span>';}).join('');
  var arts=(d.articles||[]).map(function(a){return '<li>'+escq(a)+'</li>';}).join('');
  var linkHtml='';
  if(d.artlinks&&d.artlinks.length){var chips='';
    for(var k=0;k<d.artlinks.length;k++){chips+='<a class="artlink" href="'+escq(d.artlinks[k].u)+'" target="_blank" rel="noopener">'+escq(d.artlinks[k].t)+' →</a>';}
    linkHtml='<div class="m-sec"><h4>조문별 원문 바로가기</h4><div class="artlinks">'+chips+'</div></div>';}
  mb.innerHTML='<h2 class="m-title">'+escq(d.law)+'</h2><div class="m-meta">'+escq(d.meta)+'</div>'
    +sec('주요 제·개정 내용',d.summary_main?'<p>'+escq(d.summary_main)+'</p>':'')
    +sec('자격증 활용 분석',d.summary_use?'<p>'+escq(d.summary_use)+'</p>':'')
    +sec('관련 자격종목 ('+((d.certs||[]).length)+'개)',certs?'<div class="m-chips">'+certs+'</div>':'<p class="m-none">없음</p>')
    +sec('근거 조문',arts?'<ul class="m-arts">'+arts+'</ul>':'<p class="m-none">표기된 조문 없음</p>')
    +linkHtml
    +'<a class="m-ext" href="'+escq(d.url)+'" target="_blank" rel="noopener">법제처에서 원문 보기 →</a>';
  openM(modal);}
// radar 자격증 상세(1차)
function openCert(i){var d=RCERTS[i];if(!d)return;
  var pfs=(d.prefs||[]).map(pfBadge).join('');
  var laws=(d.idx||[]).map(function(ei){var l=RENTRIES[ei];var t2n=(T2[l.t2]||[l.t2])[0];var tags='';
    if(l.t2)tags+=' <span class="tag-t2">'+escq(l.t2+' '+t2n)+'</span>';
    if(l.s)tags+=' <span class="tag-sjb">중처법</span>';
    return '<div class="law" data-ei="'+ei+'"><div class="law-h">'+pfBadge(l.p)+'<span class="law-name">'+escq(l.law)+'</span>'+tags+'<span class="law-go">상세 →</span></div><div class="law-m">'+escq(l.a)+(l.e?'<span class="law-eff">시행 '+escq(l.e)+'</span>':'')+'</div></div>';
  }).join('');
  mb.innerHTML='<h2 class="m-cert">'+escq(d.cert)+'</h2>'
    +'<div class="m-pfs">'+pfs+'</div><div class="m-sec"><h4>이 자격증을 우대하는 법령 ('+(d.idx||[]).length+'건)</h4>'+laws+'</div>';
  openM(modal);}
// radar 법령 상세(2차)
function trkBlock(k,code,name,desc,sub){return '<div class="trk"><div class="k">'+k+'</div><div class="v">'+escq(code)+(name?' · '+escq(name):'')+(sub?' <span class="sub">('+escq(sub)+')</span>':'')+'</div>'+(desc?'<div class="d">'+escq(desc)+'</div>':'')+'</div>';}
function openLaw(ei){var l=RENTRIES[ei];if(!l)return;
  var h='<h2 class="m2-law">'+escq(l.law)+'</h2><div class="m2-art">'+escq(l.a)+(l.e?' · 시행 '+escq(l.e):'')+'</div><div class="m-pfs" style="margin-top:12px;">'+pfBadge(l.p)+'</div>';
  h+='<div class="m-sec"><h4>상세 분석 결과</h4>'+(l.d?'<p style="margin:0;font-size:14.5px;line-height:1.7;">'+escq(l.d)+'</p>':'<p style="margin:0;font-size:14px;color:var(--muted);">상세 분석 결과는 일일 분석(관련법령) 연동 시 표시됩니다.</p>')+'</div>';
  if(l.r)h+='<div class="m-sec note-sec"><h4>📌 참고: 직접 확인이 필요한 내용</h4><p style="margin:0;font-size:13.5px;line-height:1.65;color:#8A5A00;">'+escq(l.r)+'</p></div>';
  var tt=T1TYPE[l.t1],tr=T1RISK[l.t1r];
  h+='<div class="m-sec"><h4>정책 관점 분류 (Track 1)</h4>';
  if(tt)h+=trkBlock('자격을 다루는 방식 · 취급유형',l.t1,tt[0],tt[1]);
  if(tr)h+=trkBlock('경력이음 위험도',l.t1r,tr[0],tr[1]);
  if(!tt&&!tr)h+='<p class="law-m">분류 정보 없음</p>';h+='</div>';
  var t2=T2[l.t2];h+='<div class="m-sec"><h4>국민 취업정보 관점 분류 (Track 2)</h4>';
  if(t2)h+=trkBlock('노동시장 효용 · 효용코드',l.t2,t2[0],t2[2],t2[1]);else h+='<p class="law-m">분류 정보 없음</p>';h+='</div>';
  if(l.lk&&l.lk.length){h+='<div class="m-sec"><h4>조문별 원문 바로가기</h4><div class="artlinks">';
    for(var k=0;k<l.lk.length;k++){h+='<a class="artlink" href="'+escq(l.lk[k].u)+'" target="_blank" rel="noopener">'+escq(l.lk[k].t)+' →</a>';}
    h+='</div></div>';}
  h+='<a class="m2-ext" href="'+escq(lawUrl(l.law))+'" target="_blank" rel="noopener">법제처에서 원문 보기 →</a>';
  mb2.innerHTML=h;openM(modal2);}

gm.addEventListener('click',function(e){var t=e.target.closest('.title-btn,.detail-link');if(!t)return;var c=t.closest('.card');if(c)openMonitor(+c.dataset.i);});
gr.addEventListener('click',function(e){var t=e.target.closest('.title-btn,.detail-link');if(!t)return;var c=t.closest('.card');if(c)openCert(+c.dataset.i);});
mb.addEventListener('click',function(e){var law=e.target.closest('.law');if(law)openLaw(+law.dataset.ei);});
modal.addEventListener('click',function(e){if(e.target.classList.contains('modal-backdrop')||e.target.closest('.modal-close'))closeModal();});
modal2.addEventListener('click',function(e){if(e.target.classList.contains('modal-backdrop')||e.target.closest('.modal-close'))closeModal2();});
document.addEventListener('keydown',function(e){if(e.key==='Escape'){if(modal2.classList.contains('open'))closeModal2();else closeModal();}});
</script></body></html>
"""

if __name__ == "__main__":
    main()
