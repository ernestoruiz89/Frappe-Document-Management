from types import SimpleNamespace
from unittest.mock import patch

from document_management.document_management.utils.ocr_worker import (
    OCRConfig,
    _pdf_needs_ocr,
    _processing_source,
    _run_local_ocr,
    _should_run_local_ocr,
    _should_store_archive,
)


def _config(mode="auto", archive_generation="auto"):
    return OCRConfig(
        mode=mode,
        archive_generation=archive_generation,
        language="spa+eng",
        min_text_chars=80,
    )


def test_pdf_processing_always_uses_immutable_original():
    version = SimpleNamespace(
        attachment="/private/files/original.pdf",
        preview_attachment="/private/files/original_ocr.pdf",
    )

    source, original_ext = _processing_source(version)

    assert source == version.attachment
    assert original_ext == "pdf"


def test_office_processing_uses_converted_pdf():
    version = SimpleNamespace(
        attachment="/private/files/source.docx",
        preview_attachment="/private/files/source.pdf",
    )

    source, original_ext = _processing_source(version)

    assert source == version.preview_attachment
    assert original_ext == "docx"


def test_digital_pdf_does_not_need_ocr():
    pages = ["Digital text " * 20, "Second page with selectable text."]

    assert not _pdf_needs_ocr(pages, min_text_chars=80)


def test_pdf_with_blank_page_needs_ocr():
    pages = ["Digital text " * 20, ""]

    assert _pdf_needs_ocr(pages, min_text_chars=80)


def test_archive_auto_only_stores_scanned_or_image_output():
    config = _config()

    assert not _should_store_archive(config, "pdf", False, True)
    assert _should_store_archive(config, "pdf", True, True)
    assert _should_store_archive(config, "jpg", True, True)


def test_archive_never_does_not_store_output():
    assert not _should_store_archive(
        _config(archive_generation="never"),
        "pdf",
        True,
        True,
    )


def test_auto_skips_digital_pdf_unless_archive_is_always():
    assert not _should_run_local_ocr(_config(), needs_ocr=False)
    assert _should_run_local_ocr(
        _config(archive_generation="always"),
        needs_ocr=False,
    )
    assert not _should_run_local_ocr(
        _config(mode="off", archive_generation="always"),
        needs_ocr=True,
    )


def test_auto_ocr_uses_skip_text():
    completed = SimpleNamespace(returncode=0, stdout="", stderr="")
    with (
        patch(
            "document_management.document_management.utils.ocr_worker.subprocess.run",
            return_value=completed,
        ) as run,
        patch(
            "document_management.document_management.utils.ocr_worker.os.path.exists",
            return_value=False,
        ),
    ):
        _run_local_ocr(
            "/tmp/source.pdf",
            "pdf",
            "/tmp",
            _config(mode="auto"),
        )

    command = run.call_args.args[0]
    assert "--skip-text" in command
    assert "--redo-ocr" not in command


def test_redo_ocr_uses_redo_text_layer():
    completed = SimpleNamespace(returncode=0, stdout="", stderr="")
    with (
        patch(
            "document_management.document_management.utils.ocr_worker.subprocess.run",
            return_value=completed,
        ) as run,
        patch(
            "document_management.document_management.utils.ocr_worker.os.path.exists",
            return_value=False,
        ),
    ):
        _run_local_ocr(
            "/tmp/source.pdf",
            "pdf",
            "/tmp",
            _config(mode="redo"),
        )

    assert "--redo-ocr" in run.call_args.args[0]


def test_force_pdf_does_not_add_image_dpi():
    completed = SimpleNamespace(returncode=0, stdout="", stderr="")
    with (
        patch(
            "document_management.document_management.utils.ocr_worker.subprocess.run",
            return_value=completed,
        ) as run,
        patch(
            "document_management.document_management.utils.ocr_worker.os.path.exists",
            return_value=False,
        ),
    ):
        _run_local_ocr(
            "/tmp/source.pdf",
            "pdf",
            "/tmp",
            _config(mode="force"),
        )

    command = run.call_args.args[0]
    assert "--force-ocr" in command
    assert "--image-dpi" not in command
