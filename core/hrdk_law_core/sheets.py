"""
hrdk_law_core.sheets
--------------------
Google Sheets 인증 및 총괄현황표 로깅 공통 헬퍼.

두 레포에서 중복으로 작성하던 gspread 인증 로직과
총괄현황표 로깅을 하나로 통합합니다.

사용법:
    from hrdk_law_core.sheets import get_sheet_client, log_to_summary_sheet
"""

import json
from datetime import datetime, timezone, timedelta

import gspread
from oauth2client.service_account import ServiceAccountCredentials


def get_sheet_client(gcp_service_account_json: str, sheet_url: str):
    """
    Google Sheets 클라이언트와 스프레드시트 객체를 반환합니다.

    Parameters
    ----------
    gcp_service_account_json : GCP 서비스 계정 JSON 문자열
    sheet_url                : 구글 시트 KEY(URL의 /d/ 뒤 부분)

    Returns
    -------
    (gspread.Client, gspread.Spreadsheet) 튜플
    설정이 없으면 (None, None) 반환
    """
    if not gcp_service_account_json or not sheet_url:
        print("  ⚠️ 구글 시트 설정 정보가 없어 건너뜁니다.")
        return None, None

    creds_dict = json.loads(gcp_service_account_json.strip(), strict=False)
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(sheet_url)
    return client, spreadsheet


def log_to_summary_sheet(
    spreadsheet,
    total_len: int,
    matched_len: int,
    status: str = "🟢 정상 작동",
    log: str = "특이사항 없음",
) -> None:
    """
    총괄현황표 탭에 실행 결과를 한 줄 추가합니다.

    5개 컬럼: 수집일자(KST) | 총 검토건수 | 연관 법령건수 | 모니터링 상태 | 실행 로그
    """
    try:
        summary_sheet = spreadsheet.worksheet("총괄현황표")
        kst_now = datetime.now(timezone(timedelta(hours=9)))
        summary_row = [
            kst_now.strftime("%Y-%m-%d %H:%M:%S"),
            total_len,
            matched_len,
            status,
            log,
        ]
        summary_sheet.append_row(summary_row)
        print(f"  📊 [총괄현황표 기록] 상태: {status}")
    except Exception as e:
        print(f"  ⚠️ 총괄현황표 기록 실패: {e}")


def get_column_letter(n: int) -> str:
    """숫자를 엑셀 열 문자로 변환합니다. (1→A, 17→Q)"""
    string = ""
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        string = chr(65 + remainder) + string
    return string


def upsert_daily_summary_row(
    spreadsheet,
    sheet_name: str,
    target_date_display: str,
    cols_before_status: list,
    status_symbol: str,
    log: str = "",
    status_col_name: str = "상태",
    log_col_name: str = "로그",
) -> None:
    """
    [옵션 B] 총괄현황표에서 같은 날짜 행을 찾아 상태 칸에 시도 이력을 누적합니다.
    - 그날 행이 없으면: 새 줄 추가 (상태 = "HH:MM{심볼}")
    - 그날 행이 있으면: 상태 칸에 " → HH:MM{심볼}" 이어붙이고, 로그/건수도 갱신

    예) 상태 칸: "04:13🔴 → 08:47🔴 → 12:31🟢"  (한 줄로 그날 시도 이력이 다 보임)

    target_date_display: 행을 식별할 날짜 문자열 (예: "2026년_06월_17일")
    cols_before_status: 상태 칸 앞에 올 값들 (예: [날짜, 총건수, ...])
    status_symbol: 이번 시도 결과 심볼 (예: "🟢", "🔴")
    """
    try:
        ws = spreadsheet.worksheet(sheet_name)
    except Exception as e:
        print(f"  ⚠️ '{sheet_name}' 시트를 찾을 수 없음: {e}")
        return

    kst_now = datetime.now(timezone(timedelta(hours=9)))
    # 상태 칸 표기: "MM/DD HH:MM심볼" — 며칠에 시도했는지(실행일)까지 보이도록
    # (A열은 '처리 대상 날짜', 상태 칸 날짜는 '시도한 날' → 둘이 다르면 백필/수동이란 뜻)
    stamp = kst_now.strftime("%m/%d %H:%M")
    this_attempt = f"{stamp}{status_symbol}"

    try:
        all_values = ws.get_all_values()
        header = all_values[0] if all_values else []
        # 상태/로그 컬럼 인덱스 (헤더 표기 차이 대응: RADAR='모니터링 상태'/'실행 로그 및 비고',
        #  monitor='상태'/'로그' 등 어느 쪽이든 찾도록 폴백. 없으면 끝에 가정)
        status_idx = None
        for cand in (status_col_name, "모니터링 상태", "상태"):
            if cand and cand in header:
                status_idx = header.index(cand)
                break
        if status_idx is None:
            status_idx = len(cols_before_status)
        log_idx = None
        for cand in (log_col_name, "실행 로그 및 비고", "실행 로그", "로그"):
            if cand and cand in header:
                log_idx = header.index(cand)
                break
        if log_idx is None:
            log_idx = status_idx + 1

        # 같은 날짜 행 찾기 (A열 = 날짜)
        target_row_num = None
        for i, row in enumerate(all_values[1:], start=2):
            if row and row[0] == target_date_display:
                target_row_num = i
                existing_status = row[status_idx] if status_idx < len(row) else ""
                break

        if target_row_num is None:
            # 그날 첫 실행 → 새 줄
            new_row = list(cols_before_status)
            # status_idx 위치까지 채우고 상태/로그 추가
            while len(new_row) < status_idx:
                new_row.append("")
            new_row.append(this_attempt)
            new_row.append(log)
            ws.append_row(new_row, value_input_option="RAW")
            print(f"  📊 [총괄현황표] {target_date_display} 새 줄 ({this_attempt})")
        else:
            # 그날 재실행 → 상태 칸에 누적
            new_status = (existing_status + " → " + this_attempt) if existing_status else this_attempt
            col_letter = get_column_letter(status_idx + 1)
            log_letter = get_column_letter(log_idx + 1)
            ws.update(range_name=f"{col_letter}{target_row_num}", values=[[new_status]], value_input_option="RAW")
            if log:
                ws.update(range_name=f"{log_letter}{target_row_num}", values=[[log]], value_input_option="RAW")
            # 건수 칸들도 최신값으로 갱신 (성공 시 의미 있음)
            for ci, val in enumerate(cols_before_status[1:], start=2):
                ws.update(range_name=f"{get_column_letter(ci)}{target_row_num}", values=[[val]], value_input_option="RAW")
            print(f"  📊 [총괄현황표] {target_date_display} 누적 ({new_status})")
    except Exception as e:
        print(f"  ⚠️ 총괄현황표 누적 기록 실패: {e}")


def read_last_success_date(gcp_service_account_json: str, sheet_url: str,
                           sheet_name: str = "총괄현황표") -> str:
    """
    총괄현황표에서 '마지막으로 분석 성공한 시행일자'를 읽어 YYYYMMDD로 반환합니다.

    설계 의도 (SQLite 휘발 대응):
      GitHub Actions는 매 실행마다 SQLite가 초기화되어 last_success_date가 사라짐.
      → 영구 저장소인 구글시트(총괄현황표)에서 직접 마지막 성공일을 읽어,
        이미 처리한 날짜를 다시 분석하지 않도록 함.

    성공 판정: '모니터링 상태' 칸에 '🟢' 또는 '정상 작동'이 있는 행.
      (연결 실패 행은 🔴만 있고 시행일자 칸이 '오늘'로 기록되므로 자동 제외됨)

    반환: 성공한 행 중 가장 최근 '시행일자' (YYYYMMDD). 없으면 "".
    """
    import time as _time
    # 구글 시트 503/일시 오류 대비: 최대 3회 재시도(2초 간격) 후에도 실패하면 예외를 올린다.
    # (조용히 ""를 반환하면 호출부가 '처리 이력 없음'으로 오인해 같은 날을 재처리/0 기록할 수 있음)
    last_err = None
    for attempt in range(3):
        try:
            return _read_last_success_date_once(gcp_service_account_json, sheet_url, sheet_name)
        except Exception as e:
            last_err = e
            msg = str(e)
            transient = ("503" in msg or "500" in msg or "502" in msg or "504" in msg
                         or "unavailable" in msg.lower() or "timed out" in msg.lower()
                         or "timeout" in msg.lower() or "rate" in msg.lower())
            if attempt < 2 and transient:
                _time.sleep(2)
                continue
            raise
    raise last_err if last_err else RuntimeError("read_last_success_date 실패")


def _read_last_success_date_once(gcp_service_account_json: str, sheet_url: str,
                                 sheet_name: str = "총괄현황표") -> str:
    """read_last_success_date의 1회 시도 본체. (재시도는 상위 래퍼가 담당)"""
    if True:
        # get_sheet_client은 (client, spreadsheet) 튜플을 반환 → 두 번째(스프레드시트)만 사용
        _, ss = get_sheet_client(gcp_service_account_json, sheet_url)
        if ss is None:
            return ""
        try:
            ws = ss.worksheet(sheet_name)
        except Exception:
            return ""  # 총괄현황표가 아직 없음 → 최초 실행으로 간주

        records = ws.get_all_values()
        if len(records) <= 1:
            return ""

        header = records[0]
        # '시행일자'(권장) 우선, 기존 '수집일자'도 폴백 지원 (시트 헤더 변경 과도기 대응)
        date_idx = None
        for cand in ("시행일자", "수집일자"):
            if cand in header:
                date_idx = header.index(cand)
                break
        if date_idx is None:
            return ""
        # 상태 칸: RADAR는 '모니터링 상태', monitor는 '상태'로 헤더가 다름 → 둘 다 인식
        status_idx = None
        for cand in ("상태", "모니터링 상태"):
            if cand in header:
                status_idx = header.index(cand)
                break
        if status_idx is None:
            return ""

        success_dates = []
        for row in records[1:]:
            if date_idx >= len(row) or status_idx >= len(row):
                continue
            date_raw = (row[date_idx] or "").strip()
            status = (row[status_idx] or "").strip()
            if not date_raw:
                continue
            # 성공 판정
            is_success = ("🟢" in status) or ("정상 작동" in status)
            if not is_success:
                continue
            # 날짜 정규화: '2026-06-18' → '20260618'
            digits = "".join(ch for ch in date_raw if ch.isdigit())
            if len(digits) == 8:
                success_dates.append(digits)

        if not success_dates:
            return ""
        return max(success_dates)  # 가장 최근 성공일
