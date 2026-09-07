"""Q-Page 연계 내보내기 (규격 v1.3, 2026-09-07 양측 승인 합의본).

migrate_tool의 한 기능. 집계 코어(aggregate)를 채널 A(CSV)·채널 B(JSON)가 공유한다.

실행 진입점은 두 개뿐이며 절대 혼용하지 않는다(양쪽 모두 실행 가드 내장):
  - run_export_github.py : [깃허브용] GitHub Actions 전용. Secrets 인증, dist/ 출력.
  - run_export_local.py  : [로컬용] VSCode/터미널 전용. .env + gcp-key.json, local_out/ 출력.
"""

__version__ = "0.1.0"
SPEC_VERSION = "v1.3"
