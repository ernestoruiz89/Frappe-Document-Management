from pathlib import Path

import frappe
from frappe.model.document import Document

from document_management.frappe_document_management.utils.storage_paths import (
    validate_storage_template,
)


class DocumentManagementSettings(Document):
    def validate(self):
        self._validate_indexed_doctypes()
        if int(self.trash_retention_days or 0) < 0:
            frappe.throw("Trash Retention (Days) cannot be negative.")
        if int(self.export_retention_hours or 0) < 0:
            frappe.throw("Export Retention (Hours) cannot be negative.")
        validate_storage_template(self.get("file_storage_path_template"))
        self._validate_folder_ingestion()
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

    def _validate_folder_ingestion(self):
        if not self.get("enable_folder_ingestion"):
            return
        source = (self.get("ingestion_folder_path") or "").strip()
        if not source:
            frappe.throw("Ingestion Folder Path is required when ingestion is enabled.")
        source_path = Path(source).expanduser()
        if not source_path.is_dir():
            frappe.throw("Ingestion Folder Path must be an existing directory.")

        source_resolved = source_path.resolve()
        for fieldname, label in (
            ("ingestion_done_folder_path", "Done Folder Path"),
            ("ingestion_error_folder_path", "Error Folder Path"),
        ):
            value = (self.get(fieldname) or "").strip()
            if not value:
                continue
            resolved = Path(value).expanduser().resolve()
            if resolved == source_resolved:
                frappe.throw(f"{label} cannot be the ingestion folder itself.")

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
