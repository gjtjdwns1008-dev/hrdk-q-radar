# -*- coding: utf-8 -*-
"""
count_efyd.py — 특정 시행일의 법제처 시행 법령 총수·목록 확인 (읽기 전용 진단, 국내 IP 로컬 실행)
=============================================================================
용도: Q-RADAR가 그날 분석한 건수(예: 10/2 = 247건)와 법제처가 실제로 내려주는 건수를 대조해
      '목록 잘림(수집 중단)' 여부를 확인한다. 시트·레포는 건드리지 않는다.

실행(레포 폴더에서):
  python migrate_tool/count_efyd.py                       ← 기본: 20261002, 기준 법령명 = 아동학대범죄의 처벌 등에 관한 특례법 시행령
  python migrate_tool/count_efyd.py 20261002
  python migrate_tool/count_efyd.py 20261002 "아동학대범죄의 처벌 등에 관한 특례법 시행령"
                                                           ← 기준 법령명 '뒤'(가나다순)에 오는 법령 = 누락 추정분을 따로 보여줌
준비물: 이 폴더의 .env에 LAW_API_KEY (backfill 도구들과 동일)
"""
import sys
from pathlib import Path
import xml.etree.ElementTree as ET
import requests

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import backfill_usage as bu  # .env 로딩 재사용

KEY = bu._ENV.get("LAW_API_KEY", "")
if not KEY:
    print("❌ LAW_API_KEY 없음 — .env 확인"); sys.exit(1)

args = [a for a in sys.argv[1:] if not a.startswith("--")]
DATE = args[0] if args else "20261002"
MARK = args[1] if len(args) > 1 else "아동학대범죄의 처벌 등에 관한 특례법 시행령"
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

names, total_cnt, page = [], None, 1
while True:
    r = requests.get("https://www.law.go.kr/DRF/lawSearch.do", headers=H, timeout=30,
                     params={"OC": KEY, "target": "law", "type": "XML",
                             "efYd": f"{DATE}~{DATE}", "display": 100, "page": page})
    if not r.text.strip():
        print(f"⚠️ {page}페이지 응답이 비어 있음(법제처 빈 응답) — 여기서 중단"); break
    root = ET.fromstring(r.text)
    if total_cnt is None:
        total_cnt = root.findtext(".//totalCnt")
    nodes = root.findall(".//law")
    if not nodes:
        break
    for n in nodes:
        names.append((n.findtext("법령명한글", "").strip(), n.findtext("소관부처명", "").strip(),
                      n.findtext("법령구분명", "").strip()))
    if len(nodes) < 100:
        break
    page += 1

uniq = sorted({n for n, _, _ in names})
print("=" * 64)
print(f"  {DATE[:4]}-{DATE[4:6]}-{DATE[6:]} 시행 법령 — 법제처 totalCnt {total_cnt} · 수신 {len(names)}건 · 중복 이름 제외 {len(uniq)}건")
print("=" * 64)
after = [n for n in uniq if n > MARK]
before = [n for n in uniq if n <= MARK]
print(f"기준 법령명: {MARK}")
print(f"  기준 포함 앞쪽(가나다순): {len(before)}건  ← Q-RADAR 분석분과 대조")
print(f"  기준 뒤쪽(누락 추정):     {len(after)}건")
if after:
    print("\n[누락 추정 목록]")
    for n in after:
        print("  ·", n)
print("\n(참고) 전체 목록은 아래 — 필요하면 복사해서 시트와 대조")
for n in uniq:
    print("   ", n)
