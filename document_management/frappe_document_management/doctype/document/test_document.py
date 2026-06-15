import hashlib
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import frappe

from document_management.frappe_document_management.doctype.document.document import (
    Document,
    get_permission_query_conditions,
    has_permission,
)


def _document(name, versions):
    document = Document(
        {
            "doctype": "Document",
            "name": name,
            "title": "Test",
            "category": "Test",
            "versions": versions,
        }
    )
    document.flags.name_set = True
    return document


class TestDocumentValidation(TestCase):
    def test_original_and_preview_checksums_are_populated(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            original_path = Path(temp_dir) / "original.pdf"
            preview_path = Path(temp_dir) / "preview.pdf"
            original_content = b"original-content"
            preview_content = b"preview-content"
            original_path.write_bytes(original_content)
            preview_path.write_bytes(preview_content)
            document = _document(
                "DOC-2026-0001",
                [
                    {
                        "doctype": "Document Version",
                        "version_number": "1",
                        "release_date": "2026-06-13",
                        "attachment": "/private/files/original.pdf",
                        "preview_attachment": "/private/files/preview.pdf",
                    }
                ],
            )

            with patch(
                "document_management.frappe_document_management.doctype.document.document.get_file_path",
                side_effect=lambda url: (
                    str(preview_path)
                    if url.endswith("preview.pdf")
                    else str(original_path)
                ),
            ):
                document.populate_version_checksums()

        version = document.versions[0]
        self.assertEqual(
            version.file_checksum,
            hashlib.sha256(original_content).hexdigest(),
        )
        self.assertEqual(version.file_size, len(original_content))
        self.assertEqual(
            version.preview_checksum,
            hashlib.sha256(preview_content).hexdigest(),
        )

    def test_metadata_save_excludes_versions_from_the_same_document(self):
        document = _document(
            "DOC-2026-0001",
            [
                {
                    "doctype": "Document Version",
                    "name": "VERSION-1",
                    "version_number": "1",
                    "release_date": "2026-06-13",
                    "attachment": "/private/files/test.pdf",
                    "file_checksum": "same-checksum",
                }
            ],
        )

        with patch(
            "document_management.frappe_document_management.doctype.document.document.frappe.db.get_value",
            return_value=None,
        ) as get_value:
            document.validate_duplicate_files()

        filters = get_value.call_args.args[1]
        self.assertEqual(filters["parent"], ["!=", document.name])
        self.assertEqual(filters["parenttype"], "Document")
        self.assertEqual(filters["parentfield"], "versions")

    def test_same_file_in_two_versions_of_one_document_is_rejected(self):
        document = _document(
            "DOC-2026-0001",
            [
                {
                    "doctype": "Document Version",
                    "version_number": "1",
                    "release_date": "2026-06-13",
                    "attachment": "/private/files/one.pdf",
                    "file_checksum": "same-checksum",
                },
                {
                    "doctype": "Document Version",
                    "version_number": "2",
                    "release_date": "2026-06-13",
                    "attachment": "/private/files/two.pdf",
                    "file_checksum": "same-checksum",
                },
            ],
        )

        with self.assertRaisesRegex(
            frappe.ValidationError,
            "same file more than once",
        ):
            document.validate_duplicate_files()

    def test_same_file_in_another_document_is_rejected(self):
        document = _document(
            "DOC-2026-0002",
            [
                {
                    "doctype": "Document Version",
                    "version_number": "1",
                    "release_date": "2026-06-13",
                    "attachment": "/private/files/test.pdf",
                    "file_checksum": "same-checksum",
                }
            ],
        )

        with patch(
            "document_management.frappe_document_management.doctype.document.document.frappe.db.get_value",
            side_effect=lambda doctype, filters, fieldname=None: (
                "DOC-2026-0001"
                if doctype == "Document Version"
                else "Conflicting Document Title"
            ),
        ):
            with self.assertRaisesRegex(
                frappe.ValidationError,
                "DOC-2026-0001 \\(Conflicting Document Title\\)",
            ):
                document.validate_duplicate_files()

    def test_duplicate_version_numbers_are_rejected(self):
        document = _document(
            "DOC-2026-0001",
            [
                {
                    "doctype": "Document Version",
                    "version_number": "1",
                    "release_date": "2026-06-13",
                    "attachment": "/private/files/one.pdf",
                },
                {
                    "doctype": "Document Version",
                    "version_number": "1",
                    "release_date": "2026-06-14",
                    "attachment": "/private/files/two.pdf",
                },
            ],
        )

        with self.assertRaisesRegex(
            frappe.ValidationError,
            "used more than once",
        ):
            document.validate_version_numbers()

    def test_owner_keeps_access_when_roles_are_restricted(self):
        document = _document("DOC-2026-0001", [])
        document.owner = "owner@example.com"
        document.only_me = 0
        document.roles_with_access = [{"role": "Restricted Role"}]

        with patch(
            "document_management.frappe_document_management.doctype.document.document.frappe.get_roles",
            return_value=["All"],
        ):
            self.assertTrue(has_permission(document, "read", user=document.owner))
            self.assertTrue(has_permission(document, "write", user=document.owner))
            self.assertFalse(has_permission(document, "delete", user=document.owner))

    def test_permission_query_always_includes_owner(self):
        with (
            patch(
                "document_management.frappe_document_management.doctype.document.document.frappe.get_roles",
                return_value=["All"],
            ),
            patch(
                "document_management.frappe_document_management.doctype.document.document.frappe.db.escape",
                side_effect=lambda value: f"'{value}'",
            ),
        ):
            condition = get_permission_query_conditions("owner@example.com")

        self.assertIn("`tabDocument`.owner = 'owner@example.com'", condition)
        self.assertIn("ds.read = 1", condition)
