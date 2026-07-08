# -*- coding: utf-8 -*-
"""
probe_annex.py — 별표 태그 구조 진단 프로브 (1회용, 국내 IP 로컬 실행)
=============================================================================
목적: 법제처 법령 상세 XML에서 '별표'가 실제로 어떤 태그·필드로 오는지 실물 확인.
      (별표 심층 수집기가 0건이면 → 이 출력 전체를 복사해 Claude에게 붙여넣기)

실행: python probe_annex.py           ← 기본: 위험물안전관리법 시행규칙(별표 부자)
      python probe_annex.py "법령명"  ← 다른 법령으로도 가능
준비물: .env의 LAW_API_KEY (backfill 도구들과 동일 폴더)
"""
import sys
import re
import shutil
from pathlib import Path
import xml.etree.ElementTree as ET
import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import backfill_usage as bu  # .env 로딩 재사용

H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
KEY = bu._ENV.get("LAW_API_KEY", "")
NAME = sys.argv[1] if len(sys.argv) > 1 else "위험물안전관리법 시행규칙"

print("=" * 64)
print("  별표 구조 진단 프로브")
print("=" * 64)

# 0) 환경 자가진단
try:
    import hrdk_law_core.annex as ax
    print(f"core: 신판(annex 탑재) ✓  {ax.__file__}")
except ImportError:
    print("core: 🚨 구판 (annex 없음) — pip install -e ./core --force-reinstall 필요")
print(f"hwp5txt: {'✓ ' + str(shutil.which('hwp5txt')) if shutil.which('hwp5txt') else '🚨 미설치'}")
if not KEY:
    print("❌ LAW_API_KEY 없음 — .env 확인"); sys.exit(1)

# 1) 법령명 → MST
r = requests.get("https://www.law.go.kr/DRF/lawSearch.do", headers=H, timeout=30,
                 params={"OC": KEY, "target": "law", "type": "XML", "query": NAME, "display": 20})
root = ET.fromstring(r.text)
law = root.find(".//law")
if law is None:
    print(f"❌ '{NAME}' 검색 결과 없음"); sys.exit(1)
mst = law.findtext("법령일련번호", "")
print(f"\n대상: {law.findtext('법령명한글','')} (MST {mst}, 시행 {law.findtext('시행일자','')})")

# 2) 상세 XML
r = requests.get("https://www.law.go.kr/DRF/lawService.do", headers=H, timeout=60,
                 params={"OC": KEY, "target": "law", "MST": mst, "type": "XML"})
xml = r.text
root = ET.fromstring(xml)
print(f"상세 XML 크기: {len(xml):,}자")

# 3) '별표'가 들어간 모든 태그 집계
tags = {}
for el in root.iter():
    t = el.tag
    if isinstance(t, str) and "별표" in t:
        tags[t] = tags.get(t, 0) + 1
print("\n[별표 관련 태그 집계]")
for t, n in sorted(tags.items()):
    print(f"  <{t}> × {n}")
if not tags:
    print("  (없음! — '별표' 문자열이 태그명에 없음)")
    hits = sorted({m for m in re.findall(r"<([가-힣A-Za-z]+)", xml) if "표" in m or "서식" in m})
    print("  참고 — '표/서식' 포함 태그 후보:", hits[:15])

# 4) 별표 단위 노드의 자식 구조 샘플 2건
units = root.findall(".//별표단위") or [el for el in root.iter()
        if isinstance(el.tag, str) and el.tag.startswith("별표") and len(list(el)) > 0][:5]
print(f"\n[별표 단위 후보 노드: {len(units)}개 — 앞 2개의 자식 필드]")
for u in units[:2]:
    print(f"  ── <{u.tag}> ──")
    for c in list(u)[:12]:
        v = (c.text or "").strip().replace("\n", " ")[:60]
        print(f"     <{c.tag}> = {v!r}")

# 5) 파일 링크 후보 필드 전수
print("\n[태그명에 '파일' 또는 '링크' 포함 — 값 샘플]")
found = 0
for el in root.iter():
    t = el.tag
    if isinstance(t, str) and ("파일" in t or "링크" in t):
        v = (el.text or "").strip()
        if v:
            found += 1
            if found <= 8:
                print(f"  <{t}> = {v[:80]}")
if not found:
    print("  (값 있는 파일/링크 필드 없음)")

# 6) 별표내용 채움 실태
contents = root.findall(".//별표내용")
filled = sum(1 for c in contents if (c.text or "").strip())
print(f"\n[별표내용 실태] 노드 {len(contents)}개 중 텍스트 채움 {filled}개")

# 7) 법령 XML에 별표 단서가 빈약하면 → 별도 '별표서식' API 자동 탐침
need_licbyl = (not tags) or (found == 0)
if need_licbyl:
    print("\n[7] 별표서식 전용 API 탐침 (법령 XML에 파일링크 단서 없음 → 별도 API 구조 확인)")
    for tgt in ("licbyl", "licByl"):
        try:
            r = requests.get("https://www.law.go.kr/DRF/lawSearch.do", headers=H, timeout=30,
                             params={"OC": KEY, "target": tgt, "type": "XML",
                                     "query": NAME, "display": 5})
            body = r.text
            print(f"  target={tgt}: HTTP {r.status_code}, {len(body):,}자")
            try:
                rt = ET.fromstring(body)
                items = list(rt)
                print(f"  루트 <{rt.tag}>, 자식 {len(items)}개")
                # 첫 결과 항목의 전체 필드 덤프
                for it in items:
                    if len(list(it)) >= 3:
                        print(f"  ── 첫 항목 <{it.tag}> 필드 전체 ──")
                        for c in list(it)[:20]:
                            v = (c.text or "").strip().replace("\n", " ")[:70]
                            print(f"     <{c.tag}> = {v!r}")
                        break
                break
            except ET.ParseError:
                print("  (XML 파싱 실패 — 원문 앞부분)")
                print("  " + body[:400].replace("\n", " "))
        except Exception as e:
            print(f"  target={tgt} 요청 실패: {str(e)[:60]}")

print("\n" + "=" * 64)
print("  ↑ 이 출력 전체를 복사해서 붙여넣어 주세요 (태그명 교정에 사용)")
print("=" * 64)
