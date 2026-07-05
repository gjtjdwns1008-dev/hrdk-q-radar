"""
scripts/build_reference_articles.py
-----------------------------------
AI 재분류 원천 자료(정책관점 Track1 + 국민관점 Track2, 각 383건)를
연번으로 병합하여 reference_articles 테이블에 적재합니다.

이 데이터는 하이브리드 검증의 '투트랙 정답지'로 사용됩니다.
(2022.1.3. 직능연 검토안 기준 / 168법률·383조항)

실행:
    python scripts/build_reference_articles.py \\
        --policy 정책관점_3_전체조항재분류.csv \\
        --citizen 국민관점_2_전체조항재분류.csv \\
        --db hrdk_law.db
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd
from hrdk_law_core.db import KnowledgeBase
# Track2 세부유형명은 certs의 매핑을 단일 기준으로 사용
# (원본 CSV의 '신규세부유형'은 Ⅲ을 모두 '부가우대형'으로 뭉뚱그려 놓았으므로,
#  코드를 보고 정확한 세부명으로 보정한다)
from hrdk_law_core.certs import TRACK2_CODE_KO


def readcsv(f):
    try:
        return pd.read_csv(f)
    except UnicodeDecodeError:
        return pd.read_csv(f, encoding="cp949")


def norm_law(s):
    """db.lookup_reference와 동일한 정규화 (소문자화 포함)."""
    return re.sub(r"[\s·\(\)\[\]ㆍ・,]", "", str(s)).strip().lower()


def first_article_no(article_text):
    """조문내역에서 대표 조문번호(첫 번째 제N조) 추출."""
    m = re.search(r"제\d+조(?:의\d+)?", str(article_text))
    return m.group(0) if m else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", required=True, help="정책관점 전체조항재분류 CSV")
    ap.add_argument("--citizen", required=True, help="국민관점 전체조항재분류 CSV")
    ap.add_argument("--db", default="hrdk_law.db")
    args = ap.parse_args()

    pol = readcsv(args.policy)
    nat = readcsv(args.citizen)
    print(f"📂 정책관점 {len(pol)}건, 국민관점 {len(nat)}건 로드")

    # 국민관점을 연번으로 인덱싱 (Track2 코드 매핑용)
    nat_by_seq = {}
    for _, r in nat.iterrows():
        nat_by_seq[r.get("연번")] = r

    kb = KnowledgeBase(args.db)
    n = 0
    for _, r in pol.iterrows():
        seq = r.get("연번")
        nat_row = nat_by_seq.get(seq)

        law_raw = str(r.get("법령명", "")).strip()
        article = str(r.get("조문내역", "")).strip()

        kb.upsert_reference_article({
            "seq": int(seq) if pd.notna(seq) else None,
            "law_name": norm_law(law_raw),
            "law_name_raw": law_raw,
            "article": article,
            "article_no": first_article_no(article),
            # Track 1 (정책)
            "track1_code": str(r.get("1차축_코드", "") or "").strip(),
            "track1_type": str(r.get("1차축_유형", "") or "").strip(),
            "track1_risk_code": str(r.get("2차축_코드", "") or "").strip(),
            "track1_risk": str(r.get("2차축_강도", "") or "").strip(),
            "track1_unified": str(r.get("통합코드", "") or "").strip(),
            "track1_basis": str(r.get("1차축 근거", "") or "").strip(),
            # Track 2 (국민)
            "track2_code": str(nat_row.get("신규코드", "") if nat_row is not None else "").strip(),
            # 세부유형명: 원본 CSV는 Ⅲ을 모두 '부가우대형'으로 뭉뚱그려 놓았으므로
            # 코드(Ⅲ-1/2/3 등)를 보고 certs의 정확한 세부명으로 보정. 매핑에 없으면 원본값 유지.
            "track2_type": TRACK2_CODE_KO.get(
                str(nat_row.get("신규코드", "") if nat_row is not None else "").strip(),
                str(nat_row.get("신규세부유형", "") if nat_row is not None else "").strip()
            ),
            "track2_basis": str(nat_row.get("분류 근거", "") if nat_row is not None else "").strip(),
            "source_year": "2022",
        })
        n += 1

    print(f"\n✅ 적재 완료: {n}건")
    print(f"   기준조항 총 {kb.count_reference_articles()}건")


if __name__ == "__main__":
    main()
