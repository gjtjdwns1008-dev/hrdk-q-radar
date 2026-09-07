"""[로컬용] VSCode/터미널 전용 진입점 — 커밋 전 육안 확인 + 채널 A CSV 수동 산출.

■ 실행 위치: 로컬 PC에서만. GitHub Actions 안에서 실행되면 가드가 즉시 중단시킨다.
■ 인증: .env + gcp-key.json (둘 다 깃허브 업로드 금지 파일) — python-dotenv로 로드.
        레포 루트에서 실행:  python -m migrate_tool.pref_export.run_export_local
■ 출력(레포 루트 기준, .gitignore 대상):
    local_out/pref_export.preview.json           채널 B 미리보기(검산 실패 시 .INVALID 표기)
    local_out/pref_export_{asof}.csv             채널 A — 원장 '9_우대법령' 반입 원본(utf-8-sig)
■ 용도:
    ① '확인 없이 커밋 금지' 원칙 이행 — 이 결과를 육안 확인한 뒤에 워크플로 스텝을 커밋.
    ② Phase 1(심사 전) 연 1회 CSV 수동 완주.
콘솔 리포트는 깃허브 Job Summary와 동일 서식이다(같은 summary 모듈 사용).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import aggregate, codemap, csv_writer, json_writer, ledger_adapter, summary, validate


def main() -> int:
    # ── 혼용 방지 가드: Actions 안에서 실행되면 중단 ─────────────────
    if os.environ.get("GITHUB_ACTIONS") == "true":
        sys.exit("[중단] run_export_local.py 는 로컬 전용 진입점입니다(.env 사용). "
                 "Actions에서는 run_export_github.py 를 사용하세요.")

    ap = argparse.ArgumentParser(description="Q-Page 내보내기 — 로컬 검증 러너")
    ap.add_argument("--out-dir", default="local_out")
    ap.add_argument("--codemap", default=None, help="종목코드 매핑 CSV 경로(기본: codemap.DEFAULT)")
    args = ap.parse_args()

    try:
        from dotenv import load_dotenv
    except ImportError:
        sys.exit("[중단] python-dotenv 미설치 — pip install python-dotenv")
    load_dotenv()   # .env → 환경변수 (core가 이 값으로 인증. core import보다 먼저!)

    # TODO(확인 5): core 모듈 실제 경로에 맞출 것 (아래는 자리표시)
    from core.sheets import get_sheet_client  # type: ignore
    _, ss = get_sheet_client()   # ⚠ (client, spreadsheet) 튜플 — 언패킹 고정

    code_table = codemap.load_codemap(args.codemap)
    rows = ledger_adapter.fetch_ledger_rows(ss)
    records = list(ledger_adapter.iter_confirmed_records(rows))
    snap = aggregate.build_snapshot(records, code_table)

    problems: list[str] = []
    if snap.report.unknown_cat:
        problems.append("표기 사전(§2.3)에 없는 성격값: " + ", ".join(snap.report.unknown_cat))
    text = json_writer.to_text(json_writer.build_payload(snap))
    problems += validate.validate_export_text(text)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    json_name = "pref_export.preview.json" if not problems else "pref_export.preview.INVALID.json"
    json_path = out_dir / json_name
    json_path.write_text(text, encoding="utf-8")

    csv_path = None
    if not problems:
        csv_path = out_dir / f"pref_export_{snap.asof}.csv"
        csv_path.write_text(csv_writer.to_csv_text(snap), encoding="utf-8-sig")

    status = ("✅ 로컬 검산 통과(미리보기 — 게시 아님)" if not problems
              else "🔴 로컬 검산 실패(.INVALID 표기)")
    md = summary.build_summary_md(status, snap, problems, str(json_path),
                                  json_path.stat().st_size, kept_last_good=False)
    print(md)
    if csv_path:
        print(f"채널 A CSV: {csv_path}  (utf-8-sig, 원장 '9_우대법령' 반입 원본)")
    else:
        print("채널 A CSV: 검산 실패로 생성 생략 — 원인 해소 후 재실행")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
