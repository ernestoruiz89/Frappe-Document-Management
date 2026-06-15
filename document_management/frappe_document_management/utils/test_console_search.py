import uuid
from unittest import TestCase
from unittest.mock import patch

import frappe

from document_management.frappe_document_management.page.document_management_console.document_management_console import (
    get_documents,
)
from document_management.frappe_document_management.rag.index import (
    IndexRebuildRequired,
)


class TestDocumentConsoleSearch(TestCase):
    def test_database_search_remains_available_when_rag_index_is_missing(self):
        frappe.set_user("Administrator")
        marker = f"Fallback Search {uuid.uuid4().hex}"
        doc = frappe.get_doc(
            {
                "doctype": "Document",
                "title": marker,
                "status": "Draft",
            }
        ).insert(ignore_permissions=True)
        try:
            with patch(
                "document_management.frappe_document_management.rag.index.search_documents",
                side_effect=IndexRebuildRequired("missing index"),
            ):
                rows = get_documents(search_text=marker, limit=10)

            self.assertEqual([row.name for row in rows], [doc.name])
        finally:
            frappe.delete_doc(
                "Document",
                doc.name,
                ignore_permissions=True,
                force=True,
            )
