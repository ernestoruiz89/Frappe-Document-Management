import hashlib
import os
import secrets
from urllib.parse import quote

import frappe
from frappe.utils import add_days, get_datetime, now_datetime
from frappe.utils.file_manager import get_file_path

from document_management.frappe_document_management.utils.file_crypto import (
    read_path_bytes,
)


def _token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _share_url(token):
    return f"{frappe.utils.get_url()}/document_share?token={quote(token)}"


def _shared_download_url(token, download=1):
    return (
        "/api/method/document_management.frappe_document_management.utils."
        "document_sharing.download_shared_document"
        f"?token={quote(token)}&download={int(bool(download))}"
    )


def _file_kind(file_url):
    extension = os.path.splitext(file_url or "")[1].lower()
    if extension == ".pdf":
        return "pdf"
    if extension in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}:
        return "image"
    if extension == ".md":
        return "markdown"
    if extension == ".txt":
        return "text"
    return "download"


def _get_share_link(token):
    if not token:
        frappe.throw("Share token is required.", frappe.PermissionError)
    link_name = frappe.db.get_value(
        "Document Share Link",
        {"token_hash": _token_hash(token), "enabled": 1},
        "name",
    )
    if not link_name:
        frappe.throw("Share link is invalid or revoked.", frappe.PermissionError)
    link = frappe.get_doc("Document Share Link", link_name)
    if get_datetime(link.expires_at) <= now_datetime():
        frappe.throw("Share link has expired.", frappe.PermissionError)
    return link


def get_shared_document_context(token):
    link = _get_share_link(token)
    document = frappe.get_doc("Document", link.document)
    if document.is_deleted:
        frappe.throw("Shared document is no longer available.", frappe.PermissionError)
    version = document.get_current_version()
    file_url = (
        version.preview_attachment
        if link.file_version == "Searchable PDF"
        else version.attachment
    )
    path = get_file_path(file_url)
    if not path or not os.path.isfile(path):
        frappe.throw("Shared file is unavailable.")
    filename = os.path.basename(path)
    return {
        "document": document,
        "link": link,
        "file_url": file_url,
        "filename": filename,
        "kind": _file_kind(file_url),
        "preview_url": _shared_download_url(token, download=0),
        "download_url": _shared_download_url(token, download=1),
    }


@frappe.whitelist()
def create_share_link(document, expiration_days=7, file_version="Original"):
    doc = frappe.get_doc("Document", document)
    doc.check_permission("share")
    if doc.is_deleted:
        frappe.throw("Restore the document before sharing it.")
    try:
        expiration_days = int(expiration_days)
    except (TypeError, ValueError):
        frappe.throw("Expiration days must be a number.")
    if expiration_days < 1 or expiration_days > 365:
        frappe.throw("Expiration days must be between 1 and 365.")
    if file_version not in {"Original", "Searchable PDF"}:
        frappe.throw("Invalid shared file version.")
    version = doc.get_current_version()
    if not version or not version.attachment:
        frappe.throw("The document has no file to share.")
    if file_version == "Searchable PDF" and not version.preview_attachment:
        frappe.throw("This document has no searchable PDF.")

    token = secrets.token_urlsafe(36)
    link = frappe.get_doc(
        {
            "doctype": "Document Share Link",
            "document": doc.name,
            "token_hash": _token_hash(token),
            "expires_at": add_days(now_datetime(), expiration_days),
            "file_version": file_version,
            "enabled": 1,
        }
    )
    link.insert(ignore_permissions=True)
    return {
        "name": link.name,
        "url": _share_url(token),
        "expires_at": link.expires_at,
    }


@frappe.whitelist()
def revoke_share_link(name):
    link = frappe.get_doc("Document Share Link", name)
    document = frappe.get_doc("Document", link.document)
    document.check_permission("share")
    link.db_set("enabled", 0)
    return {"name": link.name, "enabled": 0}


@frappe.whitelist()
def get_share_links(document):
    doc = frappe.get_doc("Document", document)
    doc.check_permission("share")
    return frappe.get_all(
        "Document Share Link",
        filters={"document": doc.name},
        fields=[
            "name",
            "expires_at",
            "file_version",
            "enabled",
            "creation",
            "owner",
        ],
        order_by="creation desc",
    )


@frappe.whitelist(allow_guest=True)
def download_shared_document(token, download=1):
    context = get_shared_document_context(token)
    path = get_file_path(context["file_url"])
    frappe.local.response.filecontent = read_path_bytes(path)
    frappe.local.response.filename = context["filename"]
    frappe.local.response.type = "download"
    frappe.local.response.display_content_as = (
        "attachment" if int(download or 0) else "inline"
    )


def cleanup_expired_share_links():
    expired = frappe.get_all(
        "Document Share Link",
        filters={"expires_at": ["<", now_datetime()]},
        pluck="name",
        limit_page_length=0,
    )
    for name in expired:
        frappe.delete_doc(
            "Document Share Link",
            name,
            ignore_permissions=True,
            force=True,
        )
    if expired:
        frappe.db.commit()
    return expired
