import frappe


def _field_exists(fieldname):
    try:
        return bool(
            frappe.get_meta(
                "Document Management Settings",
                cached=False,
            ).has_field(fieldname)
        )
    except Exception:
        return False


def _set_default_if_empty(fieldname, value):
    if not _field_exists(fieldname):
        return
    if not frappe.db.get_single_value(
        "Document Management Settings",
        fieldname,
    ):
        frappe.db.set_single_value(
            "Document Management Settings",
            fieldname,
            value,
        )


def execute():
    frappe.reload_doc(
        "frappe_document_management",
        "doctype",
        "document_management_settings",
    )
    _set_default_if_empty("ocr_processing_timeout_minutes", 60)
    _set_default_if_empty("trash_retention_days", 30)
    _set_default_if_empty("export_retention_hours", 24)
