import json
import math
import re
import time
from collections import Counter
from pathlib import Path

import frappe
import numpy as np
from unidecode import unidecode

from document_management.document_management.rag.chunking import chunk_pages
from document_management.document_management.rag.config import get_rag_config
from document_management.document_management.rag.providers import (
    complete_chat,
    embed_texts,
    rerank_texts,
)
from document_management.document_management.rag.service import (
    SYSTEM_PROMPT,
    _cited_reference_indexes,
    _history_messages,
    _standalone_query,
)


REFUSAL_MARKERS = (
    "no hay evidencia",
    "no existe evidencia",
    "no encontre evidencia",
    "no encontré evidencia",
    "no contienen suficiente",
    "no contiene suficiente",
    "no dispongo de evidencia",
    "not enough information",
    "insufficient evidence",
)


def _dataset_path():
    return Path(__file__).with_name("evaluation_cases.json")


def _as_bool(value):
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off"}
    return bool(value)


def load_dataset(path=None):
    dataset_path = Path(path) if path else _dataset_path()
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    validate_dataset(dataset)
    return dataset


def validate_dataset(dataset):
    if not isinstance(dataset, dict) or dataset.get("version") != 1:
        raise ValueError("Unsupported RAG evaluation dataset.")
    documents = dataset.get("documents")
    cases = dataset.get("cases")
    thresholds = dataset.get("thresholds")
    if not isinstance(documents, list) or not isinstance(cases, list):
        raise ValueError("Evaluation dataset requires documents and cases.")
    if not isinstance(thresholds, dict):
        raise ValueError("Evaluation dataset requires thresholds.")

    required_thresholds = {
        "retrieval_hit_rate",
        "page_hit_rate",
        "permission_pass_rate",
        "rewrite_pass_rate",
        "answer_pass_rate",
        "citation_pass_rate",
    }
    if required_thresholds - set(thresholds):
        raise ValueError("Evaluation dataset is missing required thresholds.")

    document_ids = [row.get("id") for row in documents]
    case_ids = [row.get("id") for row in cases]
    if any(not value for value in document_ids + case_ids):
        raise ValueError("Every evaluation document and case requires an id.")
    if len(document_ids) != len(set(document_ids)):
        raise ValueError("Evaluation document ids must be unique.")
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("Evaluation case ids must be unique.")

    known_documents = set(document_ids)
    for document in documents:
        if not isinstance(document.get("pages"), list) or not document["pages"]:
            raise ValueError(f"Document {document['id']} requires pages.")
    for case in cases:
        if not case.get("question") or not case.get("user"):
            raise ValueError(f"Case {case['id']} requires a question and user.")
        referenced = set(case.get("expected_documents") or [])
        referenced.update(case.get("forbidden_documents") or [])
        unknown = referenced - known_documents
        if unknown:
            raise ValueError(
                f"Case {case['id']} references unknown documents: {sorted(unknown)}"
            )
    return True


def _tokens(text):
    normalized = unidecode((text or "").lower())
    return re.findall(r"[a-z0-9]+", normalized)


def _build_corpus(dataset, chunk_size, overlap):
    corpus = []
    for document in dataset["documents"]:
        for chunk in chunk_pages(document["pages"], chunk_size, overlap):
            corpus.append(
                {
                    "id": f"{document['id']}:{chunk.page_number}:{chunk.chunk_index}",
                    "document": document["id"],
                    "title": document["title"],
                    "page": chunk.page_number,
                    "content": chunk.content,
                    "allowed_users": set(document.get("allowed_users") or []),
                }
            )
    return corpus


def _embedding_text(chunk):
    return f"Title: {chunk['title']}\nContent:\n{chunk['content']}"


def _lexical_ranking(query, corpus):
    query_tokens = _tokens(query)
    if not query_tokens:
        return []
    document_frequency = Counter()
    tokenized = []
    for chunk in corpus:
        tokens = _tokens(_embedding_text(chunk))
        tokenized.append(tokens)
        document_frequency.update(set(tokens))

    ranking = []
    corpus_size = max(len(corpus), 1)
    for chunk, tokens in zip(corpus, tokenized):
        counts = Counter(tokens)
        score = 0.0
        for token in query_tokens:
            if token not in counts:
                continue
            inverse_frequency = math.log(
                1
                + (corpus_size - document_frequency[token] + 0.5)
                / (document_frequency[token] + 0.5)
            )
            score += inverse_frequency * counts[token] / (counts[token] + 1.2)
        if score > 0:
            ranking.append((chunk, score))
    ranking.sort(key=lambda item: item[1], reverse=True)
    return ranking


def _semantic_ranking(query, corpus, corpus_vectors, min_score):
    if not corpus:
        return []
    query_vector = embed_texts([query])[0]
    indexes = [chunk["_vector_index"] for chunk in corpus]
    scores = np.dot(corpus_vectors[indexes], query_vector)
    ranking = [
        (chunk, float(score))
        for chunk, score in zip(corpus, scores)
        if float(score) >= min_score
    ]
    ranking.sort(key=lambda item: item[1], reverse=True)
    return ranking


def _hybrid_ranking(
    query,
    corpus,
    corpus_vectors,
    limit=8,
    max_per_document=3,
    min_semantic_score=0.25,
    enable_reranker=True,
):
    try:
        semantic = _semantic_ranking(
            query,
            corpus,
            corpus_vectors,
            min_semantic_score,
        )
    except Exception as exc:
        frappe.logger("document_rag").warning(
            "Evaluation semantic retrieval unavailable: %s",
            type(exc).__name__,
        )
        semantic = []
    lexical = _lexical_ranking(query, corpus)
    fused = {}
    for source, ranking in (("semantic", semantic), ("lexical", lexical)):
        for rank, (chunk, raw_score) in enumerate(ranking, start=1):
            entry = fused.setdefault(
                chunk["id"],
                {
                    "chunk": chunk,
                    "score": 0.0,
                    "semantic_score": None,
                    "lexical_score": None,
                },
            )
            entry["score"] += 1.0 / (60 + rank)
            entry[f"{source}_score"] = float(raw_score)

    ranked = sorted(
        fused.values(),
        key=lambda row: row["score"],
        reverse=True,
    )[: max(limit * 3, 20)]
    if enable_reranker and ranked:
        try:
            scores = rerank_texts(
                query,
                [row["chunk"]["content"] for row in ranked],
            )
            if scores is not None:
                for row, score in zip(ranked, scores):
                    row["reranker_score"] = float(score)
                ranked.sort(key=lambda row: row["reranker_score"], reverse=True)
        except Exception as exc:
            frappe.logger("document_rag").warning(
                "Evaluation reranker unavailable: %s",
                type(exc).__name__,
            )

    selected = []
    per_document = Counter()
    for row in ranked:
        document = row["chunk"]["document"]
        if per_document[document] >= max_per_document:
            continue
        selected.append(row)
        per_document[document] += 1
        if len(selected) >= limit:
            break
    return selected


def _visible_corpus(corpus, user):
    return [
        chunk
        for chunk in corpus
        if not chunk["allowed_users"] or user in chunk["allowed_users"]
    ]


def _retrieval_metrics(case, ranked):
    expected = set(case.get("expected_documents") or [])
    forbidden = set(case.get("forbidden_documents") or [])
    retrieved_documents = [row["chunk"]["document"] for row in ranked]
    retrieved_pages = [
        row["chunk"]["page"]
        for row in ranked
        if row["chunk"]["document"] in expected
    ]
    first_relevant_rank = next(
        (
            rank
            for rank, document in enumerate(retrieved_documents, start=1)
            if document in expected
        ),
        None,
    )
    if expected:
        retrieval_pass = bool(expected.intersection(retrieved_documents))
        page_pass = (
            not case.get("expected_pages")
            or bool(set(case["expected_pages"]).intersection(retrieved_pages))
        )
    else:
        retrieval_pass = not ranked
        page_pass = not ranked
    permission_pass = not forbidden.intersection(retrieved_documents)
    return {
        "retrieval_pass": retrieval_pass,
        "page_pass": page_pass,
        "permission_pass": permission_pass,
        "abstained": not ranked,
        "reciprocal_rank": (
            1.0 / first_relevant_rank if first_relevant_rank else 0.0
        ),
        "retrieved": [
            {
                "document": row["chunk"]["document"],
                "page": row["chunk"]["page"],
                "rrf_score": round(float(row["score"]), 6),
                "semantic_score": (
                    round(row["semantic_score"], 6)
                    if row["semantic_score"] is not None
                    else None
                ),
                "lexical_score": (
                    round(row["lexical_score"], 6)
                    if row["lexical_score"] is not None
                    else None
                ),
                "reranker_score": (
                    round(row["reranker_score"], 6)
                    if row.get("reranker_score") is not None
                    else None
                ),
            }
            for row in ranked
        ],
    }


def _generation_prompt(question, retrieval_query, ranked):
    context = []
    for index, row in enumerate(ranked, start=1):
        chunk = row["chunk"]
        context.append(
            f"[SOURCE {index}]\n"
            f"Document: {chunk['title']} ({chunk['document']})\n"
            f"Page: {chunk['page']}\n"
            f"Content:\n{chunk['content']}"
        )
    return (
        "Evidence:\n\n"
        + "\n\n".join(context)
        + f"\n\nOriginal question:\n{question}"
        + f"\n\nSelf-contained retrieval question:\n{retrieval_query}"
        + "\n\nAnswer in the user's language. Every factual claim must "
        "include its [SOURCE n] marker."
    )


def _contains(text, term):
    return unidecode(term.lower()) in unidecode((text or "").lower())


def score_generation(case, answer, ranked):
    cited_indexes = _cited_reference_indexes(answer, len(ranked))
    cited_chunks = [ranked[index - 1]["chunk"] for index in cited_indexes]
    cited_documents = {chunk["document"] for chunk in cited_chunks}
    cited_pages = {
        chunk["page"]
        for chunk in cited_chunks
        if chunk["document"] in set(case.get("expected_documents") or [])
    }
    expected_documents = set(case.get("expected_documents") or [])
    expected_pages = set(case.get("expected_pages") or [])
    expected_terms = case.get("expected_terms") or []
    forbidden_terms = case.get("forbidden_terms") or []
    expects_answer = bool(case.get("expect_answer"))
    is_refusal = any(_contains(answer, marker) for marker in REFUSAL_MARKERS)

    if expects_answer:
        terms_pass = all(_contains(answer, term) for term in expected_terms)
        answer_pass = bool(answer.strip()) and not is_refusal and terms_pass
    else:
        terms_pass = True
        answer_pass = is_refusal

    forbidden_pass = not any(_contains(answer, term) for term in forbidden_terms)
    if case.get("expect_citations"):
        citation_pass = (
            bool(cited_indexes)
            and bool(expected_documents.intersection(cited_documents))
            and (not expected_pages or bool(expected_pages.intersection(cited_pages)))
        )
    else:
        citation_pass = not cited_indexes
    return {
        "answer_pass": answer_pass and forbidden_pass,
        "terms_pass": terms_pass,
        "forbidden_pass": forbidden_pass,
        "citation_pass": citation_pass,
        "cited_sources": cited_indexes,
        "answer": answer,
    }


def _rate(results, accessor):
    return sum(bool(accessor(row)) for row in results) / max(len(results), 1)


def _summary(results, thresholds, include_generation):
    retrieval_hit_rate = _rate(
        results, lambda row: row["retrieval"]["retrieval_pass"]
    )
    page_hit_rate = _rate(results, lambda row: row["retrieval"]["page_pass"])
    permission_pass_rate = _rate(
        results, lambda row: row["retrieval"]["permission_pass"]
    )
    rewrite_results = [
        row for row in results if row.get("rewrite_required")
    ]
    rewrite_pass_rate = _rate(
        rewrite_results, lambda row: row["rewrite_pass"]
    )
    summary = {
        "cases": len(results),
        "retrieval_hit_rate": round(retrieval_hit_rate, 4),
        "page_hit_rate": round(page_hit_rate, 4),
        "permission_pass_rate": round(permission_pass_rate, 4),
        "rewrite_pass_rate": round(rewrite_pass_rate, 4),
        "mean_reciprocal_rank": round(
            sum(row["retrieval"]["reciprocal_rank"] for row in results)
            / max(len(results), 1),
            4,
        ),
    }
    checks = [
        retrieval_hit_rate >= thresholds["retrieval_hit_rate"],
        page_hit_rate >= thresholds["page_hit_rate"],
        permission_pass_rate >= thresholds["permission_pass_rate"],
        rewrite_pass_rate >= thresholds["rewrite_pass_rate"],
    ]
    if include_generation:
        answer_pass_rate = _rate(
            results, lambda row: row["generation"]["answer_pass"]
        )
        citation_pass_rate = _rate(
            results, lambda row: row["generation"]["citation_pass"]
        )
        summary.update(
            {
                "answer_pass_rate": round(answer_pass_rate, 4),
                "citation_pass_rate": round(citation_pass_rate, 4),
            }
        )
        checks.extend(
            [
                answer_pass_rate >= thresholds["answer_pass_rate"],
                citation_pass_rate >= thresholds["citation_pass_rate"],
            ]
        )
    summary["passed"] = all(checks)
    return summary


def _require_system_manager(user=None):
    user = user or frappe.session.user
    if user != "Administrator" and "System Manager" not in frappe.get_roles(user):
        frappe.throw("System Manager role is required.", frappe.PermissionError)


def run_evaluation(include_generation=False, dataset_path=None, requested_by=None):
    if requested_by:
        frappe.set_user(requested_by)
    _require_system_manager(requested_by)
    include_generation = _as_bool(include_generation)
    dataset = load_dataset(dataset_path)
    config = get_rag_config()
    try:
        from document_management.document_management.rag.index import index_status

        production_index = index_status()
    except Exception as exc:
        production_index = {
            "available": False,
            "error": type(exc).__name__,
        }
    corpus = _build_corpus(
        dataset,
        config.chunk_size,
        config.chunk_overlap,
    )
    for index, chunk in enumerate(corpus):
        chunk["_vector_index"] = index
    corpus_vectors = embed_texts([_embedding_text(chunk) for chunk in corpus])
    results = []
    started = time.monotonic()

    for case in dataset["cases"]:
        visible = _visible_corpus(corpus, case["user"])
        history = [frappe._dict(row) for row in case.get("history") or []]
        retrieval_query = (
            _standalone_query(case["question"], history)
            if history
            else case["question"]
        )
        ranked = _hybrid_ranking(
            retrieval_query,
            visible,
            corpus_vectors,
            limit=config.top_k,
            min_semantic_score=config.min_semantic_score,
            enable_reranker=config.enable_reranker,
        )
        required_rewrite_terms = case.get("rewrite_must_include") or []
        result = {
            "id": case["id"],
            "category": case["category"],
            "retrieval_query": retrieval_query,
            "rewrite_required": bool(required_rewrite_terms),
            "rewrite_pass": all(
                _contains(retrieval_query, term)
                for term in required_rewrite_terms
            ),
            "retrieval": _retrieval_metrics(case, ranked),
        }
        if include_generation:
            if ranked:
                messages = [{"role": "system", "content": SYSTEM_PROMPT}]
                messages.extend(_history_messages(history))
                messages.append(
                    {
                        "role": "user",
                        "content": _generation_prompt(
                            case["question"],
                            retrieval_query,
                            ranked,
                        ),
                    }
                )
                answer = complete_chat(messages).strip()
            else:
                answer = (
                    "No existe evidencia suficiente en los documentos disponibles."
                )
            result["generation"] = score_generation(case, answer, ranked)
        results.append(result)

    report = {
        "dataset_version": dataset["version"],
        "run_by": requested_by or frappe.session.user,
        "include_generation": include_generation,
        "provider": {
            "chat": config.chat_provider,
            "chat_model": config.chat_model,
            "embeddings": config.embedding_provider,
            "embedding_model": config.embedding_model,
            "reranker_enabled": config.enable_reranker,
            "reranker_model": (
                config.reranker_model if config.enable_reranker else None
            ),
        },
        "production_index": production_index,
        "duration_seconds": round(time.monotonic() - started, 3),
        "thresholds": dataset["thresholds"],
        "summary": _summary(
            results,
            dataset["thresholds"],
            include_generation,
        ),
        "results": results,
    }
    output_dir = Path(frappe.get_site_path("private", "rag-evaluations"))
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "latest.json"
    temporary_path = output_dir / "latest.json.tmp"
    temporary_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary_path.replace(output_path)
    return report


def get_latest_report():
    _require_system_manager()
    path = Path(frappe.get_site_path("private", "rag-evaluations", "latest.json"))
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
