import json
import ipaddress
import socket
from functools import lru_cache
from urllib.parse import urlparse

import numpy as np
import requests

from document_management.document_management.rag.config import get_rag_config


@lru_cache(maxsize=4)
def _local_embedding_model(model_name):
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model_name)


@lru_cache(maxsize=2)
def _local_reranker(model_name):
    from sentence_transformers import CrossEncoder

    return CrossEncoder(model_name)


def _normalize(vectors):
    array = np.asarray(vectors, dtype="float32")
    if array.ndim == 1:
        array = array.reshape(1, -1)
    norms = np.linalg.norm(array, axis=1, keepdims=True)
    norms[norms == 0] = 1
    return array / norms


def _validated_endpoint(endpoint, allow_internal=False):
    if not endpoint:
        return None
    parsed = urlparse(endpoint)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("AI endpoint must be an absolute HTTP(S) URL.")
    if not allow_internal:
        try:
            addresses = {
                result[4][0]
                for result in socket.getaddrinfo(
                    parsed.hostname,
                    parsed.port or (443 if parsed.scheme == "https" else 80),
                    type=socket.SOCK_STREAM,
                )
            }
        except socket.gaierror as exc:
            raise ValueError("AI endpoint hostname could not be resolved.") from exc
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_multicast
                or ip.is_unspecified
            ):
                raise ValueError(
                    "AI endpoint resolves to a private or local address."
                )
    return endpoint


def embed_texts(texts):
    config = get_rag_config()
    if not texts:
        return np.empty((0, 0), dtype="float32")

    if config.embedding_provider in {"OpenAI", "OpenAI Compatible", "LM Studio"}:
        from openai import OpenAI

        if config.embedding_provider == "OpenAI" and not config.embedding_api_key:
            raise ValueError("OpenAI API key is required for embeddings.")
        client = OpenAI(
            api_key=config.embedding_api_key or "not-required",
            base_url=_validated_endpoint(
                config.embedding_endpoint,
                allow_internal=(
                    config.allow_internal_endpoints
                    or config.embedding_provider == "LM Studio"
                ),
            ),
            timeout=config.timeout,
        )
        vectors = []
        for start in range(0, len(texts), 128):
            response = client.embeddings.create(
                model=config.embedding_model,
                input=texts[start : start + 128],
            )
            vectors.extend(row.embedding for row in response.data)
    else:
        model = _local_embedding_model(config.embedding_model)
        vectors = model.encode(texts, show_progress_bar=False)

    return _normalize(vectors)


def embedding_signature():
    config = get_rag_config()
    return {
        "provider": config.embedding_provider,
        "model": config.embedding_model,
        "endpoint": config.embedding_endpoint or "",
    }


def rerank_texts(query, texts):
    config = get_rag_config()
    if not config.enable_reranker or not texts:
        return None
    model = _local_reranker(config.reranker_model)
    scores = model.predict([(query, text) for text in texts])
    return [float(score) for score in scores]


def complete_chat(messages):
    return "".join(stream_chat(messages))


def stream_chat(messages):
    config = get_rag_config()
    if config.chat_provider == "Ollama":
        endpoint = _validated_endpoint(
            config.chat_endpoint or "http://localhost:11434",
            allow_internal=True,
        ).rstrip("/")
        response = requests.post(
            f"{endpoint}/api/chat",
            json={
                "model": config.chat_model,
                "messages": messages,
                "stream": True,
                "options": {"num_ctx": config.context_size},
            },
            stream=True,
            timeout=config.timeout,
        )
        response.raise_for_status()
        for line in response.iter_lines():
            if not line:
                continue
            payload = json.loads(line)
            content = (payload.get("message") or {}).get("content")
            if content:
                yield content
        return

    from openai import OpenAI

    if config.chat_provider == "OpenAI" and not config.chat_api_key:
        raise ValueError("OpenAI API key is required for chat.")
    client = OpenAI(
        api_key=config.chat_api_key or "not-required",
        base_url=_validated_endpoint(
            config.chat_endpoint,
            allow_internal=(
                config.allow_internal_endpoints
                or config.chat_provider == "LM Studio"
            ),
        ),
        timeout=config.timeout,
    )
    response = client.chat.completions.create(
        model=config.chat_model,
        messages=messages,
        stream=True,
    )
    for event in response:
        content = event.choices[0].delta.content
        if content:
            yield content
