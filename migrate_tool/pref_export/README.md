# Q-Page 내보내기 (migrate_tool/pref_export) — 규격 v1.3

우대사항_대장(구글 시트) → 채널 A(CSV, 연 1회 수동) + 채널 B(pref_export.json, 일 4회 게시).
집계 코어 1개를 두 채널이 공유하므로 CSV와 JSON의 숫자가 어긋날 수 없는 구조.

## 전체 흐름

```
우대사항_대장(검토상태=확정만)
        │
   [집계 코어 aggregate]  ← catmap(§2.3 표기사전) · codemap(Q-Net 코드·검정형 필터)
        │
        ├─ 채널 B: json_writer → validate(§3 미러) → dist/pref_export.json
        │           일 4회 Pages 재빌드 편승 → 고정 URL → Q-Page 오버레이 주입
        │
        └─ 채널 A: csv_writer → local_out/pref_export_{asof}.csv
                    연 1회 수동 → 원장 '9_우대법령' 반입 (Phase 1)
```

## 파일 지도 — 실행 위치 구분

| 파일 | 실행 위치 | 역할 |
|---|---|---|
| `run_export_github.py` | **깃허브 Actions 전용** (로컬 실행 시 가드가 중단) | Secrets 인증 → JSON 생성·검산 → dist/ 게시, keep-last-good, Job Summary 기록 |
| `run_export_local.py` | **로컬 VSCode 전용** (Actions 실행 시 가드가 중단) | .env+gcp-key.json 인증 → JSON 미리보기 + 채널 A CSV 산출, 콘솔 리포트 |
| `aggregate.py` `catmap.py` `codemap.py` `ledger_adapter.py` `json_writer.py` `csv_writer.py` `validate.py` `summary.py` | 공용 라이브러리 (양쪽에서 import) | 실행 진입점 아님 — 직접 돌리지 않음 |
| `docs/qpage_export_workflow_snippet.yml` | 깃허브 | 기존 일 4회 재빌드 워크플로에 삽입할 스텝 |

- 파일 전부 커밋해도 안전(자격정보 없음). 자격정보는 Secrets(깃허브) / .env·gcp-key.json(로컬)에만 존재.
- `.gitignore`에 `local_out/`, `.env`, `gcp-key.json` 포함 확인.

## 실행법

```bash
# 로컬 (레포 루트에서) — 커밋 전 육안 확인 + 채널 A CSV
python -m migrate_tool.pref_export.run_export_local

# 깃허브 — 워크플로 스텝으로만 실행 (스니펫 참조)
python -m migrate_tool.pref_export.run_export_github --out dist/pref_export.json
```

## 커밋 전 확인 목록 (코드 내 TODO 마커와 1:1)

1. **확인 1** `codemap.py` — 종목코드 매핑 소스 경로·컬럼명 (541개 종목 사전에 Q-Net 코드 포함 여부)
2. **확인 2** `ledger_adapter.py` — 대장 탭 이름·헤더 4개(종목/법령/우대분류/검토상태)·'확정' 표기
3. **확인 3** `aggregate.py` — 대표 법령 규칙을 규격 v1.1 §1 원문으로 교체 (현재 임시: 성격 우선순위→가나다)
4. **확인 4** `csv_writer.py` — 14열 레이아웃을 v1.1 §1 원문과 대조 (현재 v1.3 단서 역산 잠정안)
5. **확인 5** 양쪽 진입점 — `core.get_sheet_client` 실제 모듈 경로 + 워크플로 시크릿명·인증 주입 스텝
6. **확인 6** 삽입 대상 워크플로 파일명 (일 4회 Pages 재빌드)

## 가동 순서 (규격 §5 준수)

1. 지금: 확인 1~5 반영 → 로컬 러너로 육안 확인
2. Phase 1(심사 전): 로컬 러너의 CSV → 원장 반입 → Q-Page 전 파이프라인 수동 완주
3. Phase 2(심사 후): 워크플로 스텝 커밋 → 첫 게시 런 → **런 Summary 캡처 → Q-Page 전달** → 오버레이 가동
