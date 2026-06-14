import frappe

from document_management.document_management.rag.index import (
    IndexRebuildRequired,
    index_document as index_rag_document,
    remove_document as remove_rag_document,
    search_chunks,
)
from document_management.search.query import make_excerpt


def generate_embedding(text):
    from document_management.document_management.rag.providers import embed_texts

    return embed_texts([text])[0].tolist()


def index_document(doc_type, doc_name, content=None):
    if doc_type != "Document":
        return 0
    return index_rag_document(doc_name)


def remove_document(doc_type, doc_name=None):
    if doc_name is None:
        doc_name = doc_type
        doc_type = "Document"
    if doc_type == "Document":
        remove_rag_document(doc_name)


def search(query_str, limit=5):
    if not query_str:
        return []
    allowed = frappe.get_list("Document", pluck="name", limit_page_length=100000)
    try:
        chunks = search_chunks(query_str, allowed, limit=int(limit))
    except IndexRebuildRequired:
        raise

    best_by_document = {}
    for chunk in chunks:
        current = best_by_document.get(chunk.document)
        if not current or chunk.score > current["score"]:
            best_by_document[chunk.document] = {
                "doc_type": "Document",
                "doc_name": chunk.document,
                "distance": 1.0 - float(chunk.score),
                "score": float(chunk.score),
                "excerpt": make_excerpt(chunk.content, query_str, maximum=280),
                "page": chunk.page_number,
            }
    return list(best_by_document.values())[: int(limit)]
