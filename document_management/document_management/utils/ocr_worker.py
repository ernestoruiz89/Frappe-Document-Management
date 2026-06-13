import base64
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass

import frappe
from frappe.utils.file_manager import get_file_path


IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "tiff", "tif"}
OFFICE_EXTENSIONS = {"doc", "docx", "xls", "xlsx", "ppt", "pptx"}
PDF_EXTENSION = "pdf"
OCR_MODES = {"auto", "redo", "force", "off"}
ARCHIVE_MODES = {"auto", "always", "never"}


@dataclass(frozen=True)
class OCRConfig:
    mode: str
    archive_generation: str
    language: str
    min_text_chars: int


def _ocr_config():
    settings = frappe.get_single("Document Management Settings")
    mode = (settings.get("ocr_mode") or "Auto").strip().lower()
    archive_generation = (
        settings.get("ocr_archive_generation") or "Auto"
    ).strip().lower()
    if mode not in OCR_MODES:
        mode = "auto"
    if archive_generation not in ARCHIVE_MODES:
        archive_generation = "auto"
    try:
        min_text_chars = max(int(settings.get("ocr_min_text_chars") or 80), 1)
    except (TypeError, ValueError):
        min_text_chars = 80
    return OCRConfig(
        mode=mode,
        archive_generation=archive_generation,
        language=(settings.get("ocr_language") or "spa+eng").strip() or "spa+eng",
        min_text_chars=min_text_chars,
    )


def _openai_ocr_image(client, model, encoded_image, mime_type):
    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Extract all text from this page accurately. Preserve "
                            "tables and headings as plain text. Output only the text."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": (
                                f"data:{mime_type};base64,{encoded_image}"
                            )
                        },
                    },
                ],
            }
        ],
    )
    return (response.choices[0].message.content or "").strip()


def extract_pages_with_openai(file_path, ext, page_numbers=None):
    """Fallback OCR for content that local extraction could not read."""
    try:
        settings = frappe.get_single("Document Management Settings")
        if not settings.enable_openai_for_ocr or not settings.openai_api_key:
            return []
        api_key = settings.get_password("openai_api_key")
    except Exception:
        return []

    if not api_key:
        return []

    try:
        from openai import OpenAI
    except ImportError:
        return []

    wanted_pages = set(page_numbers or [])
    try:
        client = OpenAI(api_key=api_key)
        model = settings.openai_ocr_model or "gpt-5-mini"
        if ext in IMAGE_EXTENSIONS:
            with open(file_path, "rb") as image_file:
                encoded = base64.b64encode(image_file.read()).decode("utf-8")
            mime_ext = "jpeg" if ext in {"jpg", "jpeg"} else ext
            return [
                _openai_ocr_image(
                    client,
                    model,
                    encoded,
                    f"image/{mime_ext}",
                )
            ]
        if ext != "pdf":
            return []

        import pdfplumber

        extracted_pages = []
        with pdfplumber.open(file_path) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                if wanted_pages and page_number not in wanted_pages:
                    extracted_pages.append("")
                    continue
                image = page.to_image(resolution=150).original
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG")
                encoded = base64.b64encode(buffer.getvalue()).decode("utf-8")
                extracted_pages.append(
                    _openai_ocr_image(
                        client,
                        model,
                        encoded,
                        "image/jpeg",
                    )
                )
                buffer.close()
                image.close()
        return extracted_pages
    except Exception:
        frappe.log_error(
            title="OpenAI Vision API Error",
            message=frappe.get_traceback(),
        )
        return []


def _current_version(doc):
    versions = [row for row in (doc.get("versions") or []) if row.attachment]
    return versions[-1] if versions else None


def _set_ocr_state(doc, version, status, content=None):
    values = {"ocr_status": status}
    if content is not None:
        values["ocr_content"] = content
    frappe.db.set_value("Document Version", version.name, values)
    frappe.db.set_value(
        "Document",
        doc.name,
        {
            "ocr_status": status,
            **({"ocr_content": content} if content is not None else {}),
        },
    )
    version.ocr_status = status
    if content is not None:
        version.ocr_content = content
        doc.ocr_content = content
    doc.ocr_status = status


def _extract_pdf_pages(pdf_path):
    import pdfplumber

    with pdfplumber.open(pdf_path) as pdf:
        return [(page.extract_text() or "").strip() for page in pdf.pages]


def _aggregate_pages(pages):
    return "\n\n".join(page.strip() for page in pages if page and page.strip())


def _non_whitespace_length(text):
    return len("".join((text or "").split()))


def _pdf_needs_ocr(pages, min_text_chars):
    if not pages:
        return True
    aggregate = _aggregate_pages(pages)
    if _non_whitespace_length(aggregate) < min_text_chars:
        return True
    return any(not (page or "").strip() for page in pages)


def _should_store_archive(config, ext, needs_ocr, output_exists):
    if not output_exists or config.archive_generation == "never":
        return False
    if config.archive_generation == "always":
        return True
    return ext in IMAGE_EXTENSIONS or (ext == PDF_EXTENSION and needs_ocr)


def _should_run_local_ocr(config, needs_ocr):
    if config.mode == "off":
        return False
    return (
        config.mode in {"redo", "force"}
        or needs_ocr
        or config.archive_generation == "always"
    )


def _processing_source(version):
    attachment = version.attachment or ""
    original_ext = attachment.lower().rsplit(".", 1)[-1]
    if original_ext in OFFICE_EXTENSIONS:
        if not version.preview_attachment:
            raise RuntimeError("Office preview PDF was not generated.")
        return version.preview_attachment, original_ext
    return attachment, original_ext


def _replace_document_pages(doc, version, pages, extraction_method):
    rows = []
    for page_number, content in enumerate(pages, start=1):
        content = (content or "").strip()
        if not content:
            continue
        rows.append(
            {
                "doctype": "Document Page",
                "document": doc.name,
                "document_version": version.name,
                "version_number": version.version_number,
                "page_number": page_number,
                "content": content,
                "content_hash": hashlib.sha256(
                    content.encode("utf-8")
                ).hexdigest(),
                "extraction_method": extraction_method,
            }
        )

    savepoint = "replace_document_pages"
    frappe.db.savepoint(savepoint)
    try:
        frappe.db.delete(
            "Document Page",
            {"document_version": version.name},
        )
        for row in rows:
            frappe.get_doc(row).insert(ignore_permissions=True)
    except Exception:
        frappe.db.rollback(save_point=savepoint)
        raise
    return len(rows)


def _save_ocr_preview(version, source_url, pdf_path):
    from frappe.core.doctype.file.file import File
    from frappe.utils import file_manager as frappe_file_manager
    from frappe.utils.file_manager import save_file

    with open(pdf_path, "rb") as file_handle:
        content = file_handle.read()

    original_check = getattr(frappe_file_manager, "check_max_file_size", None)
    original_file_check = getattr(File, "check_max_file_size", None)
    frappe_file_manager.check_max_file_size = lambda value: len(value)

    def file_check(instance):
        return len(
            getattr(instance, "_content", None)
            or getattr(instance, "content", None)
            or b""
        )

    File.check_max_file_size = file_check
    try:
        saved_file = save_file(
            os.path.basename(pdf_path),
            content,
            "Document Version",
            version.name,
            is_private=1 if source_url.startswith("/private") else 0,
            df="preview_attachment",
        )
    finally:
        if original_check is None:
            delattr(frappe_file_manager, "check_max_file_size")
        else:
            frappe_file_manager.check_max_file_size = original_check
        if original_file_check is None:
            delattr(File, "check_max_file_size")
        else:
            File.check_max_file_size = original_file_check

    previous_url = version.preview_attachment or ""
    frappe.db.set_value(
        "Document Version",
        version.name,
        {
            "preview_attachment": saved_file.file_url,
            "preview_status": "Completed",
            "preview_error": "",
        },
    )
    version.preview_attachment = saved_file.file_url
    return previous_url, saved_file.file_url


def _clear_ocr_preview(version):
    previous_url = version.preview_attachment or ""
    if not previous_url:
        return "", ""
    frappe.db.set_value(
        "Document Version",
        version.name,
        {
            "preview_attachment": "",
            "preview_status": "Completed",
            "preview_error": "",
        },
    )
    version.preview_attachment = ""
    return previous_url, ""


def _delete_replaced_preview(version_name, previous_url, current_url):
    if not previous_url or previous_url == current_url:
        return
    stored_url = (
        frappe.db.get_value(
            "Document Version",
            version_name,
            "preview_attachment",
        )
        or ""
    )
    if stored_url != current_url:
        return
    file_name = frappe.db.get_value(
        "File",
        {
            "file_url": previous_url,
            "attached_to_doctype": "Document Version",
            "attached_to_name": version_name,
        },
        "name",
    )
    if not file_name:
        return
    try:
        frappe.delete_doc("File", file_name, ignore_permissions=True)
        frappe.db.commit()
    except Exception:
        frappe.log_error(
            title="OCR Preview Cleanup Error",
            message=frappe.get_traceback(),
        )


def _run_local_ocr(source_path, ext, temp_dir, config):
    base_name = os.path.splitext(os.path.basename(source_path))[0]
    output_pdf = os.path.join(temp_dir, f"{base_name}_ocr.pdf")
    sidecar = os.path.join(temp_dir, f"{base_name}_ocr.txt")
    command = [
        sys.executable,
        "-m",
        "ocrmypdf",
        "--optimize",
        "1",
        "-l",
        config.language,
        "--sidecar",
        sidecar,
    ]
    if ext in IMAGE_EXTENSIONS:
        command.extend(["--force-ocr", "--image-dpi", "300"])
    elif config.mode == "force":
        command.append("--force-ocr")
    elif config.mode == "redo":
        command.append("--redo-ocr")
    else:
        command.append("--skip-text")
    command.extend([source_path, output_pdf])

    result = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    pages = []
    if os.path.exists(output_pdf):
        pages = _extract_pdf_pages(output_pdf)
    if not _aggregate_pages(pages) and os.path.exists(sidecar):
        with open(sidecar, "r", encoding="utf-8") as sidecar_file:
            text = sidecar_file.read().strip()
        pages = [text] if text else []
    return result, output_pdf, pages


def _apply_tags(doc, extracted_text):
    # Reload fresh document to avoid TimestampMismatchError from concurrent edits during OCR processing
    doc = frappe.get_doc("Document", doc.name)
    changed = False
    try:
        from document_management.document_management.utils.auto_tagger import apply_auto_tags

        changed = apply_auto_tags(doc, extracted_text) or changed
    except Exception:
        frappe.log_error(title="Auto Tagger Exception", message=frappe.get_traceback())

    try:
        from document_management.document_management.utils.ml_tagger import predict_tags

        changed = predict_tags(doc, extracted_text) or changed
    except Exception:
        frappe.log_error(title="ML Tagger Exception", message=frappe.get_traceback())

    if changed:
        doc.save(ignore_permissions=True)


def process_ocr(doc_name):
    temp_dir = None
    replaced_preview = ("", "")
    try:
        doc = frappe.get_doc("Document", doc_name)
        version = _current_version(doc)
        if not version:
            doc.db_set("ocr_status", "Pending")
            return
        if version.ocr_status in ("Processing", "Completed"):
            return

        _set_ocr_state(doc, version, "Processing")
        frappe.db.commit()

        config = _ocr_config()
        source_url, original_ext = _processing_source(version)
        source_path = get_file_path(source_url)
        if not source_path or not os.path.exists(source_path):
            raise FileNotFoundError(f"File not found: {source_path}")

        ext = source_url.rsplit(".", 1)[-1].lower()
        if ext not in IMAGE_EXTENSIONS and ext != "pdf":
            raise RuntimeError(f"Unsupported OCR file type: {ext}")

        temp_dir = tempfile.mkdtemp()
        source_pages = []
        if ext == "pdf":
            try:
                source_pages = _extract_pdf_pages(source_path)
            except Exception as exc:
                frappe.logger("document_ocr").warning(
                    "Initial PDF text extraction failed for %s: %s",
                    doc.name,
                    type(exc).__name__,
                )
        needs_ocr = (
            ext in IMAGE_EXTENSIONS
            or _pdf_needs_ocr(source_pages, config.min_text_chars)
        )
        run_local_ocr = _should_run_local_ocr(config, needs_ocr)
        result = None
        output_pdf = ""
        if run_local_ocr:
            result, output_pdf, extracted_pages = _run_local_ocr(
                source_path,
                ext,
                temp_dir,
                config,
            )
            extraction_method = f"ocrmypdf-{config.mode}"
        else:
            extracted_pages = source_pages
            extraction_method = "pdf-text" if ext == "pdf" else "ocr-disabled"

        if not _aggregate_pages(extracted_pages) and source_pages:
            extracted_pages = source_pages
            extraction_method = "pdf-text"
        missing_pages = [
            page_number
            for page_number, content in enumerate(extracted_pages, start=1)
            if not (content or "").strip()
        ]
        if missing_pages and config.mode != "off":
            fallback_pages = extract_pages_with_openai(
                source_path,
                ext,
                page_numbers=missing_pages,
            )
            for page_number in missing_pages:
                if page_number <= len(fallback_pages):
                    extracted_pages[page_number - 1] = (
                        fallback_pages[page_number - 1] or ""
                    )
            if _aggregate_pages(fallback_pages):
                extraction_method += "+openai"
        if not _aggregate_pages(extracted_pages) and config.mode != "off":
            extracted_pages = extract_pages_with_openai(source_path, ext)
            extraction_method = "openai"
        extracted_text = _aggregate_pages(extracted_pages)
        if not extracted_text and config.mode != "off":
            detail = (
                (result.stderr or result.stdout or "").strip()
                if result
                else ""
            )
            raise RuntimeError(f"OCR produced no text. {detail}")

        store_archive = _should_store_archive(
            config,
            ext,
            needs_ocr,
            bool(output_pdf and os.path.exists(output_pdf)),
        ) and original_ext not in OFFICE_EXTENSIONS

        _replace_document_pages(
            doc,
            version,
            extracted_pages,
            extraction_method,
        )
        _set_ocr_state(doc, version, "Completed", extracted_text)
        _apply_tags(doc, extracted_text)
        if store_archive:
            replaced_preview = _save_ocr_preview(
                version,
                source_url,
                output_pdf,
            )
        elif original_ext not in OFFICE_EXTENSIONS:
            replaced_preview = _clear_ocr_preview(version)
        frappe.db.commit()
        _delete_replaced_preview(version.name, *replaced_preview)

        frappe.enqueue(
            "document_management.search.indexer.run_indexing_background",
            doc_type="Document",
            doc_name=doc.name,
            queue="long",
            timeout=900,
            enqueue_after_commit=True,
        )
    except Exception:
        frappe.log_error(title="OCR Exception", message=frappe.get_traceback())
        try:
            if "doc" in locals() and "version" in locals() and version:
                frappe.db.rollback()
                _set_ocr_state(doc, version, "Failed")
                frappe.db.commit()
        except Exception:
            frappe.log_error(
                title="OCR Status Update Error",
                message=frappe.get_traceback(),
            )
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
