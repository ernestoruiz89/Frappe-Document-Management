import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np

from document_management.document_management.rag import providers


def _config(**overrides):
    values = {
        "chat_provider": "OpenAI Compatible",
        "chat_model": "chat-model",
        "chat_endpoint": None,
        "chat_api_key": "secret",
        "embedding_provider": "Local (sentence-transformers)",
        "embedding_model": "local-model",
        "embedding_endpoint": None,
        "embedding_api_key": "secret",
        "context_size": 4096,
        "enable_reranker": True,
        "reranker_model": "reranker-model",
        "timeout": 30,
        "allow_internal_endpoints": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_local_embeddings_are_normalized():
    model = MagicMock()
    model.encode.return_value = [[3.0, 4.0], [0.0, 2.0]]

    with (
        patch.object(providers, "get_rag_config", return_value=_config()),
        patch.object(providers, "_local_embedding_model", return_value=model),
    ):
        vectors = providers.embed_texts(["first", "second"])

    np.testing.assert_allclose(np.linalg.norm(vectors, axis=1), [1.0, 1.0])


def test_ollama_chat_stream():
    response = MagicMock()
    response.iter_lines.return_value = [
        json.dumps({"message": {"content": "hello"}}).encode(),
        json.dumps({"message": {"content": " world"}}).encode(),
    ]

    with (
        patch.object(
            providers,
            "get_rag_config",
            return_value=_config(
                chat_provider="Ollama",
                chat_endpoint="http://localhost:11434",
            ),
        ),
        patch.object(providers.requests, "post", return_value=response),
    ):
        output = "".join(providers.stream_chat([{"role": "user", "content": "Hi"}]))

    assert output == "hello world"
    response.raise_for_status.assert_called_once()


def test_openai_compatible_chat_stream():
    event = lambda content: SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=content))]
    )
    create = MagicMock(return_value=[event("hello"), event(" world")])
    client = MagicMock()
    client.chat.completions.create = create
    fake_openai = SimpleNamespace(OpenAI=MagicMock(return_value=client))

    with (
        patch.object(providers, "get_rag_config", return_value=_config()),
        patch.dict(sys.modules, {"openai": fake_openai}),
    ):
        output = "".join(providers.stream_chat([{"role": "user", "content": "Hi"}]))

    assert output == "hello world"
    create.assert_called_once()


def test_chat_uses_chat_endpoint():
    event = lambda content: SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=content))]
    )
    client = MagicMock()
    client.chat.completions.create.return_value = [event("ok")]
    openai_constructor = MagicMock(return_value=client)
    fake_openai = SimpleNamespace(OpenAI=openai_constructor)

    with (
        patch.object(
            providers,
            "get_rag_config",
            return_value=_config(chat_endpoint="https://chat.example/v1"),
        ),
        patch.dict(sys.modules, {"openai": fake_openai}),
        patch.object(
            providers,
            "_validated_endpoint",
            return_value="https://chat.example/v1",
        ),
    ):
        list(providers.stream_chat([{"role": "user", "content": "Hi"}]))

    assert openai_constructor.call_args.kwargs["base_url"] == "https://chat.example/v1"


def test_embeddings_use_embedding_endpoint():
    response = SimpleNamespace(
        data=[SimpleNamespace(embedding=[1.0, 0.0])]
    )
    client = MagicMock()
    client.embeddings.create.return_value = response
    openai_constructor = MagicMock(return_value=client)
    fake_openai = SimpleNamespace(OpenAI=openai_constructor)

    with (
        patch.object(
            providers,
            "get_rag_config",
            return_value=_config(
                embedding_provider="OpenAI Compatible",
                embedding_endpoint="https://embeddings.example/v1",
            ),
        ),
        patch.dict(sys.modules, {"openai": fake_openai}),
        patch.object(
            providers,
            "_validated_endpoint",
            return_value="https://embeddings.example/v1",
        ),
    ):
        providers.embed_texts(["document"])

    assert (
        openai_constructor.call_args.kwargs["base_url"]
        == "https://embeddings.example/v1"
    )


def test_lm_studio_chat_allows_local_endpoint_without_api_key():
    event = lambda content: SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=content))]
    )
    client = MagicMock()
    client.chat.completions.create.return_value = [event("ok")]
    openai_constructor = MagicMock(return_value=client)
    fake_openai = SimpleNamespace(OpenAI=openai_constructor)

    with (
        patch.object(
            providers,
            "get_rag_config",
            return_value=_config(
                chat_provider="LM Studio",
                chat_endpoint="http://localhost:1234/v1",
                chat_api_key=None,
            ),
        ),
        patch.dict(sys.modules, {"openai": fake_openai}),
        patch.object(
            providers,
            "_validated_endpoint",
            return_value="http://localhost:1234/v1",
        ) as validate,
    ):
        list(providers.stream_chat([{"role": "user", "content": "Hi"}]))

    validate.assert_called_once_with(
        "http://localhost:1234/v1",
        allow_internal=True,
    )
    assert openai_constructor.call_args.kwargs["api_key"] == "not-required"


def test_lm_studio_embeddings_allow_local_endpoint():
    response = SimpleNamespace(
        data=[SimpleNamespace(embedding=[1.0, 0.0])]
    )
    client = MagicMock()
    client.embeddings.create.return_value = response
    fake_openai = SimpleNamespace(OpenAI=MagicMock(return_value=client))

    with (
        patch.object(
            providers,
            "get_rag_config",
            return_value=_config(
                embedding_provider="LM Studio",
                embedding_endpoint="http://localhost:1234/v1",
                embedding_api_key=None,
            ),
        ),
        patch.dict(sys.modules, {"openai": fake_openai}),
        patch.object(
            providers,
            "_validated_endpoint",
            return_value="http://localhost:1234/v1",
        ) as validate,
    ):
        vectors = providers.embed_texts(["document"])

    validate.assert_called_once_with(
        "http://localhost:1234/v1",
        allow_internal=True,
    )
    np.testing.assert_allclose(vectors, [[1.0, 0.0]])


def test_local_reranker_scores_pairs():
    model = MagicMock()
    model.predict.return_value = [0.7, 0.2]

    with (
        patch.object(providers, "get_rag_config", return_value=_config()),
        patch.object(providers, "_local_reranker", return_value=model),
    ):
        scores = providers.rerank_texts("question", ["best", "other"])

    assert scores == [0.7, 0.2]
    model.predict.assert_called_once_with(
        [("question", "best"), ("question", "other")]
    )


def test_openai_compatible_endpoint_rejects_private_addresses():
    with patch.object(
        providers.socket,
        "getaddrinfo",
        return_value=[
            (
                providers.socket.AF_INET,
                providers.socket.SOCK_STREAM,
                6,
                "",
                ("127.0.0.1", 8000),
            )
        ],
    ):
        try:
            providers._validated_endpoint("http://internal.example:8000")
        except ValueError as exc:
            assert "private or local" in str(exc)
        else:
            raise AssertionError("Private endpoint was accepted")


def test_internal_endpoint_can_be_explicitly_allowed():
    assert (
        providers._validated_endpoint(
            "http://localhost:11434",
            allow_internal=True,
        )
        == "http://localhost:11434"
    )
