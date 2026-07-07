# -*- coding: utf-8 -*-
"""
Q-RADAR 통합 brain — law-monitor(연관도·활용도) + LAW-RADAR(우대·투트랙) 프롬프트 병합
=====================================================================================
호출 1회로 두 관점을 모두 분석해 통합 대장 1행 분량의 dict를 반환합니다.
  반환 계약: (성공여부, 연관도, law_info)   ← RADAR의 기존 계약과 동일 형태
  · 연관도: "연관높음" | "단순관련" | "해당없음"  (해당없음이면 main이 저장 생략)
  · law_info: 통합 대장 컬럼명 키 (report_maker가 그대로 시트에 씀)

설계 원칙:
  1) 두 시스템의 검증된 프롬프트 문구를 최대한 원형 보존 (battle-tested)
  2) 세 텍스트의 역할 분담 + 상호 반복 금지 (이슈브리핑 원칙 계승)
       주요 제·개정내용 = 법령이 무엇을 바꿨나 (객관 팩트)
       활용도_상세     = 자격 수요·노동시장 관점 (광역)
       상세 분석 결과   = 정책 모순(경력이음) + 구직자 기회 (정밀, 우대법령만)
  3) 우대여부=X면 우대 블록을 기본값·빈칸으로 → 출력 토큰 절약 + 후처리로 정합 강제
"""
import json
import re
from hrdk_law_core.llm_client import get_llm_client


def _normalize_relevance(raw: str) -> str:
    """연관도 값 교정. 정식값: 연관높음/단순관련/해당없음 (monitor의 '일반'도 해당없음으로 흡수)"""
    if not raw:
        return "해당없음"
    s = str(raw).strip().replace(" ", "")
    if s in ("연관높음", "단순관련", "해당없음"):
        return s
    if s == "일반":
        return "해당없음"
    if "연관" in s or "높" in s:
        return "연관높음"
    if "단순" in s or "관련" in s:
        return "단순관련"
    return "해당없음"


def _normalize_usage(raw: str) -> str:
    """활용도_구분 교정. 정식값: 대폭 증가/소폭 증가/현상 유지/소폭 감소/대폭 감소 (빈칸 허용)"""
    if not raw:
        return ""
    s = str(raw).strip().replace(" ", "")
    table = {"대폭증가": "대폭 증가", "소폭증가": "소폭 증가", "현상유지": "현상 유지",
             "소폭감소": "소폭 감소", "대폭감소": "대폭 감소"}
    if s in table:
        return table[s]
    if "대폭" in s and "증" in s: return "대폭 증가"
    if "소폭" in s and "증" in s: return "소폭 증가"
    if "대폭" in s and "감" in s: return "대폭 감소"
    if "소폭" in s and "감" in s: return "소폭 감소"
    if "유지" in s or "현상" in s: return "현상 유지"
    if "증가" in s: return "소폭 증가"
    if "감소" in s: return "소폭 감소"
    return ""


# 🌟 [모델 추상화] 통역 창구 (LLM_PROVIDER로 교체 가능)
_llm = None
def _client():
    global _llm
    if _llm is None:
        _llm = get_llm_client()
    return _llm


# 🌟 링크 조립 공장 (RESTful 포맷 생성기) — RADAR와 동일, 변경 없음
def generate_new_law_link(law_name, enforce_date, prom_num, prom_date, article_name):
    """별표/서식인지 일반 조항인지 구분해서 법제처 RESTful 링크를 완성합니다."""
    star_match = re.search(r'(별표|서식)\s*(\d+)', article_name)
    if star_match:
        target_id = f"{star_match.group(1)}{star_match.group(2)}"
        return f"https://www.law.go.kr/법령별표서식/({law_name},{enforce_date},{target_id})"

    jo_match = re.search(r'(제\d+조(?:의\d+)?)', article_name)
    if jo_match:
        target_id = jo_match.group(1)
        return f"https://www.law.go.kr/법령/{law_name}/({enforce_date},{prom_num},{prom_date})/{target_id}"

    return f"https://www.law.go.kr/법령/{law_name}"




def _sanitize_review(reason, related_certs, source_text, qnet_certs_text):
    """★재발방지(2026-07-06): 검토사유 환각 자동검증.
    사유에 등장한 '별표 번호'나 '자격종목명'이 본 법령 원문·관련종목에 없으면
    오염(타 법령 사례 인용)으로 보고 사실 기반 문구로 교체한다."""
    r = (reason or "").strip()
    if not r:
        return r
    nsp = lambda x: re.sub(r"[\s\u318D\u00B7]+", "", str(x or ""))
    src, rel = nsp(source_text), nsp(related_certs)
    # ① 별표 번호 검증
    for m in set(re.findall(r"별표\s*\d+(?:의\s*\d+)?", r)):
        if nsp(m) not in src:
            print(f"    ⚠️ 검토사유 자동검증: 원문에 없는 '{m}' 인용 감지 → 사유 교체")
            return "자동검증에서 본 법령 원문에 없는 별표·사례 인용이 감지되어 사유를 무효화함. 원문 및 별표 파일 직접 확인 필요."
    # ② 종목명 검증 (사전에 있는 종목이 사유엔 있는데 원문·관련종목엔 없음)
    reason_n = nsp(r)
    for name in re.findall(r"[가-힣()]{4,}", qnet_certs_text or ""):
        n = nsp(name)
        if len(n) >= 4 and n in reason_n and n not in rel and n not in src:
            print(f"    ⚠️ 검토사유 자동검증: 원문 외 종목 '{name}' 언급 감지 → 사유 교체")
            return "자동검증에서 본 법령 원문에 없는 자격종목 언급이 감지되어 사유를 무효화함. 원문 및 별표 파일 직접 확인 필요."
    return r

try:  # ★라벨 주석화(2026-07-07): 신규 행도 "B (영업요건형)" 규약으로 기록
    from hrdk_law_core.certs import (label_track1_type, label_track1_risk,
                                     label_track2_code)
except Exception:  # core 미설치 등 극단 상황 — 원시 코드 유지(무해)
    label_track1_type = label_track1_risk = label_track2_code = (lambda v: v)


def run_ai_analysis(law, qnet_certs_text, attempt_count=5):
    # 🌟 [Q-RADAR 통합 프롬프트] — monitor(연관도·활용도) × RADAR(우대·투트랙) 병합
    prompt = f"""
    당신은 한국산업인력공단(HRDK)의 '국가기술자격 법령 레이더(Q-RADAR)'를 담당하는 수석 연구원(AI)입니다.
    당신의 임무는 매일 수집되는 제·개정 법령 조문을 한 번에 입체 분석하여,
    (1) 국가기술자격과의 연관도와 노동시장 활용도, (2) 자격 취득자 우대 여부와 투트랙 분류를
    모두 담은 정형화된 JSON 하나로 출력하는 것입니다.

    ⚠️ 가장 중요한 규칙: 아래 [분석 대상 법령]의 실제 조문 내용만을 근거로 분석하십시오.
    법령 본문에 없는 내용을 추측하거나 지어내지 마십시오. 법령명과 조문 내용이 논리적으로
    맞지 않으면(예: 국방부 소관 계엄법인데 소방 자격이 도출됨) "해당없음"으로 판별하십시오.
    억지로 연관성을 찾지 마십시오. 직접 연관되거나 실무상 명백히 영향을 받는 자격증만 추출하십시오.

    ### 📌 [분석 대상 법령]
    - 법령명: {law.get('법령명', '')}
    - 소관부처: {law.get('소관부처', '')}
    - 시행일자: {law.get('시행일자', '')}
    [법령 원문 (마크다운)]
    {law.get('원본', '(본문 없음)')}

    ---
    위 법령이 다음 국가기술자격 종목 중 어느 것과 연관되는지 파악하십시오.
    [국가기술자격 종목 리스트]
    {qnet_certs_text}

    ---
    ### 🧭 [STEP 1. 연관도 판별 — 가장 먼저]
    - "연관높음": 자격 종목이 조문·별표에 직접 명시되거나, 자격자의 선임·배치·우대·직무를 실질 규율
    - "단순관련": 직접 명시는 없으나 해당 직무 분야에 실무상 영향
    - "해당없음": 자격과 무관 (이 경우 아래 모든 항목을 기본값으로 빠르게 결론)

    ### 📋 [STEP 2. 공통 추출 (모든 연관 법령)]
    - 개정유형: 제정 / 일부개정 / 전부개정 / 폐지 등
    - 주요_제개정내용: 실제 개정된 조항과 객관적 팩트만 글머리 기호('-')로 나열 (법령이 '무엇을' 바꿨는지)
    - 종목: 연관된 자격증 이름만 쉼표로 나열 (아래 치명적 에러 방지 규칙 4·5 준수)
    - 조문리스트: 근거 조문이 여러 개면 **반드시 모두 추출**
        - 제O조 형태: {{"조문명": "제23조의2", "숫자": "23.2"}}
        - 별표 형태: "별표1"이 아닌 "별표 1"처럼 반드시 띄어쓰기 (숫자는 빈칸 "")

    ### 📈 [STEP 3. 노동시장 활용도 (연관높음·단순관련 모두 작성, 해당없음일 때만 빈칸)]
    - 활용도_구분: [대폭 증가 / 소폭 증가 / 현상 유지 / 소폭 감소 / 대폭 감소] 중 택1
    - 활용도_상세: 자격 수요·활용도 변화 분석 3문장 이내. ① 개정 배경 ② 방향성 ③ 파급효과 중심.
    - 단순관련이면 간접 영향 관점에서 판단하되, 증감 근거가 뚜렷하지 않으면 '현상 유지'.
      ※ 주요_제개정내용과 내용 반복 금지 — 여기는 '자격 수요가 어떻게 변하나'만.

    ### ⭐ [STEP 4. 우대 판별 (핵심)]
    우대여부: 이 법령의 조문이 국가기술자격 취득자에게 실질적 우대·요구(의무고용, 직무권한,
    인사우대, 시험면제, 위촉자격 등)를 부여하면 "O", 관련은 있으나 우대 조항이 없으면 "X".
    ★ 우대여부가 "X"이면: 우대분류="기타", Track1_취급유형="Z", Track1_위험도="X",
      Track2_효용코드="Ⅳ-0", 중처법대상="비대상", 조문_요약과 상세_분석은 빈칸("")으로 두고 즉시 종료.
    ★ 우대여부가 "O"이면 아래 4-1 ~ 4-5를 모두 작성:

    #### 4-1. 우대분류 (메인 분류)
    다음 5가지 중 1개. 여러 우대가 섞이면 우선순위: 의무고용 > 직무권한부여 > 인사우대 > 시험면제
      - 의무고용: 사업자가 사업 등록·허가·운영을 위해 자격자를 반드시 고용·선임해야 함
      - 직무권한부여: 특정 직무·검사·업무를 자격자만 수행할 수 있도록 권한을 부여
      - 인사우대: 채용·보수·승진·평정 등에서 가산점·우대 (의무는 아님)
      - 시험면제: 다른 자격·면허·임용시험에서 시험과목 면제 또는 응시자격 부여
      - 기타: 위 4가지에 해당하지 않는 우대 (위원 위촉, 자문 자격 등)

    #### 4-2. Track 1. 정책 담당자 관점 : 「경력이음형 자격제도」와의 정합성
    * 1차 축 (법령의 취급):
      - A. 신분형성형 (예: 자격 취득자만 특정 명칭/신분 사용)
      - B. 영업요건형 (예: 기업이 사업을 등록/지정받기 위해 자격자 고용)
      - C. 직역독점형 (예: 특정 업무/행위는 자격자만 수행 가능)
      - D. 인사가산형 (예: 채용, 보수, 승진 시 가점 부여)
      - E. 검정연계형 (예: 타 시험 응시자격 부여 또는 과목 면제)
      - Z. 제외 (A~E 어디에도 해당하지 않음)
    * 2차 축 (위험도 — 선경력을 요구하는 경력이음형과 충돌하는 정도):
      - C (임계위험): 오직 단일 자격만 인정하고 대체 경로가 전혀 없음
      - H (고위험): '자격 + 경력 N년'을 동시에 요구함
      - M (중위험): 복수의 자격을 OR 조건으로 대체 가능함
      - L (저위험): 자격이 없어도 '관련 학과 졸업 + 경력' 등으로 진입 우회 가능
      - N (무관): 직역 진입 자체를 막지 않는 단순 부가우대 (D, E 유형)
      - X (해당없음): 취급유형이 Z인 경우. ★취급유형 Z ↔ 위험도 X는 항상 짝★

    #### 4-3. Track 2. 국민(구직자) 관점 : 노동시장 효용
    한 조항은 반드시 한 칸. 우선순위: 직업창출(Ⅰ) > 취업관문(Ⅱ) > 부가우대(Ⅲ)
    * Ⅰ-1 (면허전환형): 자격 취득 → 행정청 면허 발급으로 평생 직업·신분 부여
    * Ⅰ-2 (개업창업형): 자격자 본인이 단독으로 직무 수행·서명 가능 → 1인 사업 가능
    * Ⅱ-1 (등록필수형): 사업체 등록·허가 시 자격자 보유 의무
    * Ⅱ-2 (지정인력형): 국가 지정·위탁·대행 기관의 인력 요건 (검사·검정·인증·진단기관 등)
    * Ⅱ-3 (전속배치형): 단일 자격자만 선임 가능(대체 불가). ※매우 드묾
    * Ⅱ-4 (선택배치형): 법령이 인정하는 복수 자격 중 택일 선임 (「또는」, 「어느 하나」)
    * Ⅱ-5 (현장배치형): 공사·사업장 규모·종별에 따라 배치 의무 (프로젝트 단위)
    * Ⅲ-1 (부가우대-시험면제) / Ⅲ-2 (부가우대-인사) / Ⅲ-3 (부가우대-위촉·자문)
    * Ⅳ-0 (제외): 타 조문 통합·삭제·이관, 정의 조항, 우대 외 사항

    #### 4-4. 중대재해처벌법 대상 여부
    중처법과 연계되는 안전관리 의무를 자격자에게 부여하면 "대상":
      - 중대산업재해(안전관리자·보건관리자 선임 등), 중대시민재해(시설물·환경·에너지 안전관리),
        고압가스 안전관리자, 검사대상기기 조종자·에너지관리자
    판단 원칙: 단순 인사우대·시험면제(D·E)는 "비대상". 의무 선임·배치로 사고 시 책임이 따르는
    경우만 "대상". 애매하면 "비대상" + 검토필요 표시.

    #### 4-5. 우대 텍스트 (우대여부 O일 때만)
    - 조문_요약: 우대 조문의 핵심을 3문장 이내로 요약 (구직자 친화적 톤)
    - 상세_분석: 정책적(경력이음)으로 어떤 모순 위험이 있고, 구직자에게 어떤 취업 기회를
      여는지 5문장 이내. ※ 활용도_상세와 내용 반복 금지 — 여기는 '우대·정책' 관점만.

    ---
    ### 🧪 [공통 판정 항목]
    - AI_신뢰도: '높음'(종목 명칭이 조문·별표에 텍스트로 명시) / '보통'(직무 내용상 강한 추론) /
      '낮음'(논리적 비약 필요)
    - 검토필요: ① AI_신뢰도가 '낮음'이거나 ② 활용도_구분이 '대폭 증가/대폭 감소'이거나
      ③ 중처법·Track 판단이 애매하거나 ④ 자격·인력·선임·검사·교육 '기준'을 담은 별표가
      '파일 전용(내용 미확보)'라 그 기준을 확인할 수 없는 경우에만 "O", 그 외 "X".
      ※ 신뢰도가 '보통'이라는 이유만으로 O를 달지 말 것 (검토필요 남발 금지).
      ※ 미확보 별표가 서식·별지(신청서·증·대장 등 행정 양식), 수수료·과태료 기준,
        인·허가 의제 목록 같은 비(非)자격기준류뿐이면 ④에 해당하지 않음 — 확보된
        본문·별표만으로 판정하고 "X". ('별표 상태' 섹션의 '서식류' 표시가 그 예)
    - 검토사유: 검토필요가 "O"일 때만 구체적으로, "X"면 빈칸("")
    - 검토사유 작성 규칙: 본 법령 원문에 실제 등장한 조문 번호·별표 번호·자격종목명만
      언급할 것. 다른 법령의 사례, 기억 속 일반 사례(예: 타 법령의 별표 기준) 인용 절대
      금지. 자격 기준류 별표가 '파일 전용(미확보)' 상태면 그 사실을 그대로 기재
      (서식류 미확보는 검토사유로 쓰지 말 것).

    ### 🚨 [치명적 에러 방지 규칙 — 반드시 지킬 것]
    1. **JSON 형태 유지**: Key는 반드시 큰따옴표(")를 사용해야 합니다.
    2. **내부 텍스트 큰따옴표 금지**: 한국어 내용 안에서 강조할 때는 절대 큰따옴표(")를 쓰지 말고 작은따옴표(')를 쓰세요.
    3. **엔터키(줄바꿈) 절대 금지**: 모든 텍스트는 중간 줄바꿈 없이 한 줄로 이어서 작성하세요.
    4. **순수 종목명만 추출**: '[안전관리]', 'ㅇ 직무분야:' 같은 분류명이나 기호를 절대 쓰지 마십시오.
       오직 '자격증이름1, 자격증이름2' 처럼 이름만 쉼표로 연결하세요.
    5. **모든 자격증 무제한 추출**: 연관 자격증이 수백 개라도 단 하나도 누락하지 말고 끝까지 다 작성하세요.

    ### 📤 [출력 JSON 포맷 (Strict Rule)]
    반드시 아래 JSON 형식만 출력하고, 설명이나 마크다운 백틱(```json)을 포함하지 마십시오.

    {{
      "연관도": "연관높음" | "단순관련" | "해당없음",
      "개정유형": "제정/일부개정/전부개정/폐지 중 택1",
      "주요_제개정내용": "- 팩트1 - 팩트2 (글머리 '-' 나열, 한 줄)",
      "종목": "관련 자격증 이름만 쉼표로 나열 (없으면 '없음')",
      "활용도_구분": "대폭 증가/소폭 증가/현상 유지/소폭 감소/대폭 감소 중 택1 (해당없음일 땐 \\"\\")",
      "활용도_상세": "자격 수요·활용도 분석 3문장 이내 (해당없음일 땐 \\"\\")",
      "우대여부": "O" | "X",
      "우대분류": "의무고용" | "직무권한부여" | "인사우대" | "시험면제" | "기타",
      "Track1_취급유형": "A" | "B" | "C" | "D" | "E" | "Z",
      "Track1_위험도": "C" | "H" | "M" | "L" | "N" | "X",
      "Track2_효용코드": "Ⅰ-1" | "Ⅰ-2" | "Ⅱ-1" | "Ⅱ-2" | "Ⅱ-3" | "Ⅱ-4" | "Ⅱ-5" | "Ⅲ-1" | "Ⅲ-2" | "Ⅲ-3" | "Ⅳ-0",
      "중처법대상": "대상" | "비대상",
      "조문_요약": "우대 조문 3문장 요약 (우대여부 X면 \\"\\")",
      "상세_분석": "정책 모순 + 취업 기회 5문장 (우대여부 X면 \\"\\")",
      "AI_신뢰도": "높음" | "보통" | "낮음",
      "검토필요": "O" | "X",
      "검토사유": "O인 경우만 사유 (아니면 \\"\\")",
      "조문리스트": [
        {{"조문명": "제1조(목적)", "숫자": "1"}},
        {{"조문명": "별표 1", "숫자": ""}}
      ]
    }}

    ### ⚠️ [코드 작성 필수 규칙]
    - Track1_취급유형 "Z" ↔ Track1_위험도 "X"는 항상 짝입니다.
    - "N/A", "없음", 빈칸 등은 코드 칸에 절대 사용하지 마십시오. 반드시 제시된 코드 중 하나를 고르십시오.
    """

    # 🌟 [모델 추상화] 호출/재시도는 통역 창구에 위임
    try:
        raw_text = _client().generate_with_retry(
            prompt,
            attempt_count=attempt_count,
            max_output_tokens=32768,
            temperature=0.1,
        )
    except Exception as e:
        return False, "", {"error": str(e)}

    # ── 응답 파싱 (두 시스템 공통 로직 계승) ──
    try:
        match = re.search(r'```json\s*(.*?)\s*```', raw_text, re.DOTALL | re.IGNORECASE)
        if match:
            json_str = match.group(1)
        else:
            json_str = raw_text.replace("```json", "").replace("```JSON", "").replace("```", "").strip()

        json_str = json_str.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')

        try:
            data = json.loads(json_str, strict=False)
        except json.JSONDecodeError as je:
            print(f"\n    🚨 [AI 문법 파괴 발생! 범인 색출 블랙박스 로그]")
            print(f"    >> AI가 뱉은 날것의 텍스트:\n{json_str}\n")
            return False, "", {"error": f"JSON 문법 오류: {je}"}

        # ── 조문 링크 조립 (RADAR 로직 계승) ──
        jomun_list = data.get("조문리스트", [])
        if not jomun_list or not isinstance(jomun_list, list):
            jomun_list = [{"조문명": "내용 확인", "숫자": ""}]

        links_str_list, names_str_list = [], []
        for j in jomun_list:
            j_name = j.get("조문명", "확인불가") if isinstance(j, dict) else str(j)
            if "별표" in j_name:
                j_name = re.sub(r'별표\s*(\d+)', r'별표 \1', j_name)
            if j_name == "내용 확인":
                names_str_list.append("전체 (세부 조문 미지정)")
                links_str_list.append(f"▶ {law['법령명']}\n{law.get('링크', '')}")
            else:
                names_str_list.append(j_name)
                new_link = generate_new_law_link(
                    law_name=law.get('법령명', ''),
                    enforce_date=law.get('시행일자', ''),
                    prom_num=law.get('공포번호', ''),
                    prom_date=law.get('공포일자', ''),
                    article_name=j_name,
                )
                links_str_list.append(f"▶ {law['법령명']} {j_name}\n{new_link}")
        links_str = "\n\n".join(links_str_list)
        names_str = ", ".join(names_str_list)

        # ── 정합성 강제 (후처리 방어망) ──
        연관도 = _normalize_relevance(data.get("연관도") or data.get("분류") or data.get("연관성_판별"))
        우대여부 = "O" if str(data.get("우대여부", "")).strip().upper() == "O" else "X"
        if 연관도 == "해당없음":
            우대여부 = "X"   # 해당없음인데 우대일 수 없음 (모순 방지)

        # Track1 코드 교정 (RADAR 방어망 계승) + 우대여부=X 정합 강제
        _t1_type = (data.get("Track1_취급유형", "") or "").strip()
        _t1_risk = (data.get("Track1_위험도", "") or "").strip()
        _INVALID = {"N/A", "n/a", "NA", "없음", "-", ""}
        if 우대여부 == "X":
            _t1_type, _t1_risk = "Z", "X"
            data["우대분류"] = "기타"
            data["Track2_효용코드"] = "Ⅳ-0"
            data["중처법대상"] = "비대상"
            data["조문_요약"] = data.get("조문_요약", "") if str(data.get("조문_요약", "")).strip() else ""
            data["상세_분석"] = data.get("상세_분석", "") if str(data.get("상세_분석", "")).strip() else ""
        else:
            if _t1_type in _INVALID:
                _t1_type = "Z"
            if _t1_type == "Z":
                _t1_risk = "X"
            elif _t1_risk in _INVALID:
                _t1_risk = "N"

        # ★정책 통일(2026-07-05): 활용도는 연관높음·단순관련 모두 산출 (해당없음만 빈칸)
        활용도_구분 = _normalize_usage(data.get("활용도_구분", "")) if 연관도 in ("연관높음", "단순관련") else ""

        law_info = {
            "시행일자": law["시행일자"],
            "소관부처": law.get("소관부처", "") or data.get("소관부처", ""),
            "법령명": law["법령명"],
            "개정유형": data.get("개정유형", ""),
            "연관도": 연관도,
            "우대여부": 우대여부 if 우대여부 == "O" else "",
            "관련 종목": data.get("종목", ""),
            "주요 제·개정내용": data.get("주요_제개정내용", ""),
            "활용도_구분": 활용도_구분,
            "활용도_상세": data.get("활용도_상세", "") if 연관도 in ("연관높음", "단순관련") else "",
            "조문 요약": data.get("조문_요약", ""),
            "우대분류": data.get("우대분류", "기타"),
            "Track1_취급유형": label_track1_type(_t1_type),
            "Track1_위험도": label_track1_risk(_t1_risk),
            "Track2_효용코드": label_track2_code(data.get("Track2_효용코드", "Ⅳ-0")),
            "중처법대상": data.get("중처법대상", "비대상"),
            "상세 분석 결과": data.get("상세_분석", ""),
            "근거조문": names_str,
            "AI신뢰도": data.get("AI_신뢰도", ""),
            "검토필요": data.get("검토필요", "X"),
            "검토사유": _sanitize_review(data.get("검토사유", ""), data.get("관련_종목", ""),
                                        law.get("원본", ""), qnet_certs_text),
            "조문별 다이렉트 링크": links_str,
        }

        return True, 연관도, law_info

    except Exception as e:
        return False, "", {"error": str(e)}
