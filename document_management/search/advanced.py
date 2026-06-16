import re
import shlex
from dataclasses import dataclass, field

import frappe
from frappe.utils import getdate


TEXT_FIELDS = {
    "title": "title",
    "name": "name",
    "code": "document_code",
    "document_code": "document_code",
    "description": "description",
    "content": "ocr_content",
    "ocr": "ocr_content",
}
EXACT_FIELDS = {
    "category": "category",
    "status": "status",
    "department": "department",
    "owner": "owner",
}
DATE_FIELDS = {
    "created": "creation",
    "creation": "creation",
    "modified": "modified",
}


@dataclass
class AdvancedDocumentQuery:
    raw: str = ""
    text: str = ""
    positive_text_fields: list[tuple[str, str]] = field(default_factory=list)
    negative_text_fields: list[tuple[str, str]] = field(default_factory=list)
    positive_exact_fields: list[tuple[str, str]] = field(default_factory=list)
    negative_exact_fields: list[tuple[str, str]] = field(default_factory=list)
    positive_date_fields: list[tuple[str, str]] = field(default_factory=list)
    negative_date_fields: list[tuple[str, str]] = field(default_factory=list)
    positive_tags: list[str] = field(default_factory=list)
    negative_tags: list[str] = field(default_factory=list)

    @property
    def has_syntax(self):
        return any(
            (
                self.positive_text_fields,
                self.negative_text_fields,
                self.positive_exact_fields,
                self.negative_exact_fields,
                self.positive_date_fields,
                self.negative_date_fields,
                self.positive_tags,
                self.negative_tags,
            )
        )

    @property
    def highlight_query(self):
        terms = [self.text]
        terms.extend(value for _, value in self.positive_text_fields)
        return " ".join(term for term in terms if term).strip()


def parse_advanced_document_query(query):
    query = (query or "").strip()
    if not query:
        return AdvancedDocumentQuery(raw="")
    try:
        tokens = shlex.split(query)
    except ValueError:
        return AdvancedDocumentQuery(raw=query, text=query)

    parsed = AdvancedDocumentQuery(raw=query)
    free = []
    for token in tokens:
        negative = token.startswith("-") and len(token) > 1
        if negative:
            token = token[1:]
        if ":" not in token:
            free.append(("-" if negative else "") + token)
            continue
        fieldname, value = token.split(":", 1)
        fieldname = fieldname.strip().lower()
        value = value.strip()
        if not fieldname or not value:
            free.append(("-" if negative else "") + token)
            continue
        values = [part.strip() for part in value.split(",") if part.strip()]
        if fieldname == "tag":
            target = parsed.negative_tags if negative else parsed.positive_tags
            target.extend(values)
        elif fieldname in TEXT_FIELDS:
            target = (
                parsed.negative_text_fields
                if negative
                else parsed.positive_text_fields
            )
            target.extend((TEXT_FIELDS[fieldname], item) for item in values)
        elif fieldname in EXACT_FIELDS:
            target = (
                parsed.negative_exact_fields
                if negative
                else parsed.positive_exact_fields
            )
            target.extend((EXACT_FIELDS[fieldname], item) for item in values)
        elif fieldname in DATE_FIELDS:
            target = (
                parsed.negative_date_fields
                if negative
                else parsed.positive_date_fields
            )
            target.append((DATE_FIELDS[fieldname], value))
        else:
            free.append(("-" if negative else "") + token)
    parsed.text = " ".join(free).strip()
    return parsed


def _escape_like(value):
    return str(value).replace("%", r"\%").replace("_", r"\_")


def _intersect(existing, names):
    names = set(names)
    if existing is None:
        return names
    return set(existing) & names


def _matching_names(filters=None, or_filters=None):
    return set(
        frappe.get_all(
            "Document",
            filters=filters or {},
            or_filters=or_filters,
            pluck="name",
            limit_page_length=100000,
        )
    )


def _text_field_names(fieldname, value):
    return _matching_names(
        or_filters=[
            ["Document", fieldname, "like", f"%{_escape_like(value)}%"],
        ]
    )


def _tag_names(tags, require_all=True):
    tags = [str(tag).strip() for tag in tags if str(tag).strip()]
    if not tags:
        return set()
    if require_all:
        return set(
            frappe.db.sql(
                """
                SELECT parent FROM `tabDocument Tag Link`
                WHERE parenttype = 'Document' AND tag IN %(tags)s
                GROUP BY parent
                HAVING COUNT(DISTINCT tag) = %(count)s
                """,
                {"tags": tags, "count": len(set(tags))},
                pluck="parent",
            )
        )
    return set(
        frappe.db.sql(
            """
            SELECT DISTINCT parent FROM `tabDocument Tag Link`
            WHERE parenttype = 'Document' AND tag IN %(tags)s
            """,
            {"tags": tags},
            pluck="parent",
        )
    )


def _date_filter(fieldname, value):
    value = str(value or "").strip()
    if ".." in value:
        start, end = [part.strip() for part in value.split("..", 1)]
        if start and end:
            return [fieldname, "between", [str(getdate(start)), str(getdate(end))]]
    match = re.match(r"^(>=|<=|>|<)(.+)$", value)
    if match:
        return [fieldname, match.group(1), str(getdate(match.group(2).strip()))]
    date_value = str(getdate(value))
    return [fieldname, "between", [date_value, date_value]]


def _combine_name_filter(filters, include_names, exclude_names):
    include_names = set(include_names) if include_names is not None else None
    exclude_names = set(exclude_names or [])
    existing = filters.get("name")
    if existing and isinstance(existing, list) and existing[0] == "in":
        include_names = _intersect(include_names, existing[1])
    if include_names is not None:
        include_names -= exclude_names
        filters["name"] = ["in", sorted(include_names)]
    elif exclude_names:
        filters["name"] = ["not in", sorted(exclude_names)]


def apply_advanced_document_filters(filters, parsed):
    filters = dict(filters or {})
    if not parsed or not parsed.has_syntax:
        return filters, (parsed.text if parsed else "")

    include_names = None
    exclude_names = set()

    for fieldname, value in parsed.positive_exact_fields:
        if fieldname in filters:
            include_names = _intersect(
                include_names,
                _matching_names({fieldname: value}),
            )
        else:
            filters[fieldname] = value
    for fieldname, value in parsed.negative_exact_fields:
        exclude_names.update(_matching_names({fieldname: value}))

    for fieldname, value in parsed.positive_text_fields:
        include_names = _intersect(include_names, _text_field_names(fieldname, value))
    for fieldname, value in parsed.negative_text_fields:
        exclude_names.update(_text_field_names(fieldname, value))

    for fieldname, value in parsed.positive_date_fields:
        include_names = _intersect(
            include_names,
            _matching_names([_date_filter(fieldname, value)]),
        )
    for fieldname, value in parsed.negative_date_fields:
        exclude_names.update(_matching_names([_date_filter(fieldname, value)]))

    if parsed.positive_tags:
        include_names = _intersect(include_names, _tag_names(parsed.positive_tags))
    if parsed.negative_tags:
        exclude_names.update(_tag_names(parsed.negative_tags, require_all=False))

    _combine_name_filter(filters, include_names, exclude_names)
    return filters, parsed.text
