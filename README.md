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
You may also need to install system dependencies for OCR if you plan to use local OCR extraction:
```bash
# Ubuntu/Debian example for OCRmyPDF
sudo apt-get install ocrmypdf tesseract-ocr
```

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

## Archive integrity check

Run a read-only audit of document originals, generated previews, checksums,
OCR state, duplicate version numbers, and orphaned file references:

```bash
bench --site [your-site-name] execute \
  document_management.frappe_document_management.utils.archive_sanity.check_document_archive
```

The same audit runs weekly and logs a Frappe error entry only when issues are
detected.
