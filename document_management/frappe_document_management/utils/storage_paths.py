import os
import re
import shutil
from pathlib import Path

import frappe
from frappe.utils import get_datetime, now_datetime


TOKEN_PATTERN = re.compile(r"{([a-zA-Z_][a-zA-Z0-9_]*)}")
SAFE_SEGMENT_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")
ALLOWED_TOKENS = {
    "category",
    "department",
    "document",
    "document_code",
    "month",
    "status",
    "year",
}


def validate_storage_template(template):
    template = (template or "").strip()
    if not template:
        return
    normalized = template.replace("\\", "/")
    if normalized.startswith("/") or normalized.startswith(".."):
        frappe.throw("File Storage Path Template must be a relative path.")
    parts = [part for part in normalized.split("/") if part]
    if any(part == ".." for part in parts):
        frappe.throw("File Storage Path Template cannot contain '..'.")
    unknown = sorted(
        token
        for token in TOKEN_PATTERN.findall(normalized)
        if token not in ALLOWED_TOKENS
    )
    if unknown:
        frappe.throw(
            "Unknown File Storage Path Template token(s): "
            + ", ".join(unknown)
        )


def storage_template():
    try:
        template = frappe.db.get_single_value(
            "Document Management Settings",
            "file_storage_path_template",
        )
    except Exception:
        return ""
    return (template or "").strip()


def private_files_root():
    return Path(frappe.get_site_path("private", "files")).resolve()


def private_file_path(file_url):
    file_url = (file_url or "").strip()
    prefix = "/private/files/"
    if not file_url.startswith(prefix):
        return None
    relative = file_url[len(prefix):].strip("/")
    if not relative:
        return None
    root = private_files_root()
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path


def _clean_segment(value, fallback):
    value = str(value or "").strip() or fallback
    value = SAFE_SEGMENT_PATTERN.sub("_", value).strip("._-")
    return value[:80] or fallback


def _document_datetime(doc):
    value = doc.get("creation") or now_datetime()
    try:
        return get_datetime(value)
    except Exception:
        return now_datetime()


def render_storage_relative_path(doc):
    template = storage_template()
    if not template:
        return ""
    validate_storage_template(template)
    dt = _document_datetime(doc)
    tokens = {
        "category": _clean_segment(doc.get("category"), "uncategorized"),
        "department": _clean_segment(doc.get("department"), "no-department"),
        "document": _clean_segment(doc.get("name"), "new-document"),
        "document_code": _clean_segment(
            doc.get("document_code") or doc.get("name"),
            "no-code",
        ),
        "month": f"{dt.month:02d}",
        "status": _clean_segment(doc.get("status"), "no-status"),
        "year": f"{dt.year:04d}",
    }
    rendered = TOKEN_PATTERN.sub(
        lambda match: tokens.get(match.group(1), ""),
        template.replace("\\", "/"),
    )
    segments = [
        _clean_segment(segment, "documents")
        for segment in rendered.split("/")
        if segment and segment != "."
    ]
    if not segments:
        return ""
    return "/".join(segments)


def _target_path(source_path, relative_dir):
    root = private_files_root()
    directory = (root / relative_dir).resolve()
    directory.relative_to(root)
    filename = source_path.name
    target = directory / filename
    if not target.exists() or target.resolve() == source_path.resolve():
        return target
    stem = source_path.stem
    suffix = source_path.suffix
    for index in range(1, 1000):
        candidate = directory / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
    frappe.throw("Unable to allocate a unique document storage filename.")


def _url_from_path(path):
    relative = path.resolve().relative_to(private_files_root()).as_posix()
    return f"/private/files/{relative}"


def _has_shared_blob(file_doc):
    if not file_doc.content_hash:
        return False
    return bool(
        frappe.db.exists(
            "File",
            {
                "content_hash": file_doc.content_hash,
                "name": ["!=", file_doc.name],
            },
        )
    )


def _register_rollback_for_move(source_path, target_path, copied):
    def rollback():
        try:
            if copied:
                if target_path.exists():
                    target_path.unlink()
            elif target_path.exists():
                source_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(target_path), str(source_path))
        except Exception:
            frappe.log_error(
                title="Document Storage Rollback Error",
                message=frappe.get_traceback(),
            )

    frappe.db.after_rollback.add(rollback)


def organize_file_for_document(file_doc, doc):
    relative_dir = render_storage_relative_path(doc)
    if not relative_dir:
        return file_doc
    source_path = private_file_path(file_doc.file_url)
    if not source_path or not source_path.exists():
        return file_doc
    target_path = _target_path(source_path, relative_dir)
    if target_path.resolve() == source_path.resolve():
        return file_doc

    target_path.parent.mkdir(parents=True, exist_ok=True)
    copied = _has_shared_blob(file_doc)
    try:
        if copied:
            shutil.copy2(str(source_path), str(target_path))
        else:
            shutil.move(str(source_path), str(target_path))
    except Exception:
        if target_path.exists() and target_path.resolve() != source_path.resolve():
            try:
                target_path.unlink()
            except OSError:
                pass
        raise

    new_url = _url_from_path(target_path)
    old_url = file_doc.file_url
    _register_rollback_for_move(source_path, target_path, copied)
    file_doc.db_set(
        {
            "file_url": new_url,
            "file_name": os.path.basename(target_path),
        },
        update_modified=False,
    )
    file_doc.file_url = new_url
    file_doc.file_name = os.path.basename(target_path)
    frappe.logger("document_storage").info(
        "Organized file %s from %s to %s",
        file_doc.name,
        old_url,
        new_url,
    )
    return file_doc


def organize_file_for_version(file_doc, doc, version, fieldname):
    file_doc = organize_file_for_document(file_doc, doc)
    if version and fieldname:
        version.set(fieldname, file_doc.file_url)
    return file_doc


def organize_document_files(doc):
    if not storage_template():
        return
    for version in doc.get("versions") or []:
        for fieldname in ("attachment", "preview_attachment"):
            file_url = version.get(fieldname)
            if not file_url or file_url.endswith("/pending-upload"):
                continue
            file_name = frappe.db.get_value(
                "File",
                {
                    "file_url": file_url,
                    "attached_to_doctype": "Document Version",
                    "attached_to_name": version.name,
                    "attached_to_field": fieldname,
                },
                "name",
            )
            if not file_name:
                continue
            file_doc = frappe.get_doc("File", file_name)
            organize_file_for_version(file_doc, doc, version, fieldname)
