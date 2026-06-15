import re
import unicodedata


SPANISH_STOPWORDS = frozenset(
    {
        "a",
        "al",
        "ante",
        "bajo",
        "con",
        "contra",
        "como",
        "cómo",
        "cual",
        "cuál",
        "cuales",
        "cuáles",
        "cuando",
        "cuándo",
        "cuanto",
        "cuánto",
        "de",
        "del",
        "desde",
        "donde",
        "dónde",
        "durante",
        "e",
        "el",
        "ella",
        "ellas",
        "ellos",
        "en",
        "entre",
        "era",
        "es",
        "esa",
        "ese",
        "eso",
        "esta",
        "este",
        "esto",
        "fue",
        "ha",
        "hay",
        "hacia",
        "hasta",
        "la",
        "las",
        "lo",
        "los",
        "más",
        "mi",
        "ni",
        "no",
        "o",
        "para",
        "pero",
        "por",
        "que",
        "qué",
        "quien",
        "quién",
        "quienes",
        "quiénes",
        "se",
        "según",
        "ser",
        "sin",
        "son",
        "sobre",
        "su",
        "sus",
        "tras",
        "tu",
        "tiene",
        "tienen",
        "un",
        "una",
        "uno",
        "unos",
        "unas",
        "y",
        "ya",
    }
)

ENGLISH_STOPWORDS = frozenset(
    {
        "a",
        "about",
        "above",
        "after",
        "again",
        "against",
        "all",
        "am",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "because",
        "been",
        "before",
        "being",
        "below",
        "between",
        "both",
        "but",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "doing",
        "down",
        "during",
        "each",
        "few",
        "for",
        "from",
        "further",
        "had",
        "has",
        "have",
        "having",
        "he",
        "her",
        "here",
        "hers",
        "herself",
        "him",
        "himself",
        "his",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "itself",
        "just",
        "me",
        "more",
        "most",
        "my",
        "myself",
        "no",
        "nor",
        "not",
        "now",
        "of",
        "off",
        "on",
        "once",
        "only",
        "or",
        "other",
        "our",
        "ours",
        "ourselves",
        "out",
        "over",
        "own",
        "same",
        "she",
        "should",
        "so",
        "some",
        "such",
        "than",
        "that",
        "the",
        "their",
        "theirs",
        "them",
        "themselves",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "to",
        "too",
        "under",
        "until",
        "up",
        "very",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "whom",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
        "yours",
        "yourself",
        "yourselves",
    }
)

FRENCH_STOPWORDS = frozenset(
    {
        "a",
        "ai",
        "aie",
        "aient",
        "ait",
        "as",
        "au",
        "aucun",
        "aucune",
        "aux",
        "avec",
        "avez",
        "avons",
        "ayant",
        "ayez",
        "c",
        "car",
        "ce",
        "ces",
        "cet",
        "cette",
        "d",
        "dans",
        "de",
        "des",
        "du",
        "elle",
        "elles",
        "en",
        "entre",
        "es",
        "est",
        "et",
        "eu",
        "eue",
        "eues",
        "eurent",
        "eus",
        "eusse",
        "eussent",
        "eusses",
        "eussiez",
        "eussions",
        "eut",
        "eût",
        "eûmes",
        "eûtes",
        "faire",
        "fait",
        "faites",
        "fois",
        "font",
        "furent",
        "fus",
        "fusse",
        "fussent",
        "fusses",
        "fussiez",
        "fussions",
        "fut",
        "fûmes",
        "fût",
        "fûtes",
        "ici",
        "il",
        "ils",
        "j",
        "je",
        "l",
        "la",
        "le",
        "les",
        "leur",
        "leurs",
        "lui",
        "m",
        "ma",
        "mais",
        "me",
        "mes",
        "moi",
        "mon",
        "n",
        "ne",
        "ni",
        "nos",
        "notre",
        "nous",
        "on",
        "ont",
        "ou",
        "où",
        "par",
        "pas",
        "pour",
        "qu",
        "que",
        "quel",
        "quelle",
        "quelles",
        "quels",
        "qui",
        "s",
        "sa",
        "sans",
        "se",
        "sera",
        "serai",
        "seraient",
        "serais",
        "serait",
        "seras",
        "serez",
        "seriez",
        "serions",
        "serons",
        "seront",
        "ses",
        "soi",
        "soient",
        "sois",
        "soit",
        "sommes",
        "son",
        "sont",
        "soyez",
        "soyons",
        "suis",
        "sur",
        "t",
        "ta",
        "te",
        "tes",
        "toi",
        "ton",
        "tu",
        "un",
        "une",
        "vos",
        "votre",
        "vous",
        "y",
        "à",
        "ça",
        "étaient",
        "étais",
        "était",
        "étant",
        "étiez",
        "étions",
        "été",
        "étée",
        "étées",
        "étés",
        "êtes",
    }
)

STOPWORDS_BY_LANGUAGE = {
    "en": ENGLISH_STOPWORDS,
    "es": SPANISH_STOPWORDS,
    "fr": FRENCH_STOPWORDS,
}

LANGUAGE_ALIASES = {
    "english": "en",
    "espanol": "es",
    "french": "fr",
    "francais": "fr",
    "spanish": "es",
}

TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def normalize_language(language):
    normalized = str(language or "").strip().lower().replace("_", "-")
    code = normalized.split("-", 1)[0]
    if code in STOPWORDS_BY_LANGUAGE:
        return code

    name = re.split(r"[\s(]", normalized, maxsplit=1)[0]
    folded_name = "".join(
        character
        for character in unicodedata.normalize("NFKD", name)
        if not unicodedata.combining(character)
    )
    return LANGUAGE_ALIASES.get(folded_name, code)


def current_search_language():
    try:
        import frappe
    except Exception:
        return ""

    language = None
    try:
        language = getattr(frappe.local, "lang", None)
    except Exception:
        pass
    if not language:
        try:
            user = getattr(getattr(frappe, "session", None), "user", None)
            if user and user != "Guest":
                language = frappe.db.get_value("User", user, "language")
        except Exception:
            pass
    if not language:
        try:
            language = frappe.db.get_single_value("System Settings", "language")
        except Exception:
            pass
    return normalize_language(language)


def stopwords_for_language(language=None):
    code = normalize_language(language) if language else current_search_language()
    return STOPWORDS_BY_LANGUAGE.get(code, frozenset())


def query_tokens(query):
    return TOKEN_RE.findall((query or "").lower())


def fold_text(value):
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", str(value or ""))
        if not unicodedata.combining(character)
    ).lower()


def significant_terms(query, language=None):
    stopwords = stopwords_for_language(language)
    return list(
        dict.fromkeys(
            token
            for token in query_tokens(query)
            if len(token) > 1 and token not in stopwords
        )
    )


def make_excerpt(content, query, maximum=360, language=None):
    normalized = " ".join((content or "").split())
    if len(normalized) <= maximum:
        return normalized

    lowered = normalized.lower()
    positions = [
        lowered.find(term)
        for term in significant_terms(query, language=language)
    ]
    positions = [position for position in positions if position >= 0]
    center = min(positions) if positions else 0
    start = max(center - maximum // 3, 0)
    end = min(start + maximum, len(normalized))
    if end - start < maximum:
        start = max(end - maximum, 0)
    prefix = "..." if start else ""
    suffix = "..." if end < len(normalized) else ""
    return prefix + normalized[start:end].strip() + suffix


def build_natural_query(
    index,
    query,
    fields,
    language=None,
    require_all=True,
):
    import tantivy

    tokens = query_tokens(query)
    terms = significant_terms(query, language=language)
    if not terms:
        return tantivy.Query.empty_query()

    field_weights = (
        fields
        if isinstance(fields, dict)
        else {field: 1.0 for field in fields}
    )
    clauses = []
    for term in terms:
        variants = list(dict.fromkeys([term, fold_text(term)]))
        alternatives = []
        for field, weight in field_weights.items():
            for variant in variants:
                parsed = index.parse_query(variant, [field])
                if weight != 1:
                    parsed = tantivy.Query.boost_query(parsed, float(weight))
                alternatives.append((tantivy.Occur.Should, parsed))
        term_query = (
            alternatives[0][1]
            if len(alternatives) == 1
            else tantivy.Query.boolean_query(alternatives)
        )
        clauses.append(
            (
                tantivy.Occur.Must if require_all else tantivy.Occur.Should,
                term_query,
            )
        )

    if len(tokens) > 1:
        phrases = list(
            dict.fromkeys(
                [
                    " ".join(tokens),
                    fold_text(" ".join(tokens)),
                ]
            )
        )
        for field, weight in field_weights.items():
            for phrase_text in phrases:
                phrase = index.parse_query(f'"{phrase_text}"', [field])
                clauses.append(
                    (
                        tantivy.Occur.Should,
                        tantivy.Query.boost_query(
                            phrase,
                            max(float(weight) * 2.0, 2.0),
                        ),
                    )
                )
    if len(clauses) == 1:
        return clauses[0][1]
    return tantivy.Query.boolean_query(clauses)
