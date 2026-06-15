import uuid
from unittest import TestCase
from unittest.mock import patch

import frappe

from document_management.frappe_document_management.page.document_management_console.document_management_console import (
    quick_upload,
)
from document_management.frappe_document_management.utils.archive_lifecycle import (
    permanently_delete_document,
)
from document_management.frappe_document_management.utils.document_sharing import (
    _token_hash,
    create_share_link,
    download_shared_document,
    revoke_share_link,
)


class TestDocumentSharing(TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        self.original_form_dict = frappe.local.form_dict
        self.original_uploaded_file = getattr(frappe.local, "uploaded_file", None)
        self.original_uploaded_filename = getattr(
            frappe.local,
            "uploaded_filename",
            None,
        )
        frappe.local.form_dict = frappe._dict()
        frappe.local.uploaded_filename = f"{uuid.uuid4().hex}.txt"
        frappe.local.uploaded_file = b"shared document"
        with patch(
            "document_management.frappe_document_management.doctype.document.document.Document.enqueue_processing"
        ):
            result = quick_upload(title="Shared document")
        self.document_name = result["docname"]
        frappe.db.commit()

    def tearDown(self):
        frappe.set_user("Administrator")
        if frappe.db.exists("Document", self.document_name):
            permanently_delete_document(self.document_name)
        frappe.db.commit()
        frappe.local.form_dict = self.original_form_dict
        frappe.local.uploaded_file = self.original_uploaded_file
        frappe.local.uploaded_filename = self.original_uploaded_filename

    def test_share_token_is_not_stored_in_plaintext_and_can_be_revoked(self):
        result = create_share_link(self.document_name, 7, "Original")
        token = result["url"].split("token=", 1)[1]
        link = frappe.get_doc("Document Share Link", result["name"])

        self.assertNotEqual(link.token_hash, token)
        self.assertEqual(link.token_hash, _token_hash(token))

        download_shared_document(token)
        self.assertEqual(frappe.local.response.filecontent, b"shared document")

        revoke_share_link(link.name)
        link.reload()
        self.assertFalse(link.enabled)
        with self.assertRaisesRegex(
            frappe.PermissionError,
            "invalid or revoked",
        ):
            download_shared_document(token)

    def test_searchable_pdf_link_requires_preview(self):
        with self.assertRaisesRegex(
            frappe.ValidationError,
            "no searchable PDF",
        ):
            create_share_link(
                self.document_name,
                7,
                "Searchable PDF",
            )
