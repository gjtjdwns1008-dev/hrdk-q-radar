"""[로컬용] 내 컴퓨터 전용 진입점 — 커밋 전 육안 확인 + 채널 A CSV 수동 산출.

■ 실행: 이 파일을 그냥 실행하면 됩니다 (백필 도구와 같은 방식).
      VSCode ▶ 버튼, 또는:  python "run_export_local.py 파일의 전체 경로"
      (예전 방식인  python -m ...run_export_local  도 여전히 동작)
■ 준비물(이 파일과 같은 폴더, 백필과 동일):
      .env          — 시트 ID 등 (백필 .env 복사해오면 됨)
      gcp-key.json  — 구글 서비스계정 키 (백필 것과 동일 파일)
■ 인증 방식: 백필 도구(run_local_backfill.py)와 동일 —
      .env를 직접 읽어 환경변수로 넣고, gcp-key.json을 통째로
      GCP_SERVICE_ACCOUNT_JSON 환경변수에 주입. (별도 라이브러리 불필요)
■ 시트 권한: 읽기 전용으로만 접속 — 이 도구는 시트를 절대 수정하지 못함.
■ 출력: local_out/pref_export.preview.json (+ 검산 통과 시 채널 A CSV)
■ GitHub Actions 안에서 실행되면 가드가 즉시 중단시킵니다(깃허브용 별도).
"""

from __future__ import annotations

# ── 자립 시동: 파일을 직접 실행해도 조립 세트(패키지)로 스스로 등록 ──
if __package__ in (None, ""):
    import importlib, os as _os, sys as _sys
    _here = _os.path.dirname(_os.path.abspath(__file__))
    _parent = _os.path.dirname(_here)
    if _parent not in _sys.path:
        _sys.path.insert(0, _parent)
    __package__ = _os.path.basename(_here)          # 폴더명이 곧 세트 이름
    importlib.import_module(__package__)
# ──────────────────────────────────────────────────────────────────────

import argparse
import json
from collections import Counter
import os
import re
import sys
from pathlib import Path

from . import aggregate, codemap, csv_writer, json_writer, sheets_client, source_adapter, summary, validate

HERE = Path(__file__).resolve().parent


# ═════════ 준비물 로딩 — 백필 도구와 동일한 방식 ═════════

def load_env() -> list[str]:
    """이 파일 옆의 .env를 읽어 환경변수로 넣고, 넣은 이름 목록을 돌려준다."""
    env_path = HERE / ".env"
    if not env_path.exists():
        print(f"❌ .env 파일이 없습니다: {env_path}")
        print("   백필 도구(local_backfill)의 .env를 이 폴더로 복사하세요.")
        sys.exit(1)
    keys: list[str] = []
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key:
            os.environ[key] = val
            keys.append(key)
    return keys


def load_gcp_key() -> None:
    """gcp-key.json을 통째로 읽어 GCP_SERVICE_ACCOUNT_JSON 환경변수에 넣는다."""
    key_path = HERE / "gcp-key.json"
    if not key_path.exists():
        print(f"❌ gcp-key.json 파일이 없습니다: {key_path}")
        sys.exit(1)
    raw = key_path.read_text(encoding="utf-8")
    try:
        json.loads(raw)
    except Exception as e:
        print(f"❌ gcp-key.json이 올바른 JSON이 아닙니다: {e}")
        sys.exit(1)
    os.environ["GCP_SERVICE_ACCOUNT_JSON"] = raw


# ═════════ 구글시트 접속 — 읽기 전용, 이 파일에 자체 내장 ═════════

_ID_CANDIDATE_KEYS = (
    "PREF_SHEET_ID", "SPREADSHEET_ID", "SHEET_ID", "GOOGLE_SHEET_ID",
    "GSHEET_ID", "RADAR_SPREADSHEET_ID", "SPREADSHEET_KEY", "SHEET_URL",
)


def _find_sheet_id(env_keys: list[str], cli_value: str | None) -> str:
    """시트 ID를 찾는다: 명령행 지정 > 흔한 이름 > .env 안에서 자동 탐지."""
    if cli_value:
        sid = sheets_client.extract_id(cli_value)
        if sid:
            return sid
        sys.exit(f"❌ --sheet-id 값에서 시트 ID를 읽지 못했습니다: {cli_value!r}")
    for k in _ID_CANDIDATE_KEYS:
        if k in env_keys:
            sid = sheets_client.extract_id(os.environ.get(k, ""))
            if sid:
                print(f"🔑 시트 ID 사용: .env의 {k} (…{sid[-4:]})")
                return sid
    hits = []
    for k in env_keys:
        if "SHEET" in k.upper() or "SPREAD" in k.upper():
            sid = sheets_client.extract_id(os.environ.get(k, ""))
            if sid:
                hits.append((k, sid))
    if len(hits) == 1:
        k, sid = hits[0]
        print(f"🔑 시트 ID 사용: .env의 {k} (…{sid[-4:]})")
        return sid
    print("❌ .env에서 구글시트 ID를 찾지 못했습니다.")
    print("   .env에 들어있는 이름들(값은 표시 안 함):", ", ".join(env_keys) or "(없음)")
    print("   → 어느 이름이 시트 주소/ID인지 알려주시거나,")
    print("     .env에  PREF_SHEET_ID=시트ID(또는 전체 URL)  한 줄을 추가하세요.")
    sys.exit(1)


def _connect_sheet(sheet_id: str):
    """읽기 전용 접속(공용 부품 사용) + 실패 시 로컬용 힌트."""
    try:
        return sheets_client.connect_readonly(os.environ["GCP_SERVICE_ACCOUNT_JSON"], sheet_id)
    except SystemExit:
        raise
    except Exception as e:
        print(f"❌ 시트 접속 실패: {e}")
        print("   힌트: 서비스계정 이메일이 해당 시트에 공유돼 있어야 합니다"
              " (백필이 쓰는 그 시트면 이미 공유돼 있습니다).")
        sys.exit(1)


# ═════════ 본편 ═════════

def main() -> int:
    # 혼용 방지 가드: Actions 안에서 실행되면 중단
    if os.environ.get("GITHUB_ACTIONS") == "true":
        sys.exit("[중단] run_export_local.py 는 로컬 전용 진입점입니다(.env 사용). "
                 "Actions에서는 run_export_github.py 를 사용하세요.")

    ap = argparse.ArgumentParser(description="Q-Page 내보내기 — 로컬 검증 러너")
    ap.add_argument("--out-dir", default=str(HERE / "local_out"))
    ap.add_argument("--codemap", default=None, help="종목코드 매핑 CSV 경로")
    ap.add_argument("--sheet-id", default=None, help="시트 ID 또는 전체 URL(미지정 시 .env에서 탐지)")
    ap.add_argument("--year", type=int, default=codemap.DEFAULT_BASE_YEAR,
                    help="기준연도(명찰 세대) — 명칭→코드 변환·표준명 출력 기준. 현행 화면=2024")
    args = ap.parse_args()

    print("=" * 60)
    print("🧪 Q-Page 내보내기 — 로컬 검증 러너 (읽기 전용)")
    print("=" * 60)

    env_keys = load_env()
    load_gcp_key()
    ss = _connect_sheet(_find_sheet_id(env_keys, args.sheet_id))
    try:
        print("🗂 연결된 시트의 탭:", " / ".join(w.title for w in ss.worksheets()))
    except Exception:
        pass

    # ── 관련법령 읽기 (+ 자가진단) ──────────────────────────────────
    try:
        rows = source_adapter.fetch_source_rows(ss)
    except Exception as e:
        if type(e).__name__ == "WorksheetNotFound":
            tabs = [w.title for w in ss.worksheets()]
            print(f"❌ '{source_adapter.SOURCE_TAB}' 탭이 없습니다.")
            print("   이 시트의 실제 탭 목록:", " / ".join(tabs))
            print("   → .env의 PREF_SHEET_ID가 현행 운영 시트를 가리키는지 확인하세요.")
            return 1
        raise
    print(f"📖 관련법령 읽기 성공: {len(rows)}행  (탭: {source_adapter.SOURCE_TAB})")

    required = {source_adapter.COL_LAW, source_adapter.COL_QUAL, source_adapter.COL_CAT,
                source_adapter.COL_FLAG, source_adapter.COL_REVIEW, source_adapter.COL_DATE}
    if rows:
        missing = required - set(rows[0].keys())
        if missing:
            print(f"❌ 원천 탭에서 기대한 열이 안 보입니다: {', '.join(sorted(missing))}")
            print("   실제 첫 줄(헤더):", " / ".join(str(k) for k in rows[0].keys()))
            return 1

    flag_dist = Counter(str(r.get(source_adapter.COL_FLAG, "")).strip() or "(빈칸)" for r in rows)
    print("   [우대여부] 값 분포:", ", ".join(f"{k}={v}" for k, v in flag_dist.most_common(5)))
    pos = [r for r in rows if source_adapter.is_export_row(r)]
    cat_dist = Counter(str(r.get(source_adapter.COL_CAT, "")).strip() or "(빈칸)" for r in pos)
    print("   [내보내기 대상의 우대분류]:", ", ".join(f"{k}={v}" for k, v in cat_dist.most_common(10)))
    _samples = [str(r.get(source_adapter.COL_QUAL, "")) for r in pos
                if any(sep in str(r.get(source_adapter.COL_QUAL, "")) for sep in source_adapter.QUAL_SEPARATORS)][:2]
    if _samples:
        print("   [복수 종목 셀 예시]", " | ".join(repr(x[:60]) for x in _samples))

    records = list(source_adapter.iter_export_records(rows))
    print(f"   내보내기 대상 원자화: {len(records)}건 "
          f"(우대여부='{source_adapter.FLAG_EXPORT}' 이고 검토필요≠'{source_adapter.REVIEW_EXCLUDE}')")
    if rows and not records:
        print("⚠️ 내보내기 대상이 0건입니다. 위 분포를 화면째 보내주세요.")

    # ── 종목코드 매핑 (없으면 비치명 — 오늘은 시트 연결 검증 모드) ──
    try:
        code_table, canonical = codemap.load_maps(args.codemap, year=args.year)
        print(f"🎯 기준연도(명찰 세대): {args.year}")
        print(f"🔢 번역 사전 로드: 명칭 {len(code_table)}건 / 표준명 {len(canonical)}건")
    except FileNotFoundError:
        code_table, canonical = {}, {}
        print("⚠️ 종목코드 매핑 파일이 아직 없습니다. [확인 1]")
        print("   → 전 종목 '미매칭' 처리로 계속합니다(시트 연결 검증 모드).")
        print("     541 종목 사전에 Q-Net 코드가 있으면 그 파일을 --codemap 으로 지정하세요.")

    # ── 집계 → JSON → 검산 → 저장 ──────────────────────────────────
    snap = aggregate.build_snapshot(records, code_table, canonical=canonical)
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
        csv_path = out_dir / f"qradar_pref_export_{snap.asof}.csv"   # 파일명 = v1.1 §1.1
        csv_path.write_text(csv_writer.to_csv_text(snap), encoding="utf-8-sig")

    status = "✅ 검산 통과 (미리보기)" if not problems else "🔴 검산 미통과 (게시 불가 상태)"
    print()
    print(summary.build_summary_md(status, snap, problems,
                                   str(json_path), len(text.encode("utf-8")), False,
                                   base_year=args.year))
    if snap.report.unmatched_quals:
        um_path = out_dir / f"unmatched_{snap.asof}.txt"
        um_path.write_text(
            f"# 종목코드 미매칭 목록 (기준연도 {args.year}, {snap.asof}) — Q-Page 대조용\n"
            + "\n".join(sorted(snap.report.unmatched_quals)) + "\n", encoding="utf-8")
        print(f"📎 미매칭 목록 파일: {um_path}  (Q-Page 대조 동봉용)")
    if not code_table and problems:
        print("ℹ️ 위 '미통과'는 코드 매핑 전이라 예정된 결과입니다. "
              "오늘 확인 목표는 '대장 읽기 성공' 줄까지입니다.")
    if csv_path:
        print(f"📄 채널 A CSV: {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
