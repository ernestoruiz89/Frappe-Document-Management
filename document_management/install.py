import frappe


SETTINGS_DOCTYPE = "Document Management Settings"


def ensure_document_management_settings():
    """Reload settings metadata and seed missing Single defaults."""
    frappe.reload_doc(
        "frappe_document_management",
        "doctype",
        "indexed_doctype",
    )
    frappe.reload_doc(
        "frappe_document_management",
        "doctype",
        "document_management_settings",
    )

    frappe.db.delete(
        "Singles",
        {
            "doctype": SETTINGS_DOCTYPE,
            "field": "name",
        },
    )

    defaults = {
        "enable_full_text_search": 1,
        "enable_semantic_search": 1,
        "ocr_mode": "Auto",
        "ocr_archive_generation": "Auto",
        "ocr_language": "spa+eng",
        "ocr_min_text_chars": 80,
        "ocr_processing_timeout_minutes": 60,
        "enable_openai_for_ocr": 1,
        "chat_provider": "OpenAI",
        "rag_model": "gpt-5-mini",
        "rag_context_size": 8192,
        "embedding_provider": "Local (sentence-transformers)",
        "embedding_model": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        "rag_chunk_size": 650,
        "rag_chunk_overlap": 120,
        "rag_top_k": 8,
        "rag_min_semantic_score": 0.25,
        "enable_rag_reranker": 1,
        "rag_reranker_model": "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
        "rag_timeout": 120,
        "chat_rate_limit_per_minute": 10,
        "allow_internal_ai_endpoints": 0,
        "index_path": "search_index",
        "trash_retention_days": 30,
        "export_retention_hours": 24,
        "enable_file_encryption": 0,
        "enable_folder_ingestion": 0,
    }

    meta = frappe.get_meta(SETTINGS_DOCTYPE, cached=False)
    for fieldname, value in defaults.items():
        if meta.has_field(fieldname) and frappe.db.get_single_value(
            SETTINGS_DOCTYPE,
            fieldname,
        ) in (None, ""):
            frappe.db.set_single_value(SETTINGS_DOCTYPE, fieldname, value)

    frappe.clear_cache(doctype=SETTINGS_DOCTYPE)
