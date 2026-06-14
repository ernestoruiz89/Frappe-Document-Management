from types import SimpleNamespace

from document_management.search.indexer import (
    _parse_doctypes,
    get_document_content,
)
from document_management.search.query import (
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


def _field(fieldname, fieldtype, label=None):
    return SimpleNamespace(
        fieldname=fieldname,
        fieldtype=fieldtype,
        label=label or fieldname,
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
