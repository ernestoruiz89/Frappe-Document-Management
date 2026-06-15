import hashlib
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe

from document_management.frappe_document_management.doctype.document.document import (
    Document,
    get_permission_query_conditions,
    has_permission,
)
from document_management.frappe_document_management.doctype.document_category.document_category import (
    get_permission_query_conditions as get_category_permission_query_conditions,
)
from document_management.frappe_document_management.doctype.document_category.document_category import (
    has_permission as category_has_permission,
)
from document_management.frappe_document_management.page.document_management_console.document_management_console import (
    move_documents_to_trash,
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
    def test_category_is_optional(self):
        category_field = frappe.get_meta("Document").get_field("category")
        self.assertFalse(category_field.reqd)

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
            self.assertTrue(has_permission(document, "share", user=document.owner))
            self.assertTrue(has_permission(document, "delete", user=document.owner))
            self.assertTrue(
                frappe.has_permission(
                    "Document",
                    "share",
                    doc=document,
                    user=document.owner,
                )
            )
            self.assertTrue(
                frappe.has_permission(
                    "Document",
                    "delete",
                    doc=document,
                    user=document.owner,
                )
            )

    def test_normal_user_cannot_share_or_delete_another_users_document(self):
        document = _document("DOC-2026-0001", [])
        document.owner = "owner@example.com"

        with patch(
            "document_management.frappe_document_management.doctype.document.document.frappe.get_roles",
            return_value=["All"],
        ):
            self.assertFalse(
                has_permission(document, "share", user="other@example.com")
            )
            self.assertFalse(
                has_permission(document, "delete", user="other@example.com")
            )
            self.assertFalse(
                frappe.has_permission(
                    "Document",
                    "share",
                    doc=document,
                    user="other@example.com",
                )
            )
            self.assertFalse(
                frappe.has_permission(
                    "Document",
                    "delete",
                    doc=document,
                    user="other@example.com",
                )
            )

    def test_system_manager_can_share_and_delete_other_users_documents(self):
        document = _document("DOC-2026-0001", [])
        document.owner = "owner@example.com"

        with patch(
            "document_management.frappe_document_management.doctype.document.document.frappe.get_roles",
            return_value=["All", "System Manager"],
        ):
            self.assertTrue(
                has_permission(document, "share", user="manager@example.com")
            )
            self.assertTrue(
                has_permission(document, "delete", user="manager@example.com")
            )

    def test_department_restriction_grants_document_read_access(self):
        document = _document("DOC-2026-0001", [])
        document.owner = "owner@example.com"
        document.only_me = 0
        document.roles_with_access = []
        document.departments_with_access = [
            frappe._dict(department="Operations")
        ]

        with (
            patch(
                "document_management.frappe_document_management.doctype.document.document.frappe.get_roles",
                return_value=["All"],
            ),
            patch(
                "document_management.frappe_document_management.doctype.document.document.frappe.db.exists",
                return_value=False,
            ),
            patch(
                "document_management.frappe_document_management.doctype.document.document.get_user_departments",
                return_value={"Operations"},
            ),
        ):
            self.assertTrue(
                has_permission(document, "read", user="employee@example.com")
            )

    def test_department_restriction_denies_user_from_another_department(self):
        document = _document("DOC-2026-0001", [])
        document.owner = "owner@example.com"
        document.only_me = 0
        document.roles_with_access = []
        document.departments_with_access = [
            frappe._dict(department="Operations")
        ]

        with (
            patch(
                "document_management.frappe_document_management.doctype.document.document.frappe.get_roles",
                return_value=["All"],
            ),
            patch(
                "document_management.frappe_document_management.doctype.document.document.frappe.db.exists",
                return_value=False,
            ),
            patch(
                "document_management.frappe_document_management.doctype.document.document.get_user_departments",
                return_value={"Finance"},
            ),
        ):
            self.assertFalse(
                has_permission(document, "read", user="employee@example.com")
            )

    def test_document_inherits_department_access_from_category(self):
        document = _document("DOC-2026-0001", [])
        document.owner = "owner@example.com"
        document.only_me = 0
        document.roles_with_access = []
        document.departments_with_access = []
        category = frappe._dict(
            owner="category-owner@example.com",
            only_me=0,
            roles_with_access=[],
            departments_with_access=[
                frappe._dict(department="Operations")
            ],
        )

        with (
            patch(
                "document_management.frappe_document_management.doctype.document.document.frappe.get_roles",
                return_value=["All"],
            ),
            patch(
                "document_management.frappe_document_management.doctype.document.document.frappe.db.exists",
                return_value=False,
            ),
            patch(
                "document_management.frappe_document_management.doctype.document.document.frappe.get_cached_doc",
                return_value=category,
            ),
            patch(
                "document_management.frappe_document_management.doctype.document.document.get_user_departments",
                return_value={"Operations"},
            ),
        ):
            self.assertTrue(
                has_permission(document, "read", user="employee@example.com")
            )

    def test_department_restriction_applies_to_category_access(self):
        category = frappe._dict(
            owner="owner@example.com",
            only_me=0,
            roles_with_access=[],
            departments_with_access=[
                frappe._dict(department="Operations")
            ],
        )

        with (
            patch(
                "document_management.frappe_document_management.doctype.document_category.document_category.frappe.get_roles",
                return_value=["All"],
            ),
            patch(
                "document_management.frappe_document_management.doctype.document_category.document_category.get_user_departments",
                return_value={"Operations"},
            ),
        ):
            self.assertTrue(
                category_has_permission(
                    category,
                    "read",
                    user="employee@example.com",
                )
            )

    def test_move_to_trash_requires_delete_permission(self):
        document = frappe._dict(name="DOC-1", is_deleted=0)
        document.db_set = MagicMock()

        with (
            patch(
                "document_management.frappe_document_management.page.document_management_console.document_management_console._authorized_documents",
                return_value=[document],
            ) as authorized,
            patch(
                "document_management.frappe_document_management.page.document_management_console.document_management_console._enqueue_index_refresh",
                return_value="job-1",
            ),
        ):
            move_documents_to_trash(["DOC-1"])

        authorized.assert_called_once_with(["DOC-1"], "delete")

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
        self.assertIn("`tabDocument Department Access`", condition)
        self.assertIn("`tabEmployee`", condition)
        self.assertIn("access_employee.user_id = 'owner@example.com'", condition)
        self.assertIn("`tabDocument`.category IS NULL", condition)
        self.assertIn("`tabDocument`.category = ''", condition)

    def test_category_permission_query_includes_department_membership(self):
        with (
            patch(
                "document_management.frappe_document_management.doctype.document_category.document_category.frappe.get_roles",
                return_value=["All"],
            ),
            patch(
                "document_management.frappe_document_management.doctype.document_category.document_category.frappe.db.escape",
                side_effect=lambda value: f"'{value}'",
            ),
        ):
            condition = get_category_permission_query_conditions(
                "employee@example.com"
            )

        self.assertIn("`tabDocument Department Access`", condition)
        self.assertIn("`tabEmployee`", condition)
        self.assertIn(
            "access_employee.user_id = 'employee@example.com'",
            condition,
        )
