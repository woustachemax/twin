import http.client
import json
import os
import shutil
import ssl
import subprocess
import time
import urllib.error
import urllib.request

import anthropic

KEYCHAIN_ACCOUNT = "twin"
OLLAMA_APP = "/Applications/Ollama.app"
OLLAMA_DOWNLOAD_URL = "https://ollama.com/download"
OLLAMA_START_TIMEOUT = 8
OLLAMA_START_POLL = 0.25
VALIDATION_SYSTEM = "Reply with the single word OK."
VALIDATION_MAX_TOKENS = 16
VALIDATION_TIMEOUT = 30
CHAT_TIMEOUT = 90

PROVIDERS = {
    "anthropic": {
        "label": "Anthropic",
        "blurb": "Claude models",
        "model": "claude-sonnet-4-6",
        "env": "ANTHROPIC_API_KEY",
        "key_prefix": "sk-ant-",
        "key_url": "https://console.anthropic.com/settings/keys",
        "api": "anthropic",
    },
    "openai": {
        "label": "OpenAI",
        "blurb": "GPT models",
        "model": "gpt-6-luna",
        "env": "OPENAI_API_KEY",
        "key_prefix": "sk-",
        "key_url": "https://platform.openai.com/api-keys",
        "api": "responses",
        "base_url": "https://api.openai.com/v1",
        "extra": {"reasoning": {"effort": "none"}, "store": False},
    },
    "gemini": {
        "label": "Google Gemini",
        "blurb": "Gemini models",
        "model": "gemini-3.5-flash-lite",
        "env": "GEMINI_API_KEY",
        "key_prefix": "AIza",
        "key_url": "https://aistudio.google.com/apikey",
        "api": "chat",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "extra": {"reasoning_effort": "minimal"},
    },
    "xai": {
        "label": "xAI",
        "blurb": "Grok models",
        "model": "grok-4.3",
        "env": "XAI_API_KEY",
        "key_prefix": "xai-",
        "key_url": "https://console.x.ai",
        "api": "responses",
        "base_url": "https://api.x.ai/v1",
        "extra": {},
    },
    "ollama": {
        "label": "Ollama",
        "blurb": "Local models, fully offline",
        "model": "llama3.2:1b",
        "env": "",
        "key_prefix": None,
        "key_url": "https://ollama.com/library",
        "api": "chat",
        "base_url": "http://localhost:11434/v1",
        "extra": {},
        "local": True,
    },
}

OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"

INVALID_KEY_MARKERS = (
    "api key not valid", "api_key_invalid", "invalid api key", "incorrect api key", "invalid x-api-key",
    "valid api key",
)


class ProviderError(Exception):
    def __init__(self, kind, detail=""):
        super().__init__(detail or kind)
        self.kind = kind
        self.detail = detail


def display_url(url):
    return url.split("://", 1)[-1]


def detect_provider(key):
    for provider in sorted(PROVIDERS, key=lambda p: -len(PROVIDERS[p]["key_prefix"] or "")):
        prefix = PROVIDERS[provider]["key_prefix"]
        if prefix and key.startswith(prefix):
            return provider
    return None


def model_for(provider, config=None):
    override = os.environ.get("TWIN_MODEL", "").strip()
    if override:
        return override
    config = config or {}
    if config.get("provider") == provider and config.get("model"):
        return config["model"]
    return PROVIDERS[provider]["model"]


def keychain_service(provider):
    return f"Twin {PROVIDERS[provider]['label']} API key"


def keychain_get(provider):
    if PROVIDERS[provider].get("local"):
        return None
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-a", KEYCHAIN_ACCOUNT, "-s", keychain_service(provider), "-w"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    key = result.stdout.strip()
    return key if result.returncode == 0 and key else None


def keychain_save(provider, key):
    if PROVIDERS[provider].get("local"):
        return True
    try:
        result = subprocess.run(
            ["security", "add-generic-password", "-U", "-a", KEYCHAIN_ACCOUNT, "-s", keychain_service(provider),
             "-l", keychain_service(provider), "-w", key],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def env_key(provider):
    return os.environ.get(PROVIDERS[provider]["env"], "").strip() or None


def saved_key(provider):
    return env_key(provider) or keychain_get(provider)


def ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def error_message(text):
    try:
        data = json.loads(text)
    except ValueError:
        return text.strip()[:200]
    if isinstance(data, list) and data:
        data = data[0]
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict):
        error = error.get("message")
    return str(error or "").strip()[:200]


def http_failure_kind(status, text):
    lowered = text.lower()
    if status in (401, 403) or (status == 400 and any(marker in lowered for marker in INVALID_KEY_MARKERS)):
        return "auth"
    if status == 429:
        return "rate_limit"
    if status == 404 and "model" in lowered:
        return "model_not_found"
    return "api_error"


def ollama_installed_models():
    request = urllib.request.Request(OLLAMA_TAGS_URL, headers={"User-Agent": "Twin/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None
    return [model.get("name", "") for model in data.get("models") or [] if model.get("name")]


def ollama_installed():
    return os.path.isdir(OLLAMA_APP) or shutil.which("ollama") is not None


def start_ollama():
    if ollama_installed_models() is not None:
        return True
    if not ollama_installed():
        return False
    try:
        if os.path.isdir(OLLAMA_APP):
            subprocess.run(["open", "-a", "Ollama"], check=False, timeout=10)
        else:
            subprocess.Popen(["ollama", "serve"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
    except (OSError, subprocess.TimeoutExpired):
        return False
    deadline = time.monotonic() + OLLAMA_START_TIMEOUT
    while time.monotonic() < deadline:
        if ollama_installed_models() is not None:
            return True
        time.sleep(OLLAMA_START_POLL)
    return ollama_installed_models() is not None


def json_request(url, api_key, body):
    return urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}", "User-Agent": "Twin/0.1"},
    )


def provider_error(e):
    if isinstance(e, urllib.error.HTTPError):
        text = e.read().decode("utf-8", "replace")
        return ProviderError(http_failure_kind(e.code, text), error_message(text) or f"HTTP {e.code}")
    return ProviderError("offline", str(getattr(e, "reason", e)))


def post_json(url, api_key, body, timeout):
    try:
        with urllib.request.urlopen(json_request(url, api_key, body), timeout=timeout,
                                    context=ssl_context()) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise provider_error(e)
    except ValueError:
        raise ProviderError("api_error", "the response couldn't be read")


def open_stream(url, api_key, body, timeout):
    try:
        return urllib.request.urlopen(json_request(url, api_key, body), timeout=timeout, context=ssl_context())
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise provider_error(e)


class BaseClient:
    def __init__(self, provider, api_key, model):
        self.provider = provider
        self.label = PROVIDERS[provider]["label"]
        self.model = model
        self.api_key = api_key


class AnthropicClient(BaseClient):
    def __init__(self, provider, api_key, model):
        super().__init__(provider, api_key, model)
        self.sdk = anthropic.Anthropic(api_key=api_key, max_retries=1)

    def complete(self, system, messages, max_tokens, timeout=CHAT_TIMEOUT):
        try:
            response = self.sdk.with_options(timeout=timeout).messages.create(
                model=self.model, max_tokens=max_tokens, system=system, messages=messages,
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            raise ProviderError("auth", getattr(e, "message", str(e)))
        except anthropic.RateLimitError as e:
            raise ProviderError("rate_limit", getattr(e, "message", str(e)))
        except anthropic.APIConnectionError as e:
            raise ProviderError("offline", str(e))
        except anthropic.APIError as e:
            raise ProviderError("api_error", getattr(e, "message", str(e)))
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        return text, response.stop_reason == "refusal"


class ResponsesClient(BaseClient):
    def complete(self, system, messages, max_tokens, timeout=CHAT_TIMEOUT):
        spec = PROVIDERS[self.provider]
        body = {"model": self.model, "input": messages, "max_output_tokens": max(max_tokens, 16), **spec["extra"]}
        if system:
            body["instructions"] = system
        data = post_json(f"{spec['base_url']}/responses", self.api_key, body, timeout)
        texts, refused = [], False
        for item in data.get("output") or []:
            if item.get("type") != "message":
                continue
            for part in item.get("content") or []:
                if part.get("type") == "output_text":
                    texts.append(part.get("text") or "")
                elif part.get("type") == "refusal":
                    refused = True
        if not texts and isinstance(data.get("output_text"), str):
            texts.append(data["output_text"])
        return "".join(texts).strip(), refused


class ChatClient(BaseClient):
    def complete(self, system, messages, max_tokens, timeout=CHAT_TIMEOUT):
        spec = PROVIDERS[self.provider]
        chat = ([{"role": "system", "content": system}] if system else []) + messages
        body = {"model": self.model, "messages": chat, "max_tokens": max_tokens, **spec["extra"]}
        url = f"{spec['base_url']}/chat/completions"
        try:
            data = post_json(url, self.api_key, body, timeout)
        except ProviderError as e:
            if e.kind != "offline" or not spec.get("local"):
                raise
            if not start_ollama():
                raise ProviderError("not_installed" if not ollama_installed() else "offline", e.detail)
            data = post_json(url, self.api_key, body, timeout)
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content") or ""
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        refused = bool(message.get("refusal")) or choice.get("finish_reason") == "content_filter"
        return content.strip(), refused

    def complete_stream(self, system, messages, max_tokens, timeout=CHAT_TIMEOUT):
        spec = PROVIDERS[self.provider]
        chat = ([{"role": "system", "content": system}] if system else []) + messages
        body = {"model": self.model, "messages": chat, "max_tokens": max_tokens, "stream": True, **spec["extra"]}
        url = f"{spec['base_url']}/chat/completions"
        try:
            response = open_stream(url, self.api_key, body, timeout)
        except ProviderError as e:
            if e.kind != "offline" or not spec.get("local"):
                raise
            if not start_ollama():
                raise ProviderError("not_installed" if not ollama_installed() else "offline", e.detail)
            response = open_stream(url, self.api_key, body, timeout)
        refused, finished = False, False
        try:
            with response:
                for raw in response:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[len("data:"):].strip()
                    if payload == "[DONE]":
                        finished = True
                        break
                    try:
                        data = json.loads(payload)
                    except ValueError:
                        raise ProviderError("api_error", "the response couldn't be read")
                    if not isinstance(data, dict):
                        raise ProviderError("api_error", "the response couldn't be read")
                    if data.get("error"):
                        raise ProviderError("api_error", error_message(payload) or "the stream reported an error")
                    choices = data.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0] or {}
                    delta = choice.get("delta") or {}
                    content = delta.get("content")
                    if isinstance(content, list):
                        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
                    if content:
                        yield content
                    if delta.get("refusal"):
                        refused = True
                    if choice.get("finish_reason"):
                        finished = True
                        refused = refused or choice["finish_reason"] == "content_filter"
        except (urllib.error.URLError, http.client.HTTPException, OSError) as e:
            raise provider_error(e)
        if not finished:
            raise ProviderError("offline", "the reply was cut off")
        return refused


CLIENTS ={"anthropic": AnthropicClient, "responses": ResponsesClient, "chat": ChatClient}


def make_client(provider, api_key, model=None):
    return CLIENTS[PROVIDERS[provider]["api"]](provider, api_key, model or PROVIDERS[provider]["model"])


def validation_message(provider, error, model=None):
    spec = PROVIDERS[provider]
    label = spec["label"]
    if spec.get("local"):
        if error.kind == "not_installed":
            return f"Ollama isn't installed yet. Download it at {display_url(OLLAMA_DOWNLOAD_URL)}, then try again."
        if error.kind == "offline":
            return "Ollama is installed but didn't start. Open Ollama, then try again."
        if error.kind == "model_not_found":
            return f"Ollama doesn't have \"{model}\" yet. Run `ollama pull {model}` in a terminal, then try again."
        detail = f": {error.detail}" if error.detail else "."
        return f"Ollama returned an error while checking that model{detail}"
    if error.kind == "auth":
        return f"{label} didn't accept that key. Check that you copied the whole key, then paste it again."
    if error.kind == "rate_limit":
        return (f"That key works, but {label} says it's rate-limited or out of credits. Check your plan or "
                f"billing at {display_url(spec['key_url'])}, then try again.")
    if error.kind == "offline":
        return f"Couldn't reach {label}. Check your internet connection, then try again."
    detail = f": {error.detail}" if error.detail else "."
    return f"{label} returned an error while checking the key{detail}"


def validate_key(provider, api_key, model=None):
    client = make_client(provider, api_key, model)
    try:
        client.complete(VALIDATION_SYSTEM, [{"role": "user", "content": "ping"}], VALIDATION_MAX_TOKENS,
                        timeout=VALIDATION_TIMEOUT)
    except ProviderError as e:
        return None, validation_message(provider, e, client.model)
    return client, None


def precheck_key(provider, api_key):
    if provider not in PROVIDERS:
        return "Pick an AI provider first."
    spec = PROVIDERS[provider]
    if spec.get("local"):
        if not api_key:
            return "Type the name of a model you've pulled with Ollama, like llama3.2."
        return None
    if not api_key:
        return f"Paste your {spec['label']} API key first. You can create one at {display_url(spec['key_url'])}."
    if any(c.isspace() for c in api_key):
        return "That key has spaces in it. Copy it again without any extra spaces or line breaks."
    detected = detect_provider(api_key)
    if detected and detected != provider:
        return (f"That looks like a key from {PROVIDERS[detected]['label']}, but you picked {spec['label']}. "
                f"Switch the provider above, or paste your {spec['label']} key instead.")
    return None


def saved_client(config):
    if not config:
        return None
    provider = config.get("provider") or "anthropic"
    if provider not in PROVIDERS:
        return None
    spec = PROVIDERS[provider]
    if spec.get("local"):
        model = model_for(provider, config)
        return make_client(provider, model, model)
    key = saved_key(provider)
    return make_client(provider, key, model_for(provider, config)) if key else None
