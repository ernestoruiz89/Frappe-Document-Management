# Document Management

Document Management is a standalone app for the Frappe Framework, designed to archive, index, and organize your digital documents.

**Inspiration:** This project is heavily inspired by **Paperless-ngx**, bringing robust document organization, optical character recognition (OCR), and full-text/semantic search directly into the Frappe ecosystem.

## Features

- **Document Categorization:** Organize documents with categories, tags, and customizable views.
- **OCR Integration:** Automatically extract text from scanned documents and images using OCRmyPDF.
- **Advanced Search:** Full-text indexing powered by Tantivy and semantic search using local or remote embeddings.
- **Document Chat:** Ask questions about your documents using an integrated RAG (Retrieval-Augmented Generation) system.

## Installation

### 1. Prerequisites

Ensure you have a working Frappe bench (version 15+). 
You may also need to install system dependencies for OCR and document preview generation (like LibreOffice for converting office documents to PDF):
```bash
# Ubuntu/Debian example for OCRmyPDF and LibreOffice
sudo apt-get install ocrmypdf tesseract-ocr
sudo apt-get install -y libreoffice-core libreoffice-writer libreoffice-calc libreoffice-impress default-jre
```

LibreOffice Writer, Calc, and Impress provide the filters used to convert Word,
Excel, and PowerPoint files to PDF.


### 2. Get the App

Download the app into your bench directory:
```bash
cd /path/to/your/frappe-bench
bench get-app https://github.com/ernestoruiz89/Frappe-Document-Management.git
```

### 3. Install on a Site

Install the app onto your Frappe site:
```bash
bench --site [your-site-name] install-app document_management
```

### 4. Install Python Dependencies

This app may require additional Python libraries for semantic search and AI integrations (like sentence-transformers, 	antivy, openai, etc.). Ensure they are installed in your bench's virtual environment:
```bash
./env/bin/pip install -e apps/document_management
```
*(Note: Define your dependencies in pyproject.toml before running this).*

## Configuration

After installation, go to the **Document Management Console** in your Frappe interface to get started. You can configure AI providers, OCR settings, and search indices via the **Document Management Settings** page.

### Physical document storage

By default, files keep Frappe's standard `/private/files` storage layout. To
organize document files into metadata-based folders, set **File Storage Path
Template** in **Document Management Settings**. The path is always relative to
`/private/files` and supports these tokens:

```text
documents/{category}/{year}/{month}
documents/{department}/{status}
documents/{document_code}
```

Supported tokens are `{category}`, `{department}`, `{document}`,
`{document_code}`, `{status}`, `{year}`, and `{month}`. Existing files are moved
when a document is saved and new uploads are placed in the configured path.

Enable **Encrypt Document Files at Rest** to encrypt private document originals
and generated previews on disk using the site's `encryption_key` from
`site_config.json`, the same key family Frappe uses for encrypted passwords.
Back up `site_config.json`; encrypted files cannot be recovered without that key.

### Folder ingestion

Enable **Folder Ingestion** in **Document Management Settings** to import files
from a server-side folder on the hourly scheduler. The scanner is non-recursive:
each supported file in **Ingestion Folder Path** is uploaded as a new Document
using the filename stem as the title. Successfully imported files are moved to
**Done Folder Path**; failed files are moved to **Error Folder Path** with a
`.error.txt` note. If done/error paths are blank, `done` and `error` folders are
created inside the ingestion folder.

### Advanced document search syntax

The Document Management Console search box accepts normal text and optional
field filters:

```text
contract status:Published category:Legal
title:"loan agreement" code:LN-2026
tag:Urgent -tag:Archived
department:Finance owner:admin@example.com
created:2026-01-01..2026-01-31 modified:>=2026-06-01
description:"retention notes" ocr:"signed by"
```

Supported fields are `title`, `name`, `code`/`document_code`, `description`,
`content`/`ocr`, `category`, `status`, `department`, `owner`, `tag`, `created`,
and `modified`. Prefix a filter with `-` to exclude matches.

### Optional ERPNext integration

Department-based document access requires ERPNext/HRMS DocTypes such as
`Department` and `Employee`. If they are not installed, Document Management keeps
working as a standalone Frappe app: department fields are hidden in the UI and
department access rules are ignored. Role, owner, category, share, and standard
document permissions continue to apply.

## Archive integrity check

Run a read-only audit of document originals, generated previews, checksums,
OCR state, duplicate version numbers, and orphaned file references:

```bash
bench --site [your-site-name] execute \
  document_management.frappe_document_management.utils.archive_sanity.check_document_archive
```

The same audit runs weekly and logs a Frappe error entry only when issues are
detected.
