"""
scripts/review_held_laws.py
---------------------------
'버리지 않는 체'에 보류된 법령 목록을 담당자가 점검하는 스크립트.

AI 분석을 건너뛴(보류한) 법령들이 정말 무관한지 사람이 사후 확인하는 용도입니다.
필터가 잘못 걸렀다고 판단되면, 해당 법령을 다시 분석 대상으로 꺼낼 수 있습니다.

실행 방법:
    python scripts/review_held_laws.py --db hrdk_law.db          # 미검토 목록 보기
    python scripts/review_held_laws.py --db hrdk_law.db --all     # 전체 보기
    python scripts/review_held_laws.py --db hrdk_law.db --done 5   # 5번 항목 검토완료 표시
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hrdk_law_core.db import KnowledgeBase


def main():
    parser = argparse.ArgumentParser(description="보류 법령 점검")
    parser.add_argument("--db", default="hrdk_law.db", help="SQLite 경로")
    parser.add_argument("--all", action="store_true", help="검토 완료 포함 전체 보기")
    parser.add_argument("--done", type=int, help="해당 ID를 검토완료 처리")
    args = parser.parse_args()

    kb = KnowledgeBase(args.db)

    if args.done is not None:
        kb.mark_held_reviewed(args.done)
        print(f"✅ {args.done}번 항목을 검토 완료로 표시했습니다.")
        return

    only_unreviewed = not args.all
    held = kb.get_held_laws(only_unreviewed=only_unreviewed)
    total = kb.count_held_laws(only_unreviewed=only_unreviewed)

    label = "미검토" if only_unreviewed else "전체"
    print(f"\n📋 보류 법령 목록 ({label}): {total}건")
    print("=" * 70)
    if not held:
        print("  (없음)")
        return

    for h in held:
        mark = "✔" if h["reviewed"] else " "
        print(f"  [{mark}] #{h['id']:<4} {h['law_name']}")
        print(f"        시행일: {h['enforce_date']} | 부처: {h['ministry']}")
        print(f"        보류사유: {h['hold_reason']}")
        if h["law_link"]:
            print(f"        링크: {h['law_link']}")
        print()

    print("=" * 70)
    print("💡 필터가 잘못 걸렀다고 판단되면, 해당 법령을 법제처에서 직접 확인하거나")
    print("   다음 실행 시 SKIP_KEYWORDS에서 해당 키워드를 빼면 다시 분석됩니다.")
    print("   검토를 마쳤으면: python scripts/review_held_laws.py --done <번호>")


if __name__ == "__main__":
    main()
