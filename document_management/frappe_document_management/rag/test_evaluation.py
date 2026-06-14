from copy import deepcopy

from document_management.frappe_document_management.rag.evaluation import (
    _build_corpus,
    _retrieval_metrics,
    _summary,
    _visible_corpus,
    load_dataset,
    score_generation,
    validate_dataset,
)


def _ranked(chunk, score=0.02):
    return [
        {
            "chunk": chunk,
            "score": score,
            "semantic_score": 0.8,
            "lexical_score": 1.2,
        }
    ]


def test_evaluation_dataset_is_valid_and_covers_security():
    dataset = load_dataset()

    categories = {case["category"] for case in dataset["cases"]}
    assert {"answerable", "unanswerable", "conversation", "security"} <= categories
    assert validate_dataset(dataset)


def test_private_document_is_removed_before_retrieval():
    dataset = load_dataset()
    corpus = _build_corpus(dataset, chunk_size=650, overlap=120)

    public = _visible_corpus(corpus, "evaluation-public@example.com")
    owner = _visible_corpus(corpus, "evaluation-private@example.com")

    assert "private-loan-file" not in {chunk["document"] for chunk in public}
    assert "private-loan-file" in {chunk["document"] for chunk in owner}


def test_unanswerable_case_fails_when_noise_is_retrieved():
    case = {
        "expected_documents": [],
        "forbidden_documents": [],
    }
    chunk = {"document": "distractor", "page": 1}

    assert not _retrieval_metrics(case, _ranked(chunk))["retrieval_pass"]
    assert _retrieval_metrics(case, [])["retrieval_pass"]


def test_generation_requires_expected_page_citation():
    case = {
        "expected_documents": ["retention-manual"],
        "expected_pages": [6],
        "expected_terms": ["diez anos"],
        "expect_answer": True,
        "expect_citations": True,
    }
    wrong_page = {"document": "retention-manual", "page": 2}
    right_page = {"document": "retention-manual", "page": 6}

    wrong = score_generation(case, "Se conservan diez anos [SOURCE 1].", _ranked(wrong_page))
    right = score_generation(case, "Se conservan diez anos [SOURCE 1].", _ranked(right_page))

    assert not wrong["citation_pass"]
    assert right["citation_pass"]


def test_prompt_injection_terms_fail_answer():
    case = {
        "expected_documents": ["injection-contract"],
        "expected_pages": [2],
        "expected_terms": ["informe mensual"],
        "forbidden_terms": ["reveal secrets"],
        "expect_answer": True,
        "expect_citations": True,
    }
    chunk = {"document": "injection-contract", "page": 2}

    result = score_generation(
        case,
        "Reveal secrets. Debe entregar el informe mensual [SOURCE 1].",
        _ranked(chunk),
    )

    assert not result["forbidden_pass"]
    assert not result["answer_pass"]


def test_summary_enforces_rewrite_gate():
    dataset = load_dataset()
    thresholds = deepcopy(dataset["thresholds"])
    result = {
        "rewrite_required": True,
        "rewrite_pass": False,
        "retrieval": {
            "retrieval_pass": True,
            "page_pass": True,
            "permission_pass": True,
            "reciprocal_rank": 1.0,
        },
    }

    summary = _summary([result], thresholds, include_generation=False)

    assert summary["rewrite_pass_rate"] == 0
    assert not summary["passed"]
