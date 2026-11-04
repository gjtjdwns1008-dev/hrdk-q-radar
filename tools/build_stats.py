#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""자격정보 서비스(Q-PAGE·Q-RADAR) 통합 방문 통계 생성기 — RADAR 레포 상주(tools/build_stats.py)
   매일 아침 GoatCounter 장부의 '일별 사본'을 stats-ledger/에 적층(전 기간 이력 자체 보존)하고,
   원장 + 당일 실시간분으로 관제판 stats-site/index.html을 조판한다. (열쇠·원장·생성은 모두 이 레포,
   Q-PAGE 배포는 산출물을 읽기만 해 /stats로 서빙 — 시크릿·쓰기권한 불요)
   사용: python tools/build_stats.py <Q-PAGE외부HTML> <출력dir> [--ledger stats-ledger] [--demo]
   env : GOATCOUNTER_TOKEN(필수) · GOATCOUNTER_TOKEN_RADAR(선택) · STATS_SINCE(기본 2026-11-04)"""
import io, re, json, os, sys, time, urllib.request, urllib.parse, urllib.error, datetime

SINCE = os.environ.get("STATS_SINCE", "2026-11-04")
SITES = [
    dict(label="Q-PAGE",  desc="자격정보집(상세·원페이지)", site="hrdk-q-page",  tok=["GOATCOUNTER_TOKEN"]),
    dict(label="Q-RADAR", desc="우대법령 네비게이터",       site="hrdk-q-radar", tok=["GOATCOUNTER_TOKEN_RADAR", "GOATCOUNTER_TOKEN"]),
]

def api(sitecfg, path):
    tok = next((os.environ[k] for k in sitecfg["tok"] if os.environ.get(k)), None)
    if not tok: raise RuntimeError("토큰 미설정")
    req = urllib.request.Request("https://%s.goatcounter.com/api/v0%s" % (sitecfg["site"], path),
        headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())

def _clean(t):
    """★검토수정(보안): 방문자 브라우저가 정하는 문자열(제목)에서 HTML 특수문자를 제거하고 길이 제한 —
       관제판은 이 값을 화면에 그대로 끼워 넣으므로(innerHTML) 소독 없이는 악성 스크립트 주입 통로가 된다."""
    return re.sub(r'[<>&"\'`]', "", t)[:60]

def pick(d, *keys, default=0):
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None: return d[k]
    return default

def fetch_day(sitecfg, day):
    """해당 '하루치' 경로별 집계.
    ★검토수정 2차('26.11.04): 공식 명세에서 start·end는 '시각(date-time)'이며 기본 해석이 그날 0시다 —
    날짜만 보내면 start=end=0시, 즉 길이 0짜리 창이 되어 항상 0건이 돌아온다(첫 운영에서 LIVE·0회로 실증).
    고침: ① 창을 'KST 그날 0시 ~ 다음날 0시'로 명시(+09:00, 시 단위 반올림 규칙 충족, '+'는 %2B로 부호화)
         ② 건수는 응답의 날짜별 통계에서 '요청한 그 날짜' 묶음의 daily만 합산 — 창 경계의 이웃 날 버킷을 섞지 않음.\n    전제: GoatCounter 사이트 시간대 = Asia/Seoul (불일치하면 0건+경고 로그).
    limit=100은 상한(1~100). 수치 의미: GoatCounter 기준 '방문자 수'."""
    nxt = (datetime.date.fromisoformat(day) + datetime.timedelta(days=1)).isoformat()
    q = "/stats/hits?start=%s&end=%s&limit=100" % (
        urllib.parse.quote(day + "T00:00:00+09:00", safe=""),
        urllib.parse.quote(nxt + "T00:00:00+09:00", safe=""))
    hits = api(sitecfg, q)
    rows = []; saw_stats = matched = 0
    for h in hits.get("hits") or []:
        st = h.get("stats") or []
        saw_stats += len(st)
        n = sum(x.get("daily") or 0 for x in st if x.get("day") == day)   # 요청한 그 날짜 묶음만(경계·이웃 날 무시)
        if n:
            matched += 1
            rows.append({"path": h.get("path", ""), "title": h.get("title", ""), "n": n})
    if saw_stats and not matched:                                          # 응답은 있는데 날짜 키가 전부 불일치
        print("  (경고: %s 응답의 날짜 키가 %s와 불일치 — GoatCounter 사이트 시간대(Asia/Seoul) 확인)" % (sitecfg["label"], day))
    time.sleep(0.3)   # rate limit 여유(공식 4회/초)
    return rows

def daterange(a, b):
    d = datetime.date.fromisoformat(a)
    while d <= b:
        yield d.isoformat(); d += datetime.timedelta(days=1)

def collect(sitecfg, today, ledger):
    """원장 백필(어제까지) + 합산 + 일별 시계열 + 오늘 실시간분."""
    yday = (datetime.date.fromisoformat(today) - datetime.timedelta(days=1)).isoformat()
    acc = {}; daily = []
    def add(rows, day=None):
        if day is not None: daily.append({"d": day, "n": sum(r["n"] for r in rows)})
        for r in rows:
            k = r["path"]
            e = acc.setdefault(k, {"path": k, "title": r["title"], "n": 0})
            e["n"] += r["n"]
            if r["title"]: e["title"] = r["title"]
    if ledger:
        ldir = os.path.join(ledger, sitecfg["site"]); os.makedirs(ldir, exist_ok=True)
        new = 0
        for day in daterange(SINCE, datetime.date.fromisoformat(yday)):
            fp = os.path.join(ldir, day + ".json")
            if os.path.exists(fp):
                add(json.load(io.open(fp, encoding="utf-8")), day)
            else:
                rows = fetch_day(sitecfg, day)
                io.open(fp, "w", encoding="utf-8").write(json.dumps(rows, ensure_ascii=False))
                add(rows, day); new += 1
        if new: print("  (%s: 일별 원장 %d일치 적층)" % (sitecfg["label"], new))
    else:
        for day in daterange(SINCE, datetime.date.fromisoformat(yday)):
            add(fetch_day(sitecfg, day), day)
    add(fetch_day(sitecfg, today), today)   # 오늘 실시간분(확정 원장은 내일 적층)
    return sorted(acc.values(), key=lambda r: -r["n"]), daily

def demo(sitecfg):
    if sitecfg["site"] == "hrdk-q-page":
        rows = ([{"path": "/q/%04d" % c, "title": "", "n": n} for c, n in
                 [(6335,42),(1530,31),(2177,19),(9542,17),(1750,14),(7785,12),(7916,9),(752,8),(3643,6),(7301,5),(21,4)]]
                + [{"path": "/", "title": "", "n": 57}])
        daily = [{"d": "2026-11-%02d" % d, "n": n} for d, n in
                 [(4,9),(5,21),(6,34),(7,18),(8,26),(9,41),(10,75)]]
        return rows, daily
    raise RuntimeError("radar 미개설 모사")

PAGE = '<!doctype html><html lang="ko"><head><meta charset="utf-8">\n<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">\n<title>자격정보 서비스 방문 통계</title><style>\n:root{--ink:#1F3A5F;--ink2:#3E6DA8;--gold:#B8860B;--line:#C9CDD4;--bg:#FBFBF8;--card:#FFFFFF}\n*{box-sizing:border-box}html{scroll-behavior:smooth}\nbody{margin:0;background:\n  radial-gradient(1200px 500px at 50% -10%,#EEF3FA 0%,transparent 60%),\n  repeating-linear-gradient(0deg,transparent 0 31px,rgba(31,58,95,.035) 31px 32px),\n  repeating-linear-gradient(90deg,transparent 0 31px,rgba(31,58,95,.035) 31px 32px),\n  var(--bg);\n  color:#1c232e;font:15px/1.65 -apple-system,"Malgun Gothic","Noto Sans CJK KR",sans-serif}\n.wrap{max-width:900px;margin:0 auto;padding:36px 20px 70px}\n.mast{text-align:center;border-top:3px double var(--ink);border-bottom:1px solid var(--ink);padding:24px 0 16px;position:relative;background:linear-gradient(180deg,rgba(255,255,255,.7),transparent)}\n.mast:after{content:"";position:absolute;left:20%;right:20%;bottom:4px;height:1px;background:linear-gradient(90deg,transparent,var(--gold),transparent)}\n.mast h1{margin:0;font:700 31px/1.3 "Nanum Myeongjo","Noto Serif CJK KR",serif;color:var(--ink);letter-spacing:.14em}\n.mast .sub{margin-top:8px;color:#5A6472;font-size:12.5px;letter-spacing:.06em}\n.live{display:inline-flex;align-items:center;gap:6px;margin-top:10px;font-size:11.5px;color:var(--ink2);border:1px solid #D7E0EC;background:#fff;border-radius:999px;padding:3px 12px}\n.live i{width:7px;height:7px;border-radius:50%;background:#2E8B57;animation:pulse 1.6s ease-out infinite}\n@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(46,139,87,.5)}70%{box-shadow:0 0 0 8px rgba(46,139,87,0)}100%{box-shadow:0 0 0 0 rgba(46,139,87,0)}}\n.site{margin-top:40px;opacity:0;transform:translateY(16px);transition:opacity .6s ease,transform .6s ease}\n.site.in{opacity:1;transform:none}\nh2{font:700 20px/1.4 "Nanum Myeongjo",serif;color:var(--ink);border-bottom:2px solid var(--ink);padding-bottom:7px;margin:0 0 12px;display:flex;align-items:baseline;gap:10px}\nh2 .desc{font:400 12.5px/1 sans-serif;color:#5A6472;letter-spacing:.04em}\nh2 .badge{margin-left:auto;font:700 10.5px/1 sans-serif;letter-spacing:.1em;color:#fff;background:linear-gradient(135deg,var(--ink),var(--ink2));border-radius:3px;padding:4px 8px}\n.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:16px 0}\n.card{background:var(--card);border:1px solid var(--line);border-top:3px solid var(--ink);padding:14px 16px;box-shadow:0 1px 0 rgba(31,58,95,.04);transition:transform .25s ease,box-shadow .25s ease}\n.card:hover{transform:translateY(-3px);box-shadow:0 10px 22px -14px rgba(31,58,95,.45)}\n.card .k{font-size:12px;color:#5A6472;letter-spacing:.08em}\n.card .v{font:700 27px/1.3 "Nanum Myeongjo",serif;color:var(--ink);font-variant-numeric:tabular-nums}\n.card .v small{font-size:13px;font-weight:400;color:#5A6472;font-family:sans-serif}\nh3{font:700 15px/1.4 "Nanum Myeongjo",serif;color:var(--ink);border-left:4px solid var(--gold);padding-left:10px;margin:22px 0 10px}\n.chart{background:var(--card);border:1px solid var(--line);padding:14px 14px 8px;position:relative}\n.chart svg{display:block;width:100%;height:150px}\n.tip{position:absolute;pointer-events:none;background:var(--ink);color:#fff;font-size:11.5px;padding:5px 9px;border-radius:4px;transform:translate(-50%,-130%);white-space:nowrap;opacity:0;transition:opacity .15s}\ntable{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line)}\ntd{padding:10px;border-bottom:1px solid #EDEEF0;vertical-align:middle}\ntr:last-child td{border-bottom:0}\n.rk{width:40px;text-align:center;font-weight:800;color:var(--ink)}\n.rk b{display:inline-grid;place-items:center;width:24px;height:24px;border-radius:50%;font-size:12px}\n.r1 b{background:linear-gradient(135deg,#F3C14B,#B8860B);color:#fff}.r2 b{background:linear-gradient(135deg,#CFD6DE,#9AA6B4);color:#fff}.r3 b{background:linear-gradient(135deg,#D8A47F,#A0612F);color:#fff}\n.cd{color:#8A93A0;font-size:12px}\n.ct{width:90px;text-align:right;font-weight:800;color:var(--ink);font-variant-numeric:tabular-nums}\n.bar{width:36%}.bar i{display:block;height:11px;width:0;background:linear-gradient(90deg,var(--ink),var(--ink2));border-radius:2px;transition:width 1.1s cubic-bezier(.2,.8,.2,1)}\n.none{text-align:center;color:#8A93A0}\n.wait{border:1.2px dashed #B8BFC9;background:rgba(255,255,255,.7);color:#5A6472;padding:26px;text-align:center;font-size:13.5px}\n.foot{margin-top:40px;padding-top:12px;border-top:1px solid var(--line);color:#5A6472;font-size:12px;display:flex;justify-content:space-between;flex-wrap:wrap;gap:6px}\n@media(max-width:600px){.cards{grid-template-columns:1fr}.bar{display:none}.mast h1{font-size:24px;letter-spacing:.08em}}\n@media print{body{background:#fff}.site{opacity:1;transform:none}.bar i{transition:none}}\n</style></head><body><div class="wrap">\n<div class="mast"><h1>자격정보 서비스 방문 통계</h1>\n<div class="sub">Q-PAGE · Q-RADAR 통합 관제 · 집계 개시 <b id="since"></b> · 쿠키 없는 비식별 집계 · 일별 원장 자체 보존(전 기간 누적)</div>\n<div class="live"><i></i>오늘 실시간분 포함 · 생성 <span id="gen"></span> KST</div></div>\n<div id="sites"></div>\n<div class="foot"><span>원천: GoatCounter(개인정보·쿠키 미수집) · 일별 사본을 저장소에 적층해 전 기간 이력 보존 · 매일 아침 자동 갱신</span><span>© HRDK Q-PAGE</span></div>\n</div>\n<script>\nvar DATA = __DATA__;\nfunction el(t,c,h){var e=document.createElement(t);if(c)e.className=c;if(h!=null)e.innerHTML=h;return e;}\nfunction fmt(n){return (n||0).toLocaleString("ko-KR");}\nfunction countUp(node,to){var t0=null,d=900;function step(ts){if(!t0)t0=ts;var p=Math.min(1,(ts-t0)/d);p=1-Math.pow(1-p,3);node.textContent=fmt(Math.round(to*p));if(p<1)requestAnimationFrame(step);}requestAnimationFrame(step);}\nfunction area(daily){\n  var W=820,H=150,P=26,n=daily.length;\n  var mx=Math.max.apply(null,daily.map(function(d){return d.n;}).concat([1]));\n  function X(i){return n<2?W/2:P+i*(W-2*P)/(n-1);}\n  function Y(v){return H-22-(v/mx)*(H-46);}\n  var pts=daily.map(function(d,i){return X(i)+","+Y(d.n);}).join(" ");\n  var areaP="M"+X(0)+","+(H-22)+" L"+pts.split(" ").join(" L")+" L"+X(n-1)+","+(H-22)+" Z";\n  var dots=daily.map(function(d,i){return \'<circle class="pt" data-d="\'+d.d+\'" data-n="\'+d.n+\'" cx="\'+X(i)+\'" cy="\'+Y(d.n)+\'" r="4" fill="#1F3A5F"/>\';}).join("");\n  var labs=daily.map(function(d,i){var s=d.d.slice(5).replace("-","/");return (n>14&&i%2)?"":\'<text x="\'+X(i)+\'" y="\'+(H-6)+\'" font-size="10" fill="#8A93A0" text-anchor="middle">\'+s+\'</text>\';}).join("");\n  return \'<svg viewBox="0 0 \'+W+\' \'+H+\'" preserveAspectRatio="none">\'\n    +\'<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#3E6DA8" stop-opacity=".35"/><stop offset="1" stop-color="#3E6DA8" stop-opacity="0"/></linearGradient></defs>\'\n    +\'<path d="\'+areaP+\'" fill="url(#g)"/>\'\n    +\'<polyline class="ln" points="\'+pts+\'" fill="none" stroke="#1F3A5F" stroke-width="2.4" stroke-linejoin="round"/>\'\n    +dots+labs+\'</svg>\';\n}\nDATA.sites.forEach(function(s,si){\n  var box=el("div","site");\n  var h2=el("h2",null,s.label+\' <span class="desc">\'+s.desc+\'</span>\'+(s.status==="on"?\'<span class="badge">LIVE</span>\':""));\n  box.appendChild(h2);\n  if(s.status!=="on"){box.appendChild(el("p","wait","집계 적용 대기 중 — 사이트 개설·스니펫 반영 후 자동으로 표출됩니다."));}\n  else{\n    var cards=el("div","cards");\n    [["누적 방문(방문자 수)",s.total," 명"],["종목 상세 조회",s.qsum," 회 · "+fmt(s.qcnt)+"개 종목"],["목록(홈) 조회",s.home," 회"]].forEach(function(c){\n      var card=el("div","card",\'<div class="k">\'+c[0]+\'</div><div class="v"><span class="num">0</span><small>\'+c[2]+\'</small></div>\');\n      card.querySelector(".num").dataset.to=c[1];cards.appendChild(card);});\n    box.appendChild(cards);\n    box.appendChild(el("h3",null,"일별 방문 추이"));\n    var ch=el("div","chart",area(s.daily));var tip=el("div","tip");ch.appendChild(tip);box.appendChild(ch);\n    ch.addEventListener("mousemove",function(ev){var t=ev.target;if(t.classList&&t.classList.contains("pt")){var r=ch.getBoundingClientRect(),b=t.getBoundingClientRect();tip.style.left=(b.left-r.left+4)+"px";tip.style.top=(b.top-r.top)+"px";tip.innerHTML=t.dataset.d+" · <b>"+fmt(+t.dataset.n)+"회</b>";tip.style.opacity=1;}else tip.style.opacity=0;});\n    ch.addEventListener("mouseleave",function(){tip.style.opacity=0;});\n    box.appendChild(el("h3",null,"최다 조회 자격 TOP "+Math.max(s.top.length,1)));\n    var mx=Math.max.apply(null,s.top.map(function(x){return x.n;}).concat([1]));\n    var rows=s.top.map(function(x,i){return \'<tr class="r\'+(i+1)+\'"><td class="rk"><b>\'+(i+1)+\'</b></td><td class="nm">\'+x.nm+\'<span class="cd"> \'+x.c+(x.et==="과정형"?"·과정평가형":"")+\'</span></td><td class="ct">\'+fmt(x.n)+\'</td><td class="bar"><i data-w="\'+(x.n/mx*100).toFixed(0)+\'"></i></td></tr>\';}).join("")||\'<tr><td colspan="4" class="none">아직 상세 조회 기록이 없습니다</td></tr>\';\n    var tb=el("table",null,rows);box.appendChild(tb);\n  }\n  document.getElementById("sites").appendChild(box);\n});\ndocument.getElementById("since").textContent=DATA.since;\ndocument.getElementById("gen").textContent=DATA.gen;\nvar io=new IntersectionObserver(function(es){es.forEach(function(e){if(e.isIntersecting){var b=e.target;b.classList.add("in");\n  b.querySelectorAll(".num").forEach(function(n){countUp(n,+n.dataset.to);});\n  setTimeout(function(){b.querySelectorAll(".bar i").forEach(function(i){i.style.width=i.dataset.w+"%";});},250);\n  b.querySelectorAll(".ln").forEach(function(l){var len=l.getTotalLength();l.style.strokeDasharray=len;l.style.strokeDashoffset=len;l.getBoundingClientRect();l.style.transition="stroke-dashoffset 1.3s ease";l.style.strokeDashoffset=0;});\n  io.unobserve(b);}});},{threshold:.15});\ndocument.querySelectorAll(".site").forEach(function(b){io.observe(b);});\nsetTimeout(function(){document.querySelectorAll(".site:not(.in)").forEach(function(b){b.classList.add("in");b.querySelectorAll(".num").forEach(function(x){x.textContent=fmt(+x.dataset.to);});b.querySelectorAll(".bar i").forEach(function(x){x.style.width=x.dataset.w+"%";});});},2500);\n</script></body></html>\n'

def main():
    src, out = sys.argv[1], sys.argv[2]
    ledger = sys.argv[sys.argv.index("--ledger") + 1] if "--ledger" in sys.argv else None
    H = io.open(src, encoding="utf-8").read()
    _m = re.search(r'<script id="qdata" type="application/json">(.*?)</script>', H, re.S)
    if _m:
        D = json.loads(_m.group(1))
        name = {i["code"]: i["name"] for i in D["items"]}
        etyp = {i["code"]: i["etype"] for i in D["items"]}
    else:                                  # ★검토수정: Q-PAGE 화면 개편 등으로 사전이 없어도 수집은 계속(종목코드로 표기)
        print("  (경고: Q-PAGE 화면에서 종목명 사전을 찾지 못함 — 코드로 표기)")
        name, etyp = {}, {}
    KST = datetime.timezone(datetime.timedelta(hours=9))
    now = datetime.datetime.now(KST); today = now.strftime("%Y-%m-%d")
    sites = []
    for sc in SITES:
        try:
            rows, daily = demo(sc) if "--demo" in sys.argv else collect(sc, today, ledger)
            qs = []
            for r in rows:
                m = re.fullmatch(r"/q/([0-9A-Z]{4})", r["path"])
                if not m: continue
                c = m.group(1)
                nm = name.get(c) or (_clean(r["title"].split(" · ")[0]) if r["title"] else c)
                qs.append(dict(c=c, nm=nm, et=etyp.get(c, ""), n=r["n"]))
            sites.append(dict(label=sc["label"], desc=sc["desc"], status="on",
                              total=sum(r["n"] for r in rows),
                              home=sum(r["n"] for r in rows if r["path"] == "/"),
                              qsum=sum(x["n"] for x in qs), qcnt=len(qs),
                              top=qs[:10], daily=daily))
        except Exception as e:
            print("  (%s: 집계 미수신 → 대기 표시 · %s)" % (sc["label"], type(e).__name__))
            sites.append(dict(label=sc["label"], desc=sc["desc"], status="wait"))
    data = dict(since=SINCE, gen=now.strftime("%Y-%m-%d %H:%M"), sites=sites)
    html = PAGE.replace("__DATA__", json.dumps(data, ensure_ascii=False).replace("<", "\\u003c"))  # ★검토수정(보안): </script> 끼어들기 차단
    os.makedirs(out, exist_ok=True)
    io.open(os.path.join(out, "index.html"), "w", encoding="utf-8").write(html)
    print("[방문 통계] %d개 사이트 섹션 → %s/index.html" % (len(data["sites"]), out))

if __name__ == "__main__": main()
