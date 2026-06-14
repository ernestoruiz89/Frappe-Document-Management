import frappe
import json

from frappe.utils import now_datetime


DOCUMENT_FIELDS = [
    "name",
    "document_code",
    "title as document_name",
    "category",
    "folder",
    "department",
    "status",
    "ocr_status",
    "current_version as version",
    "creation as document_date",
    "party_type",
    "party_name",
    "is_deleted",
    "deleted_at",
    "deleted_by",
]


def _database_search_names(search_text, filters, limit):
    escaped = search_text.replace("%", r"\%").replace("_", r"\_")
    pattern = f"%{escaped}%"
    rows = frappe.get_list(
        "Document",
        filters=filters,
        or_filters=[
            ["Document", "title", "like", pattern],
            ["Document", "name", "like", pattern],
            ["Document", "document_code", "like", pattern],
            ["Document", "ocr_content", "like", pattern],
        ],
        pluck="name",
        order_by="modified desc",
        limit_page_length=limit,
    )
    return list(rows)


def _document_names(raw_documents, maximum=100):
    if isinstance(raw_documents, str):
        try:
            raw_documents = json.loads(raw_documents)
        except (TypeError, ValueError):
            frappe.throw("Invalid document selection.")
    if not isinstance(raw_documents, (list, tuple)):
        frappe.throw("Invalid document selection.")
    names = list(
        dict.fromkeys(
            str(name).strip()
            for name in raw_documents[:maximum]
            if str(name).strip()
        )
    )
    if not names:
        frappe.throw("Select at least one document.")
    return names


def _authorized_documents(names, permission_type="write"):
    documents = []
    for name in names:
        doc = frappe.get_doc("Document", name)
        doc.check_permission(permission_type)
        documents.append(doc)
    return documents


def _enqueue_index_refresh(names, remove=False):
    job = frappe.enqueue(
        "document_management.frappe_document_management.rag.index.refresh_document_indexes",
        document_names=names,
        remove=remove,
        queue="long",
        timeout=max(900, len(names) * 120),
        enqueue_after_commit=True,
    )
    return getattr(job, "id", None)


def _saved_view_filters(raw_filters):
    if isinstance(raw_filters, str):
        try:
            raw_filters = json.loads(raw_filters)
        except (TypeError, ValueError):
            frappe.throw("Invalid saved view filters.")
    if not isinstance(raw_filters, dict):
        frappe.throw("Invalid saved view filters.")
    result = {}
    search = raw_filters.get("search")
    if search:
        result["search"] = str(search).strip()[:500]
    
    # Categories (multiple or single)
    categories = raw_filters.get("categories") or raw_filters.get("category")
    if categories:
        if isinstance(categories, str):
            try:
                categories = json.loads(categories)
            except (TypeError, ValueError):
                categories = [c.strip() for c in categories.split(",") if c.strip()]
        if not isinstance(categories, list):
            categories = [categories]
        valid_categories = []
        for cat in categories:
            cat_str = str(cat).strip()
            if cat_str and frappe.db.exists("Document Category", cat_str):
                valid_categories.append(cat_str)
        if valid_categories:
            result["categories"] = valid_categories

    # Statuses (multiple or single)
    statuses = raw_filters.get("statuses") or raw_filters.get("status")
    if statuses:
        if isinstance(statuses, str):
            try:
                statuses = json.loads(statuses)
            except (TypeError, ValueError):
                statuses = [s.strip() for s in statuses.split(",") if s.strip()]
        if not isinstance(statuses, list):
            statuses = [statuses]
        valid_statuses = []
        for s in statuses:
            s_str = str(s).strip()
            if s_str in {"Draft", "Published", "Obsolete"}:
                valid_statuses.append(s_str)
        if valid_statuses:
            result["statuses"] = valid_statuses

    tags = raw_filters.get("tags")
    if tags:
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except (TypeError, ValueError):
                tags = [t.strip() for t in tags.split(",") if t.strip()]
        if isinstance(tags, list):
            result["tags"] = [str(t) for t in tags]
    view_mode = raw_filters.get("view_mode")
    if view_mode:
        if view_mode not in {"grid", "large", "list"}:
            frappe.throw("Invalid view mode.")
        result["view_mode"] = view_mode
    result["trash"] = 1 if raw_filters.get("trash") else 0
    return result


@frappe.whitelist()
def list_saved_views():
    return frappe.get_all(
        "Document Saved View",
        filters={"user": frappe.session.user},
        fields=["name", "view_name", "filters_json"],
        order_by="view_name asc",
        limit_page_length=100,
    )


@frappe.whitelist()
def save_current_view(view_name, filters):
    view_name = (view_name or "").strip()[:140]
    if not view_name:
        frappe.throw("View name is required.")
    parsed = _saved_view_filters(filters)
    existing = frappe.db.get_value(
        "Document Saved View",
        {"user": frappe.session.user, "view_name": view_name},
        "name",
    )
    doc = (
        frappe.get_doc("Document Saved View", existing)
        if existing
        else frappe.new_doc("Document Saved View")
    )
    doc.view_name = view_name
    doc.user = frappe.session.user
    doc.filters_json = json.dumps(parsed, ensure_ascii=True)
    doc.save()
    return {
        "name": doc.name,
        "view_name": doc.view_name,
        "filters_json": doc.filters_json,
    }


@frappe.whitelist()
def delete_saved_view(view):
    doc = frappe.get_doc("Document Saved View", view)
    doc.check_permission("delete")
    frappe.delete_doc("Document Saved View", doc.name)
    return {"deleted": doc.name}


def _get_deleted_documents(search_text, filters, limit):
    filters = {**filters, "is_deleted": 1}
    or_filters = None
    if search_text:
        escaped = search_text.replace("%", r"\%").replace("_", r"\_")
        pattern = f"%{escaped}%"
        or_filters = [
            ["Document", "title", "like", pattern],
            ["Document", "name", "like", pattern],
            ["Document", "document_code", "like", pattern],
            ["Document", "ocr_content", "like", pattern],
        ]
    candidates = frappe.get_all(
        "Document",
        fields=DOCUMENT_FIELDS,
        filters=filters,
        or_filters=or_filters,
        limit_page_length=min(limit * 5, 500),
        order_by="deleted_at desc, modified desc",
    )
    visible = []
    for row in candidates:
        doc = frappe.get_doc("Document", row.name)
        if frappe.has_permission(
            "Document",
            "read",
            doc=doc,
            user=frappe.session.user,
        ):
            visible.append(row)
        if len(visible) >= limit:
            break
    return visible


@frappe.whitelist()
def get_documents(
    search_text=None,
    category=None,
    status=None,
    categories=None,
    statuses=None,
    tags=None,
    trash=0,
    limit=50,
):
    try:
        limit = min(max(int(limit), 1), 100)
    except (TypeError, ValueError):
        limit = 50
    search_text = (search_text or "").strip()
    search_terms = []
    if search_text:
        from document_management.search.query import significant_terms

        search_terms = significant_terms(search_text)
    filters = {}
    try:
        trash = bool(int(trash or 0))
    except (TypeError, ValueError):
        trash = False

    # Categories filter (supports array, string or comma-separated)
    cats = categories or category
    if cats:
        if isinstance(cats, str):
            try:
                cats = json.loads(cats)
            except (TypeError, ValueError):
                cats = [c.strip() for c in cats.split(",") if c.strip()]
        if not isinstance(cats, (list, tuple)):
            cats = [cats]
        cats = [str(c).strip() for c in cats if str(c).strip()]
        if cats:
            filters["category"] = ["in", cats]

    # Statuses filter (supports array, string or comma-separated)
    stats = statuses or status
    if stats:
        if isinstance(stats, str):
            try:
                stats = json.loads(stats)
            except (TypeError, ValueError):
                stats = [s.strip() for s in stats.split(",") if s.strip()]
        if not isinstance(stats, (list, tuple)):
            stats = [stats]
        stats = [str(s).strip() for s in stats if str(s).strip() in {"Draft", "Published", "Obsolete"}]
        if stats:
            filters["status"] = ["in", stats]

    if tags:
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except (TypeError, ValueError):
                tags = [t.strip() for t in tags.split(",") if t.strip()]
        if not isinstance(tags, (list, tuple)):
            tags = [tags]
        tags = [str(tag).strip() for tag in tags if str(tag).strip()]
        if tags:
            matching_parents = frappe.db.sql(
                """
                SELECT parent FROM `tabDocument Tag Link`
                WHERE parenttype = 'Document' AND tag IN %(tags)s
                GROUP BY parent
                HAVING COUNT(DISTINCT tag) = %(count)s
                """,
                {"tags": tags, "count": len(tags)},
                pluck="parent",
            )
            if not matching_parents:
                return []
            filters["name"] = ["in", matching_parents]

    ranked_names = None
    search_metadata = {}
    if trash:
        docs = _get_deleted_documents(search_text, filters, limit)
    elif search_text:
        filters["is_deleted"] = 0
        allowed_names = set(
            frappe.get_list(
                "Document",
                filters=filters,
                pluck="name",
                limit_page_length=100000,
            )
        )
        try:
            from document_management.frappe_document_management.rag.index import (
                IndexRebuildRequired,
                search_documents,
            )

            results = search_documents(search_text, allowed_names, limit=limit)
            ranked_names = [row["doc_name"] for row in results]
            search_metadata = {row["doc_name"]: row for row in results}
        except IndexRebuildRequired as exc:
            frappe.throw(
                str(exc),
                title="Document Search Index Rebuild Required",
            )
        except Exception:
            frappe.log_error(
                title="Document Search Error",
                message=frappe.get_traceback(),
            )
            ranked_names = _database_search_names(search_text, filters, limit)

        if not ranked_names:
            return []
        if "name" in filters:
            existing = filters["name"]
            if isinstance(existing, list) and existing[0] == "in":
                filters["name"] = ["in", [n for n in ranked_names if n in existing[1]]]
            else:
                filters["name"] = ["in", ranked_names]
        else:
            filters["name"] = ["in", ranked_names]
        docs = frappe.get_list(
            "Document",
            fields=DOCUMENT_FIELDS,
            filters=filters,
            limit_page_length=limit,
            order_by="modified desc",
        )
    elif not trash:
        filters["is_deleted"] = 0
        docs = frappe.get_list(
            "Document",
            fields=DOCUMENT_FIELDS,
            filters=filters,
            limit_page_length=limit,
            order_by="modified desc",
        )

    if ranked_names is not None:
        by_name = {doc.name: doc for doc in docs}
        docs = [by_name[name] for name in ranked_names if name in by_name]
        for doc in docs:
            metadata = search_metadata.get(doc.name, {})
            doc.search_score = metadata.get("score")
            doc.search_excerpt = metadata.get("excerpt")
            doc.search_page = metadata.get("page")
            doc.search_terms = search_terms

    doc_names = [d.name for d in docs]
    tags_by_document = {}
    versions_by_document = {}
    if doc_names:
        tags_data = frappe.get_all(
            "Document Tag Link",
            filters={"parent": ("in", doc_names)},
            fields=["parent", "tag"],
            order_by="parent asc, idx asc",
        )
        tag_colors = frappe.get_all("Document Tag", fields=["name", "color"])
        color_map = {t.name: t.color for t in tag_colors}
        for tag in tags_data:
            tags_by_document.setdefault(tag.parent, []).append(
                {"name": tag.tag, "color": color_map.get(tag.tag, "#e2e8f0")}
            )

        versions = frappe.get_all(
            "Document Version",
            filters={"parent": ["in", doc_names], "parenttype": "Document"},
            fields=[
                "parent",
                "attachment",
                "preview_attachment",
                "preview_status",
                "version_number",
                "is_markdown",
                "idx",
            ],
            order_by="parent asc, idx desc",
        )
        for version in versions:
            versions_by_document.setdefault(version.parent, version)

    for doc in docs:
        doc.tags = tags_by_document.get(doc.name, [])
        latest_version = versions_by_document.get(doc.name)
        if latest_version:
            doc.original_file = latest_version.attachment
            doc.document_file = (
                latest_version.preview_attachment or latest_version.attachment
            )
            doc.preview_status = latest_version.preview_status
            doc.version = latest_version.version_number
            doc.is_markdown = getattr(latest_version, "is_markdown", 0)

    return docs


@frappe.whitelist()
def bulk_update_documents(
    documents,
    status=None,
    category=None,
    add_tags=None,
    remove_tags=None,
):
    names = _document_names(documents)
    docs = _authorized_documents(names, "write")
    if status and status not in {"Draft", "Published", "Obsolete"}:
        frappe.throw("Invalid document status.")
    if category and not frappe.db.exists("Document Category", category):
        frappe.throw("Document category does not exist.")

    add_tags = _document_names(add_tags, maximum=50) if add_tags else []
    remove_tags = _document_names(remove_tags, maximum=50) if remove_tags else []
    for tag in add_tags + remove_tags:
        if not frappe.db.exists("Document Tag", tag):
            frappe.throw(f"Document tag does not exist: {tag}")

    for doc in docs:
        if doc.is_deleted:
            frappe.throw("Restore documents before editing them.")
        if status:
            doc.status = status
        if category:
            doc.category = category
        current_tags = {row.tag for row in doc.get("tags") or []}
        final_tags = (current_tags | set(add_tags)) - set(remove_tags)
        if final_tags != current_tags:
            doc.set("tags", [])
            for tag in sorted(final_tags):
                doc.append("tags", {"tag": tag})
        doc.save()
    return {"updated": names}


@frappe.whitelist()
def move_documents_to_trash(documents):
    names = _document_names(documents)
    docs = _authorized_documents(names, "write")
    deleted_at = now_datetime()
    changed = []
    for doc in docs:
        if doc.is_deleted:
            continue
        doc.db_set(
            {
                "is_deleted": 1,
                "deleted_at": deleted_at,
                "deleted_by": frappe.session.user,
            }
        )
        changed.append(doc.name)
    job_id = _enqueue_index_refresh(changed, remove=True) if changed else None
    return {"deleted": changed, "job_id": job_id}


@frappe.whitelist()
def restore_documents(documents):
    names = _document_names(documents)
    docs = _authorized_documents(names, "write")
    restored = []
    for doc in docs:
        if not doc.is_deleted:
            continue
        doc.db_set(
            {
                "is_deleted": 0,
                "deleted_at": None,
                "deleted_by": None,
            }
        )
        restored.append(doc.name)
    job_id = _enqueue_index_refresh(restored) if restored else None
    return {"restored": restored, "job_id": job_id}


@frappe.whitelist()
def permanently_delete_documents(documents):
    if "System Manager" not in frappe.get_roles():
        frappe.throw(
            "System Manager role is required for permanent deletion.",
            frappe.PermissionError,
        )
    names = _document_names(documents)
    docs = _authorized_documents(names, "delete")
    for doc in docs:
        if not doc.is_deleted:
            frappe.throw("Only documents in trash can be permanently deleted.")
    for doc in docs:
        frappe.delete_doc("Document", doc.name)
    return {"deleted": names}

@frappe.whitelist()
def force_generate_pdf(doc_name):
    import shutil
    from frappe import _
    frappe.get_doc("Document", doc_name).check_permission("write")
    if not shutil.which("libreoffice"):
        return _("ERROR: LibreOffice is not installed on the server ('libreoffice' command not found). Please install it using: sudo apt-get install libreoffice-core --no-install-recommends")
        
    from document_management.frappe_document_management.doctype.document.document import convert_office_to_pdf
    try:
        status = convert_office_to_pdf(doc_name)
        doc = frappe.get_doc("Document", doc_name)
        doc.enqueue_ocr(enqueue_after_commit=False)
        return status
    except Exception as e:
        frappe.throw(_("Fatal Error: {0}").format(str(e)))


@frappe.whitelist()
def reprocess_ocr(doc_name):
    doc = frappe.get_doc("Document", doc_name)
    doc.check_permission("write")
    version = doc.get_current_version()
    if not version:
        frappe.throw("The document does not have an attached version.")
    if version.ocr_status == "Processing":
        frappe.throw("OCR is already processing this document.")

    frappe.db.set_value(
        "Document Version",
        version.name,
        "ocr_status",
        "Pending",
    )
    frappe.db.set_value("Document", doc.name, "ocr_status", "Pending")
    frappe.enqueue(
        "document_management.frappe_document_management.utils.ocr_worker.process_ocr",
        doc_name=doc.name,
        queue="long",
        enqueue_after_commit=True,
    )
    return {"status": "Pending", "document": doc.name}


@frappe.whitelist()
def get_categories():
    return frappe.get_list("Document Category", fields=["name"], order_by="name asc")

@frappe.whitelist()
def get_folders():
    return frappe.get_list("File", filters={"is_folder": 1}, fields=["name as folder_name", "file_name"], order_by="file_name asc")

@frappe.whitelist()
def quick_upload(title, category, document_code=None, tags=None, file_data=None, folder=None):
    import json
    from frappe.utils import today
    from frappe.utils.file_manager import save_file
    
    frappe.has_permission("Document", "create", throw=True)
    doc = frappe.new_doc("Document")
    if document_code:
        doc.document_code = document_code
        
    doc.title = title
    doc.category = category
    doc.status = "Draft"
    if folder:
        doc.folder = folder
    
    if tags:
        tags_list = json.loads(tags)
        for tag in tags_list:
            doc.append("tags", {"tag": tag})
            
    doc.insert(ignore_permissions=True)
    
    # Process the file if provided
    file_url = ""
    if file_data:
        file_dict = json.loads(file_data)
        
        save_args = {
            "fname": file_dict.get("filename"),
            "content": file_dict.get("content"),
            "dt": "Document",
            "dn": doc.name,
            "decode": True,
            "is_private": 1,
            "df": "attachment"
        }
        if folder:
            save_args["folder"] = folder
            
        file_doc = save_file(**save_args)
        file_url = file_doc.file_url

    # Create the first version placeholder
    version = doc.append("versions", {
        "version_number": "1",
        "release_date": today(),
        "attachment": file_url,
        "change_log": "Initial upload"
    })
    doc.save(ignore_permissions=True)
    
    return {
        "docname": doc.name,
        "version_name": version.name
    }


@frappe.whitelist()
def get_tags():
    return frappe.get_list("Document Tag", fields=["name", "color"], order_by="name asc")
