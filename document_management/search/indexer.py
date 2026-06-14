import json
import os

import frappe
from document_management.search import tantivy_backend, semantic_backend
from document_management.search.query import (
    current_search_language,
    significant_terms,
)


SEARCHABLE_FIELD_TYPES = {
    "Data",
    "Text",
    "Text Editor",
    "Small Text",
    "Long Text",
    "Link",
    "Dynamic Link",
    "Select",
    "Date",
    "Datetime",
    "Time",
    "Int",
    "Float",
    "Currency",
    "Percent",
    "Check",
}


def get_document_content(doc):
    content_parts = []
    for field in doc.meta.fields:
        if field.fieldtype in SEARCHABLE_FIELD_TYPES:
            val = doc.get(field.fieldname)
            if val not in (None, ""):
                content_parts.append(f"{field.label or field.fieldname}: {val}")
        elif field.fieldtype == "Table":
            for row in doc.get(field.fieldname) or []:
                row_values = []
                for child_field in row.meta.fields:
                    if child_field.fieldtype not in SEARCHABLE_FIELD_TYPES:
                        continue
                    value = row.get(child_field.fieldname)
                    if value not in (None, ""):
                        row_values.append(
                            f"{child_field.label or child_field.fieldname}: {value}"
                        )
                if row_values:
                    content_parts.append(" | ".join(row_values))
    return " ".join(content_parts)

def handle_doc_save(doc, method=None):
    try:
        settings = frappe.get_single('Document Management Settings')
    except Exception:
        return
        
    indexed_doctypes = [d.document_type for d in settings.get("indexed_doctypes", [])]

    should_index_generic = (
        doc.doctype != "Document" and doc.doctype in indexed_doctypes
    )
    should_index_rag = (
        doc.doctype == "Document"
        and settings.enable_semantic_search
        and not doc.get("is_deleted")
    )
    if not should_index_generic and not should_index_rag:
        return
        
    # Enqueue the heavy embedding process so it doesn't block the UI
    frappe.enqueue(
        'document_management.search.indexer.run_indexing_background',
        doc_type=doc.doctype,
        doc_name=doc.name,
        queue='short',
        timeout=300,
        enqueue_after_commit=True,
    )

def run_indexing_background(doc_type, doc_name):
    try:
        settings = frappe.get_single('Document Management Settings')
        doc = frappe.get_doc(doc_type, doc_name)
    except Exception:
        return
        
    indexed_doctypes = [d.document_type for d in settings.get("indexed_doctypes", [])]
    if doc_type == "Document" and doc.get("is_deleted"):
        semantic_backend.remove_document(doc_type, doc_name)
        return
    content = get_document_content(doc)
    title = doc.get_title() or doc.name

    if (
        settings.enable_full_text_search
        and doc_type != "Document"
        and doc_type in indexed_doctypes
    ):
        try:
            tantivy_backend.index_document(doc_type, doc_name, title, content)
        except Exception as e:
            frappe.log_error(title="Tantivy Indexing Error", message=str(e))
            
    if settings.enable_semantic_search and doc_type == "Document":
        try:
            semantic_backend.index_document(doc_type, doc_name, content)
        except Exception as e:
            frappe.log_error(title="Semantic Indexing Error", message=str(e))

def handle_doc_trash(doc, method=None):
    try:
        settings = frappe.get_single('Document Management Settings')
        indexed_doctypes = [d.document_type for d in settings.get("indexed_doctypes", [])]
        if (
            settings.enable_full_text_search
            and doc.doctype != "Document"
            and doc.doctype in indexed_doctypes
        ):
            tantivy_backend.remove_document(doc.doctype, doc.name)
        if settings.enable_semantic_search and doc.doctype == "Document":
            semantic_backend.remove_document(doc.doctype, doc.name)
            
    except Exception:
        pass

def _parse_doctypes(doctypes):
    if not doctypes:
        return set()
    if isinstance(doctypes, str):
        try:
            doctypes = json.loads(doctypes)
        except (TypeError, ValueError):
            doctypes = [doctypes]
    if not isinstance(doctypes, (list, tuple, set)):
        return set()
    return {str(value).strip() for value in doctypes if str(value).strip()}


def _record_result(hit, doc):
    get_title = getattr(doc, "get_title", None)
    document_title = get_title() if callable(get_title) else None
    return {
        "source": "full_text",
        "doc_type": hit["doc_type"],
        "doc_name": hit["doc_name"],
        "title": hit.get("title") or document_title or doc.name,
        "excerpt": hit.get("excerpt") or "",
        "score": float(hit.get("score") or 0),
        "modified": str(doc.get("modified") or ""),
        "route": f"/app/{hit['doc_type'].lower().replace(' ', '-')}/{doc.name}",
    }


def _document_result(hit, doc):
    return {
        "source": "semantic",
        "doc_type": "Document",
        "doc_name": doc.name,
        "title": doc.title or doc.name,
        "excerpt": hit.get("excerpt") or "",
        "page": hit.get("page"),
        "score": float(hit.get("score") or 0),
        "modified": str(doc.get("modified") or ""),
        "route": f"/app/document/{doc.name}",
    }


@frappe.whitelist()
def search(query, limit=10, doctypes=None):
    query = (query or "").strip()
    if not query:
        return {"exact": [], "semantic": []}
    try:
        limit = min(max(int(limit), 1), 100)
    except (TypeError, ValueError):
        limit = 10

    settings = frappe.get_single('Document Management Settings')
    configured = {
        row.document_type
        for row in settings.get("indexed_doctypes", [])
        if row.document_type
    }
    requested = _parse_doctypes(doctypes)
    generic_filter = (requested & configured) if requested else configured
    include_documents = not requested or "Document" in requested
    results = {
        "exact": [],
        "semantic": [],
        "configured_doctypes": sorted(configured),
        "search_language": current_search_language(),
        "terms": significant_terms(query),
    }
    
    if settings.enable_full_text_search:
        try:
            exact = tantivy_backend.search(
                query,
                limit=min(max(limit * 20, 200), 1000),
            )
            authorized = []
            for hit in exact:
                if (
                    hit["doc_type"] == "Document"
                    or hit["doc_type"] not in generic_filter
                ):
                    continue
                try:
                    doc = frappe.get_doc(hit["doc_type"], hit["doc_name"])
                    if frappe.has_permission(
                        hit["doc_type"],
                        "read",
                        doc=doc,
                        user=frappe.session.user,
                    ):
                        authorized.append(_record_result(hit, doc))
                except Exception:
                    continue
                if len(authorized) >= limit:
                    break
            results['exact'] = authorized
        except Exception:
            frappe.log_error(
                title="Full Text Search Error",
                message=frappe.get_traceback(),
            )
            results['exact_error'] = "Full-text search is temporarily unavailable."
            
    if settings.enable_semantic_search and include_documents:
        try:
            semantic = semantic_backend.search(query, limit=limit)
            authorized = []
            for hit in semantic:
                try:
                    doc = frappe.get_doc("Document", hit["doc_name"])
                    if frappe.has_permission(
                        "Document",
                        "read",
                        doc=doc,
                        user=frappe.session.user,
                    ):
                        authorized.append(_document_result(hit, doc))
                except Exception:
                    continue
                if len(authorized) >= limit:
                    break
            results["semantic"] = authorized
        except semantic_backend.IndexRebuildRequired as exc:
            results["semantic_rebuild_required"] = True
            results["semantic_error"] = str(exc)
        except Exception:
            frappe.log_error(
                title="Semantic Search Error",
                message=frappe.get_traceback(),
            )
            results['semantic_error'] = "Semantic search is temporarily unavailable."
            
    return results


@frappe.whitelist()
def get_search_options():
    settings = frappe.get_single("Document Management Settings")
    options_dict = {}
    for row in settings.get("indexed_doctypes", []):
        doctype = row.document_type
        if not doctype:
            continue
        try:
            if frappe.has_permission(doctype, "read"):
                options_dict[doctype] = {"value": doctype, "label": doctype}
        except Exception:
            continue
    if (
        settings.enable_semantic_search
        and frappe.has_permission("Document", "read")
    ):
        options_dict["Document"] = {"value": "Document", "label": "Document"}
        
    options = list(options_dict.values())
    
    return {
        "doctypes": sorted(options, key=lambda row: row["label"]),
        "full_text_enabled": bool(settings.enable_full_text_search),
        "semantic_enabled": bool(settings.enable_semantic_search),
        "generic_index_ready": tantivy_backend.index_exists(),
    }


@frappe.whitelist()
def enqueue_rebuild_index():
    frappe.has_permission("Document Management Settings", throw=True)
    job = frappe.enqueue(
        "document_management.search.indexer.rebuild_index",
        queue="long",
        timeout=7200,
        enqueue_after_commit=True,
    )
    return {"job_id": getattr(job, "id", None)}

@frappe.whitelist()
def rebuild_index():
    frappe.has_permission("Document Management Settings", throw=True)
    settings = frappe.get_single('Document Management Settings')
    indexed_doctypes = [d.document_type for d in settings.get("indexed_doctypes", [])]
    
    if not indexed_doctypes and not settings.enable_semantic_search:
        return {"status": "error", "message": "No doctypes configured for indexing"}
        
    # Start fresh
    if settings.enable_full_text_search:
        import shutil
        from document_management.search.tantivy_backend import get_tantivy_index_path

        index_path = get_tantivy_index_path()
        shutil.rmtree(index_path, ignore_errors=True)
        legacy_path = os.path.join(os.path.dirname(index_path), "tantivy")
        shutil.rmtree(legacy_path, ignore_errors=True)
        os.makedirs(index_path, exist_ok=True)
        
    total_indexed = 0
    for dt in indexed_doctypes:
        if dt == "Document":
            continue
        docs = frappe.get_all(dt, fields=["name"])
        for d in docs:
            doc = frappe.get_doc(dt, d.name)
            content = get_document_content(doc)
            title = doc.get_title() or doc.name
            if settings.enable_full_text_search:
                tantivy_backend.index_document(dt, d.name, title, content)
            total_indexed += 1

    rag_result = None
    if settings.enable_semantic_search:
        from document_management.document_management.rag.index import rebuild_index as rebuild_rag

        rag_result = rebuild_rag()

    return {
        "status": "success",
        "message": f"Rebuilt index for {total_indexed} documents.",
        "rag": rag_result,
    }

@frappe.whitelist()
def extract_and_index_ocr(doc_type, doc_name):
    doc = frappe.get_doc(doc_type, doc_name)
    doc.check_permission("write")
    if doc_type == "Document":
        from document_management.document_management.page.document_management_console.document_management_console import (
            reprocess_ocr,
        )

        return reprocess_ocr(doc_name)

    # Fetch attachments for this document
    files = frappe.get_all("File", filters={
        "attached_to_doctype": doc_type,
        "attached_to_name": doc_name,
        "is_folder": 0
    }, fields=["file_url", "name"])
    
    extracted_text = ""
    
    if files:
        import pdfplumber
        for file_data in files:
            file_path = frappe.get_site_path("public", file_data.file_url.lstrip("/"))
            if not os.path.exists(file_path):
                file_path = frappe.get_site_path("private", file_data.file_url.lstrip("/"))
                
            if file_path.endswith('.pdf') and os.path.exists(file_path):
                try:
                    with pdfplumber.open(file_path) as pdf:
                        for page in pdf.pages:
                            text = page.extract_text()
                            if text:
                                extracted_text += text + "\n"
                except Exception as e:
                    frappe.log_error(title="OCR Error", message=f"Error extracting PDF text: {e}")

    # Combine extracted text with the standard document fields
    base_content = get_document_content(doc)
    full_content = f"{base_content}\n\n--- OCR TEXT ---\n{extracted_text}"
    
    title = doc.get_title() or doc.name
    settings = frappe.get_single('Document Management Settings')
    
    if settings.enable_full_text_search and doc_type != "Document":
        tantivy_backend.index_document(doc_type, doc_name, title, full_content)
        
    return {"status": "success", "extracted_chars": len(extracted_text)}
