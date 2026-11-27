import hashlib
import gc
import json
import os
import shutil
import sqlite3
from functools import lru_cache
from pathlib import Path

import frappe
import numpy as np
from filelock import FileLock
from frappe.utils.file_manager import get_file_path

from document_management.frappe_document_management.utils.file_crypto import (
    decrypted_temp_file,
)
from document_management.frappe_document_management.rag.chunking import chunk_pages
from document_management.frappe_document_management.rag.config import get_rag_config
from document_management.frappe_document_management.rag.providers import (
    embed_texts,
    embedding_signature,
    rerank_texts,
)


INDEX_VERSION = 5


class IndexRebuildRequired(RuntimeError):
    pass


def _root_path():
    settings = frappe.get_single("Document Management Settings")
    base = frappe.utils.get_site_path("private", settings.index_path or "search_index")
    return Path(base) / "rag"


def _lock_path():
    root = _root_path()
    root.parent.mkdir(parents=True, exist_ok=True)
    return root.parent / "rag.lock"


def _generations_path():
    return _root_path() / "generations"


def _pointer_path():
    return _root_path() / "current.json"


def _active_root():
    pointer = _pointer_path()
    if not pointer.exists():
        raise IndexRebuildRequired(
            "The generation-based RAG index has not been built."
        )
    try:
        generation = json.loads(pointer.read_text(encoding="utf-8"))["generation"]
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise IndexRebuildRequired("The RAG generation pointer is invalid.") from exc
    if (
        not isinstance(generation, str)
        or not generation.startswith("gen-")
        or Path(generation).name != generation
    ):
        raise IndexRebuildRequired("The RAG generation pointer is invalid.")
    root = _generations_path() / generation
    if not root.is_dir():
        raise IndexRebuildRequired("The active RAG generation is missing.")
    return root


def _active_generation_name():
    try:
        return _active_root().name
    except IndexRebuildRequired:
        return None


def _new_temp_generation():
    generations = _generations_path()
    generations.mkdir(parents=True, exist_ok=True)
    path = generations / f".tmp-{frappe.generate_hash(length=12)}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def _faiss_path(root=None):
    return (root or _active_root()) / "vectors.faiss"


def _tantivy_path(root=None):
    return (root or _active_root()) / "tantivy"


def _meta_path(root=None):
    return (root or _active_root()) / "meta.json"


def _chunks_path(root=None):
    return (root or _active_root()) / "chunks.json"


def _chunks_db_path(root=None):
    return (root or _active_root()) / "chunks.sqlite"


def _connect_sqlite_ro(db_path):
    resolved = Path(db_path).resolve()
    try:
        return sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)
    except sqlite3.Error:
        return sqlite3.connect(str(resolved))


def _init_chunks_db(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chunks (
            name TEXT PRIMARY KEY,
            document TEXT NOT NULL,
            document_version TEXT,
            version_number TEXT,
            page_number INTEGER,
            chunk_index INTEGER,
            content TEXT NOT NULL,
            content_hash TEXT,
            embedding_model TEXT,
            vector_id INTEGER NOT NULL UNIQUE
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document)")


def _row_from_sqlite(record):
    return {
        "name": record[0],
        "document": record[1],
        "document_version": record[2],
        "version_number": record[3],
        "page_number": record[4],
        "chunk_index": record[5],
        "content": record[6],
        "content_hash": record[7],
        "embedding_model": record[8],
        "vector_id": str(record[9]),
    }


def _read_meta(root=None):
    path = _meta_path(root)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_meta(meta, root=None):
    path = _meta_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def _serializable_chunk(row):
    return {
        "name": row["name"],
        "document": row["document"],
        "document_version": row.get("document_version"),
        "version_number": row.get("version_number"),
        "page_number": row.get("page_number"),
        "chunk_index": row.get("chunk_index"),
        "content": row.get("content") or "",
        "content_hash": row.get("content_hash"),
        "embedding_model": row.get("embedding_model"),
        "vector_id": str(row["vector_id"]),
    }


def _write_chunk_metadata(rows, root):
    db_path = _chunks_db_path(root)
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(str(db_path))
    try:
        _init_chunks_db(conn)
        serialized = [_serializable_chunk(row) for row in rows]
        conn.executemany(
            """
            INSERT OR REPLACE INTO chunks (
                name, document, document_version, version_number, page_number,
                chunk_index, content, content_hash, embedding_model, vector_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    r["name"],
                    r["document"],
                    r["document_version"],
                    r["version_number"],
                    r["page_number"],
                    r["chunk_index"],
                    r["content"],
                    r["content_hash"],
                    r["embedding_model"],
                    int(r["vector_id"]),
                )
                for r in serialized
            ],
        )
        conn.commit()
    finally:
        conn.close()
    legacy_json = _chunks_path(root)
    if legacy_json.exists():
        try:
            legacy_json.unlink()
        except OSError:
            pass


def _ensure_sqlite_chunk_store(root):
    db_path = _chunks_db_path(root)
    if db_path.exists():
        return db_path
    legacy_json = _chunks_path(root)
    if not legacy_json.exists():
        raise IndexRebuildRequired("The RAG chunk metadata is missing.")
    rows = json.loads(legacy_json.read_text(encoding="utf-8"))
    _write_chunk_metadata(rows, root)
    return db_path


def _load_document_chunks_from_store(root, document_names):
    names = list(dict.fromkeys(document_names or []))
    if not names:
        return []
    db_path = _ensure_sqlite_chunk_store(root)
    conn = _connect_sqlite_ro(db_path)
    try:
        rows = []
        for start in range(0, len(names), 500):
            batch = names[start : start + 500]
            placeholders = ",".join("?" for _ in batch)
            cursor = conn.execute(
                f"SELECT name, document, document_version, version_number, page_number, "
                f"chunk_index, content, content_hash, embedding_model, vector_id "
                f"FROM chunks WHERE document IN ({placeholders})",
                batch,
            )
            rows.extend(frappe._dict(_row_from_sqlite(rec)) for rec in cursor.fetchall())
        return rows
    finally:
        conn.close()


def _mutate_sqlite_chunks(root, remove_documents, new_rows):
    db_path = _ensure_sqlite_chunk_store(root)
    conn = sqlite3.connect(str(db_path))
    try:
        _init_chunks_db(conn)
        remove_list = list(dict.fromkeys(remove_documents or []))
        for start in range(0, len(remove_list), 500):
            batch = remove_list[start : start + 500]
            placeholders = ",".join("?" for _ in batch)
            conn.execute(f"DELETE FROM chunks WHERE document IN ({placeholders})", batch)
        if new_rows:
            serialized = [_serializable_chunk(row) for row in new_rows]
            conn.executemany(
                """
                INSERT OR REPLACE INTO chunks (
                    name, document, document_version, version_number, page_number,
                    chunk_index, content, content_hash, embedding_model, vector_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        r["name"],
                        r["document"],
                        r["document_version"],
                        r["version_number"],
                        r["page_number"],
                        r["chunk_index"],
                        r["content"],
                        r["content_hash"],
                        r["embedding_model"],
                        int(r["vector_id"]),
                    )
                    for r in serialized
                ],
            )
        conn.commit()
    finally:
        conn.close()


def _count_chunk_metadata(root):
    db_path = _chunks_db_path(root)
    if db_path.exists():
        conn = _connect_sqlite_ro(db_path)
        try:
            row = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()
            return int(row[0] if row else 0)
        finally:
            conn.close()
    return len(_load_chunk_metadata(root))


@lru_cache(maxsize=6)
def _cached_chunk_metadata(path_string):
    path = Path(path_string)
    if path.suffix == ".sqlite" and path.exists():
        conn = _connect_sqlite_ro(path)
        try:
            cursor = conn.execute(
                "SELECT name, document, document_version, version_number, page_number, "
                "chunk_index, content, content_hash, embedding_model, vector_id FROM chunks"
            )
            return tuple(_row_from_sqlite(rec) for rec in cursor.fetchall())
        finally:
            conn.close()
    if not path.exists():
        raise IndexRebuildRequired("The RAG chunk metadata is missing.")
    rows = json.loads(path.read_text(encoding="utf-8"))
    return tuple(rows)


def _load_chunk_metadata(root):
    db_path = _chunks_db_path(root)
    if db_path.exists():
        return list(_cached_chunk_metadata(str(db_path)))
    return list(_cached_chunk_metadata(str(_chunks_path(root))))


_DEFAULT_LOAD_CHUNK_METADATA = _load_chunk_metadata


@lru_cache(maxsize=6)
def _cached_chunk_lookups(path_string):
    rows = _cached_chunk_metadata(path_string)
    by_id = {int(row["vector_id"]): row for row in rows}
    by_name = {row["name"]: row for row in rows}
    return by_id, by_name


def _query_chunks_by_vector_ids(root, vector_ids):
    ids = [int(v) for v in dict.fromkeys(vector_ids or []) if int(v) >= 0]
    if not ids:
        return {}
    db_path = _chunks_db_path(root)
    if _load_chunk_metadata is _DEFAULT_LOAD_CHUNK_METADATA and db_path.exists():
        conn = _connect_sqlite_ro(db_path)
        try:
            result = {}
            for start in range(0, len(ids), 500):
                batch = ids[start : start + 500]
                placeholders = ",".join("?" for _ in batch)
                cursor = conn.execute(
                    f"SELECT name, document, document_version, version_number, page_number, "
                    f"chunk_index, content, content_hash, embedding_model, vector_id "
                    f"FROM chunks WHERE vector_id IN ({placeholders})",
                    batch,
                )
                for rec in cursor.fetchall():
                    row = _row_from_sqlite(rec)
                    result[int(row["vector_id"])] = row
            return result
        finally:
            conn.close()
    by_id, _ = _load_chunk_lookups(root)
    return {vid: by_id[vid] for vid in ids if vid in by_id}


def _query_chunks_by_names(root, names):
    unique_names = list(dict.fromkeys(names or []))
    if not unique_names:
        return {}
    db_path = _chunks_db_path(root)
    if _load_chunk_metadata is _DEFAULT_LOAD_CHUNK_METADATA and db_path.exists():
        conn = _connect_sqlite_ro(db_path)
        try:
            result = {}
            for start in range(0, len(unique_names), 500):
                batch = unique_names[start : start + 500]
                placeholders = ",".join("?" for _ in batch)
                cursor = conn.execute(
                    f"SELECT name, document, document_version, version_number, page_number, "
                    f"chunk_index, content, content_hash, embedding_model, vector_id "
                    f"FROM chunks WHERE name IN ({placeholders})",
                    batch,
                )
                for rec in cursor.fetchall():
                    row = _row_from_sqlite(rec)
                    result[row["name"]] = row
            return result
        finally:
            conn.close()
    _, by_name = _load_chunk_lookups(root)
    return {name: by_name[name] for name in unique_names if name in by_name}


def _load_chunk_lookups(root):
    if _load_chunk_metadata is _DEFAULT_LOAD_CHUNK_METADATA:
        db_path = _chunks_db_path(root)
        store = db_path if db_path.exists() else _chunks_path(root)
        return _cached_chunk_lookups(str(store))
    rows = _load_chunk_metadata(root)
    return (
        {int(row["vector_id"]): row for row in rows},
        {row["name"]: row for row in rows},
    )


def _insert_chunk_rows(rows):
    if not rows:
        return
    clean_rows = []
    for row in rows:
        item = dict(row)
        item.pop("_embedding_text", None)
        clean_rows.append(item)

    bulk_insert = getattr(frappe.db, "bulk_insert", None)
    if callable(bulk_insert) and not getattr(getattr(frappe, "flags", None), "in_test", False):
        fields = [
            "name",
            "creation",
            "modified",
            "modified_by",
            "owner",
            "docstatus",
            "idx",
            "document",
            "document_version",
            "version_number",
            "page_number",
            "chunk_index",
            "content",
            "content_hash",
            "embedding_model",
            "vector_id",
        ]
        now = frappe.utils.now()
        user = frappe.session.user or "Administrator"
        values = [
            (
                item["name"],
                now,
                now,
                user,
                user,
                0,
                0,
                item["document"],
                item.get("document_version"),
                item.get("version_number"),
                item.get("page_number"),
                item.get("chunk_index"),
                item.get("content") or "",
                item.get("content_hash"),
                item.get("embedding_model"),
                str(item["vector_id"]),
            )
            for item in clean_rows
        ]
        try:
            for start in range(0, len(values), 500):
                bulk_insert(
                    "Document Chunk",
                    fields=fields,
                    values=values[start : start + 500],
                    ignore_duplicates=True,
                )
            return
        except Exception:
            pass

    for item in clean_rows:
        frappe.get_doc(item).insert(ignore_permissions=True)


def _expected_meta(dimension=None):
    signature = embedding_signature()
    config = get_rag_config()
    return {
        "index_version": INDEX_VERSION,
        "provider": signature["provider"],
        "model": signature["model"],
        "endpoint": signature["endpoint"],
        "chunk_size": config.chunk_size,
        "chunk_overlap": config.chunk_overlap,
        "dimension": dimension,
    }


def _validate_meta(meta):
    expected = _expected_meta()
    if not meta:
        raise IndexRebuildRequired("The RAG index has not been built.")
    if (
        meta.get("index_version") != INDEX_VERSION
        or meta.get("provider") != expected["provider"]
        or meta.get("model") != expected["model"]
        or meta.get("endpoint", "") != expected["endpoint"]
        or meta.get("chunk_size") != expected["chunk_size"]
        or meta.get("chunk_overlap") != expected["chunk_overlap"]
    ):
        raise IndexRebuildRequired(
            "The RAG indexing configuration changed. Rebuild the RAG index."
        )


def _new_faiss_index(dimension):
    import faiss

    return faiss.IndexIDMap2(faiss.IndexFlatIP(dimension))


def _load_faiss(root=None):
    import faiss

    root = root or _active_root()
    meta = _read_meta(root)
    _validate_meta(meta)
    path = _faiss_path(root)
    if not path.exists():
        raise IndexRebuildRequired("The RAG vector index is missing.")
    index = faiss.read_index(str(path))
    if index.d != int(meta["dimension"]):
        raise IndexRebuildRequired("The RAG vector dimension does not match its metadata.")
    return index


def _save_faiss(index, root=None):
    import faiss

    root = root or _active_root()
    root.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(_faiss_path(root)))


def _tantivy_schema():
    import tantivy

    builder = tantivy.SchemaBuilder()
    builder.add_text_field("chunk_name", stored=True, tokenizer_name="raw")
    builder.add_text_field("document", stored=True, tokenizer_name="raw")
    builder.add_text_field("title", stored=False, tokenizer_name="default")
    builder.add_text_field("content", stored=False, tokenizer_name="default")
    return builder.build()


def _add_tantivy_document(writer, row, title):
    import tantivy

    document = tantivy.Document()
    document.add_text("chunk_name", str(row["name"]))
    document.add_text("document", str(row["document"]))
    document.add_text("title", str(title or ""))
    document.add_text("content", str(row["content"] or ""))
    writer.add_document(document)


def _open_tantivy(root=None, create=False):
    import tantivy

    path = _tantivy_path(root)
    schema = _tantivy_schema()
    if create:
        path.mkdir(parents=True, exist_ok=True)
        return tantivy.Index(schema, path=str(path))
    if not path.exists():
        raise IndexRebuildRequired("The RAG full-text index is missing.")
    return tantivy.Index.open(str(path))


def _latest_version(document):
    versions = [
        row for row in (document.get("versions") or []) if row.attachment
    ]
    return versions[-1] if versions else None


def extract_document_pages(document):
    version = _latest_version(document)
    if version and frappe.db.exists("DocType", "Document Page"):
        stored_pages = frappe.get_all(
            "Document Page",
            filters={"document_version": version.name},
            fields=["page_number", "content"],
            order_by="page_number asc",
        )
        if stored_pages:
            page_count = max(int(row.page_number) for row in stored_pages)
            pages = [""] * page_count
            for row in stored_pages:
                pages[int(row.page_number) - 1] = row.content or ""
            return pages, version

    file_url = None
    if version:
        file_url = version.preview_attachment or version.attachment

    if file_url and file_url.lower().endswith(".pdf"):
        path = get_file_path(file_url)
        if path and os.path.exists(path):
            import pdfplumber

            with decrypted_temp_file(file_url) as readable_path:
                with pdfplumber.open(readable_path) as pdf:
                    pages = [
                        (page.extract_text() or "").strip()
                        for page in pdf.pages
                    ]
            if any(pages):
                return pages, version

    return [document.ocr_content or ""], version


def _vector_id(document_name, content_hash, chunk_index):
    seed = f"{document_name}:{content_hash}:{chunk_index}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(seed).digest()[:8], "big") & ((1 << 63) - 1)


def build_document_chunks(document):
    config = get_rag_config()
    pages, version = extract_document_pages(document)
    chunks = chunk_pages(pages, config.chunk_size, config.chunk_overlap)
    rows = []
    used_ids = set()
    tags = ", ".join(row.tag for row in document.get("tags") or [])
    metadata = "\n".join(
        [
            f"Title: {document.title or ''}",
            f"Category: {document.category or ''}",
            f"Department: {document.department or ''}",
            f"Party Type: {document.party_type or ''}",
            f"Party: {document.party_name or ''}",
            f"Tags: {tags}",
            f"Description: {document.description or ''}",
        ]
    )
    for chunk in chunks:
        vector_id = _vector_id(document.name, chunk.content_hash, chunk.chunk_index)
        while vector_id in used_ids:
            vector_id = (vector_id + 1) & ((1 << 63) - 1)
        used_ids.add(vector_id)
        rows.append(
            {
                "doctype": "Document Chunk",
                "document": document.name,
                "document_version": version.name if version else None,
                "version_number": version.version_number if version else None,
                "page_number": chunk.page_number,
                "chunk_index": chunk.chunk_index,
                "content": chunk.content,
                "_embedding_text": f"{metadata}\nContent:\n{chunk.content}",
                "content_hash": chunk.content_hash,
                "embedding_model": get_rag_config().embedding_model,
                "vector_id": str(vector_id),
            }
        )
    return rows


def _cleanup_generations(active_generation, keep=3):
    generations = _generations_path()
    candidates = sorted(
        (
            path
            for path in generations.glob("gen-*")
            if path.is_dir() and path.name != active_generation
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for stale in candidates[max(keep - 1, 0) :]:
        shutil.rmtree(stale, ignore_errors=True)


def _publish_generation(temp_root):
    generation = f"gen-{frappe.generate_hash(length=16)}"
    final_root = _generations_path() / generation
    os.replace(temp_root, final_root)

    root = _root_path()
    root.mkdir(parents=True, exist_ok=True)
    pointer_temp = root / f".current-{frappe.generate_hash(length=8)}.json"
    pointer_temp.write_text(
        json.dumps({"generation": generation}),
        encoding="utf-8",
    )
    os.replace(pointer_temp, _pointer_path())
    _cleanup_generations(generation)
    return final_root


def _remove_legacy_index_files():
    root = _root_path()
    for path in (
        root / "vectors.faiss",
        root / "meta.json",
        root / "chunks.json",
        root / "tantivy",
        root.parent / "faiss",
    ):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            try:
                path.unlink()
            except OSError:
                pass


IMMUTABLE_TANTIVY_SUFFIXES = frozenset(
    {".store", ".idx", ".pos", ".term", ".fast", ".fieldnorm"}
)


def _copy_or_hardlink_generation_file(src, dst):
    if Path(src).suffix.lower() in IMMUTABLE_TANTIVY_SUFFIXES:
        try:
            os.link(src, dst)
            return dst
        except OSError:
            pass
    return shutil.copy2(src, dst)


def _copy_root_for_mutation():
    root = _active_root()
    temp_root = _generations_path() / f".tmp-{frappe.generate_hash(length=12)}"
    shutil.copytree(
        root,
        temp_root,
        copy_function=_copy_or_hardlink_generation_file,
    )
    return temp_root


def _iter_sqlite_chunks_batched(db_path, batch_size=500):
    conn = _connect_sqlite_ro(db_path)
    try:
        cursor = conn.execute(
            "SELECT name, document, document_version, version_number, page_number, "
            "chunk_index, content, content_hash, embedding_model, vector_id FROM chunks"
        )
        while True:
            records = cursor.fetchmany(batch_size)
            if not records:
                break
            yield [
                {"doctype": "Document Chunk", **_row_from_sqlite(rec)}
                for rec in records
            ]
    finally:
        conn.close()


def rebuild_index():
    starting_generation = _active_generation_name()
    temp_root = _new_temp_generation()

    try:
        document_rows = frappe.get_all(
            "Document",
            filters={"is_deleted": 0},
            fields=["name", "title"],
            limit_page_length=0,
        )
        documents = [row.name for row in document_rows]
        titles_by_doc = {row.name: row.title or "" for row in document_rows}

        text_index = _open_tantivy(temp_root, create=True)
        writer = text_index.writer()
        db_path = _chunks_db_path(temp_root)
        conn = sqlite3.connect(str(db_path))
        faiss_index = None
        dimension = None
        total_chunks = 0
        pending_batch = []

        def _flush_batch(batch, index_writer):
            nonlocal faiss_index, dimension, total_chunks
            if not batch:
                return
            vectors = embed_texts([row.pop("_embedding_text") for row in batch])
            if faiss_index is None:
                dimension = int(vectors.shape[1])
                faiss_index = _new_faiss_index(dimension)
            ids = np.asarray([row["vector_id"] for row in batch], dtype="int64")
            faiss_index.add_with_ids(vectors, ids)
            sqlite_params = []
            for row in batch:
                title = titles_by_doc.get(row["document"], "")
                row["name"] = row["content_hash"] + f"-{row['vector_id']}"
                _add_tantivy_document(index_writer, row, title)
                s = _serializable_chunk(row)
                sqlite_params.append(
                    (
                        s["name"],
                        s["document"],
                        s["document_version"],
                        s["version_number"],
                        s["page_number"],
                        s["chunk_index"],
                        s["content"],
                        s["content_hash"],
                        s["embedding_model"],
                        int(s["vector_id"]),
                    )
                )
            conn.executemany(
                """
                INSERT OR REPLACE INTO chunks (
                    name, document, document_version, version_number, page_number,
                    chunk_index, content, content_hash, embedding_model, vector_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                sqlite_params,
            )
            total_chunks += len(batch)

        try:
            _init_chunks_db(conn)
            for name in documents:
                doc_rows = build_document_chunks(frappe.get_doc("Document", name))
                for row in doc_rows:
                    pending_batch.append(row)
                    if len(pending_batch) >= 128:
                        _flush_batch(pending_batch, writer)
                        pending_batch = []
            if pending_batch:
                _flush_batch(pending_batch, writer)
                pending_batch = []
            if faiss_index is None:
                probe = embed_texts(["dimension probe"])
                dimension = int(probe.shape[1])
                faiss_index = _new_faiss_index(dimension)
            conn.commit()
        finally:
            conn.close()

        _save_faiss(faiss_index, temp_root)
        writer.commit()
        _write_meta(_expected_meta(dimension), temp_root)
        del writer, text_index, faiss_index
        gc.collect()

        with FileLock(str(_lock_path())):
            if _active_generation_name() != starting_generation:
                raise IndexRebuildRequired(
                    "The active RAG generation changed during rebuild. "
                    "Run the rebuild again."
                )
            frappe.db.delete("Document Chunk")
            for db_batch in _iter_sqlite_chunks_batched(db_path, batch_size=500):
                _insert_chunk_rows(db_batch)
            active_root = _publish_generation(temp_root)
            _remove_legacy_index_files()
            frappe.db.commit()
    finally:
        if temp_root.exists():
            shutil.rmtree(temp_root, ignore_errors=True)

    return {
        "documents": len(documents),
        "chunks": total_chunks,
        "dimension": dimension,
        "generation": active_root.name,
    }


def _remove_document_from_indexes(rows, faiss_index, text_index):
    if rows:
        ids = np.asarray([int(row.vector_id) for row in rows], dtype="int64")
        faiss_index.remove_ids(ids)
        writer = text_index.writer()
        for row in rows:
            writer.delete_documents("chunk_name", row.name)
        writer.commit()


def index_document(document_name):
    document = frappe.get_doc("Document", document_name)
    if document.is_deleted:
        return remove_document(document_name)
    rows = build_document_chunks(document)
    vectors = embed_texts([row["_embedding_text"] for row in rows]) if rows else None

    with FileLock(str(_lock_path())):
        temp_root = _copy_root_for_mutation()
        try:
            faiss_index = _load_faiss(temp_root)
            text_index = _open_tantivy(temp_root)
            if rows and vectors.shape[1] != faiss_index.d:
                raise IndexRebuildRequired("Embedding dimension changed. Rebuild the index.")

            existing_rows = _load_document_chunks_from_store(temp_root, [document_name])
            _remove_document_from_indexes(existing_rows, faiss_index, text_index)
            if rows:
                ids = np.asarray([row["vector_id"] for row in rows], dtype="int64")
                faiss_index.add_with_ids(vectors, ids)
                writer = text_index.writer()
                for row in rows:
                    row["name"] = row["content_hash"] + f"-{row['vector_id']}"
                    _add_tantivy_document(writer, row, document.title)
                writer.commit()
                del writer
            _mutate_sqlite_chunks(temp_root, [document_name], rows)
            _save_faiss(faiss_index, temp_root)
            del text_index, faiss_index
            gc.collect()

            frappe.db.delete("Document Chunk", {"document": document_name})
            _insert_chunk_rows(rows)
            _publish_generation(temp_root)
            frappe.db.commit()
        finally:
            if temp_root.exists():
                shutil.rmtree(temp_root, ignore_errors=True)
    return len(rows)


def remove_document(document_name):
    try:
        _active_root()
    except IndexRebuildRequired:
        frappe.db.delete("Document Chunk", {"document": document_name})
        return
    with FileLock(str(_lock_path())):
        temp_root = _copy_root_for_mutation()
        try:
            faiss_index = _load_faiss(temp_root)
            text_index = _open_tantivy(temp_root)
            existing_rows = _load_document_chunks_from_store(temp_root, [document_name])
            _remove_document_from_indexes(existing_rows, faiss_index, text_index)
            _mutate_sqlite_chunks(temp_root, [document_name], [])
            _save_faiss(faiss_index, temp_root)
            del text_index, faiss_index
            gc.collect()
            frappe.db.delete("Document Chunk", {"document": document_name})
            _publish_generation(temp_root)
            frappe.db.commit()
        finally:
            if temp_root.exists():
                shutil.rmtree(temp_root, ignore_errors=True)


def refresh_document_indexes(document_names, remove=False):
    names = list(dict.fromkeys(document_names or []))
    if not names:
        return {"completed": [], "failed": []}

    if remove:
        try:
            _active_root()
        except IndexRebuildRequired:
            frappe.db.delete("Document Chunk", {"document": ["in", names]})
            frappe.db.commit()
            return {"completed": names, "failed": []}

        with FileLock(str(_lock_path())):
            temp_root = _copy_root_for_mutation()
            try:
                faiss_index = _load_faiss(temp_root)
                text_index = _open_tantivy(temp_root)
                existing_rows = _load_document_chunks_from_store(temp_root, names)
                _remove_document_from_indexes(existing_rows, faiss_index, text_index)
                _mutate_sqlite_chunks(temp_root, names, [])
                _save_faiss(faiss_index, temp_root)
                del text_index, faiss_index
                gc.collect()
                frappe.db.delete("Document Chunk", {"document": ["in", names]})
                _publish_generation(temp_root)
                frappe.db.commit()
            finally:
                if temp_root.exists():
                    shutil.rmtree(temp_root, ignore_errors=True)
        return {"completed": names, "failed": []}

    # Prepare chunks and embeddings outside the write lock
    completed = []
    failed = []
    prepared = []
    for document_name in names:
        try:
            if not frappe.db.exists("Document", {"name": document_name, "is_deleted": 0}):
                completed.append(document_name)
                continue
            document = frappe.get_doc("Document", document_name)
            rows = build_document_chunks(document)
            vectors = (
                embed_texts([row["_embedding_text"] for row in rows])
                if rows
                else None
            )
            prepared.append((document_name, document.title or "", rows, vectors))
        except IndexRebuildRequired as exc:
            failed.append({"document": document_name, "error": str(exc)})
            break
        except Exception:
            failed.append({"document": document_name, "error": "Index update failed."})
            frappe.log_error(
                title="Document RAG Batch Index Error",
                message=frappe.get_traceback(),
            )

    if not prepared:
        return {"completed": completed, "failed": failed}

    try:
        with FileLock(str(_lock_path())):
            temp_root = _copy_root_for_mutation()
            try:
                faiss_index = _load_faiss(temp_root)
                text_index = _open_tantivy(temp_root)
                batch_doc_names = {doc_name for doc_name, _, _, _ in prepared}
                for _, _, rows, vectors in prepared:
                    if rows and vectors.shape[1] != faiss_index.d:
                        raise IndexRebuildRequired(
                            "Embedding dimension changed. Rebuild the index."
                        )

                existing_rows = _load_document_chunks_from_store(temp_root, list(batch_doc_names))
                _remove_document_from_indexes(existing_rows, faiss_index, text_index)

                all_new_rows = []
                writer = text_index.writer()
                for doc_name, doc_title, rows, vectors in prepared:
                    if rows:
                        ids = np.asarray(
                            [row["vector_id"] for row in rows],
                            dtype="int64",
                        )
                        faiss_index.add_with_ids(vectors, ids)
                        for row in rows:
                            row["name"] = row["content_hash"] + f"-{row['vector_id']}"
                            _add_tantivy_document(writer, row, doc_title)
                        all_new_rows.extend(rows)
                writer.commit()
                del writer

                _mutate_sqlite_chunks(temp_root, list(batch_doc_names), all_new_rows)
                _save_faiss(faiss_index, temp_root)
                del text_index, faiss_index
                gc.collect()

                frappe.db.delete(
                    "Document Chunk",
                    {"document": ["in", list(batch_doc_names)]},
                )
                _insert_chunk_rows(all_new_rows)
                _publish_generation(temp_root)
                frappe.db.commit()
                completed.extend(doc_name for doc_name, _, _, _ in prepared)
            finally:
                if temp_root.exists():
                    shutil.rmtree(temp_root, ignore_errors=True)
    except IndexRebuildRequired as exc:
        for doc_name, _, _, _ in prepared:
            failed.append({"document": doc_name, "error": str(exc)})
    except Exception:
        for doc_name, _, _, _ in prepared:
            failed.append({"document": doc_name, "error": "Index update failed."})
        frappe.log_error(
            title="Document RAG Batch Index Error",
            message=frappe.get_traceback(),
        )

    return {"completed": completed, "failed": failed}


def _semantic_search(
    query,
    allowed_documents,
    limit,
    query_vector=None,
    root=None,
):
    root = root or _active_root()
    index = _load_faiss(root)
    if index.ntotal == 0 or not allowed_documents:
        return []
    if query_vector is None:
        query_vector = embed_texts([query])
    minimum_score = get_rag_config().min_semantic_score
    wanted = min(max(limit * 4, 20), index.ntotal)
    results = []
    seen = set()
    while wanted <= index.ntotal:
        scores, ids = index.search(query_vector, wanted)
        candidate_ids = [
            int(vector_id)
            for score, vector_id in zip(scores[0], ids[0])
            if float(score) >= minimum_score and int(vector_id) >= 0
        ]
        by_id = _query_chunks_by_vector_ids(root, candidate_ids)
        for score, vector_id in zip(scores[0], ids[0]):
            if float(score) < minimum_score:
                continue
            raw_row = by_id.get(int(vector_id))
            if (
                not raw_row
                or raw_row["document"] not in allowed_documents
                or raw_row["name"] in seen
            ):
                continue
            row = frappe._dict(raw_row)
            row.score = float(score)
            seen.add(row.name)
            results.append(row)
            if len(results) >= limit:
                return results
        if wanted == index.ntotal:
            break
        wanted = min(wanted * 2, index.ntotal)
    return results


def _lexical_search(query, allowed_documents, limit, root=None):
    if not allowed_documents:
        return []
    root = root or _active_root()
    index = _open_tantivy(root)
    index.reload()
    searcher = index.searcher()
    from document_management.search.query import build_natural_query

    parsed = build_natural_query(index, query, ["title", "content"])
    hits = searcher.search(parsed, min(max(limit * 10, 50), 500))
    names = []
    scores = {}
    for score, address in hits.hits:
        stored = searcher.doc(address)
        name = stored["chunk_name"][0]
        document = stored["document"][0]
        if document not in allowed_documents:
            continue
        names.append(name)
        scores[name] = float(score)
        if len(names) >= limit:
            break
    by_name = _query_chunks_by_names(root, names)
    ordered = []
    for name in names:
        raw_row = by_name.get(name)
        if raw_row:
            row = frappe._dict(raw_row)
            row.score = scores[name]
            ordered.append(row)
    return ordered


def search_chunks(
    query,
    allowed_documents,
    limit=8,
    max_per_document=3,
    use_reranker=True,
):
    allowed_documents = set(allowed_documents)
    query = (query or "").strip()
    if not query or not allowed_documents:
        return []

    semantic_vector = None
    active_root = _active_root()
    try:
        _validate_meta(_read_meta(active_root))
        semantic_vector = embed_texts([query])
    except IndexRebuildRequired:
        raise
    except Exception as exc:
        frappe.logger("document_rag").warning(
            "Semantic query embedding unavailable: %s",
            type(exc).__name__,
        )

    semantic = []
    if semantic_vector is not None:
        try:
            semantic = _semantic_search(
                query,
                allowed_documents,
                max(limit * 3, 20),
                query_vector=semantic_vector,
                root=active_root,
            )
        except IndexRebuildRequired:
            raise
        except Exception as exc:
            frappe.logger("document_rag").warning(
                "Semantic retrieval unavailable: %s",
                type(exc).__name__,
            )

    try:
        lexical = _lexical_search(
            query,
            allowed_documents,
            max(limit * 3, 20),
            root=active_root,
        )
    except IndexRebuildRequired:
        raise
    except Exception as exc:
        frappe.logger("document_rag").warning(
            "Lexical retrieval unavailable: %s",
            type(exc).__name__,
        )
        lexical = []

    fused = {}
    for result_list in (semantic, lexical):
        for rank, row in enumerate(result_list, start=1):
            entry = fused.setdefault(row.name, {"row": row, "score": 0.0})
            entry["score"] += 1.0 / (60 + rank)

    candidate_limit = min(max(limit * max_per_document, 20), 200)
    ranked = sorted(
        fused.values(),
        key=lambda item: item["score"],
        reverse=True,
    )[:candidate_limit]
    if use_reranker:
        try:
            reranker_scores = rerank_texts(
                query,
                [item["row"].content for item in ranked],
            )
            if reranker_scores is not None:
                for item, reranker_score in zip(ranked, reranker_scores):
                    item["reranker_score"] = reranker_score
                ranked.sort(
                    key=lambda item: item["reranker_score"],
                    reverse=True,
                )
        except Exception as exc:
            frappe.logger("document_rag").warning(
                "Reranker unavailable: %s",
                type(exc).__name__,
            )
    selected = []
    per_document = {}
    for item in ranked:
        row = item["row"]
        count = per_document.get(row.document, 0)
        if count >= max_per_document:
            continue
        row.score = item.get("reranker_score", item["score"])
        selected.append(row)
        per_document[row.document] = count + 1
        if len(selected) >= limit:
            break
    return selected


def search_documents(query, allowed_documents, limit=50):
    from document_management.search.query import make_excerpt

    chunks = search_chunks(
        query,
        allowed_documents,
        limit=min(max(int(limit), 1), 500),
        max_per_document=1,
        use_reranker=False,
    )
    return [
        {
            "doc_type": "Document",
            "doc_name": chunk.document,
            "score": float(chunk.score),
            "page": chunk.page_number,
            "version": chunk.version_number,
            "excerpt": make_excerpt(chunk.content, query, maximum=720),
        }
        for chunk in chunks
    ]


def index_status():
    try:
        root = _active_root()
        meta = _read_meta(root)
        metadata_chunks = _count_chunk_metadata(root)
        database_chunks = frappe.db.count("Document Chunk")
        _validate_meta(meta)
        index = _load_faiss(root)
        _open_tantivy(root)
        valid = index.ntotal == metadata_chunks == database_chunks
        vectors = index.ntotal
        error = (
            None
            if valid
            else "Vector, generation metadata, and database chunk counts differ."
        )
        generation = root.name
    except Exception as exc:
        meta = locals().get("meta")
        metadata_chunks = locals().get("metadata_chunks", 0)
        database_chunks = locals().get("database_chunks", 0)
        valid = False
        vectors = 0
        generation = None
        error = str(exc)
    return {
        "valid": valid,
        "generation": generation,
        "meta": meta,
        "chunks": database_chunks,
        "generation_chunks": metadata_chunks,
        "vectors": vectors,
        "error": error,
    }
