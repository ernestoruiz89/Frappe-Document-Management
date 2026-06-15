import frappe
from frappe.model.document import Document


class DocumentManagementSettings(Document):
    def validate(self):
        self._validate_indexed_doctypes()
        if int(self.trash_retention_days or 0) < 0:
            frappe.throw("Trash Retention (Days) cannot be negative.")
        if int(self.export_retention_hours or 0) < 0:
            frappe.throw("Export Retention (Hours) cannot be negative.")
        if (
            self.ocr_processing_timeout_minutes
            and int(self.ocr_processing_timeout_minutes) < 5
        ):
            frappe.throw("OCR Processing Timeout must be at least 5 minutes.")
        if self.chat_provider == "OpenAI Compatible" and not (
            self.chat_endpoint or ""
        ).strip():
            frappe.throw(
                "Chat Endpoint is required for OpenAI Compatible."
            )
        if self.embedding_provider == "OpenAI Compatible" and not (
            self.embedding_endpoint or ""
        ).strip():
            frappe.throw(
                "Embedding Endpoint is required for OpenAI Compatible."
            )

    def _validate_indexed_doctypes(self):
        seen = set()
        for row in self.get("indexed_doctypes") or []:
            doctype = (row.document_type or "").strip()
            if not doctype:
                continue
            if doctype in seen:
                frappe.throw(f"Indexed DocType {frappe.bold(doctype)} is duplicated.")
            seen.add(doctype)

            meta = frappe.get_meta(doctype)
            if meta.istable:
                frappe.throw(
                    f"Child table {frappe.bold(doctype)} cannot be indexed directly."
                )
            if meta.issingle:
                frappe.throw(
                    f"Single DocType {frappe.bold(doctype)} cannot be indexed."
                )
