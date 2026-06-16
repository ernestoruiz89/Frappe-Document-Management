import tempfile
import uuid
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import frappe

from document_management.frappe_document_management.utils.folder_ingestion import (
    ingest_configured_folder,
)
from document_management.frappe_document_management.utils.archive_lifecycle import (
    permanently_delete_document,
)


class TestFolderIngestion(TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        self.document_names = []
        self.settings = frappe.get_single("Document Management Settings")
        self.original_values = {
            "enable_folder_ingestion": self.settings.get("enable_folder_ingestion"),
            "ingestion_folder_path": self.settings.get("ingestion_folder_path"),
            "ingestion_done_folder_path": self.settings.get(
                "ingestion_done_folder_path"
            ),
            "ingestion_error_folder_path": self.settings.get(
                "ingestion_error_folder_path"
            ),
        }

    def tearDown(self):
        frappe.set_user("Administrator")
        for document_name in self.document_names:
            if frappe.db.exists("Document", document_name):
                permanently_delete_document(document_name)
        self.settings.reload()
        for fieldname, value in self.original_values.items():
            self.settings.set(fieldname, value)
        self.settings.save(ignore_permissions=True)
        frappe.db.commit()

    def test_ingest_configured_folder_moves_imported_file_to_done(self):
        content = f"folder-ingestion-{uuid.uuid4().hex}".encode()
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "inbox"
            done = Path(temp_dir) / "done"
            error = Path(temp_dir) / "error"
            source.mkdir()
            incoming = source / "folder-document.txt"
            incoming.write_bytes(content)

            self.settings.enable_folder_ingestion = 1
            self.settings.ingestion_folder_path = str(source)
            self.settings.ingestion_done_folder_path = str(done)
            self.settings.ingestion_error_folder_path = str(error)
            self.settings.save(ignore_permissions=True)
            frappe.db.commit()

            with patch(
                "document_management.frappe_document_management.doctype.document.document.Document.enqueue_processing"
            ):
                result = ingest_configured_folder()

            self.assertEqual(result["processed"], 1)
            self.assertEqual(len(result["imported"]), 1)
            document_name = result["imported"][0]["document"]
            self.document_names.append(document_name)
            self.assertFalse(incoming.exists())
            self.assertTrue((done / "folder-document.txt").exists())

            document = frappe.get_doc("Document", document_name)
            self.assertEqual(document.title, "folder-document")
            self.assertEqual(document.versions[0].file_size, len(content))

    def test_ingest_configured_folder_moves_failed_file_to_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "inbox"
            done = Path(temp_dir) / "done"
            error = Path(temp_dir) / "error"
            source.mkdir()
            incoming = source / "bad.pdf"
            incoming.write_bytes(b"not-a-pdf")

            self.settings.enable_folder_ingestion = 1
            self.settings.ingestion_folder_path = str(source)
            self.settings.ingestion_done_folder_path = str(done)
            self.settings.ingestion_error_folder_path = str(error)
            self.settings.save(ignore_permissions=True)
            frappe.db.commit()

            result = ingest_configured_folder()

            self.assertEqual(result["processed"], 1)
            self.assertEqual(len(result["failed"]), 1)
            self.assertFalse(incoming.exists())
            self.assertTrue((error / "bad.pdf").exists())
            self.assertTrue((error / "bad.pdf.error.txt").exists())
