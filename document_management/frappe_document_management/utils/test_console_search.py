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

    def test_database_search_is_used_when_rag_returns_no_matches(self):
        frappe.set_user("Administrator")
        marker = f"Empty Rag Fallback {uuid.uuid4().hex}"
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
                return_value=[],
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

    def test_database_search_includes_document_description(self):
        frappe.set_user("Administrator")
        marker = f"Description Search {uuid.uuid4().hex}"
        doc = frappe.get_doc(
            {
                "doctype": "Document",
                "title": "Description search candidate",
                "description": f"<p>{marker}</p>",
                "status": "Draft",
            }
        ).insert(ignore_permissions=True)
        try:
            with patch(
                "document_management.frappe_document_management.rag.index.search_documents",
                return_value=[],
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

    def test_advanced_search_syntax_filters_documents(self):
        frappe.set_user("Administrator")
        marker = f"Advanced Search {uuid.uuid4().hex}"
        category = f"Advanced Category {uuid.uuid4().hex}"
        frappe.get_doc(
            {
                "doctype": "Document Category",
                "category_name": category,
            }
        ).insert(ignore_permissions=True)
        matching = frappe.get_doc(
            {
                "doctype": "Document",
                "title": f"{marker} Contract",
                "document_code": "ADV-001",
                "category": category,
                "status": "Published",
            }
        ).insert(ignore_permissions=True)
        other = frappe.get_doc(
            {
                "doctype": "Document",
                "title": f"{marker} Draft",
                "document_code": "ADV-002",
                "category": category,
                "status": "Draft",
            }
        ).insert(ignore_permissions=True)
        try:
            rows = get_documents(
                search_text=f'title:"{marker}" status:Published code:ADV-001',
                limit=10,
            )

            self.assertEqual([row.name for row in rows], [matching.name])
            self.assertNotIn(other.name, [row.name for row in rows])
        finally:
            for doc in (matching, other):
                frappe.delete_doc(
                    "Document",
                    doc.name,
                    ignore_permissions=True,
                    force=True,
                )
            frappe.delete_doc(
                "Document Category",
                category,
                ignore_permissions=True,
                force=True,
            )
