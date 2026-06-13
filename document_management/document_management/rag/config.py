from dataclasses import dataclass

import frappe


DEFAULT_LOCAL_EMBEDDING_MODEL = (
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
)


@dataclass(frozen=True)
class RAGConfig:
    chat_provider: str
    chat_model: str
    chat_endpoint: str | None
    chat_api_key: str | None
    embedding_provider: str
    embedding_model: str
    embedding_endpoint: str | None
    embedding_api_key: str | None
    context_size: int
    chunk_size: int
    chunk_overlap: int
    top_k: int
    min_semantic_score: float
    enable_reranker: bool
    reranker_model: str
    timeout: int
    rate_limit: int
    allow_internal_endpoints: bool


def _positive_int(value, default, minimum=1):
    try:
        normalized = default if value in (None, "") else value
        return max(int(normalized), minimum)
    except (TypeError, ValueError):
        return default


def _as_bool(value, default=False):
    if value in (None, ""):
        return default
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def _float_value(value, default):
    try:
        normalized = default if value in (None, "") else value
        return float(normalized)
    except (TypeError, ValueError):
        return default


def _password(settings, fieldname):
    if not settings.get(fieldname):
        return None
    try:
        return settings.get_password(fieldname)
    except (AttributeError, frappe.DoesNotExistError):
        return None


def get_rag_config():
    settings = frappe.get_single("Document Management Settings")
    provider = settings.get("chat_provider") or "OpenAI"
    embedding_provider = (
        settings.get("embedding_provider")
        or (
            "OpenAI Compatible"
            if settings.semantic_model_type == "OpenAI API"
            else "Local (sentence-transformers)"
        )
    )
    configured_embedding_model = (settings.get("embedding_model") or "").strip()
    if embedding_provider in {"OpenAI", "OpenAI Compatible", "LM Studio"}:
        if configured_embedding_model in (
            "",
            "sentence-transformers/all-MiniLM-L6-v2",
            "local-model",
        ):
            configured_embedding_model = (
                "local-model"
                if embedding_provider == "LM Studio"
                else settings.openai_model or "text-embedding-3-small"
            )
    elif configured_embedding_model in (
        "",
        "text-embedding-3-small",
        "sentence-transformers/all-MiniLM-L6-v2",
    ):
        configured_embedding_model = DEFAULT_LOCAL_EMBEDDING_MODEL

    configured_chat_model = (settings.rag_model or "").strip()
    if provider == "Ollama" and configured_chat_model in ("", "gpt-5-mini"):
        configured_chat_model = "llama3.1"
    elif provider == "LM Studio" and configured_chat_model in (
        "",
        "gpt-5-mini",
        "llama3.1",
    ):
        configured_chat_model = "local-model"
    elif provider in {"OpenAI", "OpenAI Compatible"} and configured_chat_model in (
        "",
        "llama3.1",
        "local-model",
    ):
        configured_chat_model = "gpt-5-mini"
    elif not configured_chat_model:
        configured_chat_model = "gpt-5-mini"
    chunk_size = _positive_int(settings.get("rag_chunk_size"), 650, 100)
    overlap = min(
        _positive_int(settings.get("rag_chunk_overlap"), 120, 0),
        chunk_size - 1,
    )
    
    chat_api_key = None
    if provider == "OpenAI":
        chat_api_key = _password(settings, "openai_api_key")
    elif provider == "OpenAI Compatible":
        chat_api_key = _password(settings, "openai_compatible_api_key")

    embedding_api_key = None
    if embedding_provider == "OpenAI":
        embedding_api_key = _password(settings, "openai_api_key")
    elif embedding_provider == "OpenAI Compatible":
        embedding_api_key = _password(settings, "openai_compatible_api_key")

    legacy_endpoint = (settings.get("ai_endpoint") or "").strip() or None
    configured_chat_endpoint = (
        (settings.get("chat_endpoint") or "").strip()
        or legacy_endpoint
    )
    configured_embedding_endpoint = (
        (settings.get("embedding_endpoint") or "").strip()
        or legacy_endpoint
    )
    chat_endpoint = _provider_endpoint(provider, configured_chat_endpoint)
    embedding_endpoint = _provider_endpoint(
        embedding_provider,
        configured_embedding_endpoint,
    )
    return RAGConfig(
        chat_provider=provider,
        chat_model=configured_chat_model,
        chat_endpoint=chat_endpoint,
        chat_api_key=chat_api_key,
        embedding_provider=embedding_provider,
        embedding_model=configured_embedding_model,
        embedding_endpoint=embedding_endpoint,
        embedding_api_key=embedding_api_key,
        context_size=_positive_int(settings.get("rag_context_size"), 8192, 1024),
        chunk_size=chunk_size,
        chunk_overlap=overlap,
        top_k=min(_positive_int(settings.get("rag_top_k"), 8), 20),
        min_semantic_score=_float_value(
            settings.get("rag_min_semantic_score"),
            0.25,
        ),
        enable_reranker=_as_bool(settings.get("enable_rag_reranker"), True),
        reranker_model=(
            settings.get("rag_reranker_model")
            or "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
        ),
        timeout=_positive_int(settings.get("rag_timeout"), 120, 10),
        rate_limit=_positive_int(settings.get("chat_rate_limit_per_minute"), 10),
        allow_internal_endpoints=_as_bool(
            settings.get("allow_internal_ai_endpoints"),
            False,
        ),
    )


def _provider_endpoint(provider, configured_endpoint):
    if provider == "Local (sentence-transformers)":
        return None
    if provider == "OpenAI":
        return "https://api.openai.com/v1"
    if provider == "LM Studio":
        return configured_endpoint or "http://localhost:1234/v1"
    if provider == "Ollama":
        return configured_endpoint or "http://localhost:11434"
    if provider == "OpenAI Compatible" and not configured_endpoint:
        raise ValueError("OpenAI Compatible provider requires an endpoint.")
    return configured_endpoint
