import json
import os
from html import unescape
from html.parser import HTMLParser

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


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        if data.strip():
            self.parts.append(data)


def _plain_text(value, fieldtype=None):
    text = str(value or "")
    if fieldtype == "Text Editor" or ("<" in text and ">" in text):
        parser = _TextExtractor()
        try:
            parser.feed(text)
            parser.close()
            text = " ".join(parser.parts)
        except Exception:
            pass
    return " ".join(unescape(text).split())


def _is_visible_field(field):
    try:
        permlevel = int(getattr(field, "permlevel", 0) or 0)
    except (TypeError, ValueError):
        permlevel = 0
    return permlevel == 0 and not bool(getattr(field, "hidden", False))


def _is_searchable_field(field):
    return (
        field.fieldtype in SEARCHABLE_FIELD_TYPES
        and _is_visible_field(field)
    )


def get_document_content(doc):
    content_parts = []
    for field in doc.meta.fields:
        if _is_searchable_field(field):
            val = doc.get(field.fieldname)
            if val not in (None, ""):
                text = _plain_text(val, field.fieldtype)
                if text:
                    content_parts.append(
                        f"{field.label or field.fieldname}: {text}"
                    )
        elif field.fieldtype == "Table" and _is_visible_field(field):
            for row in doc.get(field.fieldname) or []:
                row_values = []
                for child_field in row.meta.fields:
                    if not _is_searchable_field(child_field):
                        continue
                    value = row.get(child_field.fieldname)
                    if value not in (None, ""):
                        text = _plain_text(value, child_field.fieldtype)
                        if text:
                            row_values.append(
                                f"{child_field.label or child_field.fieldname}: {text}"
                            )
                if row_values:
                    content_parts.append(" | ".join(row_values))
    return " ".join(content_parts)


def get_document_comments(doc_type, doc_names):
    names = list(dict.fromkeys(str(name) for name in doc_names if name))
    if not names:
        return {}

    comments = {name: [] for name in names}
    rows = frappe.get_all(
        "Comment",
        filters={
            "reference_doctype": doc_type,
            "reference_name": ["in", names],
            "comment_type": "Comment",
        },
        fields=["reference_name", "content"],
        order_by="creation asc, name asc",
    )
    for row in rows:
        reference_name = row.get("reference_name")
        content = _plain_text(row.get("content"), "Text Editor")
        if reference_name in comments and content:
            comments[reference_name].append(content)
    return comments


def get_indexable_content(doc, comments=None):
    content_parts = [get_document_content(doc)]
    if comments is None:
        comments = get_document_comments(doc.doctype, [doc.name]).get(
            doc.name,
            [],
        )
    for comment in comments:
        text = _plain_text(comment, "Text Editor")
        if text:
            content_parts.append(f"Comment: {text}")
    return " ".join(part for part in content_parts if part)


def get_document_title(doc):
    title_field = getattr(doc.meta, "title_field", None)
    if not title_field:
        return doc.name
    try:
        field = doc.meta.get_field(title_field)
    except Exception:
        return doc.name
    if not field or not _is_visible_field(field):
        return doc.name
    return _plain_text(doc.get(title_field), field.fieldtype) or doc.name


def _comment_reference(doc):
    if doc.doctype != "Comment":
        return None
    if doc.get("comment_type") != "Comment":
        return None
    doc_type = doc.get("reference_doctype")
    doc_name = doc.get("reference_name")
    if not doc_type or not doc_name:
        return None
    return doc_type, doc_name


def _enqueue_indexing(doc_type, doc_name, queue="short", timeout=300):
    frappe.enqueue(
        "document_management.search.indexer.run_indexing_background",
        doc_type=doc_type,
        doc_name=doc_name,
        queue=queue,
        timeout=timeout,
        enqueue_after_commit=True,
    )


def handle_doc_save(doc, method=None):
    try:
        settings = frappe.get_single('Document Management Settings')
    except Exception:
        return
        
    indexed_doctypes = [d.document_type for d in settings.get("indexed_doctypes", [])]
    comment_reference = _comment_reference(doc)
    if comment_reference:
        doc_type, doc_name = comment_reference
        if settings.enable_full_text_search and doc_type in indexed_doctypes:
            _enqueue_indexing(doc_type, doc_name)
        return

    should_index_generic = (
        settings.enable_full_text_search
        and doc.doctype != "Document"
        and doc.doctype in indexed_doctypes
    )
    should_index_rag = (
        doc.doctype == "Document"
        and settings.enable_semantic_search
        and not doc.get("is_deleted")
    )
    if not should_index_generic and not should_index_rag:
        return
        
    # Enqueue the heavy embedding process so it doesn't block the UI
    _enqueue_indexing(doc.doctype, doc.name)

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
    content = get_indexable_content(doc)
    title = get_document_title(doc)

    if (
        settings.enable_full_text_search
        and doc_type != "Document"
        and doc_type in indexed_doctypes
    ):
        try:
            tantivy_backend.index_document(doc_type, doc_name, title, content)
        except tantivy_backend.IndexBusy:
            frappe.enqueue(
                "document_management.search.indexer.run_indexing_background",
                doc_type=doc_type,
                doc_name=doc_name,
                queue="long",
                timeout=7200,
            )
            return
        except tantivy_backend.IndexRebuildRequired:
            return
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
        comment_reference = _comment_reference(doc)
        if comment_reference:
            doc_type, doc_name = comment_reference
            if settings.enable_full_text_search and doc_type in indexed_doctypes:
                _enqueue_indexing(doc_type, doc_name)
            return
        if (
            settings.enable_full_text_search
            and doc.doctype != "Document"
            and doc.doctype in indexed_doctypes
        ):
            frappe.enqueue(
                "document_management.search.indexer.remove_from_index_background",
                doc_type=doc.doctype,
                doc_name=doc.name,
                queue="short",
                timeout=300,
                enqueue_after_commit=True,
            )
        if settings.enable_semantic_search and doc.doctype == "Document":
            semantic_backend.remove_document(doc.doctype, doc.name)
            
    except Exception:
        pass


def remove_from_index_background(doc_type, doc_name):
    try:
        tantivy_backend.remove_document(doc_type, doc_name)
    except tantivy_backend.IndexBusy:
        frappe.enqueue(
            "document_management.search.indexer.remove_from_index_background",
            doc_type=doc_type,
            doc_name=doc_name,
            queue="long",
            timeout=7200,
        )
    except tantivy_backend.IndexRebuildRequired:
        return


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


def _record_result(hit, metadata):
    return {
        "source": "full_text",
        "doc_type": hit["doc_type"],
        "doc_name": hit["doc_name"],
        "title": hit.get("title") or hit["doc_name"],
        "excerpt": hit.get("excerpt") or "",
        "score": float(hit.get("score") or 0),
        "match_type": hit.get("match_type") or "partial",
        "matched_terms": int(hit.get("matched_terms") or 0),
        "total_terms": int(hit.get("total_terms") or 0),
        "coverage": float(hit.get("coverage") or 0),
        "modified": str(metadata.get("modified") or ""),
        "route": (
            f"/app/{hit['doc_type'].lower().replace(' ', '-')}/"
            f"{hit['doc_name']}"
        ),
    }


def _permitted_metadata(hits):
    by_doctype = {}
    for hit in hits:
        by_doctype.setdefault(hit["doc_type"], []).append(hit["doc_name"])

    permitted = {}
    custom_permission_hooks = frappe.get_hooks("has_permission") or {}
    for doctype, names in by_doctype.items():
        try:
            rows = frappe.get_list(
                doctype,
                filters={"name": ["in", list(dict.fromkeys(names))]},
                fields=["name", "modified"],
                limit_page_length=len(names),
            )
        except Exception:
            continue
        for row in rows:
            name = row.get("name") if hasattr(row, "get") else row.name
            if doctype in custom_permission_hooks:
                try:
                    doc = frappe.get_doc(doctype, name)
                    if not frappe.has_permission(
                        doctype,
                        "read",
                        doc=doc,
                        user=frappe.session.user,
                    ):
                        continue
                except Exception:
                    continue
            permitted[(doctype, name)] = row
    return permitted


@frappe.whitelist()
def search(query, page=1, page_length=25, doctypes=None, limit=None):
    query = (query or "").strip()
    if not query:
        return {
            "exact": [],
            "pagination": {
                "page": 1,
                "page_length": 25,
                "has_previous": False,
                "has_more": False,
            },
        }
    try:
        page = max(int(page), 1)
    except (TypeError, ValueError):
        page = 1
    if limit is not None and page_length in (None, "", 25, "25"):
        page_length = limit
    try:
        page_length = min(max(int(page_length), 1), 100)
    except (TypeError, ValueError):
        page_length = 25

    settings = frappe.get_single('Document Management Settings')
    configured = {
        row.document_type
        for row in settings.get("indexed_doctypes", [])
        if row.document_type
    }
    requested = _parse_doctypes(doctypes)
    generic_filter = (requested & configured) if requested else configured
    results = {
        "exact": [],
        "configured_doctypes": sorted(configured),
        "search_language": current_search_language(),
        "terms": significant_terms(query),
        "pagination": {
            "page": page,
            "page_length": page_length,
            "has_previous": page > 1,
            "has_more": False,
        },
    }
    if not generic_filter:
        return results

    if settings.enable_full_text_search:
        try:
            target_start = (page - 1) * page_length
            authorized_seen = 0
            page_results = []
            for batch in tantivy_backend.iter_search(
                query,
                doctypes=sorted(generic_filter),
                batch_size=max(page_length * 4, 100),
            ):
                filtered = [
                    hit
                    for hit in batch
                    if hit["doc_type"] != "Document"
                    and hit["doc_type"] in generic_filter
                ]
                permitted = _permitted_metadata(filtered)
                for hit in filtered:
                    key = (hit["doc_type"], hit["doc_name"])
                    if key not in permitted:
                        continue
                    if authorized_seen < target_start:
                        authorized_seen += 1
                        continue
                    page_results.append(
                        _record_result(hit, permitted[key])
                    )
                    authorized_seen += 1
                    if len(page_results) > page_length:
                        break
                if len(page_results) > page_length:
                    break
            results["pagination"]["has_more"] = (
                len(page_results) > page_length
            )
            results["exact"] = page_results[:page_length]
            results["pagination"]["from"] = (
                target_start + 1 if results["exact"] else 0
            )
            results["pagination"]["to"] = (
                target_start + len(results["exact"])
            )
        except tantivy_backend.IndexRebuildRequired as exc:
            results["exact_rebuild_required"] = True
            results["exact_error"] = str(exc)
        except Exception:
            frappe.log_error(
                title="Full Text Search Error",
                message=frappe.get_traceback(),
            )
            results['exact_error'] = "Full-text search is temporarily unavailable."

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
    options = list(options_dict.values())

    indexed_count = 0
    if settings.enable_full_text_search and tantivy_backend.index_exists():
        try:
            index = tantivy_backend.get_index()
            searcher = index.searcher()
            indexed_count = searcher.num_docs
        except Exception:
            pass

    return {
        "doctypes": sorted(options, key=lambda row: row["label"]),
        "full_text_enabled": bool(settings.enable_full_text_search),
        "generic_index_ready": tantivy_backend.index_exists(),
        "indexed_count": indexed_count,
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

    def records():
        for dt in indexed_doctypes:
            if dt == "Document":
                continue
            batch_size = 1000
            offset = 0
            while True:
                docs = frappe.get_all(
                    dt,
                    fields=["name"],
                    limit_start=offset,
                    limit_page_length=batch_size,
                    order_by="creation asc, name asc",
                )
                if not docs:
                    break
                comments = get_document_comments(
                    dt,
                    [row.name for row in docs],
                )
                for row in docs:
                    try:
                        doc = frappe.get_doc(dt, row.name)
                        yield {
                            "doc_type": dt,
                            "doc_name": row.name,
                            "title": get_document_title(doc),
                            "content": get_indexable_content(
                                doc,
                                comments=comments.get(row.name, []),
                            ),
                        }
                    except Exception:
                        frappe.log_error(
                            title=f"Error indexing {dt} {row.name}",
                            message=frappe.get_traceback(),
                        )
                frappe.local.cache = {}
                if hasattr(frappe.local, "document_cache"):
                    frappe.local.document_cache = {}
                offset += batch_size

    generic_result = None
    if settings.enable_full_text_search:
        generic_result = tantivy_backend.rebuild(records())
    total_indexed = generic_result["count"] if generic_result else 0

    rag_result = None
    if settings.enable_semantic_search:
        from document_management.frappe_document_management.rag.index import rebuild_index as rebuild_rag

        rag_result = rebuild_rag()

    return {
        "status": "success",
        "message": f"Rebuilt index for {total_indexed} documents.",
        "generation": generic_result.get("generation") if generic_result else None,
        "rag": rag_result,
    }

@frappe.whitelist()
def extract_and_index_ocr(doc_type, doc_name):
    doc = frappe.get_doc(doc_type, doc_name)
    doc.check_permission("write")
    if doc_type == "Document":
        from document_management.frappe_document_management.page.document_management_console.document_management_console import (
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
    base_content = get_indexable_content(doc)
    full_content = f"{base_content}\n\n--- OCR TEXT ---\n{extracted_text}"
    
    title = get_document_title(doc)
    settings = frappe.get_single('Document Management Settings')
    indexed_doctypes = {
        row.document_type
        for row in settings.get("indexed_doctypes", [])
        if row.document_type
    }
    
    if (
        settings.enable_full_text_search
        and doc_type != "Document"
        and doc_type in indexed_doctypes
    ):
        try:
            tantivy_backend.index_document(
                doc_type,
                doc_name,
                title,
                full_content,
            )
        except tantivy_backend.IndexBusy:
            frappe.throw(
                frappe._(
                    "The search index is rebuilding. "
                    "Try OCR indexing again shortly."
                )
            )
        except tantivy_backend.IndexRebuildRequired:
            frappe.throw(
                frappe._(
                    "The search index must be rebuilt before OCR content "
                    "can be indexed."
                )
            )
        
    return {"status": "success", "extracted_chars": len(extracted_text)}




