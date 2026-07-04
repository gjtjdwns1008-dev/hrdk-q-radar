# -*- coding: utf-8 -*-
"""
briefing_pdf.py — Q-RADAR 이슈브리핑 'PDF 디자인판' 빌더
=============================================================================
· 디자인 언어: 네비게이터 B안(지하철 노선도) — 우대분류=노선 컬러, 위험도=신호 뱃지,
  사례=역(驛) 카드. 시안(mockup) CSS를 그대로 상속.
· 역할 분담: docx = 결재·편집용 원본 / PDF = 대표 디자인판. 이 모듈이 없거나
  weasyprint가 없으면 briefing_maker가 자동으로 건너뛴다 (docx·xlsx는 항상 발송).
· 의존: weasyprint, 한글 폰트(Noto Sans CJK) — run-briefing.yml에서 설치.
"""
import os
import re
import html
from collections import Counter
from pathlib import Path

# ── 팔레트 ──────────────────────────────────────────────────────────
NAVY = "#1F3864"
PREF_LINE = {"의무고용": "#E24A4A", "직무권한부여": "#2E6FD8",
             "인사우대": "#2FA36B", "시험면제": "#8A5FC0"}
PREF_GRAY = "#98A0AA"
RISK_BG = {"C": "#C62828", "H": "#E8702A", "M": "#D9A800", "L": "#7CB342", "N": "#9E9E9E"}
RISK_KO = {"C": "임계", "H": "고위험", "M": "중위험", "L": "저위험", "N": "무관"}
TYPE_KO = {"A": "신분형성", "B": "영업요건", "C": "직역독점", "D": "인사가산", "E": "검정연계"}


def esc(s):
    return html.escape(str(s or ""), quote=True)


def _code(v):
    return str(v or "").strip().split(" ")[0].split("(")[0].strip()


def _no_krivet(s):
    t = re.sub(r"[\(\[〔（][^\)\]〕）]*직능연[^\)\]〕）]*[\)\]〕）]", "", str(s or ""))
    t = re.sub(r"직능연\s*(기준|출처)?\s*[:：]?", "", t)
    return re.sub(r"\s{2,}", " ", t).strip(" ,·/|-")


def _cut(s, limit):
    s = str(s or "").strip()
    if len(s) <= limit:
        return s
    cut = s[:limit]
    for mark in ("다.", "."):
        i = cut.rfind(mark)
        if i >= limit * 0.5:
            return cut[:i + len(mark)]
    return cut.rstrip() + "…"


def _summ_certs(s, n=2):
    items = [c.strip() for c in str(s or "").split(",") if c.strip() and c.strip() != "없음"]
    if not items:
        return "-"
    if len(items) <= n:
        return " · ".join(items)
    return " · ".join(items[:n]) + f" 외 {len(items) - n}종"


def _img_uri(path):
    try:
        p = Path(path)
        if p.exists():
            return p.resolve().as_uri()
    except Exception:
        pass
    return ""


# ── CSS (시안 v2 상속 + 본문 확장) ──────────────────────────────────
_CSS = """
  @page cover { size: A4; margin: 0; }
  @page content { size: A4; margin: 13mm 14mm 16mm;
          @bottom-right { content: "Q-RADAR · " counter(page) " / " counter(pages);
                          font-size: 8pt; color: #98A0AA;
                          font-family: 'Noto Sans CJK KR', sans-serif; } }
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family:'Noto Sans CJK KR','Noto Sans KR',sans-serif; color:#222; }
  .cover { page: cover; width:210mm; height:297mm; position:relative; page-break-after:always; overflow:hidden; }
  .body { page: content; }
  .navy { color:#1F3864; }

  .hero { background:#1F3864; color:#fff; padding:22mm 18mm 14mm; height:118mm; position:relative; }
  .brand { font-size:10pt; letter-spacing:3px; color:#9FB4D8; }
  .title { font-size:29pt; font-weight:900; margin-top:6mm; line-height:1.25; }
  .title small { display:block; font-size:12.5pt; font-weight:400; color:#C9D6EC; margin-top:2mm; }
  .issue { position:absolute; right:18mm; top:22mm; text-align:right; }
  .issue .no { font-size:22pt; font-weight:900; color:#FFD166; }
  .issue .dt { font-size:9pt; color:#9FB4D8; margin-top:1mm; }
  .lines { position:absolute; left:0; right:0; bottom:0; height:34mm; }
  .line { position:relative; height:3.2mm; margin-top:4.6mm; border-radius:2mm; opacity:.95; }
  .dot { position:absolute; top:50%; width:4.6mm; height:4.6mm; margin-top:-2.3mm; background:#fff;
         border:1.1mm solid #1F3864; border-radius:50%; }
  .l1{width:96%;} .l2{width:88%; margin-left:6%;} .l3{width:76%; margin-left:2%;} .l4{width:60%; margin-left:12%;}

  .kpis { padding:11mm 18mm 0; }
  .kpi { display:inline-block; width:31%; margin-right:2.5%; background:#F4F7FB; border-radius:3mm;
         padding:6.5mm 6mm; vertical-align:top; border-top:2.2mm solid #1F3864; }
  .kpi:last-child { margin-right:0; border-top-color:#E24A4A; }
  .kpi .n { font-size:23pt; font-weight:900; color:#1F3864; }
  .kpi:last-child .n { color:#C62828; }
  .kpi .t { font-size:9.5pt; color:#5A6472; margin-top:1mm; }
  .kpi .s { font-size:8pt; color:#98A0AA; margin-top:.6mm; }

  .toc { padding:11mm 18mm 0; }
  .toc h3 { font-size:11pt; color:#1F3864; letter-spacing:2px; margin-bottom:5.5mm; }
  .stops { position:relative; margin-left:4mm; }
  .stops:before { content:""; position:absolute; left:2.2mm; top:2mm; bottom:6mm; width:1.6mm;
                  background:#1F3864; border-radius:1mm; }
  .stop { position:relative; padding:0 0 7mm 12mm; }
  .stop .st { position:absolute; left:0; top:0; width:6mm; height:6mm; background:#fff;
              border:1.6mm solid #1F3864; border-radius:50%; }
  .stop.hl .st { border-color:#E24A4A; }
  .stop b { font-size:11.5pt; color:#1F3864; }
  .stop.hl b { color:#C62828; }
  .stop span { display:block; font-size:8.8pt; color:#5A6472; margin-top:.8mm; }
  .cfoot { position:absolute; bottom:10mm; left:18mm; right:18mm; font-size:8pt; color:#98A0AA;
           border-top:.4mm solid #E3E8EF; padding-top:3mm; }

  .part { page-break-before:always; }
  .part-hd { background:#1F3864; color:#fff; border-radius:3mm; padding:5mm 6mm; margin-bottom:6mm; }
  .part-hd h1 { font-size:16pt; font-weight:900; }
  .part-hd .sub { font-size:9pt; color:#C9D6EC; margin-top:1mm; }
  .sect { margin-bottom:7mm; }
  .sect h2 { font-size:12.5pt; color:#1F3864; font-weight:900; padding-left:4mm;
             border-left:2.6mm solid #1F3864; margin-bottom:3.5mm; }
  .sect h2 em { font-style:normal; color:#98A0AA; font-size:8.6pt; font-weight:400; margin-left:3mm; }
  .fw { background:#F4F7FB; border-radius:2.5mm; padding:3.5mm 4mm; font-size:9.4pt;
        line-height:1.6; color:#3A4454; }
  .charts img { width:48.5%; margin-right:1.5%; vertical-align:top; }

  .gcap { font-size:9.5pt; font-weight:900; color:#1F3864; margin:2.5mm 0 1.5mm; }
  .tpill { display:inline-block; color:#fff; border-radius:2mm; padding:.4mm 2.2mm;
           font-size:8pt; font-weight:900; margin-right:2mm; vertical-align:.3mm; }
  table.guide { width:100%; border-collapse:collapse; font-size:8.4pt; }
  .guide th { background:#1F3864; color:#fff; padding:2mm 2.4mm; text-align:left; font-weight:700; }
  .guide td { padding:1.8mm 2.4mm; border-bottom:.35mm solid #E7ECF3; color:#444; }
  .guide td.g { background:#EDF2F9; color:#1F3864; font-weight:700; width:20mm; }
  .guide td.c { color:#1F3864; font-weight:700; width:36mm; }

  .bar { margin:2.4mm 0; }
  .bar .nm { display:inline-block; width:27mm; font-size:9pt; font-weight:700; color:#3A4454; vertical-align:middle; }
  .bar .tr { display:inline-block; width:118mm; height:5mm; background:#EFF3F8; border-radius:2.5mm;
             vertical-align:middle; position:relative; }
  .bar .fl { display:block; height:5mm; border-radius:2.5mm; position:relative; }
  .bar .fl .dot { right:-1mm; border-width:1mm; width:3.8mm; height:3.8mm; margin-top:-1.9mm; }
  .bar .ct { display:inline-block; width:13mm; text-align:right; font-size:10pt; font-weight:900;
             color:#1F3864; vertical-align:middle; }

  .chips { font-size:9pt; color:#3A4454; line-height:2.15; }
  .chips b { color:#1F3864; margin-right:1.6mm; }
  .srow { margin:0 0 2.2mm; }
  .slab { display:inline-block; width:20mm; font-size:8.6pt; font-weight:900; color:#5A6472;
          vertical-align:2px; }
  .stat { display:inline-block; background:#EDF2F9; color:#1F3864; border-radius:2.2mm;
          padding:.8mm 2.6mm; font-size:8.6pt; margin:0 1.4mm 1.2mm 0; }
  .stat b { font-weight:900; }
  .stat .n { font-weight:900; font-size:9.6pt; margin-left:1.2mm; }
  .stat.t2 { background:#FBECEC; color:#B03A3A; }
  .stat.gy { background:#F1F3F6; color:#4A5462; }
  .chip { display:inline-block; padding:.5mm 2.4mm; border-radius:3mm; color:#fff; font-size:8pt;
          font-weight:700; margin-right:1.4mm; }

  .card { position:relative; border:.4mm solid #E3E8EF; border-radius:3mm; padding:4mm 5mm 4mm 9mm;
          margin-bottom:4mm; background:#fff; page-break-inside:avoid; }
  .card .rail { position:absolute; left:0; top:0; bottom:0; width:3.2mm; border-radius:3mm 0 0 3mm; }
  .card .stdot { position:absolute; left:.6mm; top:5.6mm; width:4.4mm; height:4.4mm; background:#fff;
                 border:1.1mm solid #1F3864; border-radius:50%; }
  .card h4 { font-size:11pt; color:#1F3864; }
  .badge { display:inline-block; font-size:7.4pt; font-weight:900; color:#fff; border-radius:2mm;
           padding:.5mm 2mm; margin-left:2mm; vertical-align:1px; }
  .b-sap { background:#7A1F1F; }
  .b-up { background:#2E6FD8; } .b-down { background:#C62828; }
  .meta { font-size:8.5pt; color:#5A6472; margin:1.4mm 0; }
  .meta .pill { background:#EDF2F9; color:#1F3864; font-weight:700; border-radius:2mm;
                padding:.3mm 2mm; margin-right:2mm; }
  .desc { font-size:8.9pt; color:#444; line-height:1.55; }
  .imp { font-size:8.9pt; color:#444; line-height:1.6; margin:1mm 0; }
  .imp b { color:#1F3864; }
  .blk { font-size:8.6pt; color:#4A5462; line-height:1.55; margin-top:1.4mm; }
  .blk b { color:#E8702A; }
  .why { margin-top:2mm; font-size:8.3pt; color:#1F3864; background:#F4F7FB;
         border-left:1.6mm solid #FFD166; padding:1.4mm 2.6mm; border-radius:0 2mm 2mm 0; }
"""

_T1_TYPE = [
    ("A 신분형성형", "자격 취득자만 해당 명칭·신분을 사용할 수 있음"),
    ("B 영업요건형", "기업이 사업을 등록·지정받으려면 자격자 고용이 요건"),
    ("C 직역독점형", "특정 업무·행위는 자격자만 수행 가능"),
    ("D 인사가산형", "채용·보수·승진 등에서 가점 부여"),
    ("E 검정연계형", "다른 시험의 응시자격 부여 또는 과목 면제"),
]
_T1_RISK = [
    ("C 임계위험", "오직 단일 자격만 인정하고 대체 경로가 전혀 없음"),
    ("H 고위험", "'자격 + 경력 N년'을 동시에 요구"),
    ("M 중위험", "복수의 자격을 OR 조건으로 대체 가능"),
    ("L 저위험", "관련 학과 졸업 + 경력 등으로 진입 우회 가능"),
    ("N 무관", "직역 진입을 막지 않는 단순 부가우대"),
]
_GUIDE_T2 = [
    ("Ⅰ 직업창출", "Ⅰ-1 면허전환형", "자격 취득이 행정청 면허 발급으로 이어져 평생 직업·신분 부여"),
    ("", "Ⅰ-2 개업창업형", "자격자 본인이 단독으로 직무 수행·서명 가능 — 1인 사업 가능"),
    ("Ⅱ 취업관문", "Ⅱ-1 등록필수형", "사업체 등록·허가 시 자격자 보유가 의무"),
    ("", "Ⅱ-2 지정인력형", "검사·검정·인증·진단 등 국가 지정기관의 인력 요건"),
    ("", "Ⅱ-3 전속배치형", "해당 자격자만 선임 가능 (대체 불가)"),
    ("", "Ⅱ-4 선택배치형", "법령이 인정하는 복수 자격 중 택일 선임"),
    ("", "Ⅱ-5 현장배치형", "공사·사업장 규모·종별에 따라 현장 단위 배치 의무"),
    ("Ⅲ 부가우대", "Ⅲ-1 시험면제", "다른 자격·시험의 응시요건 완화나 과목 면제"),
    ("", "Ⅲ-2 인사우대", "채용·승진·보수에서의 가점·우대"),
    ("", "Ⅲ-3 위촉·자문", "위원 위촉, 자문·심의 참여 자격 부여"),
]


def _guide_table(caption, pill, pill_bg, rows, grouped=False):
    """캡션(Track 배지) + 표. grouped=True면 구분 열 포함 3열, 아니면 2열."""
    cap = (f'<div class="gcap"><span class="tpill" style="background:{pill_bg}">{esc(pill)}</span>'
           f'{esc(caption)}</div>')
    if grouped:
        tr = ['<tr><th style="width:22mm">구분</th><th style="width:34mm">코드·명칭</th><th>의미</th></tr>']
        for g, c, d in rows:
            tr.append(f'<tr><td class="g">{esc(g)}</td><td class="c">{esc(c)}</td><td>{esc(d)}</td></tr>')
    else:
        tr = ['<tr><th style="width:34mm">코드·명칭</th><th>의미</th></tr>']
        for c, d in rows:
            tr.append(f'<tr><td class="c">{esc(c)}</td><td>{esc(d)}</td></tr>')
    return cap + '<table class="guide">' + "".join(tr) + "</table>"


def _cover(target_month, total_laws, related_count, high_n, simple_n, preferred, issues_n):
    year, month = target_month[:4], str(int(target_month[4:6]))
    has_total = bool(total_laws) and total_laws > related_count
    total_txt = f"{total_laws:,}" if has_total else "&#8212;"
    total_sub = "법제처 일일 자동 수집" if has_total else "당월 전수 집계 없음 · 일일 집계로 자동 축적"
    risky_n = sum(1 for r in preferred if _code(r.get("Track1_위험도", "")) in ("C", "H"))
    pref_n = len(preferred)
    dots = ('<span class="dot" style="left:24%"></span><span class="dot" style="left:52%"></span>'
            '<span class="dot" style="left:83%"></span>')
    dots2 = '<span class="dot" style="left:34%"></span><span class="dot" style="left:70%"></span>'
    dots3 = '<span class="dot" style="left:45%"></span><span class="dot" style="left:78%"></span>'
    dots4 = '<span class="dot" style="left:58%"></span>'
    return f"""
<div class="cover">
  <div class="hero">
    <div class="brand">HRDK · Q-RADAR MONTHLY</div>
    <div class="title">국가기술자격<br>이슈브리핑
      <small>법령이 자격이 되고, 자격이 일자리가 되는 길을 그리다</small></div>
    <div class="issue"><div class="no">{esc(year)} · {esc(target_month[4:6])}</div>
      <div class="dt">HRDK 자격품질기획부 · Q-RADAR 자동 생성</div></div>
    <div class="lines">
      <div class="line l1" style="background:{PREF_LINE['의무고용']}">{dots}</div>
      <div class="line l2" style="background:{PREF_LINE['직무권한부여']}">{dots2}</div>
      <div class="line l3" style="background:{PREF_LINE['인사우대']}">{dots3}</div>
      <div class="line l4" style="background:{PREF_LINE['시험면제']}">{dots4}</div>
    </div>
  </div>
  <div class="kpis">
    <div class="kpi"><div class="n">{total_txt}</div><div class="t">{esc(month)}월 시행 법령 전수 검토</div>
      <div class="s">{esc(total_sub)}</div></div>
    <div class="kpi"><div class="n">{related_count:,}</div><div class="t">국가기술자격 관련 법령</div>
      <div class="s">연관높음 {high_n} · 단순관련 {simple_n}</div></div>
    <div class="kpi"><div class="n">{pref_n:,}</div><div class="t">자격 우대 신설·변경</div>
      <div class="s">임계·고위험 {risky_n}건 포함</div></div>
  </div>
  <div class="toc">
    <h3>이 달의 노선 안내</h3>
    <div class="stops">
      <div class="stop"><span class="st"></span><b>개요 · 모니터링 요약</b><span>이달의 숫자를 한눈에</span></div>
      <div class="stop"><span class="st"></span><b>제1부 — 자격 활용도 동향</b>
        <span>핵심 이슈 {issues_n}건 심층 분석 (선정 사유 표기) + 분포 차트</span></div>
      <div class="stop hl"><span class="st"></span><b>제2부 — 자격 우대사항 동향</b>
        <span>핵심 사례 {min(5, pref_n)}건 심층 분석 (정책·구직자 관점)</span></div>
      <div class="stop"><span class="st"></span><b>붙임 — 월간 상세목록(xlsx)</b>
        <span>총괄현황표 · 자격활용도분석 · 우대사항분석</span></div>
    </div>
  </div>
  <div class="cfoot">HRDK 지능형 법령 모니터링 시스템 Q-RADAR가 자동 생성 · 담당자 검토를 거쳐 발행되었습니다.</div>
</div>"""


def _part1(foreword, issues, chart_path, field_chart_path):
    charts = ""
    u1, u2 = _img_uri(chart_path), _img_uri(field_chart_path)
    if u1 or u2:
        charts = ('<div class="sect charts">'
                  + (f'<img src="{u1}">' if u1 else "")
                  + (f'<img src="{u2}">' if u2 else "") + "</div>")
    cards = []
    for i, it in enumerate(issues, 1):
        util = str(it.get("활용도_구분", "")).strip()
        bcls = "b-down" if "감소" in util else "b-up"
        imp = "".join(f'<div class="imp"><b>{k}.</b> {esc(line)}</div>'
                      for k, line in enumerate(it.get("impact_3lines", []) or [], 1))
        blks = "".join(f'<div class="blk"><b>{lbl}</b> · {esc(_cut(val, 260))}</div>'
                       for lbl, val in (("추진배경", it.get("bg", "")),
                                        ("주요내용", it.get("main", "")),
                                        ("기대효과", it.get("effect", ""))) if str(val or "").strip())
        why = str(it.get("_선정사유", "")).strip()
        why_html = f'<div class="why">선정 사유 · {esc(why)}</div>' if why else ""
        cards.append(f"""
    <div class="card"><span class="rail" style="background:#2E6FD8"></span><span class="stdot"></span>
      <h4>{i}. {esc(it.get('법령명', ''))} <span class="badge {bcls}">{esc(util or '-')}</span></h4>
      <div class="meta"><span class="pill">관련</span>{esc(_summ_certs(it.get('관련 종목', '')))}
        <span style="color:#98A0AA">|</span> {esc(it.get('소관부처', ''))}</div>
      {imp}{blks}{why_html}
    </div>""")
    return f"""
<div class="body part">
  <div class="part-hd"><h1>제1부 · 자격 활용도 동향</h1>
    <div class="sub">활용도 '대폭 증가/감소' 법령 중 핵심 이슈 — 선정 사유 병기</div></div>
  <div class="sect"><div class="fw">{esc(foreword)}</div></div>
  {charts}
  <div class="sect"><h2>핵심 이슈 심층 분석 <em>{len(issues)}건</em></h2>{''.join(cards)}</div>
</div>"""


def _part2(preferred, pref_foreword):
    pref_n = len(preferred)
    fw = f'<div class="sect"><div class="fw">{esc(pref_foreword)}</div></div>' if pref_foreword else ""
    guide = ('<div class="sect"><h2>분류체계 안내 <em>Track 1·2 읽는 법</em></h2>'
             + _guide_table("취급유형 — 법령이 자격을 다루는 방식", "Track 1 · 정책", NAVY, _T1_TYPE)
             + '<div style="height:2.5mm"></div>'
             + _guide_table("위험도 — 경력이음형 자격제도와 충돌하는 정도", "Track 1 · 정책", NAVY, _T1_RISK)
             + '<div style="height:2.5mm"></div>'
             + _guide_table("효용코드 — 취득자에게 생기는 노동시장 실익", "Track 2 · 구직자", "#C62828",
                            _GUIDE_T2, grouped=True)
             + "</div>")
    if not preferred:
        return f"""
<div class="body part">
  <div class="part-hd"><h1>제2부 · 자격 우대사항 동향</h1>
    <div class="sub">이달 신설·변경된 자격 우대 조항은 없습니다.</div></div>
  {guide}
</div>"""

    # 분포 (노선 막대)
    dist = Counter(_no_krivet(r.get("우대분류", "")) or "기타" for r in preferred)
    mx = max(dist.values())
    bars = []
    for k, v in dist.most_common():
        color = PREF_LINE.get(k, PREF_GRAY)
        w = max(10, int(v / mx * 100))
        bars.append(f'<div class="bar"><span class="nm">{esc(k)}</span>'
                    f'<span class="tr"><span class="fl" style="width:{w}%;background:{color}">'
                    f'<span class="dot"></span></span></span><span class="ct">{v}</span></div>')

    # 현황 칩
    ttype = Counter(_code(r.get("Track1_취급유형", "")) or "-" for r in preferred)
    risk = Counter(_code(r.get("Track1_위험도", "")) or "-" for r in preferred)
    grp = Counter()
    for r in preferred:
        c = _code(r.get("Track2_효용코드", ""))
        for g in ("Ⅰ", "Ⅱ", "Ⅲ"):
            if c.startswith(g):
                grp[g] += 1
                break
    certs = Counter()
    for r in preferred:
        for c in str(r.get("관련 종목", "")).split(","):
            c = c.strip()
            if c and c != "없음":
                certs[c] += 1
    sap = [r for r in preferred if str(r.get("중처법대상", "")).strip() == "대상"]
    tstats = "".join(f'<span class="stat"><b>{k}</b> {TYPE_KO[k]}<span class="n">{ttype[k]}</span></span>'
                     for k in "ABCDE" if ttype.get(k)) or "-"
    rchips = "".join(f'<span class="chip" style="background:{RISK_BG[k]}">{k} {RISK_KO[k]} {risk[k]}</span>'
                     for k in ("C", "H", "M", "L", "N") if risk.get(k)) or "-"
    _GNAME = (("Ⅰ", "직업창출"), ("Ⅱ", "취업관문"), ("Ⅲ", "부가우대"))
    gstats = "".join(f'<span class="stat t2"><b>{g}</b> {nm}<span class="n">{grp[g]}</span></span>'
                     for g, nm in _GNAME if grp.get(g)) or "-"
    cstats = "".join(f'<span class="stat gy">{esc(k)}<span class="n">{v}</span></span>'
                     for k, v in certs.most_common(5)) or "-"
    sap_html = ""
    if sap:
        names = ", ".join(esc(str(r.get("법령명", ""))[:22]) for r in sap[:3])
        more = f" 외 {len(sap) - 3}건" if len(sap) > 3 else ""
        sap_html = (f'<div class="srow"><span class="slab">중처법 연계</span>'
                    f'<span class="chip b-sap">⚠ {len(sap)}건</span> '
                    f'<span style="font-size:8.6pt;color:#5A6472">{names}{more}</span></div>')

    # 사례 카드 (위험도 우선)
    _RORD = {"C": 0, "H": 1, "M": 2, "L": 3, "N": 4}
    _PORD = {"의무고용": 0, "직무권한부여": 1, "인사우대": 2, "시험면제": 3}
    top = sorted(preferred, key=lambda x: (_RORD.get(_code(x.get("Track1_위험도", "")), 9),
                                           _PORD.get(_no_krivet(x.get("우대분류", "")), 9),
                                           str(x.get("법령명", ""))))[:5]
    cards = []
    for i, r in enumerate(top, 1):
        cls = _no_krivet(r.get("우대분류", "")) or "기타"
        rail = PREF_LINE.get(cls, PREF_GRAY)
        rk = _code(r.get("Track1_위험도", ""))
        rbadge = (f'<span class="badge" style="background:{RISK_BG.get(rk, PREF_GRAY)}">위험 {rk} {RISK_KO.get(rk, "")}</span>'
                  if rk else "")
        sapb = ('<span class="badge b-sap">⚠ 중처법</span>'
                if str(r.get("중처법대상", "")).strip() == "대상" else "")
        summ = _cut(r.get("조문 요약", ""), 420)
        det = _cut(r.get("상세 분석 결과", ""), 280)
        det_html = f'<div class="why" style="border-left-color:#C9D6EC">→ {esc(det)}</div>' if det else ""
        cards.append(f"""
    <div class="card"><span class="rail" style="background:{rail}"></span><span class="stdot"></span>
      <h4>{i}. {esc(r.get('법령명', ''))} {rbadge}{sapb}</h4>
      <div class="meta"><span class="pill">{esc(cls)}</span>{esc(_summ_certs(r.get('관련 종목', '')))}
        <span style="color:#98A0AA">|</span> 취급 {esc(r.get('Track1_취급유형', ''))} · 효용 {esc(r.get('Track2_효용코드', ''))}</div>
      <div class="desc">{esc(summ)}</div>{det_html}
    </div>""")

    return f"""
<div class="body part">
  <div class="part-hd"><h1>제2부 · 자격 우대사항 동향</h1>
    <div class="sub">우대 신설·변경 {pref_n}건 — 분포 · 정책(Track 1) · 구직자(Track 2) 관점</div></div>
  {fw}
  {guide}
  <div class="sect"><h2>이달의 우대사항 현황</h2>{''.join(bars)}</div>
  <div class="sect"><h2>분류체계 현황 <em>정책 관점 (Track 1)</em></h2>
    <div class="srow"><span class="slab">취급유형</span>{tstats}</div>
    <div class="srow"><span class="slab">위험도</span>{rchips}</div></div>
  <div class="sect"><h2>분류체계 현황 <em>구직자 관점 (Track 2)</em></h2>
    <div class="srow"><span class="slab">효용 유형</span>{gstats}</div>
    <div class="srow"><span class="slab">다빈도 자격</span>{cstats}</div>{sap_html}</div>
  <div class="sect"><h2>주요 사례 <em>선정 기준: 위험도 상위(임계→고위험 순)</em></h2>{''.join(cards)}</div>
</div>"""


def build_briefing_pdf(target_month, total_laws, related_count, high_n, simple_n,
                       foreword, issues, preferred, pref_foreword,
                       chart_path, field_chart_path, out_path):
    """디자인판 PDF 생성. weasyprint 부재 시 ImportError를 던져 호출부가 스킵하게 한다."""
    from weasyprint import HTML  # 지연 임포트 — 미설치 환경 보호
    total_laws = int(total_laws or 0)
    related_count = int(related_count or 0)
    high_n, simple_n = int(high_n or 0), int(simple_n or 0)
    print("🎨 [4-3] 이슈브리핑 PDF 디자인판 생성 중...")
    doc = ("<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'>"
           f"<style>{_CSS}</style></head><body>"
           + _cover(target_month, total_laws, related_count, high_n, simple_n,
                    preferred, len(issues))
           + _part1(foreword, issues, chart_path, field_chart_path)
           + _part2(preferred, pref_foreword)
           + "</body></html>")
    HTML(string=doc, base_url=os.getcwd()).write_pdf(out_path)
    print(f"   ✅ PDF 저장: {out_path}")
    return out_path
