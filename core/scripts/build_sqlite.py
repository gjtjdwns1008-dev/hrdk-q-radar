"""
scripts/build_sqlite.py
-----------------------
직능연 우대법령 정리본 CSV(22,772행)를 SQLite 지식베이스로 적재합니다.

실행 방법:
    python scripts/build_sqlite.py \\
        --csv  "2026년_국가기술자격_우대법령_정리본_중대재해처벌법_포함_.csv" \\
        --db   hrdk_law.db

GitHub Actions에서 매년 1회(또는 정리본 갱신 시) 수동 실행합니다.
결과 DB 파일(hrdk_law.db)을 LAW-RADAR 레포 루트에 커밋하거나
Secrets/Artifact로 배포하면 됩니다.
"""

import argparse
import sys
from pathlib import Path

# 패키지 루트를 sys.path에 추가 (로컬 실행용)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
from hrdk_law_core.db import KnowledgeBase


def load_csv(csv_path: str) -> pd.DataFrame:
    """인코딩 자동 감지(UTF-8 → CP949 폴백)로 CSV를 읽습니다."""
    try:
        return pd.read_csv(csv_path, encoding="utf-8")
    except UnicodeDecodeError:
        return pd.read_csv(csv_path, encoding="cp949")


def main():
    parser = argparse.ArgumentParser(description="직능연 정리본 CSV → SQLite 마이그레이션")
    parser.add_argument("--csv", required=True, help="정리본 CSV 파일 경로")
    parser.add_argument("--db",  default="hrdk_law.db", help="SQLite 출력 파일 경로")
    args = parser.parse_args()

    print(f"📂 CSV 읽기: {args.csv}")
    df = load_csv(args.csv)
    print(f"  → {len(df):,}행 로드 완료. 컬럼: {list(df.columns)}")

    # 컬럼 매핑 (CSV 컬럼명 → DB 필드명)
    # 정리본 CSV 컬럼: 종목명, 우대법령, 조문내역, 활용내용, ..., 우대분류, 중처법대상
    required_cols = ["종목명", "우대법령", "우대분류"]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        print(f"❌ 필수 컬럼 누락: {missing}")
        sys.exit(1)

    kb = KnowledgeBase(args.db)
    print(f"📦 SQLite 적재 시작: {args.db}")

    success, skip = 0, 0
    for idx, row in df.iterrows():
        cert_name  = str(row.get("종목명", "")).strip()
        law_name   = str(row.get("우대법령", "")).strip()
        pref_type  = str(row.get("우대분류", "")).strip()

        # 필수 필드 없으면 스킵
        if not cert_name or not law_name or pref_type in ["", "nan", "NaN"]:
            skip += 1
            continue

        # 중처법대상 판단 (NaN이 아니면 True)
        hazard_raw = row.get("중처법대상", "")
        is_hazard  = 0 if str(hazard_raw).strip() in ["", "nan", "NaN"] else 1

        kb.upsert_preference({
            "cert_name":      cert_name,
            "law_name_raw":   law_name,
            "article_text":   str(row.get("조문내역", "")).strip(),
            "usage_content":  str(row.get("활용내용", "")).strip(),
            "preference_type": pref_type,
            "is_hazard_law":  is_hazard,
        })
        success += 1

        if (idx + 1) % 2000 == 0:
            print(f"  ... {idx+1:,}/{len(df):,}행 처리 중")

    # 최종 통계
    stats = kb.count_preference_laws()
    print(f"\n✅ 적재 완료!")
    print(f"  성공: {success:,}건 / 스킵: {skip:,}건")
    print(f"  우대분류별 건수: {stats}")
    print(f"  DB 파일: {Path(args.db).resolve()}")


if __name__ == "__main__":
    main()
