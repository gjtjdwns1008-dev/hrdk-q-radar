"""
hrdk_law_core.backfill
----------------------
'되는 날 몰아서 처리(Backfill)' 전략의 핵심 부품.

법제처 IP 차단으로 며칠 건너뛰어도, 연결되는 날 밀린 날짜를 모두 따라잡습니다.
방식 B: 마지막으로 성공한 시행일자 다음날부터 어제까지의 모든 날짜를 채웁니다.

사용 흐름 (main.py):
    from hrdk_law_core.backfill import check_law_reachable, pending_dates, mark_done

    if not check_law_reachable(api_key):
        # 오늘은 IP 차단일 → 즉시 종료 (재시도로 시간 낭비 안 함)
        sys.exit(1)

    for date in pending_dates(kb):     # 밀린 날짜 + 오늘치 (과거→현재 순)
        process_one_day(date)
        mark_done(kb, date)            # 성공할 때마다 마지막 성공일 갱신
"""

from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
STATE_KEY = "last_success_date"   # sync_state에 저장되는 키 (YYYYMMDD)

# 마지막 성공일 기록이 아예 없을 때, 며칠 전부터 채울지 (최초 1회 안전장치)
DEFAULT_LOOKBACK_DAYS = 7


def _today_kst() -> datetime:
    return datetime.now(KST)


def check_law_reachable(api_key: str, timeout: int = 20) -> bool:
    """
    오늘 법제처에 연결되는 '되는 날'인지 가볍게 확인합니다.
    실제 데이터 요청 전에 1회만 찔러보고, 안 되면 빠르게 포기하기 위함.
    (IP 차단일에 5분씩 재시도하며 GitHub 시간 낭비하는 것을 방지)
    """
    import requests
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.law.go.kr/",
    }
    try:
        # 어제 날짜로 가볍게 1건만 조회해 응답이 오는지 확인
        y = (_today_kst() - timedelta(days=1)).strftime("%Y%m%d")
        url = (f"https://www.law.go.kr/DRF/lawSearch.do?OC={api_key}"
               f"&target=law&type=XML&efYd={y}~{y}&display=1")
        r = requests.get(url, headers=headers, timeout=timeout)
        if r.status_code != 200:
            return False
        # "검증에 실패" 메시지가 오면 차단된 것
        if "검증에 실패" in r.text:
            return False
        # 정상 응답(totalCnt 포함)이면 연결 OK
        return "totalCnt" in r.text or "<law" in r.text
    except Exception:
        return False


def pending_dates(kb, *, max_days: int | None = None,
                  last_success_override: str | None = None) -> list[str]:
    """
    처리해야 할 시행일자 목록을 과거→현재 순으로 반환합니다 (YYYYMMDD).

    범위: (마지막 성공일 + 1일) ~ 어제
    - 마지막 성공일 기록이 없으면 DEFAULT_LOOKBACK_DAYS 전부터
    - max_days: 한 번에 처리할 최대 일수 상한 (None=무제한, 밀린 건 다 처리)
    - last_success_override: 외부(구글시트)에서 읽은 마지막 성공일.
        SQLite가 휘발되는 GitHub Actions 환경에서, 시트값을 우선 사용해
        이미 처리한 날짜를 다시 분석하지 않도록 함. None이면 SQLite(get_state) 사용.
    """
    today = _today_kst()
    yesterday = today - timedelta(days=1)

    # 마지막 성공일: 시트값(override) 우선, 없으면 SQLite
    if last_success_override:
        last = last_success_override
    else:
        last = kb.get_state(STATE_KEY, "")
    if last:
        try:
            start = datetime.strptime(last, "%Y%m%d").replace(tzinfo=KST) + timedelta(days=1)
        except ValueError:
            start = yesterday - timedelta(days=DEFAULT_LOOKBACK_DAYS - 1)
    else:
        # 최초 실행: 어제 하루만 (과도한 소급 방지)
        start = yesterday

    # start ~ yesterday 까지의 날짜 목록
    dates = []
    d = start
    while d.date() <= yesterday.date():
        dates.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)

    if max_days is not None and len(dates) > max_days:
        # 오래된 것부터 max_days개만 (다음 실행에서 이어서 처리)
        dates = dates[:max_days]

    return dates


def mark_done(kb, date_str: str) -> None:
    """해당 시행일자 처리 완료 → 마지막 성공일을 갱신합니다 (더 최근 날짜일 때만)."""
    prev = kb.get_state(STATE_KEY, "")
    if not prev or date_str > prev:
        kb.set_state(STATE_KEY, date_str)


def is_valid_target_date(date_str: str) -> bool:
    """
    수동 실행용 날짜 검증: YYYYMMDD 형식 + 실제 존재하는 날 + 미래가 아님.
    - 형식 오류(2026-06-15, 260615 등) → False
    - 존재하지 않는 날(20260230) → False
    - 미래 날짜 → False
    """
    if not date_str or len(date_str) != 8 or not date_str.isdigit():
        return False
    try:
        d = datetime.strptime(date_str, "%Y%m%d").replace(tzinfo=KST)
    except ValueError:
        return False
    # 미래 날짜 금지 (오늘까지 허용)
    if d.date() > _today_kst().date():
        return False
    return True
