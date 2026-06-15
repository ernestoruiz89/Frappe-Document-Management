import frappe


def execute():
    settings = frappe.get_single("Document Management Settings")
    if not settings.enable_full_text_search:
        return
    if not any(
        row.document_type
        for row in settings.get("indexed_doctypes", [])
    ):
        return

    frappe.enqueue(
        "document_management.search.indexer.rebuild_index",
        queue="long",
        timeout=7200,
        enqueue_after_commit=True,
        job_name="rebuild-indexed-doctype-comments",
    )
