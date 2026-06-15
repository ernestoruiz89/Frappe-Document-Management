import frappe
from frappe.utils import add_days, add_to_date, now_datetime

from document_management.frappe_document_management.utils.archive_portability import (
    EXPORT_ATTACHMENT_DOCTYPE,
    EXPORT_ATTACHMENT_NAME,
)


def _document_file_names(document_name):
    version_names = frappe.get_all(
        "Document Version",
        filters={"parent": document_name},
        pluck="name",
    )
    file_names = set(
        frappe.get_all(
            "File",
            filters={
                "attached_to_doctype": "Document",
                "attached_to_name": document_name,
            },
            pluck="name",
        )
    )
    if version_names:
        file_names.update(
            frappe.get_all(
                "File",
                filters={
                    "attached_to_doctype": "Document Version",
                    "attached_to_name": ["in", version_names],
                },
                pluck="name",
            )
        )
    return file_names


def permanently_delete_document(document_name):
    for file_name in _document_file_names(document_name):
        if frappe.db.exists("File", file_name):
            frappe.delete_doc(
                "File",
                file_name,
                ignore_permissions=True,
                force=True,
            )
    frappe.delete_doc(
        "Document",
        document_name,
        ignore_permissions=True,
        force=True,
    )


def purge_expired_trash():
    retention_days = int(
        frappe.db.get_single_value(
            "Document Management Settings",
            "trash_retention_days",
        )
        or 0
    )
    if retention_days <= 0:
        return []
    cutoff = add_days(now_datetime(), -retention_days)
    expired = frappe.get_all(
        "Document",
        filters={
            "is_deleted": 1,
            "deleted_at": ["<=", cutoff],
        },
        pluck="name",
        limit_page_length=0,
    )
    deleted = []
    for document_name in expired:
        try:
            permanently_delete_document(document_name)
            frappe.db.commit()
            deleted.append(document_name)
        except Exception:
            frappe.db.rollback()
            frappe.log_error(
                title="Document Trash Retention Error",
                message=frappe.get_traceback(),
            )
    return deleted


def purge_expired_exports():
    retention_hours = int(
        frappe.db.get_single_value(
            "Document Management Settings",
            "export_retention_hours",
        )
        or 0
    )
    if retention_hours <= 0:
        return []
    cutoff = add_to_date(now_datetime(), hours=-retention_hours)
    expired = frappe.get_all(
        "File",
        filters={
            "attached_to_doctype": EXPORT_ATTACHMENT_DOCTYPE,
            "attached_to_name": EXPORT_ATTACHMENT_NAME,
            "creation": ["<=", cutoff],
        },
        pluck="name",
        limit_page_length=0,
    )
    deleted = []
    for file_name in expired:
        try:
            frappe.delete_doc(
                "File",
                file_name,
                ignore_permissions=True,
                force=True,
            )
            frappe.db.commit()
            deleted.append(file_name)
        except Exception:
            frappe.db.rollback()
            frappe.log_error(
                title="Document Export Retention Error",
                message=frappe.get_traceback(),
            )
    return deleted
