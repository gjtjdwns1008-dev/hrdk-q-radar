# -*- coding: utf-8 -*-
"""
마스터 DB(구글시트) 주간 자동 백업 — GitHub Actions 판
─────────────────────────────────────────────────────
· 전 워크시트(탭)를 CSV로 내려받아 ./sheet_backup_out/ 에 저장
  (워크플로가 이 폴더를 비공개 아티팩트로 업로드, 90일 보존 후 자동 만료)
· 완료/실패를 웹훅(메일)으로 통지 — 파일 첨부 대신 실행 페이지 링크
  (WEBHOOK_URL 미설정 시 통지 생략 — daily.yml과 같은 안전핀 관례)
· 탭 하나라도 실패하면 종료코드 1 → 액션 실패(빨간불)로 정직 표시
"""
import os, sys, csv, time, requests

OUT_DIR = "sheet_backup_out"

def notify(subject, body):
    url = os.environ.get("BACKUP_WEBHOOK_URL", "").strip()
    if not url:
        print("· BACKUP_WEBHOOK_URL 미설정 — 통지 생략(안전핀)")
        return
    try:
        requests.post(url, data={"subject": subject, "body": body}, timeout=30)
        print("· 통지 발송 ✓")
    except Exception as e:
        print(f"· ⚠ 통지 발송 실패(백업 자체는 별개): {e}")

def run_link():
    s, r, i = (os.environ.get(k, "") for k in
               ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    return f"{s}/{r}/actions/runs/{i}" if (s and r and i) else "(로컬 실행)"

def main():
    sa_json = os.environ.get("GCP_SERVICE_ACCOUNT_JSON")
    sheet_url = os.environ.get("GOOGLE_SHEET_URL")
    if not sa_json or not sheet_url:
        print("실패: GCP_SERVICE_ACCOUNT_JSON / GOOGLE_SHEET_URL 시크릿 필요")
        sys.exit(1)

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from core.hrdk_law_core.sheets import get_sheet_client
    try:
        _, ss = get_sheet_client(sa_json, sheet_url)
    except Exception as e:
        notify("[Q-RADAR] 주간 시트 백업 실패", f"시트 접속 오류: {e}\n실행: {run_link()}")
        print(f"실패: 시트 접속 — {e}"); sys.exit(1)

    os.makedirs(OUT_DIR, exist_ok=True)
    ok, fail, total_rows, names = 0, 0, 0, []
    for ws in ss.worksheets():
        try:
            rows = ws.get_all_values()
            safe = "".join(c if c not in '\\/:*?"<>|' else "_" for c in ws.title)
            with open(os.path.join(OUT_DIR, f"{safe}.csv"), "w",
                      encoding="utf-8-sig", newline="") as f:
                csv.writer(f).writerows(rows)
            ok += 1; total_rows += len(rows); names.append(f"{ws.title}({len(rows):,}행)")
            print(f"  ✓ {ws.title} — {len(rows):,}행")
            time.sleep(1.2)   # API 예의 (429 예방)
        except Exception as e:
            fail += 1; print(f"  ⚠ {ws.title} 실패 — {e}")

    if fail == 0:
        notify(f"[Q-RADAR] 주간 시트 백업 완료 — 탭 {ok}개 · {total_rows:,}행",
               "마스터 DB 주간 백업이 완료되었습니다.\n\n"
               f"· 탭: {', '.join(names)}\n"
               f"· 내려받기(90일 보존): {run_link()} → Artifacts\n"
               "※ 백업에는 내부 컬럼이 포함되므로 링크는 저장소 소유자만 접근 가능합니다.")
        print(f"완료: 탭 {ok}개 · {total_rows:,}행")
    else:
        notify(f"[Q-RADAR] 주간 시트 백업 일부 실패 — 성공 {ok}·실패 {fail}",
               f"실패 탭이 있습니다. 로그 확인: {run_link()}")
        print(f"일부 실패: 성공 {ok} · 실패 {fail}")
        sys.exit(1)

if __name__ == "__main__":
    main()
