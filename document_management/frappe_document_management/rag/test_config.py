from unittest.mock import patch

from document_management.frappe_document_management.rag.config import (
    DEFAULT_LOCAL_EMBEDDING_MODEL,
    _provider_endpoint,
    get_rag_config,
)


class Settings(dict):
    semantic_model_type = "Local (sentence-transformers)"
    openai_model = "text-embedding-3-small"
    rag_model = "gpt-5-mini"
    openai_api_key = "configured"
    openai_compatible_api_key = "configured"

    def __getattr__(self, key):
        return self.get(key)

    def get_password(self, fieldname):
        return {
            "openai_api_key": "official-secret",
            "openai_compatible_api_key": "compatible-secret",
        }.get(fieldname)


def _settings(**overrides):
    values = {
        "chat_provider": "OpenAI",
        "embedding_provider": "Local (sentence-transformers)",
        "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
        "openai_api_key": "configured",
        "openai_compatible_api_key": "configured",
        "rag_chunk_size": 650,
        "rag_chunk_overlap": 120,
    }
    values.update(overrides)
    return Settings(values)


def test_openai_ignores_stale_compatible_endpoints():
    with patch(
        "document_management.frappe_document_management.rag.config.frappe.get_single",
        return_value=_settings(
            chat_endpoint="https://compatible.example/v1",
            embedding_provider="OpenAI",
            embedding_endpoint="https://embeddings.example/v1",
        ),
    ):
        config = get_rag_config()

    assert config.chat_endpoint == "https://api.openai.com/v1"
    assert config.embedding_endpoint == "https://api.openai.com/v1"


def test_chat_compatible_and_openai_embeddings_use_separate_keys():
    with patch(
        "document_management.frappe_document_management.rag.config.frappe.get_single",
        return_value=_settings(
            chat_provider="OpenAI Compatible",
            chat_endpoint="https://compatible.example/v1",
            embedding_provider="OpenAI",
            embedding_model="text-embedding-3-small",
        ),
    ):
        config = get_rag_config()

    assert config.chat_api_key == "compatible-secret"
    assert config.embedding_api_key == "official-secret"
    assert config.chat_endpoint == "https://compatible.example/v1"
    assert config.embedding_endpoint == "https://api.openai.com/v1"


def test_openai_chat_and_compatible_embeddings_use_separate_keys():
    with patch(
        "document_management.frappe_document_management.rag.config.frappe.get_single",
        return_value=_settings(
            embedding_provider="OpenAI Compatible",
            embedding_endpoint="https://embeddings.example/v1",
            embedding_model="custom-embedding",
        ),
    ):
        config = get_rag_config()

    assert config.chat_api_key == "official-secret"
    assert config.embedding_api_key == "compatible-secret"


def test_compatible_provider_requires_endpoint():
    try:
        _provider_endpoint("OpenAI Compatible", None)
    except ValueError as exc:
        assert "requires an endpoint" in str(exc)
    else:
        raise AssertionError("Compatible provider accepted an empty endpoint")


def test_local_embeddings_ignore_legacy_endpoint():
    assert (
        _provider_endpoint(
            "Local (sentence-transformers)",
            "https://legacy.example/v1",
        )
        is None
    )


def test_old_local_default_uses_multilingual_model():
    with patch(
        "document_management.frappe_document_management.rag.config.frappe.get_single",
        return_value=_settings(),
    ):
        config = get_rag_config()

    assert config.embedding_model == DEFAULT_LOCAL_EMBEDDING_MODEL


def test_openai_normalizes_local_model_placeholders():
    with patch(
        "document_management.frappe_document_management.rag.config.frappe.get_single",
        return_value=_settings(
            rag_model="local-model",
            embedding_provider="OpenAI",
            embedding_model="local-model",
        ),
    ):
        config = get_rag_config()

    assert config.chat_model == "gpt-5-mini"
    assert config.embedding_model == "text-embedding-3-small"


def test_missing_compatible_key_field_does_not_break_config():
    settings = _settings(
        chat_provider="OpenAI Compatible",
        chat_endpoint="https://compatible.example/v1",
    )
    settings.pop("openai_compatible_api_key", None)

    with patch(
        "document_management.frappe_document_management.rag.config.frappe.get_single",
        return_value=settings,
    ):
        config = get_rag_config()

    assert config.chat_api_key is None
