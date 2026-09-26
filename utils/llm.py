"""
Dostawcy modeli oceniających oferty: Google Gemini, API zgodne z OpenAI, Anthropic.

Każdy dostawca dostaje ten sam prompt i ten sam schemat odpowiedzi - różni ich
tylko transport. Błędy tłumaczymy na `LLMError` z rodzajem, bo od rodzaju zależy
reakcja kaskady: limit albo zły klucz -> następny klucz, urwany JSON -> podział
paczki (patrz waterfall_analysis.score_batch).

Wybór dostawcy i modeli siedzi w .env (LLM_PROVIDER, <DOSTAWCA>_MODELS) - patrz
.env.example. Ten moduł nie importuje config.py, żeby config mógł importować jego.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

import requests
from pydantic import BaseModel

# Wartości z przykładowych plików, które nie są kluczami. (Nie trzymamy tu
# żadnych realnych kluczy - nawet wygasłych, nawet jako czarna lista.)
PLACEHOLDERS = {"YOUR_KEY_HERE", "CHANGEME", "TODO"}

# Odpowiedź modelu dla paczki 75 ofert potrafi mieć kilkanaście tysięcy tokenów;
# lokalny model na CPU generuje ją minutami.
_TIMEOUT = (30, 900)


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    key_envs: tuple[str, ...]      # pierwszy to klucz główny; reszta to zapas do rotacji
    models_env: str
    default_models: tuple[str, ...]
    # Profil preferencji to jedno wywołanie robiące otwartą syntezę - tu mocniejszy
    # model się opłaca (patrz generate_preference_profile.py).
    default_profile_models: tuple[str, ...]
    base_url_env: str = ""
    default_base_url: str = ""
    key_hint: str = ""


def _key_envs(primary: str, prefix: str) -> tuple[str, ...]:
    return (primary,) + tuple(f"{prefix}_{i}" for i in range(1, 5))


PROVIDERS: dict[str, Provider] = {
    "gemini": Provider(
        id="gemini",
        label="Google Gemini",
        key_envs=_key_envs("GEMINI_API_KEY_PRIMARY", "GEMINI_API_KEY"),
        models_env="GEMINI_MODELS",
        # Kolejność ustalona empirycznie (benchmark_models.py na 53 ofertach ocenionych
        # ręcznie, 3 przebiegi). Korelacja Spearmana z ocenami użytkownika / czas na batch:
        #   gemini-3.5-flash-lite   +0.840   15s   <- najlepsza korelacja, najszybszy
        #   gemini-3.1-flash-lite   +0.833   16s   <- najlepszy MAE (0.91), remis w korelacji
        #   gemini-3.6-flash        +0.802   40s   <- gorszy MIMO że nowszy i większy
        #   gemini-2.5-flash        +0.664  100s   <- ostatnia deska ratunku
        # Wniosek wbrew intuicji: modele "lite" wygrywają. To zadanie to klasyfikacja
        # wg jawnej rubryki (profil preferencji), a nie otwarte rozumowanie - większy
        # model nie ma tu czego dołożyć, a na darmowym planie kosztuje czas i limity.
        # Zanim zmienisz tę listę, uruchom benchmark_models.py.
        default_models=("gemini-3.5-flash-lite", "gemini-3.1-flash-lite",
                        "gemini-3.6-flash", "gemini-2.5-flash"),
        default_profile_models=("gemini-3.6-flash", "gemini-3.5-flash",
                                "gemini-3.5-flash-lite", "gemini-2.5-flash"),
        key_hint="AIza…",
    ),
    # Każdy serwer z endpointem /chat/completions: OpenAI, OpenRouter, Groq,
    # DeepSeek, Mistral, a lokalnie Ollama czy LM Studio (OPENAI_BASE_URL).
    "openai": Provider(
        id="openai",
        label="OpenAI / zgodne API",
        key_envs=_key_envs("OPENAI_API_KEY", "OPENAI_API_KEY"),
        models_env="OPENAI_MODELS",
        default_models=("gpt-6-luna",),
        default_profile_models=("gpt-5.6-terra", "gpt-6-luna"),
        base_url_env="OPENAI_BASE_URL",
        default_base_url="https://api.openai.com/v1",
        key_hint="sk-…",
    ),
    "anthropic": Provider(
        id="anthropic",
        label="Anthropic Claude",
        key_envs=_key_envs("ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
        models_env="ANTHROPIC_MODELS",
        default_models=("claude-haiku-4-5", "claude-sonnet-5"),
        default_profile_models=("claude-sonnet-5", "claude-haiku-4-5"),
        base_url_env="ANTHROPIC_BASE_URL",
        default_base_url="https://api.anthropic.com",
        key_hint="sk-ant-…",
    ),
}
DEFAULT_PROVIDER = "gemini"


@dataclass(frozen=True)
class LLMSettings:
    provider: Provider
    models: list[str]
    profile_models: list[str]
    api_keys: list[str]
    base_url: str
    models_overridden: bool
    error: str = ""               # niepusty = konfiguracja nie nadaje się do uruchomienia


def _split_models(raw: str) -> list[str]:
    out = []
    for m in (raw or "").split(","):
        m = m.strip()
        if m and m not in out:
            out.append(m)
    return out


def _clean(value: Any) -> str:
    value = str(value or "").strip()
    return "" if value in PLACEHOLDERS else value


def settings_from_env(env: Mapping[str, str]) -> LLMSettings:
    """Rozstrzyga dostawcę, kaskadę modeli i pulę kluczy z mapy zmiennych środowiskowych."""
    requested = (env.get("LLM_PROVIDER") or DEFAULT_PROVIDER).strip().lower()
    known = requested in PROVIDERS
    provider = PROVIDERS[requested if known else DEFAULT_PROVIDER]

    # Pula do rotacji: klucz główny JEST jej częścią. Gdy była budowana bez niego,
    # świeży klon z samym kluczem głównym startował z pustą pulą i analiza kręciła
    # się w kółko, zamiast cokolwiek ocenić. Nieznany dostawca = pusta pula, żeby
    # literówka w LLM_PROVIDER nie puściła płatnych zapytań do innego dostawcy.
    keys: list[str] = []
    for name in provider.key_envs if known else ():
        key = _clean(env.get(name))
        if key and key not in keys:
            keys.append(key)

    custom = _split_models(env.get(provider.models_env, ""))
    models = custom or list(provider.default_models)
    # Własne modele (np. lokalny serwer) zwykle nie znają domyślnych nazw profilu.
    profile_models = custom or list(provider.default_profile_models)

    base_url = ""
    if provider.base_url_env:
        base_url = (_clean(env.get(provider.base_url_env)) or provider.default_base_url).rstrip("/")

    error = ""
    if not known:
        error = f"Nieznany LLM_PROVIDER={requested!r} - dostępne: {', '.join(PROVIDERS)}."
    elif not keys:
        error = (f"Brak klucza API dla {provider.label} - ustaw {provider.key_envs[0]} "
                 f"w .env (patrz .env.example).")
    return LLMSettings(provider, models, profile_models, keys, base_url, bool(custom), error)


def mask_key(key: str) -> str:
    return f"{key[:4]}...{key[-4:]}" if len(key) > 8 else "***"


# --- zapytanie --------------------------------------------------------------

class LLMError(Exception):
    """Błąd wywołania modelu. `kind`: rate_limit | invalid_key | truncated | other."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


@dataclass
class LLMReply:
    data: Any
    in_tokens: int | None = None
    out_tokens: int | None = None


def ask_json(settings: LLMSettings, model: str, api_key: str, prompt: str,
             item_model: type[BaseModel] | None = None, *, max_tokens: int = 65535,
             temperature: float | None = 0.3) -> LLMReply:
    """
    Jedno zapytanie o JSON.

    Z `item_model` odpowiedź jest LISTĄ obiektów tego modelu, wymuszoną trybem
    Structured Outputs dostawcy (kolejność pól modelu = kolejność generowania).
    Bez niego - dowolny obiekt JSON. `temperature` dostaje tylko Gemini. Podnosi `LLMError`.
    """
    pid = settings.provider.id
    if pid == "gemini":
        return _ask_gemini(model, api_key, prompt, item_model, max_tokens, temperature)
    schema = _wrapped(item_model) if item_model is not None else None
    if pid == "openai":
        return _ask_openai(settings.base_url, model, api_key, prompt, schema)
    if pid == "anthropic":
        return _ask_anthropic(settings.base_url, model, api_key, prompt, schema, max_tokens)
    raise LLMError("other", f"Nieobsługiwany dostawca: {pid}")


def strict_schema(schema: dict) -> dict:
    """Schemat z pydantic -> postać ścisła (bez `title`, bez dodatkowych pól).
    OpenAI (strict) i Anthropic wymagają `additionalProperties: false` przy obiektach."""
    if isinstance(schema, dict):
        out = {k: strict_schema(v) for k, v in schema.items() if k != "title"}
        if out.get("type") == "object":
            out["additionalProperties"] = False
            out.setdefault("required", list(out.get("properties", {})))
        return out
    if isinstance(schema, list):
        return [strict_schema(v) for v in schema]
    return schema


# Obiekt-opakowanie: OpenAI i Anthropic przyjmują w korzeniu schematu tylko obiekt.
_WRAP_KEY = "evaluations"


def _wrapped(item_model: type[BaseModel]) -> dict:
    return {
        "type": "object",
        "properties": {_WRAP_KEY: {"type": "array",
                                   "items": strict_schema(item_model.model_json_schema())}},
        "required": [_WRAP_KEY],
        "additionalProperties": False,
    }


def _parse(text: str) -> Any:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        # Urwany albo zepsuty JSON: kaskada dzieli wtedy paczkę na mniejsze.
        raise LLMError("truncated", f"JSON Validate Err: {e}") from e


def _unwrap(data: Any, want_list: bool) -> Any:
    if not want_list or isinstance(data, list):
        return data
    if isinstance(data, dict):
        if isinstance(data.get(_WRAP_KEY), list):
            return data[_WRAP_KEY]
        lists = [v for v in data.values() if isinstance(v, list)]
        if len(lists) == 1:
            return lists[0]
    raise LLMError("other", "Odpowiedź nie zawiera listy ocen.")


def _http_error(resp: requests.Response, rate_codes: tuple[int, ...]) -> LLMError:
    detail = resp.text[:300].replace("\n", " ")
    msg = f"HTTP {resp.status_code}: {detail}"
    if resp.status_code in rate_codes:
        return LLMError("rate_limit", msg)
    if resp.status_code in (401, 403):
        return LLMError("invalid_key", msg)
    return LLMError("other", msg)


def _post(url: str, headers: dict, body: dict) -> requests.Response:
    try:
        return _session().post(url, headers=headers, json=body, timeout=_TIMEOUT)
    except requests.RequestException as e:
        raise LLMError("other", f"{type(e).__name__}: {e}") from e


_http: requests.Session | None = None


def _session() -> requests.Session:
    # Jedna sesja = jedno połączenie HTTP wielokrotnego użytku między paczkami.
    global _http
    if _http is None:
        _http = requests.Session()
    return _http


# --- Gemini ------------------------------------------------------------------

_gemini_clients: dict[str, Any] = {}


def _classify_gemini(err: str) -> str:
    """Czy z tego błędu wychodzi się zmianą klucza, czy podziałem batcha?"""
    if "429" in err or "403" in err or "ResourceExhausted" in err:
        return "rate_limit"
    if "Expecting" in err or "Unterminated" in err or "JSON Validate Err" in err:
        return "truncated"
    if "API_KEY_INVALID" in err or "API key not valid" in err:
        return "invalid_key"
    return "other"


def _ask_gemini(model, api_key, prompt, item_model, max_tokens, temperature) -> LLMReply:
    from google import genai
    from google.genai import types

    # Klient per klucz: tworzenie go od nowa przy każdej próbie zawiązuje nowe
    # połączenie HTTP, a kluczy jest kilka i wracają w rotacji.
    if api_key not in _gemini_clients:
        _gemini_clients[api_key] = genai.Client(api_key=api_key)
    config: dict[str, Any] = {"response_mime_type": "application/json",
                              "max_output_tokens": max_tokens}
    if item_model is not None:
        # Kolejność pól modelu to kolejność generowania (SDK wysyła propertyOrdering).
        config["response_schema"] = list[item_model]
    if temperature is not None:
        config["temperature"] = temperature
    try:
        resp = _gemini_clients[api_key].models.generate_content(
            model=model, contents=prompt, config=types.GenerateContentConfig(**config))
    except Exception as e:
        raise LLMError(_classify_gemini(str(e)), str(e)) from e
    usage = getattr(resp, "usage_metadata", None)
    return LLMReply(_unwrap(_parse(resp.text), item_model is not None),
                    getattr(usage, "prompt_token_count", None),
                    getattr(usage, "candidates_token_count", None))


# --- OpenAI i zgodne ---------------------------------------------------------

# Serwery, które nie znają `json_schema` (np. DeepSeek), dostają tryb json_object.
# Zapamiętujemy to, żeby każda paczka nie płaciła za odrzucone zapytanie.
_json_object_only: set[tuple[str, str]] = set()


def _ask_openai(base_url, model, api_key, prompt, schema) -> LLMReply:
    # Bez temperature i max_tokens: modele rozumujące odrzucają temperature != 1,
    # a limit wyjścia i tak ustawia serwer na maksimum modelu.
    url = f"{base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}"}
    key = (base_url, model)
    use_schema = schema is not None and key not in _json_object_only

    def body(schema_mode: bool) -> dict:
        text = prompt
        if schema_mode:
            fmt = {"type": "json_schema",
                   "json_schema": {"name": "job_evaluations", "strict": True,
                                   "schema": schema}}
        else:
            fmt = {"type": "json_object"}
            if schema is not None:
                text += (f'\n\nOdpowiedz obiektem JSON {{"{_WRAP_KEY}": [...]}}, '
                         "gdzie tablica to lista ocen opisana wyżej.")
        return {"model": model, "messages": [{"role": "user", "content": text}],
                "response_format": fmt}

    resp = _post(url, headers, body(use_schema))
    if use_schema and resp.status_code == 400 and (
            "response_format" in resp.text or "json_schema" in resp.text):
        _json_object_only.add(key)
        resp = _post(url, headers, body(False))
    if resp.status_code >= 400:
        raise _http_error(resp, (429, 503))

    data = resp.json()
    choice = (data.get("choices") or [{}])[0]
    if choice.get("finish_reason") == "length":
        raise LLMError("truncated", "Odpowiedź urwana na limicie tokenów (finish_reason=length).")
    message = choice.get("message") or {}
    if message.get("refusal"):
        raise LLMError("other", f"Model odmówił: {message['refusal'][:200]}")
    usage = data.get("usage") or {}
    return LLMReply(_unwrap(_parse(message.get("content") or ""), schema is not None),
                    usage.get("prompt_tokens"), usage.get("completion_tokens"))


# --- Anthropic ----------------------------------------------------------------

def _ask_anthropic(base_url, model, api_key, prompt, schema, max_tokens) -> LLMReply:
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    body: dict[str, Any] = {
        "model": model,
        # Haiku 4.5 ma 64k tokenów wyjścia; więcej niż 32k paczka nie potrzebuje.
        "max_tokens": min(max_tokens, 32000),
        "messages": [{"role": "user", "content": prompt}],
    }
    if schema is not None:
        body["output_config"] = {"format": {"type": "json_schema", "schema": schema}}
    resp = _post(f"{base_url}/v1/messages", headers, body)
    if resp.status_code >= 400:
        raise _http_error(resp, (429, 529))

    data = resp.json()
    if data.get("stop_reason") == "max_tokens":
        raise LLMError("truncated", "Odpowiedź urwana na limicie tokenów (stop_reason=max_tokens).")
    if data.get("stop_reason") == "refusal":
        raise LLMError("other", "Model odmówił odpowiedzi (stop_reason=refusal).")
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    usage = data.get("usage") or {}
    return LLMReply(_unwrap(_parse(text), schema is not None),
                    usage.get("input_tokens"), usage.get("output_tokens"))
