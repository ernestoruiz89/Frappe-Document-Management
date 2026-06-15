import gc
import json
import os
import shutil
from pathlib import Path

import frappe
from filelock import FileLock, Timeout

from document_management.search.query import (
    build_natural_query,
    fold_text,
    make_excerpt,
)


INDEX_VERSION = 3
SEARCH_FIELDS = {
    "doc_name": 6.0,
    "doc_name_normalized": 6.0,
    "title": 4.0,
    "title_normalized": 4.0,
    "content": 1.0,
    "content_normalized": 1.0,
}


class IndexRebuildRequired(RuntimeError):
    pass


class IndexBusy(RuntimeError):
    pass


def _root_path():
    settings = frappe.get_single("Document Management Settings")
    base_path = frappe.utils.get_site_path(
        "private",
        settings.index_path or "search_index",
    )
    return Path(base_path) / f"tantivy_v{INDEX_VERSION}"


def _generations_path():
    return _root_path() / "generations"


def _pointer_path():
    return _root_path() / "current.json"


def _lock_path():
    return _root_path() / "index.lock"


def _active_generation_name():
    pointer = _pointer_path()
    if not pointer.exists():
        return None
    try:
        generation = json.loads(pointer.read_text(encoding="utf-8")).get(
            "generation"
        )
    except (OSError, TypeError, ValueError):
        raise IndexRebuildRequired("The operational search index pointer is invalid.")
    if (
        not isinstance(generation, str)
        or not generation.startswith("gen-")
        or Path(generation).name != generation
    ):
        raise IndexRebuildRequired("The operational search index pointer is invalid.")
    return generation


def get_tantivy_index_path():
    generation = _active_generation_name()
    if not generation:
        raise IndexRebuildRequired(
            "The operational search index must be rebuilt before first use."
        )
    path = _generations_path() / generation / "tantivy"
    if not path.is_dir():
        raise IndexRebuildRequired(
            "The active operational search index generation is missing."
        )
    return str(path)


def index_exists():
    try:
        path = Path(get_tantivy_index_path())
        return any(path.iterdir())
    except (IndexRebuildRequired, OSError):
        return False


def get_schema():
    import tantivy

    schema_builder = tantivy.SchemaBuilder()
    schema_builder.add_text_field(
        "record_key",
        stored=True,
        tokenizer_name="raw",
    )
    schema_builder.add_text_field(
        "doc_type",
        stored=True,
        tokenizer_name="raw",
    )
    schema_builder.add_text_field(
        "doc_name",
        stored=True,
        tokenizer_name="default",
    )
    schema_builder.add_text_field(
        "doc_name_normalized",
        tokenizer_name="default",
    )
    schema_builder.add_text_field(
        "title",
        stored=True,
        tokenizer_name="default",
    )
    schema_builder.add_text_field(
        "title_normalized",
        tokenizer_name="default",
    )
    schema_builder.add_text_field(
        "content",
        stored=True,
        tokenizer_name="default",
    )
    schema_builder.add_text_field(
        "content_normalized",
        tokenizer_name="default",
    )
    return schema_builder.build()


def get_index():
    import tantivy

    try:
        return tantivy.Index.open(get_tantivy_index_path())
    except IndexRebuildRequired:
        raise
    except Exception as exc:
        raise IndexRebuildRequired(
            "The operational search index is incompatible or damaged. Rebuild it."
        ) from exc


def _new_document(record):
    import tantivy

    doc_type = str(record.get("doc_type") or "")
    doc_name = str(record.get("doc_name") or "")
    title = str(record.get("title") or "")
    content = str(record.get("content") or "")

    document = tantivy.Document()
    document.add_text("record_key", f"{doc_type}:{doc_name}")
    document.add_text("doc_type", doc_type)
    document.add_text("doc_name", doc_name)
    document.add_text("doc_name_normalized", fold_text(doc_name))
    document.add_text("title", title)
    document.add_text("title_normalized", fold_text(title))
    document.add_text("content", content)
    document.add_text("content_normalized", fold_text(content))
    return document


def index_document(doc_type, doc_name, title, content):
    record_key = f"{doc_type}:{doc_name}"
    _root_path().mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(str(_lock_path()), timeout=5):
            index = get_index()
            writer = index.writer()
            writer.delete_documents("record_key", record_key)
            writer.add_document(
                _new_document(
                    {
                        "doc_type": doc_type,
                        "doc_name": doc_name,
                        "title": title,
                        "content": content,
                    }
                )
            )
            writer.commit()
    except Timeout as exc:
        raise IndexBusy("The operational search index is rebuilding.") from exc


def remove_document(doc_type, doc_name):
    _root_path().mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(str(_lock_path()), timeout=5):
            index = get_index()
            writer = index.writer()
            writer.delete_documents("record_key", f"{doc_type}:{doc_name}")
            writer.commit()
    except Timeout as exc:
        raise IndexBusy("The operational search index is rebuilding.") from exc


def _search_hits(searcher, query, limit):
    return searcher.search(query, limit).hits


def _apply_doctype_filter(index, query, doctypes):
    if not doctypes:
        return query
    import tantivy

    type_clauses = [
        (
            tantivy.Occur.Should,
            index.parse_query(json.dumps(str(doctype)), ["doc_type"]),
        )
        for doctype in doctypes
    ]
    type_query = (
        type_clauses[0][1]
        if len(type_clauses) == 1
        else tantivy.Query.boolean_query(type_clauses)
    )
    return tantivy.Query.boolean_query(
        [
            (tantivy.Occur.Must, query),
            (tantivy.Occur.Must, type_query),
        ]
    )


def search(query_str, limit=10, doctypes=None):
    index = get_index()
    index.reload()
    searcher = index.searcher()

    strict_query = build_natural_query(
        index,
        query_str,
        SEARCH_FIELDS,
        require_all=True,
    )
    strict_query = _apply_doctype_filter(index, strict_query, doctypes)
    raw_hits = list(_search_hits(searcher, strict_query, limit))

    if len(raw_hits) < limit:
        relaxed_query = build_natural_query(
            index,
            query_str,
            SEARCH_FIELDS,
            require_all=False,
        )
        relaxed_query = _apply_doctype_filter(index, relaxed_query, doctypes)
        seen_addresses = {str(address) for _, address in raw_hits}
        for score, address in _search_hits(searcher, relaxed_query, limit * 2):
            address_key = str(address)
            if address_key in seen_addresses:
                continue
            raw_hits.append((score, address))
            seen_addresses.add(address_key)
            if len(raw_hits) >= limit:
                break

    def get_first_val(doc, field):
        try:
            val = doc[field]
            if isinstance(val, list) and val:
                return val[0]
            if val and not isinstance(val, list):
                return val
        except Exception:
            pass
        return ""

    hits = []
    for score, doc_address in raw_hits[:limit]:
        doc = searcher.doc(doc_address)
        hits.append(
            {
                "score": score,
                "doc_type": get_first_val(doc, "doc_type"),
                "doc_name": get_first_val(doc, "doc_name"),
                "title": get_first_val(doc, "title"),
                "excerpt": _excerpt(
                    get_first_val(doc, "content"),
                    query_str,
                ),
            }
        )
    return hits


def _excerpt(content, query, maximum=280):
    return make_excerpt(content, query, maximum=maximum)


def _cleanup_generations(active_generation, keep=2):
    generations = _generations_path()
    if not generations.exists():
        return
    candidates = sorted(
        (
            path
            for path in generations.iterdir()
            if path.is_dir()
            and not path.name.startswith(".tmp-")
            and path.name != active_generation
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for stale in candidates[max(keep - 1, 0) :]:
        shutil.rmtree(stale, ignore_errors=True)


def _cleanup_legacy_indexes():
    root = _root_path()
    for legacy in (root.parent / "tantivy_v2", root.parent / "tantivy"):
        if legacy.is_dir():
            shutil.rmtree(legacy, ignore_errors=True)


def rebuild(records):
    import tantivy

    root = _root_path()
    generations = _generations_path()
    root.mkdir(parents=True, exist_ok=True)
    generations.mkdir(parents=True, exist_ok=True)

    with FileLock(str(_lock_path())):
        temp_root = generations / f".tmp-{frappe.generate_hash(length=12)}"
        index_path = temp_root / "tantivy"
        index_path.mkdir(parents=True, exist_ok=False)
        count = 0
        try:
            index = tantivy.Index(get_schema(), path=str(index_path))
            writer = index.writer()
            for record in records:
                writer.add_document(_new_document(record))
                count += 1
            writer.commit()
            del writer, index
            gc.collect()

            generation = f"gen-{frappe.generate_hash(length=16)}"
            final_root = generations / generation
            os.replace(temp_root, final_root)

            pointer_temp = root / f".current-{frappe.generate_hash(length=8)}.json"
            pointer_temp.write_text(
                json.dumps(
                    {
                        "generation": generation,
                        "index_version": INDEX_VERSION,
                    }
                ),
                encoding="utf-8",
            )
            os.replace(pointer_temp, _pointer_path())
            _cleanup_generations(generation)
            _cleanup_legacy_indexes()
            return {"count": count, "generation": generation}
        finally:
            if temp_root.exists():
                shutil.rmtree(temp_root, ignore_errors=True)
