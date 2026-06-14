import frappe


def execute():
    # Delete the old Page document
    frappe.delete_doc("Page", "unified-search", ignore_missing=True, force=True)

    # Reload the new page to insert/update it in the database
    frappe.reload_doc("frappe_document_management", "page", "indexed_doctypes_search")

    # Reload the workspace to update the links
    frappe.reload_doc("frappe_document_management", "workspace", "document_management")
