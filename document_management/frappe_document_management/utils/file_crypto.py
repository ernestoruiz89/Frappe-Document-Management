import hashlib
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

import frappe
from cryptography.fernet import Fernet, InvalidToken
from frappe.utils.file_manager import get_file_path
from frappe.utils.password import get_encryption_key


MAGIC = b"FDMENC1\n"
BLOCK_SIZE = 1024 * 1024


def file_encryption_enabled():
    try:
        return bool(
            int(
                frappe.db.get_single_value(
                    "Document Management Settings",
                    "enable_file_encryption",
                )
                or 0
            )
        )
    except Exception:
        return False


def _cipher():
    return Fernet(get_encryption_key().encode("utf-8"))


def is_encrypted_path(path):
    path = str(path or "")
    if not path or not os.path.isfile(path):
        return False
    with open(path, "rb") as handle:
        return handle.read(len(MAGIC)) == MAGIC


def decrypt_bytes(data):
    if not data.startswith(MAGIC):
        return data
    try:
        return _cipher().decrypt(data[len(MAGIC) :])
    except InvalidToken:
        frappe.throw(
            "Document file could not be decrypted. Check the site's encryption_key."
        )


def read_path_bytes(path):
    with open(path, "rb") as handle:
        return decrypt_bytes(handle.read())


def read_file_bytes(file_url):
    path = get_file_path(file_url)
    if not path or not os.path.isfile(path):
        frappe.throw(f"File is unavailable: {file_url}")
    return read_path_bytes(path)


def sha256_path(path):
    if is_encrypted_path(path):
        return hashlib.sha256(read_path_bytes(path)).hexdigest()

    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(BLOCK_SIZE)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def plaintext_size(path):
    if is_encrypted_path(path):
        return len(read_path_bytes(path))
    return os.path.getsize(path)


def encrypt_path_at_rest(path):
    if not file_encryption_enabled():
        return False
    path = Path(path)
    if not path.is_file() or is_encrypted_path(path):
        return False

    plaintext = path.read_bytes()
    encrypted = MAGIC + _cipher().encrypt(plaintext)
    fd, temp_path = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".enc",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encrypted)
        os.replace(temp_path, path)
        return True
    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise


def encrypt_file_doc(file_doc):
    if not file_doc or not getattr(file_doc, "is_private", 0):
        return False
    path = get_file_path(file_doc.file_url)
    if not path:
        return False
    return encrypt_path_at_rest(path)


@contextmanager
def decrypted_temp_file(file_url=None, suffix=None, path=None):
    path = path or get_file_path(file_url)
    if not path or not os.path.isfile(path):
        raise FileNotFoundError(f"File not found: {path}")
    if not is_encrypted_path(path):
        yield path
        return

    suffix = suffix if suffix is not None else Path(path).suffix
    handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    temp_path = handle.name
    try:
        handle.write(read_path_bytes(path))
        handle.close()
        yield temp_path
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass


def document_file_response(file_url, filename=None, attachment=False):
    path = get_file_path(file_url)
    if not path or not os.path.isfile(path):
        frappe.throw("File is unavailable.")
    frappe.local.response.filecontent = read_path_bytes(path)
    frappe.local.response.filename = filename or os.path.basename(path)
    frappe.local.response.type = "download"
    frappe.local.response.display_content_as = (
        "attachment" if attachment else "inline"
    )


def document_file_url(document, file_version="Original", attachment=False):
    args = {
        "document": document,
        "file_version": file_version,
    }
    if attachment:
        args["attachment"] = "1"
    query = "&".join(f"{key}={quote(str(value))}" for key, value in args.items())
    return (
        "/api/method/"
        "document_management.frappe_document_management.page."
        "document_management_console.document_management_console."
        f"download_document_file?{query}"
    )
