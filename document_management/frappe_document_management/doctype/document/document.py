import os
import subprocess
import hashlib
import frappe
from frappe.model.document import Document as FrappeDocument
from frappe.utils.file_manager import get_file_path

from document_management.frappe_document_management.utils.document_access import (
    get_user_departments,
    matches_access_rules,
    sql_access_parts,
)

class Document(FrappeDocument):
    def autoname(self):
        self.name = frappe.model.naming.make_autoname("DOC-.YYYY.-.####")

    def before_save(self):
        self.sync_current_version()
        self.force_attachments_private()
        self.populate_version_checksums()
        self.validate_version_numbers()
        self.validate_duplicate_files()

    def after_insert(self):
        self.enqueue_processing()

    def on_update(self):
        self.enqueue_processing()

    def on_trash(self):
        if frappe.db.exists("DocType", "Document Page"):
            frappe.db.delete("Document Page", {"document": self.name})
        if frappe.db.exists("DocType", "Document Share Link"):
            frappe.db.delete("Document Share Link", {"document": self.name})

    def get_current_version(self):
        versions = [row for row in (self.get("versions") or []) if row.attachment]
        return versions[-1] if versions else None

    def populate_version_checksums(self):
        for version in self.get("versions") or []:
            if version.attachment and not version.file_checksum:
                file_path = get_file_path(version.attachment)
                if file_path and os.path.exists(file_path):
                    version.file_checksum = _sha256_file(file_path)
                    version.file_size = os.path.getsize(file_path)
            if version.preview_attachment and not version.preview_checksum:
                preview_path = get_file_path(version.preview_attachment)
                if preview_path and os.path.exists(preview_path):
                    version.preview_checksum = _sha256_file(preview_path)

    def validate_version_numbers(self):
        seen = set()
        for version in self.get("versions") or []:
            version_number = str(version.version_number or "").strip()
            if not version_number:
                frappe.throw("Every document version must have a version number.")
            if version_number in seen:
                frappe.throw(
                    f"Version number {version_number} is used more than once."
                )
            seen.add(version_number)
            version.version_number = version_number

    def validate_duplicate_files(self):
        seen = set()
        for version in self.get("versions") or []:
            checksum = version.file_checksum
            if not checksum:
                continue
            if checksum in seen:
                frappe.throw("This document contains the same file more than once.")
            seen.add(checksum)

            filters = {
                "file_checksum": checksum,
                "parenttype": "Document",
                "parentfield": "versions",
            }
            if self.name:
                filters["parent"] = ["!=", self.name]
            duplicate_parent = frappe.db.get_value("Document Version", filters, "parent")
            if duplicate_parent:
                doc_title = frappe.db.get_value("Document", duplicate_parent, "title")
                doc_display = f"{duplicate_parent} ({doc_title})" if doc_title else duplicate_parent
                frappe.throw(
                    f"An identical file already exists in Document Management (Document: {doc_display})."
                )

    def sync_current_version(self):
        version = self.get_current_version()
        self.current_version = version.version_number if version else None
        if not version:
            self.ocr_status = "Pending"
            self.ocr_content = ""
            return

        version_status = version.ocr_status or "Pending"
        self.ocr_status = version_status
        self.ocr_content = version.ocr_content or ""

    def enqueue_ocr(self, enqueue_after_commit=True):
        from document_management.frappe_document_management.utils.ocr_worker import (
            is_ocr_processing_stale,
        )

        version = self.get_current_version()
        if not version or version.ocr_status == "Completed":
            return
        if (
            version.ocr_status == "Processing"
            and not is_ocr_processing_stale(version)
        ):
            return
        source_url = version.preview_attachment or version.attachment
        extension = (source_url or "").lower().rsplit(".", 1)[-1]
        if extension in ["doc", "docx", "xls", "xlsx", "ppt", "pptx"]:
            return

        frappe.enqueue(
            "document_management.frappe_document_management.utils.ocr_worker.process_ocr",
            doc_name=self.name,
            queue="long",
            enqueue_after_commit=enqueue_after_commit,
        )

    def enqueue_processing(self):
        if self.is_deleted:
            return
        version = self.get_current_version()
        if not version:
            return
        extension = (version.attachment or "").lower().rsplit(".", 1)[-1]
        if (
            extension in ["doc", "docx", "xls", "xlsx", "ppt", "pptx"]
            and not version.preview_attachment
        ):
            frappe.enqueue(
                "document_management.frappe_document_management.doctype.document.document.convert_office_to_pdf_job",
                doc_name=self.name,
                version_name=version.name,
                queue="long",
                timeout=900,
                enqueue_after_commit=True,
            )
            return
        self.enqueue_ocr()



    def force_attachments_private(self):
        # Find all files attached to this document or its child versions
        doc_names = [self.name]
        version_names = [v.name for v in self.get("versions", []) if v.name]

        all_refs = [("Document", self.name)]
        for v in version_names:
            all_refs.append(("Document Version", v))

        for doctype, name in all_refs:
            # Skip if name is not set (e.g. unsaved child row)
            if not name:
                continue

            files = frappe.get_all("File",
                filters={"attached_to_doctype": doctype, "attached_to_name": name, "is_private": 0},
                fields=["name", "file_url"]
            )
            for file_data in files:
                f = frappe.get_doc("File", file_data.name)
                f.is_private = 1
                f.save(ignore_permissions=True)

                # Update references in the child table to reflect the new /private/files/... URL
                for v in self.get("versions", []):
                    if getattr(v, "attachment", "") == file_data.file_url:
                        v.attachment = f.file_url
                    if getattr(v, "preview_attachment", "") == file_data.file_url:
                        v.preview_attachment = f.file_url

def _convert_office_version(doc, version):
    import glob
    import tempfile
    from frappe import _

    file_path = get_file_path(version.attachment)
    if not file_path or not os.path.exists(file_path):
        raise FileNotFoundError(
            _("File not found for conversion: {0}").format(file_path)
        )

    tmp_dir = tempfile.mkdtemp()
    try:
        command = [
            "libreoffice",
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            tmp_dir,
            file_path,
        ]
        result = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                _("LibreOffice Error (Code {0}): {1}").format(
                    result.returncode,
                    result.stderr or result.stdout,
                )
            )

        # LibreOffice may produce a PDF with a slightly different name than
        # the stem of the source file (e.g. when the name contains special
        # characters).  Scan the temp directory for any PDF produced.
        pdf_files = glob.glob(os.path.join(tmp_dir, "*.pdf"))
        if not pdf_files:
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(
                _("LibreOffice ran, but the PDF file was not created.{0}").format(
                    " " + detail if detail else ""
                )
            )

        pdf_path = pdf_files[0]
        pdf_filename = os.path.basename(pdf_path)

        with open(pdf_path, "rb") as file_handle:
            file_content = file_handle.read()

        file_doc = frappe.get_doc(
            {
                "doctype": "File",
                "file_name": pdf_filename,
                "attached_to_doctype": "Document Version",
                "attached_to_name": version.name,
                "attached_to_field": "preview_attachment",
                "is_private": 1,
                "content": file_content,
            }
        )
        file_doc.insert(ignore_permissions=True)
        frappe.db.set_value(
            "Document Version",
            version.name,
            {
                "preview_attachment": file_doc.file_url,
                "preview_checksum": _sha256_file(pdf_path),
                "preview_status": "Completed",
                "preview_error": "",
            },
        )
        version.preview_attachment = file_doc.file_url
        return file_doc.file_url
    finally:
        import shutil as _shutil
        _shutil.rmtree(tmp_dir, ignore_errors=True)




def convert_office_to_pdf(doc_name):
    from frappe import _

    doc = frappe.get_doc("Document", doc_name)
    for version in doc.versions:
        if not version.attachment or version.preview_attachment:
            continue
        extension = version.attachment.lower().rsplit(".", 1)[-1]
        if extension not in ["doc", "docx", "xls", "xlsx", "ppt", "pptx"]:
            continue
        _convert_office_version(doc, version)
        return _("SUCCESS: PDF generated for version {0}.").format(
            version.version_number
        )
    return _("WARNING: No pending attachments found to convert.")


def convert_office_to_pdf_job(doc_name, version_name):
    frappe.db.sql(
        "SELECT name FROM `tabDocument Version` WHERE name = %s FOR UPDATE",
        (version_name,),
    )
    status, preview_attachment = frappe.db.get_value(
        "Document Version",
        version_name,
        ["preview_status", "preview_attachment"],
    )
    if preview_attachment or status == "Processing":
        frappe.db.commit()
        return
    frappe.db.set_value(
        "Document Version",
        version_name,
        {"preview_status": "Processing", "preview_error": ""},
    )
    frappe.db.commit()

    try:
        doc = frappe.get_doc("Document", doc_name)
        version = next(
            row for row in doc.versions if row.name == version_name
        )
        _convert_office_version(doc, version)
        frappe.db.commit()
        doc.reload()
        doc.enqueue_ocr(enqueue_after_commit=False)
    except Exception:
        error = frappe.get_traceback()
        frappe.log_error(title="Document Preview Conversion Error", message=error)
        frappe.db.set_value(
            "Document Version",
            version_name,
            {
                "preview_status": "Failed",
                "preview_error": error[-2000:],
            },
        )
        frappe.db.commit()


def _sha256_file(file_path, block_size=1024 * 1024):
    digest = hashlib.sha256()
    with open(file_path, "rb") as file_handle:
        while True:
            block = file_handle.read(block_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def has_permission(doc, ptype="read", user=None):
    if not user:
        user = frappe.session.user
    if user == "Administrator":
        return True

    user_roles = frappe.get_roles(user)

    if ptype == "delete":
        return doc.owner == user or "System Manager" in user_roles

    if ptype == "share":
        return doc.owner == user or "System Manager" in user_roles

    if doc.owner == user:
        return True


    # Check if explicitly shared via Frappe's native Share feature
    share_filters = {
        "share_doctype": "Document",
        "share_name": doc.name,
        "user": user,
    }
    if ptype == "read":
        share_filters["read"] = 1
    elif ptype == "write":
        share_filters["write"] = 1
    else:
        share_filters = None
    is_shared = (
        frappe.db.exists("DocShare", share_filters)
        if share_filters
        else False
    )
    if is_shared:
        return True

    if ptype == "write":
        return "System Manager" in user_roles

    user_departments = get_user_departments(user)

    # Check Document Level
    if doc.only_me:
        return doc.owner == user

    has_restrictions, allowed = matches_access_rules(
        doc,
        set(user_roles),
        user_departments,
    )
    if has_restrictions:
        return allowed

    # Check Category Level
    if doc.category:
        category = frappe.get_cached_doc("Document Category", doc.category)
        if category.only_me:
            return category.owner == user

        has_restrictions, allowed = matches_access_rules(
            category,
            set(user_roles),
            user_departments,
        )
        if has_restrictions:
            return allowed

    return True

def get_permission_query_conditions(user):
    if not user:
        user = frappe.session.user
    if user == "Administrator":
        return "`tabDocument`.is_deleted = 0"

    user_roles = frappe.get_roles(user)
    roles_str = ",".join(frappe.db.escape(role) for role in user_roles)
    if not roles_str:
        roles_str = "''"

    escaped_user = frappe.db.escape(user)
    doc_access = sql_access_parts(
        "`tabDocument`",
        "Document",
        user,
        roles_str,
    )
    category_access = sql_access_parts(
        "cat",
        "Document Category",
        user,
        roles_str,
    )

    doc_cond = f"""
        (`tabDocument`.only_me = 1 AND `tabDocument`.owner = {escaped_user})
        OR
        (`tabDocument`.only_me = 0 AND {doc_access["matches"]})
    """

    cat_cond = f"""
        EXISTS (
            SELECT 1 FROM `tabDocument Category` cat WHERE cat.name = `tabDocument`.category AND (
                (cat.only_me = 1 AND cat.owner = {escaped_user})
                OR
                (cat.only_me = 0 AND {category_access["matches"]})
                OR
                (cat.only_me = 0 AND NOT {category_access["has_restrictions"]})
            )
        )
    """

    fallback_cond = f"""
        (
            `tabDocument`.only_me = 0
            AND NOT {doc_access["has_restrictions"]}
            AND (
                `tabDocument`.category IS NULL
                OR `tabDocument`.category = ''
                OR {cat_cond}
            )
        )
    """

    shared_cond = f"""
        EXISTS (
            SELECT 1 FROM `tabDocShare` ds
            WHERE ds.share_doctype = 'Document'
              AND ds.share_name = `tabDocument`.name
              AND ds.user = {escaped_user}
              AND ds.read = 1
        )
    """

    return (
        f"`tabDocument`.is_deleted = 0 AND "
        f"(`tabDocument`.owner = {escaped_user} "
        f"OR ({doc_cond}) OR ({fallback_cond}) OR ({shared_cond}))"
    )
