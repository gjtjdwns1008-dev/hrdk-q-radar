# -*- coding: utf-8 -*-
"""
마스터 DB(구글시트) 주간 자동 백업 — GitHub Actions 판 (v2: 3중 방어 탑재)
──────────────────────────────────────────────────────────────
· 1차 방어: 시트 접속 5회 재시도 (지수 백오프 20→40→80→160초)
· 2차 방어: 탭 단위 3회 재시도 (15초 간격) — 일시 오류에 탭 하나만 실패 방지
· 3차 방어: 워크플로 격자 재시도(월 07시·10시 2슬롯) — 이미 성공한 날의
  2차 슬롯은 조용히 통과(성공 처리)하여 거짓 실패 알림 방지
· SQLite(data/hrdk_law.db)는 깃 커밋 이력이 백업을 담당 — 본 스크립트 대상 아님
"""
import os, sys, csv, time, requests

OUT_DIR = "sheet_backup_out"

def notify(subject, body):
    url = os.environ.get("WEBHOOK_URL", "").strip()
    if not url:
        print("· WEBHOOK_URL 미설정 — 통지 생략(안전핀)"); return
    try:
        requests.post(url, data={"subject": subject, "body": body}, timeout=30)
        print("· 통지 발송 ✓")
    except Exception as e:
        print(f"· ⚠ 통지 발송 실패(백업 자체는 별개): {e}")

def run_link():
    s, r, i = (os.environ.get(k, "") for k in
               ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    return f"{s}/{r}/actions/runs/{i}" if (s and r and i) else "(로컬 실행)"

def already_succeeded_today():
    """3차 방어 보조: 오늘 이미 성공한 실행이 있으면 True (예약 실행에서만 사용)"""
    tok, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not tok or not repo:
        return False
    import datetime as dt
    today = dt.datetime.utcnow().strftime("%Y-%m-%d")
    try:
        r = requests.get(
            f"https://api.github.com/repos/{repo}/actions/workflows/backup-sheet.yml/runs",
            params={"status": "success", "created": f"{today}..{today}", "per_page": 1},
            headers={"Authorization": f"Bearer {tok}",
                     "Accept": "application/vnd.github+json"}, timeout=30)
        return r.status_code == 200 and r.json().get("total_count", 0) > 0
    except Exception:
        return False   # 조회 실패 시엔 그냥 백업 진행 (한 번 더 해도 무해)

def connect_with_retry(sa_json, sheet_url, tries=5):
    """1차 방어: 접속 재시도 — 구글 일시 장애 흡수"""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from core.hrdk_law_core.sheets import get_sheet_client
    wait = 20
    for n in range(1, tries + 1):
        try:
            _, ss = get_sheet_client(sa_json, sheet_url)
            if n > 1: print(f"· 접속 성공 ({n}차 시도)")
            return ss, n
        except Exception as e:
            print(f"  ⚠ 접속 {n}차 실패 — {e}")
            if n == tries: raise
            print(f"    {wait}초 후 재시도..."); time.sleep(wait); wait *= 2

def fetch_tab_with_retry(ws, tries=3):
    """2차 방어: 탭 단위 재시도"""
    for n in range(1, tries + 1):
        try:
            return ws.get_all_values()
        except Exception as e:
            print(f"  ⚠ '{ws.title}' {n}차 실패 — {e}")
            if n == tries: raise
            time.sleep(15)

def main():
    if os.environ.get("GITHUB_EVENT_NAME") == "schedule" and already_succeeded_today():
        print("오늘 이미 백업 성공 — 격자 2차 슬롯 조용히 통과 ✓")
        sys.exit(0)                     # run16 교훈: 이미 성공한 날은 거짓 🔴 금지

    sa_json = os.environ.get("GCP_SERVICE_ACCOUNT_JSON")
    sheet_url = os.environ.get("GOOGLE_SHEET_URL")
    if not sa_json or not sheet_url:
        print("실패: GCP_SERVICE_ACCOUNT_JSON / GOOGLE_SHEET_URL 시크릿 필요"); sys.exit(1)

    try:
        ss, conn_try = connect_with_retry(sa_json, sheet_url)
    except Exception as e:
        notify("[Q-RADAR] 주간 시트 백업 실패 (접속 5회 모두 실패)",
               f"시트 접속 오류: {e}\n실행: {run_link()}\n"
               "※ 3시간 뒤 격자 2차 슬롯이 자동 재시도합니다.")
        print(f"실패: 접속 재시도 소진 — {e}"); sys.exit(1)

    os.makedirs(OUT_DIR, exist_ok=True)
    ok, fail, total_rows, names = 0, 0, 0, []
    for ws in ss.worksheets():
        try:
            rows = fetch_tab_with_retry(ws)
            safe = "".join(c if c not in '\\/:*?"<>|' else "_" for c in ws.title)
            with open(os.path.join(OUT_DIR, f"{safe}.csv"), "w",
                      encoding="utf-8-sig", newline="") as f:
                csv.writer(f).writerows(rows)
            ok += 1; total_rows += len(rows); names.append(f"{ws.title}({len(rows):,}행)")
            print(f"  ✓ {ws.title} — {len(rows):,}행")
            time.sleep(1.2)
        except Exception:
            fail += 1

    retry_note = f" (접속 {conn_try}차 성공)" if conn_try > 1 else ""
    if fail == 0:
        notify(f"[Q-RADAR] 주간 시트 백업 완료 — 탭 {ok}개 · {total_rows:,}행{retry_note}",
               "마스터 DB 주간 백업이 완료되었습니다.\n\n"
               f"· 탭: {', '.join(names)}\n"
               f"· 내려받기(90일 보존): {run_link()} → Artifacts\n"
               "※ 백업에는 내부 컬럼이 포함되므로 링크는 저장소 소유자만 접근 가능합니다.")
        print(f"완료: 탭 {ok}개 · {total_rows:,}행{retry_note}")
    else:
        notify(f"[Q-RADAR] 주간 시트 백업 일부 실패 — 성공 {ok}·실패 {fail}",
               f"재시도에도 실패한 탭이 있습니다. 로그: {run_link()}\n"
               "※ 3시간 뒤 격자 2차 슬롯이 자동 재시도합니다.")
        print(f"일부 실패: 성공 {ok} · 실패 {fail}"); sys.exit(1)

if __name__ == "__main__":
    main()
