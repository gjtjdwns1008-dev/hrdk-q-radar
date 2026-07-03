import os
from datetime import datetime, timedelta, timezone


# ==========================================
# 1. API 키 및 외부 연동 설정
# ==========================================
LAW_API_KEY = os.environ.get("LAW_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
# 🛠️ [D-3 패치] 웹훅 주소 하드코딩 제거 → GitHub Secrets(환경변수)에서 읽도록 변경
# ⚠️ 기존 주소는 저장소 이력에 노출되었으므로 Make.com에서 반드시 '재발급' 후 Secrets에 등록하세요.
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
WORKNET_API_KEY = os.environ.get("WORKNET_API_KEY", "")  # 🛠️ [중복 제거] 아래 중복 선언 삭제

# [V27 신규] 구글 시트 직접 제어용 환경 변수
GCP_SERVICE_ACCOUNT_JSON = os.environ.get("GCP_SERVICE_ACCOUNT_JSON")
GOOGLE_SHEET_URL = os.environ.get("GOOGLE_SHEET_URL")

# Phase 1 신규: SQLite 지식베이스 경로
DB_PATH = os.environ.get("DB_PATH", "hrdk_law.db")

# ==========================================
# 2. 날짜 및 공통 변수 설정
# ==========================================
KST = timezone(timedelta(hours=9))
today = datetime.now(KST)

# 🌟 [D-1 로직 적용] 오늘(today)에서 1일을 뺀 어제 날짜를 계산합니다.
yesterday = today - timedelta(days=1)
TARGET_DATE = yesterday.strftime("%Y%m%d")

# 💡 만약 과거 데이터를 돌리고 싶다면 이 변수를 수동으로 바꿔서 쓰면 됩니다.
# 💡 TARGET_DATE = "20260429"
# 💡 TARGET_DATE = yesterday.strftime("%Y%m%d")
# 💡 TARGET_DATE = today.strftime("%Y%m%d")

# ==========================================
# [Q-RADAR 통합 대장] 24칸 — knowledge.UNIFIED_MAP과 순서·이름 완전 동일 (단일 정의처 짝)
#   공통 12칸 + 활용도 3칸(monitor 계승) + 우대 전용 7칸(RADAR 계승, 희소) + 연관도·우대여부 키
# ==========================================
SYSTEM_NAME = "HRDK Q-RADAR"
MAIN_SHEET_NAME = "국가기술자격 관련법령"   # 통합 대장 탭 이름 (navigator·브리핑 호환 위해 RADAR 명칭 유지)

COLUMNS = [
    "MST_ID",
    "시행일자",
    "소관부처",
    "법령명",
    "개정유형",
    "연관도",
    "우대여부",
    "관련 종목",
    "주요 제·개정내용",
    "활용도_구분",
    "활용도_상세",
    "조문 요약",
    "우대분류",
    "Track1_취급유형",
    "Track1_위험도",
    "Track2_효용코드",
    "중처법대상",
    "상세 분석 결과",
    "근거조문",
    "AI신뢰도",
    "검토필요",
    "검토사유",
    "조문별 다이렉트 링크",
    "워크넷 실시간 구인건수",
]

# 총괄현황표 7칸 — ★교훈 반영: 상태는 항상 기록, 숫자(건수 4칸)는 성공 시에만
SUMMARY_COLUMNS = ["시행일자", "총 검토건수", "연관높음", "단순관련", "우대건수",
                   "모니터링 상태", "실행 로그 및 비고"]

