import frappe


def execute():
    if not frappe.db.get_single_value(
        "Document Management Settings",
        "ocr_processing_timeout_minutes",
    ):
        frappe.db.set_single_value(
            "Document Management Settings",
            "ocr_processing_timeout_minutes",
            60,
        )
    if not frappe.db.get_single_value(
        "Document Management Settings",
        "trash_retention_days",
    ):
        frappe.db.set_single_value(
            "Document Management Settings",
            "trash_retention_days",
            30,
        )
    if not frappe.db.get_single_value(
        "Document Management Settings",
        "export_retention_hours",
    ):
        frappe.db.set_single_value(
            "Document Management Settings",
            "export_retention_hours",
            24,
        )
