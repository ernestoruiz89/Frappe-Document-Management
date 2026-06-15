import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import frappe

from document_management.frappe_document_management.utils.archive_sanity import (
    _check_file_references,
    _check_stored_file,
)


class TestArchiveSanity(TestCase):
    def test_file_audit_accepts_matching_checksum(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "document.pdf"
            path.write_bytes(b"document")
            issues = []

            with patch(
                "document_management.frappe_document_management.utils.archive_sanity.get_file_path",
                return_value=str(path),
            ):
                _check_stored_file(
                    issues,
                    file_url="/private/files/document.pdf",
                    expected_checksum=(
                        "43cc23fa52b87b4cc1d02b5b114154151d6adddb17c9"
                        "fddc06b027fa99e24008"
                    ),
                    document="DOC-1",
                    version="VER-1",
                    kind="original",
                )

        self.assertEqual(issues, [])

    def test_file_audit_reports_missing_file(self):
        issues = []
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "missing.pdf"
            with patch(
                "document_management.frappe_document_management.utils.archive_sanity.get_file_path",
                return_value=str(missing),
            ):
                _check_stored_file(
                    issues,
                    file_url="/private/files/missing.pdf",
                    expected_checksum="abc",
                    document="DOC-1",
                    version="VER-1",
                    kind="preview",
                )

        self.assertEqual(issues[0]["code"], "preview_missing")

    def test_file_audit_reports_stale_version_attachment(self):
        issues = []
        versions = {
            "VER-1": frappe._dict(
                parent="DOC-1",
                attachment="/private/files/current.pdf",
                preview_attachment="/private/files/current-preview.pdf",
            )
        }
        files = [
            frappe._dict(
                name="FILE-1",
                file_url="/private/files/old.pdf",
                attached_to_doctype="Document Version",
                attached_to_name="VER-1",
            )
        ]

        with patch(
            "document_management.frappe_document_management.utils.archive_sanity.frappe.get_all",
            return_value=files,
        ):
            _check_file_references(issues, {"DOC-1"}, versions)

        self.assertEqual(issues[0]["code"], "stale_file_reference")

    def test_file_audit_reports_missing_file_record(self):
        issues = []
        versions = {
            "VER-1": frappe._dict(
                parent="DOC-1",
                attachment="/private/files/current.pdf",
                preview_attachment="",
            )
        }

        with patch(
            "document_management.frappe_document_management.utils.archive_sanity.frappe.get_all",
            return_value=[],
        ):
            _check_file_references(issues, {"DOC-1"}, versions)

        self.assertEqual(issues[0]["code"], "file_record_missing")
