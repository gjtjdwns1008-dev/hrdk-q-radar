"""[깃허브용] GitHub Actions 전용 진입점 — 채널 B(pref_export.json) 생성·검산·게시.

■ 실행 위치: GitHub Actions 안에서만. 로컬에서 실행하면 가드가 즉시 중단시킨다
             (진짜 필요할 때만 --force로 우회 — 권장 안 함).
■ 인증: 기존 배치 워크플로와 동일 — GitHub Secrets → 환경변수/키파일 →
        core.get_sheet_client(). 이 파일은 .env를 절대 읽지 않는다.
        로컬 검증·CSV 산출은 run_export_local.py.
■ 출력: dist/pref_export.json — 일 4회 Pages 재빌드에 편승해 고정 URL로 게시(§2.1).
■ 실패 정책(본 열차 우선):
    - 항상 exit 0 → 이 스텝이 실패해도 네비게이터 배포는 막지 않는다(::warning 주석만).
    - keep-last-good: 신규 스냅샷 생성·검산 실패 시 현재 라이브 JSON을 내려받아
      무수정 재게시(asof 미갱신 = '새 스냅샷을 못 만들었다'는 정직 표기).
    - 라이브 확보까지 실패하면 파일 미게시 → Q-Page 검산 실패 → 순정본 폴백(§3)으로 안전.
■ Job Summary: summary.build_summary_md() 결과를 $GITHUB_STEP_SUMMARY 에 기록.
    → 이 런의 Summary 화면 캡처가 Q-Page 최종 요구 증빙이 된다.
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.request
from pathlib import Path

from . import aggregate, codemap, json_writer, ledger_adapter, summary, validate


def _fetch_live(url: str) -> str | None:
    """현재 게시본 회수(keep-last-good용). CDN 캐시 우회를 위해 타임스탬프 부착."""
    try:
        bust = f"{url}?ts={int(aggregate.now_kst().timestamp())}"
        with urllib.request.urlopen(bust, timeout=20) as resp:
            return resp.read().decode("utf-8")
    except Exception:
        return None


def _append_step_summary(md: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(md + "\n")
    else:
        print("[알림] GITHUB_STEP_SUMMARY 미존재 — 이 진입점은 Actions 전용입니다. "
              "로컬은 run_export_local.py 를 사용하세요.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Q-Page pref_export.json (GitHub Actions 전용)")
    ap.add_argument("--out", default="dist/pref_export.json")
    ap.add_argument("--live-url", default=summary.LIVE_URL)
    ap.add_argument("--codemap", default=None, help="종목코드 매핑 CSV 경로(기본: codemap.DEFAULT)")
    ap.add_argument("--force", action="store_true", help="Actions 밖 실행 가드 우회(권장 안 함)")
    args = ap.parse_args()

    # ── 혼용 방지 가드: 로컬에서 실행되면 중단 ──────────────────────
    if os.environ.get("GITHUB_ACTIONS") != "true" and not args.force:
        sys.exit("[중단] run_export_github.py 는 GitHub Actions 전용 진입점입니다. "
                 "로컬 검증·CSV 산출은 run_export_local.py 를 사용하세요.")

    snap = None
    text: str | None = None
    problems: list[str] = []

    try:
        # TODO(확인 5): core 모듈 실제 경로에 맞출 것 (아래는 자리표시)
        from core.sheets import get_sheet_client  # type: ignore
        _, ss = get_sheet_client()   # ⚠ (client, spreadsheet) 튜플 — 언패킹 고정
        code_table = codemap.load_codemap(args.codemap)
        rows = ledger_adapter.fetch_ledger_rows(ss)
        records = list(ledger_adapter.iter_confirmed_records(rows))
        snap = aggregate.build_snapshot(records, code_table)
        if snap.report.unknown_cat:
            problems.append("표기 사전(§2.3)에 없는 성격값 발견: "
                            + ", ".join(snap.report.unknown_cat)
                            + " — 규격 개정 협의 전에는 게시 불가")
        text = json_writer.to_text(json_writer.build_payload(snap))
        problems += validate.validate_export_text(text)
    except Exception as exc:
        problems.append(f"스냅샷 생성 실패: {exc!r}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    kept = False
    if not problems and text is not None:
        out.write_text(text, encoding="utf-8")
        status = "✅ 신규 스냅샷 게시"
    else:
        prev = _fetch_live(args.live_url)
        if prev is not None and not validate.validate_export_text(prev):
            out.write_text(prev, encoding="utf-8")
            status, kept = "🟡 직전 게시본 유지(신규 스냅샷 실패)", True
        else:
            status = "🔴 미게시(직전본 확보 실패) — Q-Page 순정본 폴백에 맡김"
        print(f"::warning::pref_export — {status}")

    out_bytes = out.stat().st_size if out.exists() else None
    md = summary.build_summary_md(status, snap, problems, str(out), out_bytes, kept)
    _append_step_summary(md)
    print(md)
    return 0   # 본 열차(네비게이터 배포) 우선 — 상태는 Summary·warning으로 정직 신고


if __name__ == "__main__":
    raise SystemExit(main())
