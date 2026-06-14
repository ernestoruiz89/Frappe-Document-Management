import os
import frappe
from document_management.search.query import (
    build_natural_query,
    make_excerpt,
)

def get_tantivy_index_path():
    settings = frappe.get_single('Document Management Settings')
    base_path = frappe.utils.get_site_path('private', settings.index_path or 'search_index')
    tantivy_path = os.path.join(base_path, 'tantivy_v2')
    if not os.path.exists(tantivy_path):
        os.makedirs(tantivy_path, exist_ok=True)
    return tantivy_path


def index_exists():
    path = get_tantivy_index_path()
    return any(os.scandir(path))

def get_schema():
    import tantivy
    schema_builder = tantivy.SchemaBuilder()
    schema_builder.add_text_field(
        "record_key",
        stored=True,
        tokenizer_name="raw",
    )
    schema_builder.add_text_field("doc_type", stored=True)
    schema_builder.add_text_field("doc_name", stored=True)
    schema_builder.add_text_field("title", stored=True, tokenizer_name="default")
    schema_builder.add_text_field("content", stored=True, tokenizer_name="default")
    return schema_builder.build()

def get_index():
    import tantivy
    index_path = get_tantivy_index_path()
    schema = get_schema()
    try:
        index = tantivy.Index.open(index_path)
    except Exception:
        index = tantivy.Index(schema, path=index_path)
    return index

def index_document(doc_type, doc_name, title, content):
    import tantivy
    
    # Ensure all values are strings for Tantivy compatibility
    doc_type = str(doc_type) if doc_type is not None else ""
    doc_name = str(doc_name) if doc_name is not None else ""
    title = str(title) if title is not None else ""
    content = str(content) if content is not None else ""
    
    index = get_index()
    writer = index.writer()
    
    # First, delete existing document if any to avoid duplicates
    record_key = f"{doc_type}:{doc_name}"
    writer.delete_documents("record_key", record_key)
    
    doc = tantivy.Document()
    doc.add_text("record_key", record_key)
    doc.add_text("doc_type", doc_type)
    doc.add_text("doc_name", doc_name)
    doc.add_text("title", title)
    doc.add_text("content", content)
    
    writer.add_document(doc)
    writer.commit()

def remove_document(doc_type, doc_name):
    doc_type = str(doc_type) if doc_type is not None else ""
    doc_name = str(doc_name) if doc_name is not None else ""
    index = get_index()
    writer = index.writer()
    writer.delete_documents("record_key", f"{doc_type}:{doc_name}")
    writer.commit()

def search(query_str, limit=10):
    index = get_index()
    index.reload()
    searcher = index.searcher()
    
    query = build_natural_query(index, query_str, ["title", "content"])
        
    results = searcher.search(query, limit)
    
    def get_first_val(doc, field):
        try:
            val = doc[field]
            if isinstance(val, list) and len(val) > 0:
                return val[0]
            elif val and not isinstance(val, list):
                return val
        except Exception:
            pass
        return ""

    hits = []
    for score, doc_address in results.hits:
        doc = searcher.doc(doc_address)
        hits.append({
            "score": score,
            "doc_type": get_first_val(doc, "doc_type"),
            "doc_name": get_first_val(doc, "doc_name"),
            "title": get_first_val(doc, "title"),
            "excerpt": _excerpt(
                get_first_val(doc, "content"),
                query_str,
            ),
        })
    return hits


def _excerpt(content, query, maximum=280):
    return make_excerpt(content, query, maximum=maximum)
