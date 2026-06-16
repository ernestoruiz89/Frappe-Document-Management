import hashlib
import io
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import PurePosixPath

import frappe
from frappe.utils import now_datetime
from frappe.utils.file_manager import get_file_path

from document_management.frappe_document_management.utils.document_access import (
    department_doctype_exists,
)


ARCHIVE_FORMAT = "frappe-document-management"
ARCHIVE_VERSION = 2
MAX_ARCHIVE_MEMBERS = 10000
MAX_ARCHIVE_SIZE = 512 * 1024 * 1024
MAX_UNCOMPRESSED_SIZE = 2 * 1024 * 1024 * 1024
MAX_DOCUMENTS = 1000
EXPORT_ATTACHMENT_DOCTYPE = "Document Management Settings"
EXPORT_ATTACHMENT_NAME = "Document Management Settings"
PARENT_FIELDS = (
    "document_code",
    "title",
    "status",
    "category",
    "folder",
    "department",
    "party_type",
    "party_name",
    "description",
    "only_me",
)
VERSION_FIELDS = (
    "version_number",
    "release_date",
    "change_log",
    "is_markdown",
    "ocr_status",
    "ocr_content",
)
PAGE_FIELDS = (
    "version_number",
    "page_number",
    "content",
    "content_hash",
    "extraction_method",
)
TAG_FIELDS = (
    "tag_name",
    "color",
    "is_active",
    "matching_algorithm",
    "match_pattern",
)


def _sha256_file(path, block_size=1024 * 1024):
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        while block := handle.read(block_size):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def _archive_file(zf, document_name, version, kind, file_url, checksum):
    if not file_url:
        return None
    path = get_file_path(file_url)
    if not path or not os.path.isfile(path):
        frappe.throw(f"Archive source file is missing: {file_url}")
    actual_checksum, size = _sha256_file(path)
    if checksum and actual_checksum != checksum:
        frappe.throw(f"Archive source checksum mismatch: {file_url}")
    filename = os.path.basename(path)
    archive_path = (
        PurePosixPath("documents")
        / document_name
        / str(version.version_number)
        / kind
        / filename
    ).as_posix()
    zf.write(path, archive_path)
    return {
        "path": archive_path,
        "filename": filename,
        "checksum": actual_checksum,
        "size": size,
    }


def _category_definition(name):
    category = frappe.get_doc("Document Category", name)
    return {
        "name": category.name,
        "owner": category.owner,
        "only_me": category.only_me,
        "roles_with_access": [
            row.role for row in category.get("roles_with_access") or []
        ],
        "departments_with_access": [
            row.department
            for row in category.get("departments_with_access") or []
        ],
    }


def _tag_definition(name):
    tag = frappe.get_doc("Document Tag", name)
    return {field: tag.get(field) for field in TAG_FIELDS}


def _document_shares(document_name):
    fields = ["user", "read", "write", "share"]
    return frappe.get_all(
        "DocShare",
        filters={
            "share_doctype": "Document",
            "share_name": document_name,
            "everyone": 0,
        },
        fields=fields,
    )


def build_document_archive_file(document_names, target_path=None):
    names = list(dict.fromkeys(document_names or []))
    if not names:
        frappe.throw("Select at least one document.")
    if len(names) > MAX_DOCUMENTS:
        frappe.throw(f"An archive can contain at most {MAX_DOCUMENTS} documents.")

    if not target_path:
        handle = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
        target_path = handle.name
        handle.close()

    manifest = {
        "format": ARCHIVE_FORMAT,
        "version": ARCHIVE_VERSION,
        "exported_at": str(now_datetime()),
        "dependencies": {"categories": [], "tags": []},
        "documents": [],
    }
    categories = {}
    tags = {}
    try:
        with zipfile.ZipFile(
            target_path,
            "w",
            zipfile.ZIP_DEFLATED,
            allowZip64=True,
        ) as zf:
            for document_name in names:
                doc = frappe.get_doc("Document", document_name)
                doc.check_permission("read")
                entry = {
                    "source_name": doc.name,
                    "owner": doc.owner,
                    "creation": str(doc.creation),
                    "modified": str(doc.modified),
                    "metadata": {
                        field: doc.get(field)
                        for field in PARENT_FIELDS
                    },
                    "tags": [row.tag for row in doc.get("tags") or []],
                    "roles_with_access": [
                        row.role for row in doc.get("roles_with_access") or []
                    ],
                    "departments_with_access": [
                        row.department
                        for row in doc.get("departments_with_access") or []
                    ],
                    "shares": _document_shares(doc.name),
                    "pages": frappe.get_all(
                        "Document Page",
                        filters={"document": doc.name},
                        fields=list(PAGE_FIELDS),
                        order_by="page_number asc",
                    ),
                    "versions": [],
                }
                if doc.category:
                    categories[doc.category] = _category_definition(doc.category)
                for tag_name in entry["tags"]:
                    tags[tag_name] = _tag_definition(tag_name)
                for version in doc.get("versions") or []:
                    version_entry = {
                        field: version.get(field)
                        for field in VERSION_FIELDS
                    }
                    version_entry["original"] = _archive_file(
                        zf,
                        doc.name,
                        version,
                        "original",
                        version.attachment,
                        version.file_checksum,
                    )
                    version_entry["preview"] = _archive_file(
                        zf,
                        doc.name,
                        version,
                        "preview",
                        version.preview_attachment,
                        version.preview_checksum,
                    )
                    entry["versions"].append(version_entry)
                manifest["documents"].append(entry)
            manifest["dependencies"]["categories"] = list(categories.values())
            manifest["dependencies"]["tags"] = list(tags.values())
            zf.writestr(
                "manifest.json",
                json.dumps(manifest, ensure_ascii=True, indent=2, default=str),
            )
        if os.path.getsize(target_path) > MAX_ARCHIVE_SIZE:
            frappe.throw("The generated document archive is too large.")
        return target_path, manifest
    except Exception:
        if os.path.isfile(target_path):
            os.remove(target_path)
        raise


def build_document_archive(document_names):
    path, manifest = build_document_archive_file(document_names)
    try:
        with open(path, "rb") as handle:
            return handle.read(), manifest
    finally:
        if os.path.isfile(path):
            os.remove(path)


def _safe_info(zf, member):
    path = PurePosixPath(member)
    if path.is_absolute() or ".." in path.parts:
        frappe.throw("Archive contains an unsafe file path.")
    try:
        return zf.getinfo(member)
    except KeyError:
        frappe.throw(f"Archive member is missing: {member}")


def _read_manifest(zf):
    info = _safe_info(zf, "manifest.json")
    if info.file_size > 25 * 1024 * 1024:
        frappe.throw("Archive manifest is too large.")
    return json.loads(zf.read(info))


def _validate_zip(zf):
    members = zf.infolist()
    if len(members) > MAX_ARCHIVE_MEMBERS:
        frappe.throw("Document archive contains too many files.")
    total_size = 0
    for info in members:
        path = PurePosixPath(info.filename)
        if path.is_absolute() or ".." in path.parts:
            frappe.throw("Archive contains an unsafe file path.")
        total_size += info.file_size
        if total_size > MAX_UNCOMPRESSED_SIZE:
            frappe.throw("Document archive is too large.")


def _required_values(manifest):
    users = set()
    roles = set()
    departments = set()
    has_department = department_doctype_exists()
    for category in manifest.get("dependencies", {}).get("categories", []):
        users.add(category.get("owner"))
        roles.update(category.get("roles_with_access") or [])
        if has_department:
            departments.update(category.get("departments_with_access") or [])
    for entry in manifest.get("documents") or []:
        users.add(entry.get("owner"))
        users.update(share.get("user") for share in entry.get("shares") or [])
        roles.update(entry.get("roles_with_access") or [])
        if has_department:
            departments.update(entry.get("departments_with_access") or [])
        metadata = entry.get("metadata") or {}
        if has_department and metadata.get("department"):
            departments.add(metadata["department"])
    return {
        "User": {value for value in users if value},
        "Role": {value for value in roles if value},
        "Department": {value for value in departments if value},
    }


def _validate_prerequisites(manifest):
    missing = []
    for doctype, values in _required_values(manifest).items():
        for value in sorted(values):
            if not frappe.db.exists(doctype, value):
                missing.append(f"{doctype}: {value}")
    for entry in manifest.get("documents") or []:
        metadata = entry.get("metadata") or {}
        if metadata.get("party_type") and metadata.get("party_name"):
            if not frappe.db.exists(
                metadata["party_type"],
                metadata["party_name"],
            ):
                missing.append(
                    f'{metadata["party_type"]}: {metadata["party_name"]}'
                )
    if missing:
        frappe.throw(
            "Archive prerequisites are missing. Nothing was imported:\n"
            + "\n".join(missing)
        )


def _restore_dependencies(manifest):
    dependencies = manifest.get("dependencies") or {}
    has_department = department_doctype_exists()
    for definition in dependencies.get("categories") or []:
        name = definition["name"]
        if frappe.db.exists("Document Category", name):
            existing = frappe.get_doc("Document Category", name)
            existing_signature = {
                "owner": existing.owner,
                "only_me": existing.only_me,
                "roles_with_access": sorted(
                    row.role for row in existing.roles_with_access
                ),
                "departments_with_access": (
                    sorted(row.department for row in existing.departments_with_access)
                    if has_department
                    else []
                ),
            }
            expected_signature = {
                "owner": definition["owner"],
                "only_me": definition.get("only_me") or 0,
                "roles_with_access": sorted(
                    definition.get("roles_with_access") or []
                ),
                "departments_with_access": (
                    sorted(definition.get("departments_with_access") or [])
                    if has_department
                    else []
                ),
            }
            if existing_signature != expected_signature:
                frappe.throw(
                    f"Document Category security differs on target: {name}"
                )
            continue
        category = frappe.new_doc("Document Category")
        category.category_name = name
        category.owner = definition["owner"]
        category.only_me = definition.get("only_me") or 0
        for role in definition.get("roles_with_access") or []:
            category.append("roles_with_access", {"role": role})
        if has_department:
            for department in definition.get("departments_with_access") or []:
                category.append(
                    "departments_with_access",
                    {"department": department},
                )
        category.insert(ignore_permissions=True)

    for definition in dependencies.get("tags") or []:
        name = definition["tag_name"]
        if frappe.db.exists("Document Tag", name):
            continue
        tag = frappe.new_doc("Document Tag")
        for field in TAG_FIELDS:
            tag.set(field, definition.get(field))
        tag.insert(ignore_permissions=True)


def _extract_blob(zf, descriptor, version_name, fieldname):
    if not descriptor:
        return None, None
    info = _safe_info(zf, descriptor["path"])
    if info.file_size != int(descriptor["size"]):
        frappe.throw("Archive file size does not match its manifest.")
    extension = os.path.splitext(descriptor["filename"])[1]
    filename = f"archive-{frappe.generate_hash(length=20)}{extension}"
    destination = frappe.get_site_path("private", "files", filename)
    digest = hashlib.sha256()
    size = 0
    with zf.open(info) as source, open(destination, "xb") as target:
        while block := source.read(1024 * 1024):
            digest.update(block)
            size += len(block)
            target.write(block)
    if size != int(descriptor["size"]) or digest.hexdigest() != descriptor["checksum"]:
        os.remove(destination)
        frappe.throw("Archive file checksum does not match its manifest.")
    file_doc = frappe.get_doc(
        {
            "doctype": "File",
            "file_name": filename,
            "file_url": f"/private/files/{filename}",
            "attached_to_doctype": "Document Version",
            "attached_to_name": version_name,
            "attached_to_field": fieldname,
            "is_private": 1,
        }
    )
    try:
        file_doc.insert(ignore_permissions=True)
    except Exception:
        if os.path.isfile(destination):
            os.remove(destination)
        raise
    return file_doc, destination


def _restore_shares(document_name, shares):
    for share in shares or []:
        if not share.get("user"):
            continue
        share_doc = frappe.get_doc(
            {
                "doctype": "DocShare",
                "share_doctype": "Document",
                "share_name": document_name,
                "user": share["user"],
                "read": share.get("read") or 0,
                "write": share.get("write") or 0,
                "share": share.get("share") or 0,
            }
        )
        share_doc.insert(ignore_permissions=True, ignore_if_duplicate=True)


def restore_document_archive(content):
    if len(content) > MAX_ARCHIVE_SIZE:
        frappe.throw("Uploaded document archive is too large.")
    created_documents = []
    created_files = []
    archive_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as handle:
            handle.write(content)
            archive_path = handle.name
        with zipfile.ZipFile(archive_path) as zf:
            _validate_zip(zf)
            manifest = _read_manifest(zf)
            if (
                manifest.get("format") != ARCHIVE_FORMAT
                or manifest.get("version") != ARCHIVE_VERSION
            ):
                frappe.throw("Unsupported document archive format.")
            _validate_prerequisites(manifest)
            has_department = department_doctype_exists()
            _restore_dependencies(manifest)
            for entry in manifest.get("documents") or []:
                metadata = entry.get("metadata") or {}
                if not has_department:
                    metadata = {**metadata, "department": None}
                doc = frappe.new_doc("Document")
                for field in PARENT_FIELDS:
                    doc.set(field, metadata.get(field))
                doc.owner = entry["owner"]
                for tag in entry.get("tags") or []:
                    doc.append("tags", {"tag": tag})
                for role in entry.get("roles_with_access") or []:
                    doc.append("roles_with_access", {"role": role})
                if has_department:
                    for department in entry.get("departments_with_access") or []:
                        doc.append(
                            "departments_with_access",
                            {"department": department},
                        )

                version_blobs = []
                for archived_version in entry.get("versions") or []:
                    original = archived_version.get("original")
                    if not original:
                        frappe.throw(
                            "Archived document version has no original file."
                        )
                    extension = os.path.splitext(original["filename"])[1]
                    version = doc.append(
                        "versions",
                        {
                            field: archived_version.get(field)
                            for field in VERSION_FIELDS
                        },
                    )
                    version.attachment = (
                        f"/private/files/archive-import-"
                        f"{frappe.generate_hash(length=12)}{extension}"
                    )
                    version.file_checksum = original["checksum"]
                    version.file_size = original["size"]
                    version_blobs.append(
                        (
                            version,
                            original,
                            archived_version.get("preview"),
                        )
                    )
                doc.insert(ignore_permissions=True)
                created_documents.append(doc.name)
                versions_by_number = {
                    str(row.version_number): row for row in doc.versions
                }

                for version, original, preview in version_blobs:
                    original_file, original_path = _extract_blob(
                        zf,
                        original,
                        version.name,
                        "attachment",
                    )
                    created_files.append((original_file.name, original_path))
                    values = {"attachment": original_file.file_url}
                    if preview:
                        preview_file, preview_path = _extract_blob(
                            zf,
                            preview,
                            version.name,
                            "preview_attachment",
                        )
                        created_files.append((preview_file.name, preview_path))
                        values.update(
                            {
                                "preview_attachment": preview_file.file_url,
                                "preview_checksum": preview["checksum"],
                            }
                        )
                    frappe.db.set_value("Document Version", version.name, values)

                for page in entry.get("pages") or []:
                    version = versions_by_number.get(str(page["version_number"]))
                    if not version:
                        frappe.throw(
                            "Archive page references an unknown document version."
                        )
                    page_doc = frappe.new_doc("Document Page")
                    page_doc.document = doc.name
                    page_doc.document_version = version.name
                    for field in PAGE_FIELDS:
                        if field != "version_number":
                            page_doc.set(field, page.get(field))
                    page_doc.version_number = version.version_number
                    page_doc.insert(ignore_permissions=True)
                _restore_shares(doc.name, entry.get("shares"))
                doc.reload()
                doc.save(ignore_permissions=True)
                frappe.db.set_value(
                    "Document",
                    doc.name,
                    {
                        "owner": entry["owner"],
                        "creation": entry.get("creation"),
                        "modified": entry.get("modified"),
                    },
                    update_modified=False,
                )
        return {"documents": created_documents, "count": len(created_documents)}
    except Exception:
        frappe.db.rollback()
        for _, path in created_files:
            if path and os.path.isfile(path):
                os.remove(path)
        raise
    finally:
        if archive_path and os.path.isfile(archive_path):
            os.remove(archive_path)


def _register_export_file(path, filename):
    destination = frappe.get_site_path("private", "files", filename)
    shutil.move(path, destination)
    file_doc = frappe.get_doc(
        {
            "doctype": "File",
            "file_name": filename,
            "file_url": f"/private/files/{filename}",
            "attached_to_doctype": EXPORT_ATTACHMENT_DOCTYPE,
            "attached_to_name": EXPORT_ATTACHMENT_NAME,
            "is_private": 1,
        }
    )
    try:
        file_doc.insert(ignore_permissions=True)
    except Exception:
        if os.path.isfile(destination):
            os.remove(destination)
        raise
    return file_doc


@frappe.whitelist()
def export_document_archive(documents):
    if isinstance(documents, str):
        documents = json.loads(documents)
    filename = f"document-archive-{frappe.generate_hash(length=12)}.zip"
    path, manifest = build_document_archive_file(documents)
    file_doc = _register_export_file(path, filename)
    return {
        "file_url": file_doc.file_url,
        "documents": len(manifest["documents"]),
    }


@frappe.whitelist()
def import_document_archive():
    if "System Manager" not in frappe.get_roles():
        frappe.throw("System Manager role is required.", frappe.PermissionError)
    content = getattr(frappe.local, "uploaded_file", None)
    if not content:
        frappe.throw("A document archive ZIP is required.")
    return restore_document_archive(content)
