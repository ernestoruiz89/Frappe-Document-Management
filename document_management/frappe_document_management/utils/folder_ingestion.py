import shutil
from pathlib import Path

import frappe

from document_management.frappe_document_management.page.document_management_console.document_management_console import (
    SUPPORTED_EXTENSIONS,
    quick_upload,
)


SKIP_SUFFIXES = {".part", ".tmp", ".crdownload"}


def _settings():
    try:
        settings = frappe.get_single("Document Management Settings")
    except Exception:
        return None
    if not int(settings.get("enable_folder_ingestion") or 0):
        return None
    source = (settings.get("ingestion_folder_path") or "").strip()
    if not source:
        return None
    source_path = Path(source).expanduser().resolve()
    done_path = Path(
        (settings.get("ingestion_done_folder_path") or "").strip()
        or source_path / "done"
    ).expanduser().resolve()
    error_path = Path(
        (settings.get("ingestion_error_folder_path") or "").strip()
        or source_path / "error"
    ).expanduser().resolve()
    return source_path, done_path, error_path


def _unique_target(directory, filename):
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / filename
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    for index in range(1, 1000):
        candidate = directory / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
    frappe.throw(f"Unable to allocate a destination filename for {filename}.")


def _candidate_files(source_path, done_path, error_path, limit):
    if not source_path.is_dir():
        frappe.logger("document_ingestion").warning(
            "Document ingestion source folder does not exist: %s",
            source_path,
        )
        return []
    skip_dirs = {done_path, error_path}
    files = []
    for path in sorted(source_path.iterdir(), key=lambda item: item.name.lower()):
        resolved = path.resolve()
        if resolved in skip_dirs or path.is_dir():
            continue
        if path.name.startswith(".") or path.suffix.lower() in SKIP_SUFFIXES:
            continue
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        files.append(path)
        if len(files) >= limit:
            break
    return files


def _move_to(directory, path):
    target = _unique_target(directory, path.name)
    shutil.move(str(path), str(target))
    return target


def _write_error_note(error_path, failed_path, error):
    note = _unique_target(error_path, f"{failed_path.name}.error.txt")
    note.write_text(str(error)[-4000:], encoding="utf-8")
    return note


def ingest_file(path):
    path = Path(path).expanduser().resolve()
    content = path.read_bytes()
    original_file = getattr(frappe.local, "uploaded_file", None)
    original_filename = getattr(frappe.local, "uploaded_filename", None)
    original_form = frappe.local.form_dict
    try:
        frappe.local.uploaded_file = content
        frappe.local.uploaded_filename = path.name
        frappe.local.form_dict = frappe._dict()
        return quick_upload(title=path.stem)
    finally:
        frappe.local.uploaded_file = original_file
        frappe.local.uploaded_filename = original_filename
        frappe.local.form_dict = original_form


def ingest_configured_folder(limit=50):
    configured = _settings()
    if not configured:
        return {"processed": 0, "imported": [], "failed": []}

    source_path, done_path, error_path = configured
    try:
        limit = min(max(int(limit or 50), 1), 500)
    except (TypeError, ValueError):
        limit = 50

    imported = []
    failed = []
    original_user = frappe.session.user
    frappe.set_user("Administrator")
    try:
        for path in _candidate_files(source_path, done_path, error_path, limit):
            try:
                result = ingest_file(path)
                frappe.db.commit()
                target = _move_to(done_path, path)
                imported.append(
                    {
                        "file": path.name,
                        "document": result["docname"],
                        "destination": str(target),
                    }
                )
            except Exception as exc:
                frappe.db.rollback()
                try:
                    target = _move_to(error_path, path)
                    note = _write_error_note(
                        error_path,
                        target,
                        frappe.get_traceback() or exc,
                    )
                except Exception:
                    target = path
                    note = None
                    frappe.log_error(
                        title="Document Folder Ingestion Move Error",
                        message=frappe.get_traceback(),
                    )
                failed.append(
                    {
                        "file": path.name,
                        "destination": str(target),
                        "error_note": str(note) if note else None,
                        "error": str(exc),
                    }
                )
                frappe.log_error(
                    title="Document Folder Ingestion Error",
                    message=frappe.get_traceback(),
                )
    finally:
        frappe.set_user(original_user)

    return {
        "processed": len(imported) + len(failed),
        "imported": imported,
        "failed": failed,
    }
