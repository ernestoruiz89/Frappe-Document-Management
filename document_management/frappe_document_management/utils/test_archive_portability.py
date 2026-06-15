import uuid
from unittest import TestCase
from unittest.mock import patch

import frappe

from document_management.frappe_document_management.page.document_management_console.document_management_console import (
    quick_upload,
)
from document_management.frappe_document_management.utils.archive_lifecycle import (
    permanently_delete_document,
    purge_expired_exports,
    purge_expired_trash,
)
from document_management.frappe_document_management.utils.archive_portability import (
    build_document_archive,
    restore_document_archive,
)


class TestArchivePortability(TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        self.original_form_dict = frappe.local.form_dict
        self.original_uploaded_file = getattr(frappe.local, "uploaded_file", None)
        self.original_uploaded_filename = getattr(
            frappe.local,
            "uploaded_filename",
            None,
        )
        self.document_names = []

    def tearDown(self):
        frappe.set_user("Administrator")
        for document_name in self.document_names:
            if frappe.db.exists("Document", document_name):
                permanently_delete_document(document_name)
        frappe.db.commit()
        frappe.local.form_dict = self.original_form_dict
        frappe.local.uploaded_file = self.original_uploaded_file
        frappe.local.uploaded_filename = self.original_uploaded_filename

    def _upload(self, title, content):
        frappe.local.form_dict = frappe._dict()
        frappe.local.uploaded_filename = f"{uuid.uuid4().hex}.txt"
        frappe.local.uploaded_file = content
        with patch(
            "document_management.frappe_document_management.doctype.document.document.Document.enqueue_processing"
        ):
            result = quick_upload(title=title)
        self.document_names.append(result["docname"])
        frappe.db.commit()
        return result["docname"]

    def test_export_delete_and_restore_round_trip(self):
        content = f"portable-{uuid.uuid4().hex}".encode()
        document_name = self._upload("Portable archive", content)
        version = frappe.get_all(
            "Document Version",
            filters={"parent": document_name},
            fields=["name", "version_number"],
            limit=1,
        )[0]
        frappe.get_doc(
            {
                "doctype": "Document Page",
                "document": document_name,
                "document_version": version.name,
                "version_number": version.version_number,
                "page_number": 1,
                "content": "Portable OCR page",
                "content_hash": "portable-page-hash",
                "extraction_method": "test",
            }
        ).insert(ignore_permissions=True)
        frappe.get_doc(
            {
                "doctype": "DocShare",
                "share_doctype": "Document",
                "share_name": document_name,
                "user": "Guest",
                "read": 1,
            }
        ).insert(ignore_permissions=True)
        archive, manifest = build_document_archive([document_name])

        self.assertEqual(manifest["documents"][0]["source_name"], document_name)
        permanently_delete_document(document_name)
        self.document_names.remove(document_name)
        frappe.db.commit()

        with patch(
            "document_management.frappe_document_management.doctype.document.document.Document.enqueue_processing"
        ):
            result = restore_document_archive(archive)
        restored_name = result["documents"][0]
        self.document_names.append(restored_name)
        restored = frappe.get_doc("Document", restored_name)

        self.assertEqual(restored.title, "Portable archive")
        self.assertEqual(len(restored.versions), 1)
        self.assertEqual(restored.versions[0].file_size, len(content))
        self.assertEqual(restored.owner, "Administrator")
        restored_page = frappe.get_value(
            "Document Page",
            {"document": restored.name},
            ["content", "document_version"],
            as_dict=True,
        )
        self.assertEqual(restored_page.content, "Portable OCR page")
        self.assertEqual(
            restored_page.document_version,
            restored.versions[0].name,
        )
        self.assertTrue(
            frappe.db.exists(
                "DocShare",
                {
                    "share_doctype": "Document",
                    "share_name": restored.name,
                    "user": "Guest",
                    "read": 1,
                },
            )
        )

    def test_restore_fails_closed_when_required_role_is_missing(self):
        content = f"secure-{uuid.uuid4().hex}".encode()
        document_name = self._upload("Secure archive", content)
        document = frappe.get_doc("Document", document_name)
        document.append("roles_with_access", {"role": "System Manager"})
        document.save(ignore_permissions=True)
        archive, _ = build_document_archive([document_name])

        with (
            patch(
                "document_management.frappe_document_management.utils.archive_portability.frappe.db.exists",
                side_effect=lambda doctype, name: (
                    False if doctype == "Role" and name == "System Manager" else True
                ),
            ),
            self.assertRaisesRegex(
                frappe.ValidationError,
                "Archive prerequisites are missing",
            ),
        ):
            restore_document_archive(archive)

    def test_expired_export_file_is_purged(self):
        path = frappe.get_site_path(
            "private",
            "files",
            f"document-archive-test-{uuid.uuid4().hex}.zip",
        )
        with open(path, "wb") as handle:
            handle.write(b"test archive")
        filename = path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        file_doc = frappe.get_doc(
            {
                "doctype": "File",
                "file_name": filename,
                "file_url": f"/private/files/{filename}",
                "attached_to_doctype": "Document Management Settings",
                "attached_to_name": "Document Management Settings",
                "is_private": 1,
            }
        ).insert(ignore_permissions=True)
        frappe.db.set_value(
            "File",
            file_doc.name,
            "creation",
            "2020-01-01 00:00:00",
            update_modified=False,
        )
        settings = frappe.get_single("Document Management Settings")
        original_retention = settings.export_retention_hours
        settings.export_retention_hours = 1
        settings.save(ignore_permissions=True)
        frappe.db.commit()
        try:
            deleted = purge_expired_exports()
        finally:
            settings.reload()
            settings.export_retention_hours = original_retention
            settings.save(ignore_permissions=True)
            frappe.db.commit()

        self.assertIn(file_doc.name, deleted)
        self.assertFalse(frappe.db.exists("File", file_doc.name))

    def test_retention_purges_expired_document_and_file(self):
        document_name = self._upload(
            "Expired trash",
            f"expired-{uuid.uuid4().hex}".encode(),
        )
        version_name = frappe.db.get_value(
            "Document Version",
            {"parent": document_name},
            "name",
        )
        file_name = frappe.db.get_value(
            "File",
            {
                "attached_to_doctype": "Document Version",
                "attached_to_name": version_name,
            },
            "name",
        )
        frappe.db.set_value(
            "Document",
            document_name,
            {
                "is_deleted": 1,
                "deleted_at": "2020-01-01 00:00:00",
            },
        )
        settings = frappe.get_single("Document Management Settings")
        original_retention = settings.trash_retention_days
        settings.trash_retention_days = 1
        settings.save(ignore_permissions=True)
        frappe.db.commit()
        try:
            deleted = purge_expired_trash()
        finally:
            settings.reload()
            settings.trash_retention_days = original_retention
            settings.save(ignore_permissions=True)
            frappe.db.commit()

        self.document_names.remove(document_name)
        self.assertIn(document_name, deleted)
        self.assertFalse(frappe.db.exists("Document", document_name))
        self.assertFalse(frappe.db.exists("File", file_name))
