import os
from pathlib import Path
from urllib.parse import unquote

import frappe
from frappe.utils.file_manager import get_file_path

from document_management.frappe_document_management.utils.file_crypto import (
    sha256_path,
)


def _sha256_file(path, block_size=1024 * 1024):
    return sha256_path(path)


def _issue(issues, severity, code, message, **context):
    issues.append(
        {
            "severity": severity,
            "code": code,
            "message": message,
            **context,
        }
    )


def _check_stored_file(
    issues,
    *,
    file_url,
    expected_checksum,
    document,
    version,
    kind,
):
    if not file_url:
        return
    try:
        path = get_file_path(file_url)
    except Exception:
        path = ""
    context = {
        "document": document,
        "version": version,
        "file_url": file_url,
    }
    if not path or not os.path.isfile(path):
        _issue(
            issues,
            "error",
            f"{kind}_missing",
            f"{kind.title()} file does not exist.",
            **context,
        )
        return
    if not expected_checksum:
        _issue(
            issues,
            "warning",
            f"{kind}_checksum_missing",
            f"{kind.title()} checksum is missing.",
            **context,
        )
        return
    actual_checksum = _sha256_file(path)
    if actual_checksum != expected_checksum:
        _issue(
            issues,
            "error",
            f"{kind}_checksum_mismatch",
            f"{kind.title()} checksum does not match the stored value.",
            expected_checksum=expected_checksum,
            actual_checksum=actual_checksum,
            **context,
        )


def _check_file_references(issues, document_names, versions):
    version_names = set(versions)
    expected_by_version = {}
    expected_by_document = {}
    matched_version_urls = set()
    for version_name, version in versions.items():
        expected_urls = {
            url
            for url in (
                getattr(version, "attachment", None),
                getattr(version, "preview_attachment", None),
            )
            if url
        }
        expected_by_version[version_name] = expected_urls
        expected_by_document.setdefault(version.parent, set()).update(expected_urls)

    files = frappe.get_all(
        "File",
        filters={
            "attached_to_doctype": ["in", ["Document", "Document Version"]],
        },
        fields=[
            "name",
            "file_url",
            "attached_to_doctype",
            "attached_to_name",
        ],
        limit_page_length=0,
    )
    for file_row in files:
        if file_row.attached_to_doctype == "Document":
            target_exists = file_row.attached_to_name in document_names
            expected_urls = expected_by_document.get(
                file_row.attached_to_name,
                set(),
            )
        else:
            target_exists = file_row.attached_to_name in version_names
            expected_urls = expected_by_version.get(
                file_row.attached_to_name,
                set(),
            )
        if not target_exists:
            _issue(
                issues,
                "warning",
                "orphaned_file_reference",
                "File references a document record that does not exist.",
                file=file_row.name,
                file_url=file_row.file_url,
                attached_to_doctype=file_row.attached_to_doctype,
                attached_to_name=file_row.attached_to_name,
            )
            continue
        if file_row.file_url not in expected_urls:
            _issue(
                issues,
                "warning",
                "stale_file_reference",
                "File is attached to a document record but is not a current original or preview.",
                file=file_row.name,
                file_url=file_row.file_url,
                attached_to_doctype=file_row.attached_to_doctype,
                attached_to_name=file_row.attached_to_name,
            )
            continue

        if file_row.attached_to_doctype == "Document Version":
            matched_version_urls.add(
                (file_row.attached_to_name, file_row.file_url)
            )
        else:
            for version_name, version in versions.items():
                if (
                    version.parent == file_row.attached_to_name
                    and file_row.file_url in expected_by_version[version_name]
                ):
                    matched_version_urls.add((version_name, file_row.file_url))

    for version_name, expected_urls in expected_by_version.items():
        version = versions[version_name]
        for file_url in expected_urls:
            if (version_name, file_url) in matched_version_urls:
                continue
            _issue(
                issues,
                "warning",
                "file_record_missing",
                "Version attachment has no matching File record.",
                document=version.parent,
                version=version_name,
                file_url=file_url,
            )


def _physical_file_url(path, root, private):
    relative = path.relative_to(root).as_posix()
    prefix = "/private/files" if private else "/files"
    return f"{prefix}/{relative}"


def _check_physical_orphans(issues):
    known_urls = {
        unquote(row.file_url)
        for row in frappe.get_all(
            "File",
            filters={"is_folder": 0},
            fields=["file_url"],
            limit_page_length=0,
        )
        if row.file_url
    }
    roots = (
        (Path(frappe.get_site_path("private", "files")), True),
        (Path(frappe.get_site_path("public", "files")), False),
    )
    for root, private in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.name.startswith("."):
                continue
            file_url = _physical_file_url(path, root, private)
            if unquote(file_url) in known_urls:
                continue
            _issue(
                issues,
                "warning",
                "physical_file_orphaned",
                "Physical file has no matching File record.",
                file_url=file_url,
                path=str(path),
            )


def check_document_archive():
    issues = []
    documents = frappe.get_all(
        "Document",
        fields=["name", "ocr_status", "ocr_content", "is_deleted"],
        limit_page_length=0,
    )
    versions = frappe.get_all(
        "Document Version",
        fields=[
            "name",
            "parent",
            "version_number",
            "attachment",
            "file_checksum",
            "preview_attachment",
            "preview_checksum",
            "ocr_status",
            "ocr_content",
        ],
        limit_page_length=0,
    )
    document_names = {row.name for row in documents}
    versions_by_name = {row.name: row for row in versions}
    version_numbers = {}

    for version in versions:
        context = {
            "document": version.parent,
            "version": version.name,
        }
        if version.parent not in document_names:
            _issue(
                issues,
                "error",
                "orphaned_version",
                "Document version has no parent document.",
                **context,
            )
        number_key = (version.parent, str(version.version_number or "").strip())
        if not number_key[1]:
            _issue(
                issues,
                "error",
                "version_number_missing",
                "Document version has no version number.",
                **context,
            )
        elif number_key in version_numbers:
            _issue(
                issues,
                "error",
                "duplicate_version_number",
                "Document contains duplicate version numbers.",
                duplicate_of=version_numbers[number_key],
                version_number=number_key[1],
                **context,
            )
        else:
            version_numbers[number_key] = version.name

        _check_stored_file(
            issues,
            file_url=version.attachment,
            expected_checksum=version.file_checksum,
            kind="original",
            **context,
        )
        _check_stored_file(
            issues,
            file_url=version.preview_attachment,
            expected_checksum=version.preview_checksum,
            kind="preview",
            **context,
        )
        if version.ocr_status == "Completed" and not (version.ocr_content or "").strip():
            _issue(
                issues,
                "warning",
                "completed_ocr_empty",
                "OCR is marked completed but contains no text.",
                **context,
            )

    _check_file_references(issues, document_names, versions_by_name)
    _check_physical_orphans(issues)
    counts = {
        "error": sum(issue["severity"] == "error" for issue in issues),
        "warning": sum(issue["severity"] == "warning" for issue in issues),
    }
    return {
        "documents_checked": len(documents),
        "versions_checked": len(versions),
        "issues": issues,
        "counts": counts,
        "ok": not issues,
    }


@frappe.whitelist()
def run_document_archive_sanity_check():
    if "System Manager" not in frappe.get_roles():
        frappe.throw(
            "System Manager role is required.",
            frappe.PermissionError,
        )
    return check_document_archive()


def scheduled_archive_sanity_check():
    result = check_document_archive()
    if not result["issues"]:
        return
    log_result = {
        **result,
        "issues": result["issues"][:100],
        "issues_truncated": max(len(result["issues"]) - 100, 0),
    }
    frappe.log_error(
        title="Document Archive Sanity Check",
        message=frappe.as_json(log_result, indent=2),
    )
