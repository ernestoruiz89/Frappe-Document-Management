import frappe


def execute():
    duplicates = frappe.db.sql(
        """
        SELECT file_checksum
        FROM `tabDocument Version`
        WHERE file_checksum IS NOT NULL AND file_checksum != ''
        GROUP BY file_checksum
        HAVING COUNT(*) > 1
        LIMIT 1
        """
    )
    if duplicates:
        frappe.throw(
            "Duplicate document file checksums must be resolved before migration."
        )
    frappe.db.add_unique(
        "Document Version",
        ["file_checksum"],
        "unique_document_version_file_checksum",
    )
