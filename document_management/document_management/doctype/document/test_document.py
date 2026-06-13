from unittest.mock import patch

import frappe

from document_management.document_management.doctype.document.document import Document


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


def test_metadata_save_excludes_versions_from_the_same_document():
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
        "document_management.document_management.doctype.document.document.frappe.db.get_value",
        return_value=None,
    ) as get_value:
        document.validate_duplicate_files()

    filters = get_value.call_args.args[1]
    assert filters["parent"] == ["!=", document.name]
    assert filters["parenttype"] == "Document"
    assert filters["parentfield"] == "versions"


def test_same_file_in_two_versions_of_one_document_is_rejected():
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

    try:
        document.validate_duplicate_files()
    except frappe.ValidationError as exc:
        assert "same file more than once" in str(exc)
    else:
        raise AssertionError("Duplicate versions were accepted")


def test_same_file_in_another_document_is_rejected():
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
        "document_management.document_management.doctype.document.document.frappe.db.get_value",
        side_effect=lambda doctype, filters, fieldname=None: (
            "DOC-2026-0001"
            if doctype == "Document Version"
            else "Conflicting Document Title"
        ),
    ):
        try:
            document.validate_duplicate_files()
        except frappe.ValidationError as exc:
            assert "identical file already exists" in str(exc)
            assert "DOC-2026-0001 (Conflicting Document Title)" in str(exc)
        else:
            raise AssertionError("Cross-document duplicate was accepted")
