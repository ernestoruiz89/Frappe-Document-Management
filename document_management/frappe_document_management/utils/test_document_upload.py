import hashlib
import json
import uuid
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import frappe

from document_management.frappe_document_management.page.document_management_console.document_management_console import (
    add_document_version,
    quick_upload,
)


class TestDocumentUpload(TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        self.original_form_dict = frappe.local.form_dict
        self.original_uploaded_file = getattr(frappe.local, "uploaded_file", None)
        self.original_uploaded_filename = getattr(
            frappe.local,
            "uploaded_filename",
            None,
        )
        self.category_name = f"Upload Test {uuid.uuid4().hex}"
        frappe.get_doc(
            {
                "doctype": "Document Category",
                "category_name": self.category_name,
            }
        ).insert(ignore_permissions=True)
        frappe.db.commit()
        self.document_names = []
        self.department_name = frappe.db.get_value("Department", {}, "name")

    def tearDown(self):
        frappe.set_user("Administrator")
        for document_name in self.document_names:
            version_names = frappe.get_all(
                "Document Version",
                filters={"parent": document_name},
                pluck="name",
            )
            for file_name in frappe.get_all(
                "File",
                filters={
                    "attached_to_doctype": "Document Version",
                    "attached_to_name": ["in", version_names or [""]],
                },
                pluck="name",
            ):
                frappe.delete_doc(
                    "File",
                    file_name,
                    ignore_permissions=True,
                    force=True,
                )
            if frappe.db.exists("Document", document_name):
                frappe.delete_doc(
                    "Document",
                    document_name,
                    ignore_permissions=True,
                    force=True,
                )
        if frappe.db.exists("Document Category", self.category_name):
            frappe.delete_doc(
                "Document Category",
                self.category_name,
                ignore_permissions=True,
                force=True,
            )
        frappe.db.commit()
        frappe.local.form_dict = self.original_form_dict
        frappe.local.uploaded_file = self.original_uploaded_file
        frappe.local.uploaded_filename = self.original_uploaded_filename

    def _set_upload(self, filename, content):
        frappe.local.form_dict = frappe._dict()
        frappe.local.uploaded_filename = filename
        frappe.local.uploaded_file = content

    def test_multipart_upload_creates_file_and_version_atomically(self):
        first_content = f"first-{uuid.uuid4().hex}".encode()
        self._set_upload("first.txt", first_content)

        with patch(
            "document_management.frappe_document_management.doctype.document.document.Document.enqueue_processing"
        ):
            result = quick_upload(
                title="Multipart upload test",
                category=self.category_name,
                departments=json.dumps([self.department_name]),
            )
        self.document_names.append(result["docname"])
        frappe.db.commit()

        document = frappe.get_doc("Document", result["docname"])
        self.assertEqual(
            [row.department for row in document.departments_with_access],
            [self.department_name],
        )
        self.assertEqual(len(document.versions), 1)
        first_version = document.versions[0]
        self.assertEqual(first_version.version_number, "1")
        self.assertEqual(
            first_version.file_checksum,
            hashlib.sha256(first_content).hexdigest(),
        )
        first_file = frappe.get_doc(
            "File",
            {
                "attached_to_doctype": "Document Version",
                "attached_to_name": first_version.name,
            },
        )
        self.assertEqual(first_file.file_url, first_version.attachment)
        self.assertTrue(first_file.is_private)

        second_content = f"second-{uuid.uuid4().hex}".encode()
        self._set_upload("second.txt", second_content)
        with patch(
            "document_management.frappe_document_management.doctype.document.document.Document.enqueue_processing"
        ):
            version_result = add_document_version(document.name)

        document.reload()
        self.assertEqual(version_result["version_number"], "2")
        self.assertEqual(len(document.versions), 2)
        self.assertEqual(
            document.versions[-1].file_checksum,
            hashlib.sha256(second_content).hexdigest(),
        )

    def test_quick_upload_allows_document_without_category(self):
        self._set_upload("uncategorized.txt", b"uncategorized-content")

        with patch(
            "document_management.frappe_document_management.doctype.document.document.Document.enqueue_processing"
        ):
            result = quick_upload(title="Uncategorized upload test")

        self.document_names.append(result["docname"])
        document = frappe.get_doc("Document", result["docname"])
        self.assertFalse(document.category)
        self.assertEqual(len(document.versions), 1)

    def test_uncategorized_document_is_visible_to_another_normal_user(self):
        self._set_upload("visible-uncategorized.txt", b"visible-content")
        with patch(
            "document_management.frappe_document_management.doctype.document.document.Document.enqueue_processing"
        ):
            result = quick_upload(title="Visible uncategorized upload")
        self.document_names.append(result["docname"])
        user = f"document-reader-{uuid.uuid4().hex}@example.com"
        frappe.get_doc(
            {
                "doctype": "User",
                "email": user,
                "first_name": "Document Reader",
                "enabled": 1,
                "send_welcome_email": 0,
                "user_type": "System User",
            }
        ).insert(ignore_permissions=True)
        frappe.db.commit()
        try:
            frappe.set_user(user)
            visible = frappe.get_list(
                "Document",
                filters={"name": result["docname"]},
                pluck="name",
            )
            self.assertEqual(visible, [result["docname"]])
        finally:
            frappe.set_user("Administrator")
            frappe.delete_doc(
                "User",
                user,
                ignore_permissions=True,
                force=True,
            )

    def test_quick_upload_rejects_unsupported_type_before_insert(self):
        self._set_upload("malware.exe", b"MZ" + b"\x00" * 64)
        before_documents = frappe.db.count("Document")

        with self.assertRaisesRegex(
            frappe.ValidationError,
            "Unsupported document file type",
        ):
            quick_upload(title="Unsupported upload")

        self.assertEqual(frappe.db.count("Document"), before_documents)

    def test_quick_upload_rejects_mismatched_signature_before_insert(self):
        self._set_upload("fake.pdf", b"this is not a PDF")
        before_documents = frappe.db.count("Document")

        with self.assertRaisesRegex(
            frappe.ValidationError,
            "does not match its file extension",
        ):
            quick_upload(title="Mismatched upload")

        self.assertEqual(frappe.db.count("Document"), before_documents)

    def test_quick_upload_rejects_duplicate_before_document_insert(self):
        content = f"duplicate-upload-{uuid.uuid4().hex}".encode()
        self._set_upload("first-duplicate.txt", content)
        with patch(
            "document_management.frappe_document_management.doctype.document.document.Document.enqueue_processing"
        ):
            first = quick_upload(title="Original duplicate test")
        self.document_names.append(first["docname"])
        frappe.db.commit()
        before_documents = frappe.db.count("Document")

        self._set_upload("second-duplicate.txt", content)
        with (
            patch(
                "document_management.frappe_document_management.doctype.document.document.Document.enqueue_processing"
            ),
            self.assertRaisesRegex(
                frappe.ValidationError,
                "identical file already exists",
            ),
        ):
            quick_upload(title="Rejected duplicate test")

        self.assertEqual(frappe.db.count("Document"), before_documents)

    def test_add_version_rejects_duplicate_before_save(self):
        content = f"duplicate-version-{uuid.uuid4().hex}".encode()
        self._set_upload("version-one.txt", content)
        with patch(
            "document_management.frappe_document_management.doctype.document.document.Document.enqueue_processing"
        ):
            first = quick_upload(title="Duplicate version test")
        self.document_names.append(first["docname"])
        frappe.db.commit()

        self._set_upload("version-two.txt", content)
        with (
            patch(
                "document_management.frappe_document_management.doctype.document.document.Document.enqueue_processing"
            ),
            self.assertRaisesRegex(
                frappe.ValidationError,
                "same file more than once",
            ),
        ):
            add_document_version(first["docname"])

        document = frappe.get_doc("Document", first["docname"])
        self.assertEqual(len(document.versions), 1)

    def test_failed_file_save_rolls_back_document_and_version(self):
        self._set_upload("rollback.txt", b"rollback-content")
        before_documents = frappe.db.count("Document")
        before_versions = frappe.db.count("Document Version")

        with (
            patch(
                "document_management.frappe_document_management.doctype.document.document.Document.enqueue_processing"
            ),
            patch(
                "document_management.frappe_document_management.page.document_management_console.document_management_console._save_version_file",
                side_effect=frappe.ValidationError("forced upload failure"),
            ),
            self.assertRaisesRegex(frappe.ValidationError, "forced upload failure"),
        ):
            quick_upload(
                title="Rollback upload test",
                category=self.category_name,
            )

        self.assertEqual(frappe.db.count("Document"), before_documents)
        self.assertEqual(frappe.db.count("Document Version"), before_versions)

    def test_failure_after_file_write_removes_database_and_disk_file(self):
        content = f"filesystem-rollback-{uuid.uuid4().hex}".encode()
        self._set_upload("filesystem-rollback.txt", content)
        private_files = Path(frappe.get_site_path("private", "files"))
        files_before = set(private_files.iterdir())
        file_rows_before = frappe.db.count("File")
        original_set_value = frappe.db.set_value

        def fail_version_update(doctype, *args, **kwargs):
            if doctype == "Document Version":
                raise frappe.ValidationError("forced post-write failure")
            return original_set_value(doctype, *args, **kwargs)

        with (
            patch(
                "document_management.frappe_document_management.doctype.document.document.Document.enqueue_processing"
            ),
            patch.object(frappe.db, "set_value", side_effect=fail_version_update),
            self.assertRaisesRegex(
                frappe.ValidationError,
                "forced post-write failure",
            ),
        ):
            quick_upload(
                title="Filesystem rollback test",
                category=self.category_name,
            )

        self.assertEqual(frappe.db.count("File"), file_rows_before)
        self.assertEqual(set(private_files.iterdir()), files_before)
