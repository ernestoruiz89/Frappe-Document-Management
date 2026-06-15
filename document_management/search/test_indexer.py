import json
import sys
from pathlib import Path
from types import SimpleNamespace

from document_management.search import tantivy_backend
from document_management.search.indexer import (
    _parse_doctypes,
    get_document_content,
    get_document_title,
)
from document_management.search.query import (
    build_natural_query,
    fold_text,
    make_excerpt,
    normalize_language,
    query_tokens,
    significant_terms,
)


class Row(dict):
    def __init__(self, fields, **values):
        super().__init__(values)
        self.meta = SimpleNamespace(fields=fields)

    def get(self, key, default=None):
        return super().get(key, default)


def _field(fieldname, fieldtype, label=None, **values):
    return SimpleNamespace(
        fieldname=fieldname,
        fieldtype=fieldtype,
        label=label or fieldname,
        **values,
    )


def test_generic_index_includes_links_dates_amounts_and_children():
    child = Row(
        [
            _field("account", "Link", "Account"),
            _field("amount", "Currency", "Amount"),
        ],
        account="Cash - CO",
        amount=1250.50,
    )
    document = Row(
        [
            _field("customer", "Link", "Customer"),
            _field("status", "Select", "Status"),
            _field("posting_date", "Date", "Posting Date"),
            _field("items", "Table", "Items"),
        ],
        customer="CUS-001",
        status="Active",
        posting_date="2026-06-13",
        items=[child],
    )

    content = get_document_content(document)

    assert "Customer: CUS-001" in content
    assert "Status: Active" in content
    assert "Posting Date: 2026-06-13" in content
    assert "Account: Cash - CO" in content
    assert "Amount: 1250.5" in content


def test_generic_index_excludes_hidden_and_restricted_fields():
    document = Row(
        [
            _field("public_note", "Data", "Public Note"),
            _field("hidden_note", "Data", "Hidden Note", hidden=1),
            _field("restricted_note", "Data", "Restricted Note", permlevel=1),
        ],
        public_note="Visible",
        hidden_note="Secret hidden value",
        restricted_note="Secret restricted value",
    )

    content = get_document_content(document)

    assert "Public Note: Visible" in content
    assert "Secret hidden value" not in content
    assert "Secret restricted value" not in content


def test_generic_index_strips_html_from_rich_text():
    document = Row(
        [_field("description", "Text Editor", "Description")],
        description="<p>Approved <strong>contract</strong>&nbsp;today</p>",
    )

    content = get_document_content(document)

    assert content == "Description: Approved contract today"
    assert "<strong>" not in content


def test_restricted_title_field_falls_back_to_document_name():
    title_field = _field(
        "private_title",
        "Data",
        "Private Title",
        permlevel=1,
    )
    document = Row([title_field], private_title="Confidential title")
    document.name = "REC-0001"
    document.meta.title_field = "private_title"
    document.meta.get_field = lambda fieldname: (
        title_field if fieldname == "private_title" else None
    )

    assert get_document_title(document) == "REC-0001"


def test_doctype_filter_accepts_json_and_rejects_invalid_shapes():
    assert _parse_doctypes('["Loan", "Payment"]') == {"Loan", "Payment"}
    assert _parse_doctypes('{"doctype": "Loan"}') == set()


def test_spanish_stopwords_are_not_search_criteria():
    assert query_tokens("Señal de alerta") == ["señal", "de", "alerta"]
    assert significant_terms("Señal de alerta", language="es-NI") == [
        "señal",
        "alerta",
    ]
    assert significant_terms(
        "¿Cuáles son las señales de alerta?",
        language="es",
    ) == [
        "señales",
        "alerta",
    ]
    assert significant_terms("de la", language="es") == []


def test_english_stopwords_are_not_search_criteria():
    assert significant_terms(
        "What are the warning signs of fraud?",
        language="en_US",
    ) == [
        "warning",
        "signs",
        "fraud",
    ]
    assert significant_terms(
        "the documents of the customer",
        language="en",
    ) == [
        "documents",
        "customer",
    ]


def test_french_stopwords_are_not_search_criteria():
    assert significant_terms(
        "Quels sont les signes d'alerte de fraude ?",
        language="fr-FR",
    ) == [
        "signes",
        "alerte",
        "fraude",
    ]
    assert significant_terms("les documents du client", language="fr") == [
        "documents",
        "client",
    ]


def test_only_selected_language_stopwords_are_applied():
    assert normalize_language("es-NI") == "es"
    assert normalize_language("en_US") == "en"
    assert normalize_language("Spanish") == "es"
    assert normalize_language("Español (Nicaragua)") == "es"
    assert normalize_language("French") == "fr"
    assert significant_terms("the documents", language="es") == [
        "the",
        "documents",
    ]


def test_excerpt_is_centered_on_a_matching_term():
    content = ("inicio " * 80) + "señal de alerta importante" + (" final" * 80)
    excerpt = make_excerpt(
        content,
        "señal de alerta",
        maximum=120,
        language="es",
    )

    assert "señal de alerta" in excerpt
    assert excerpt.startswith("...")
    assert excerpt.endswith("...")


def test_long_document_excerpt_can_supply_list_card_context():
    content = ("contexto previo " * 80) + "riesgo de fraude" + (
        " contexto posterior" * 80
    )
    excerpt = make_excerpt(
        content,
        "riesgo de fraude",
        maximum=720,
        language="es",
    )

    assert "riesgo de fraude" in excerpt
    assert len(excerpt) <= 726
    assert len(excerpt) > 500


def test_fold_text_normalizes_accents_for_indexing():
    assert fold_text("Período de Nómina") == "periodo de nomina"


def test_natural_query_weights_fields_and_supports_relaxed_mode(monkeypatch):
    class FakeQuery:
        @staticmethod
        def empty_query():
            return ("empty",)

        @staticmethod
        def boost_query(query, weight):
            return ("boost", query, weight)

        @staticmethod
        def boolean_query(clauses):
            return ("boolean", clauses)

    fake_tantivy = SimpleNamespace(
        Query=FakeQuery,
        Occur=SimpleNamespace(Must="must", Should="should"),
    )
    monkeypatch.setitem(sys.modules, "tantivy", fake_tantivy)

    class FakeIndex:
        def parse_query(self, query, fields):
            return ("parsed", query, tuple(fields))

    strict = build_natural_query(
        FakeIndex(),
        "Nómina activa",
        {"doc_name": 6, "title": 4, "content": 1},
        language="es",
        require_all=True,
    )
    relaxed = build_natural_query(
        FakeIndex(),
        "Nómina activa",
        {"doc_name": 6, "title": 4, "content": 1},
        language="es",
        require_all=False,
    )

    strict_occurrences = [clause[0] for clause in strict[1][:2]]
    relaxed_occurrences = [clause[0] for clause in relaxed[1][:2]]
    assert strict_occurrences == ["must", "must"]
    assert relaxed_occurrences == ["should", "should"]
    assert "nomina" in repr(strict)
    assert "doc_name" in repr(strict)
    assert "6.0" in repr(strict)


def test_tantivy_rebuild_publishes_generation_atomically(monkeypatch, tmp_path):
    class FakeSchemaBuilder:
        def add_text_field(self, *args, **kwargs):
            return None

        def build(self):
            return "schema"

    class FakeDocument:
        def __init__(self):
            self.values = {}

        def add_text(self, field, value):
            self.values[field] = value

    class FakeWriter:
        def __init__(self, path):
            self.path = path
            self.documents = []

        def add_document(self, document):
            self.documents.append(document)

        def commit(self):
            (self.path / "documents.json").write_text(
                json.dumps([doc.values for doc in self.documents]),
                encoding="utf-8",
            )

    class FakeIndex:
        def __init__(self, schema, path):
            self.path = Path(path)

        def writer(self):
            return FakeWriter(self.path)

    fake_tantivy = SimpleNamespace(
        SchemaBuilder=FakeSchemaBuilder,
        Document=FakeDocument,
        Index=FakeIndex,
    )
    hashes = iter(["temp123", "generation123", "pointer1"])
    monkeypatch.setitem(sys.modules, "tantivy", fake_tantivy)
    monkeypatch.setattr(
        tantivy_backend,
        "_root_path",
        lambda: tmp_path / "tantivy_v3",
    )
    monkeypatch.setattr(
        tantivy_backend.frappe,
        "generate_hash",
        lambda length: next(hashes),
    )

    result = tantivy_backend.rebuild(
        [
            {
                "doc_type": "Customer",
                "doc_name": "CUS-001",
                "title": "Cliente Nómina",
                "content": "Contrato activo",
            }
        ]
    )

    pointer = json.loads(
        (tmp_path / "tantivy_v3" / "current.json").read_text(
            encoding="utf-8"
        )
    )
    index_file = (
        tmp_path
        / "tantivy_v3"
        / "generations"
        / pointer["generation"]
        / "tantivy"
        / "documents.json"
    )
    indexed = json.loads(index_file.read_text(encoding="utf-8"))
    assert result["count"] == 1
    assert pointer["generation"] == "gen-generation123"
    assert indexed[0]["doc_name_normalized"] == "cus-001"
    assert indexed[0]["title_normalized"] == "cliente nomina"
