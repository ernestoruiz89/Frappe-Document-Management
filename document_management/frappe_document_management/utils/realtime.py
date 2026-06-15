DOCUMENT_CHANGE_EVENT = "document_management_document_changed"


def publish_document_change(doc=None, document_name=None, deleted=False):
    import frappe

    name = document_name or getattr(doc, "name", None)
    if not name:
        return

    frappe.publish_realtime(
        DOCUMENT_CHANGE_EVENT,
        {
            "doctype": "Document",
            "name": name,
            "deleted": bool(deleted),
        },
        after_commit=True,
    )
