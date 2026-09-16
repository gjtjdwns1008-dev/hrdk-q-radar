"""
hrdk_law_core.llm_client
------------------------
LLM 프로바이더 추상화 계층 (통역 창구).

분석 로직(brain_gemini.py)이 특정 모델(Gemini)에 직접 묶이지 않도록,
"프롬프트를 주면 텍스트를 돌려주는" 단일 인터페이스를 제공합니다.

나중에 사내 AI나 다른 모델로 교체할 때 이 파일의 어댑터만 추가하면 되고,
분석 로직은 한 줄도 바꿀 필요가 없습니다.

사용법:
    from hrdk_law_core.llm_client import get_llm_client
    client = get_llm_client()          # 환경변수 LLM_PROVIDER로 자동 선택
    text = client.generate(prompt)     # 모델이 뭐든 동일하게 호출

환경변수:
    LLM_PROVIDER  : "gemini" (기본) | "openai_compatible" | "echo"(테스트용)
    LLM_MODEL     : 모델명 (예: "gemini-3.5-flash"). 미지정 시 프로바이더 기본값.
    GEMINI_API_KEY: Gemini 사용 시
    LLM_API_KEY   : openai_compatible(사내 AI 등) 사용 시 API 키
    LLM_BASE_URL  : openai_compatible 사용 시 엔드포인트 URL
"""

import os
import time
from abc import ABC, abstractmethod


# ──────────────────────────────────────────────────────────
# 공통 인터페이스: 모든 LLM 어댑터는 이 형태를 따른다
# ──────────────────────────────────────────────────────────
class LLMClient(ABC):
    """모든 LLM 프로바이더가 구현해야 하는 공통 규격."""

    @abstractmethod
    def generate(self, prompt: str, *, max_output_tokens: int = 32768,
                 temperature: float = 0.1, json_mode: bool = False) -> str:
        """프롬프트를 받아 모델의 응답 텍스트(원문)를 반환합니다.
        json_mode=True 이면 (지원 모델에 한해) JSON 형식 출력을 강제합니다.
        ※ 기본 False — 이슈브리핑 자유 서술 등 기존 호출부는 그대로 동작."""
        ...

    # 재시도는 모델과 무관한 공통 로직이므로 여기서 한 번만 구현
    def generate_with_retry(self, prompt: str, *, attempt_count: int = 5,
                            max_output_tokens: int = 32768,
                            temperature: float = 0.1, json_mode: bool = False) -> str:
        """
        503(서버폭주)/timeout/429(크레딧) 등 일시 오류에 대해 지수 백오프 재시도.
        attempt_count회 모두 실패하면 마지막 예외를 그대로 올립니다.
        """
        last_err = None
        for attempt in range(attempt_count):
            if attempt > 0:
                print(f"\n    🔄 [재시도 {attempt}/{attempt_count-1}] LLM 재호출 중... ", end="", flush=True)
            try:
                return self.generate(
                    prompt,
                    max_output_tokens=max_output_tokens,
                    temperature=temperature,
                    json_mode=json_mode,
                )
            except Exception as e:
                last_err = e
                msg = str(e).lower()
                if "503" in msg or "high demand" in msg or "overload" in msg:
                    wait = 60 * (attempt + 1)
                    print(f"\n    🚨 [서버 폭주] {wait}초 대기 후 재시도...", end="", flush=True)
                elif "429" in msg or "quota" in msg or "rate" in msg:
                    wait = 30 * (attempt + 1)
                    print(f"\n    🚨 [크레딧/쿼터] {wait}초 대기 후 재시도...", end="", flush=True)
                elif "timeout" in msg:
                    wait = 15 * (attempt + 1)
                    print(f"\n    🚨 [무응답(Timeout)] {wait}초 대기...", end="", flush=True)
                else:
                    wait = 15 * (attempt + 1)
                    print(f"\n    🚨 [기타 에러: {str(e)[:30]}...] {wait}초 대기...", end="", flush=True)
                time.sleep(wait)
        raise last_err if last_err else RuntimeError("LLM 재시도 초과")


# ──────────────────────────────────────────────────────────
# 어댑터 1: Gemini (현재 사용 중)
# ──────────────────────────────────────────────────────────
class GeminiClient(LLMClient):
    """Gemini 어댑터.
    ★2026-09-16 3.x 대응: Gemini 3.x 계열은 temperature/top_p/top_k를 백엔드에서 무시하므로
      온도를 보내지 않고, 결정성은 (1) thinking_level 고정 (2) JSON 형식 강제(json_mode)로 통제한다.
      · thinking_level: 환경변수 LLM_THINKING_LEVEL ("low" | "medium" | "high"). 비우면 모델 기본값.
        ("minimal"은 3.7 Flash 이후 400 오류 → 허용하지 않음)
      · JSON 강제는 호출별(json_mode)로만 켠다 — 이슈브리핑 자유 서술이 같은 함수를 쓰기 때문.
      · 2.x 구세대 모델을 LLM_MODEL로 지정하면 종전처럼 temperature를 전달한다.
    """
    _GEN3_PREFIX = ("gemini-3",)
    _THINKING_OK = ("low", "medium", "high")

    def __init__(self, api_key: str, model: str = "gemini-3.5-flash"):
        from google import genai  # 지연 import (gemini 미설치 환경 보호)
        self._genai = genai
        from google.genai import types
        self._types = types
        self._client = genai.Client(api_key=api_key)
        self._model = model
        lvl = os.environ.get("LLM_THINKING_LEVEL", "").strip().lower()
        if lvl and lvl not in self._THINKING_OK:
            print(f"    ⚠️ LLM_THINKING_LEVEL='{lvl}' 은 허용값(low/medium/high)이 아니어서 무시합니다.")
            lvl = ""
        self._thinking = lvl

    def _is_gen3(self) -> bool:
        return self._model.lower().startswith(self._GEN3_PREFIX)

    def generate(self, prompt: str, *, max_output_tokens: int = 32768,
                 temperature: float = 0.1, json_mode: bool = False) -> str:
        cfg = {"max_output_tokens": max_output_tokens}
        if json_mode:
            cfg["response_mime_type"] = "application/json"      # 형식 강제 (분석 프롬프트 전용)
        if self._is_gen3():
            # temperature 인자는 호출부 호환용으로만 받고 전송하지 않음 (3.x는 무시하는 값)
            if self._thinking:
                cfg["thinking_config"] = self._types.ThinkingConfig(thinking_level=self._thinking)
        else:
            cfg["temperature"] = temperature                      # 2.x 구세대 모델에만 온도 전달
        response = self._client.models.generate_content(
            model=self._model,
            contents=prompt,
            config=self._types.GenerateContentConfig(**cfg),
        )
        return response.text.strip()


# ──────────────────────────────────────────────────────────
# 어댑터 2: OpenAI 호환 (사내 AI / 기타 모델 대비)
# 많은 사내 LLM·오픈소스 서버가 OpenAI Chat Completions 규격을 따릅니다.
# 사내 AI가 이 규격을 열어주면 코드 변경 없이 환경변수만으로 연결됩니다.
# ──────────────────────────────────────────────────────────
class OpenAICompatibleClient(LLMClient):
    def __init__(self, api_key: str, base_url: str, model: str):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model

    def generate(self, prompt: str, *, max_output_tokens: int = 32768,
                 temperature: float = 0.1, json_mode: bool = False) -> str:
        import requests
        resp = requests.post(
            f"{self._base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}",
                     "Content-Type": "application/json"},
            json={
                "model": self._model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_output_tokens,
                "temperature": temperature,
            },
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()


# ──────────────────────────────────────────────────────────
# 어댑터 3: Echo (테스트 전용 — API 키 없이 파이프라인 점검)
# 미리 정해둔 JSON을 그대로 돌려줘, 키 없이 흐름만 검증할 때 씁니다.
# ──────────────────────────────────────────────────────────
class EchoClient(LLMClient):
    def __init__(self, canned_response: str = ""):
        self._canned = canned_response or '{"연관성_판별": "해당없음", "종목": "없음"}'

    def generate(self, prompt: str, *, max_output_tokens: int = 32768,
                 temperature: float = 0.1, json_mode: bool = False) -> str:
        return self._canned


# ──────────────────────────────────────────────────────────
# 팩토리: 환경변수로 어떤 어댑터를 쓸지 자동 결정
# ──────────────────────────────────────────────────────────
def get_llm_client(provider: str | None = None,
                   canned_response: str = "") -> LLMClient:
    """
    환경변수 또는 인자로 LLM 클라이언트를 생성합니다.

    provider 우선순위: 인자 > 환경변수 LLM_PROVIDER > "gemini"
    """
    provider = (provider or os.environ.get("LLM_PROVIDER", "gemini")).lower()
    model = os.environ.get("LLM_MODEL", "")

    if provider == "gemini":
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY 환경변수가 필요합니다.")
        return GeminiClient(api_key=api_key, model=model or "gemini-3.5-flash")

    if provider == "openai_compatible":
        api_key = os.environ.get("LLM_API_KEY", "")
        base_url = os.environ.get("LLM_BASE_URL", "")
        if not base_url:
            raise ValueError("LLM_BASE_URL 환경변수가 필요합니다 (사내 AI 엔드포인트).")
        return OpenAICompatibleClient(api_key=api_key, base_url=base_url,
                                      model=model or "default")

    if provider == "echo":
        return EchoClient(canned_response=canned_response)

    raise ValueError(f"알 수 없는 LLM_PROVIDER: {provider}")
