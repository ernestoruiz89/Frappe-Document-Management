import json

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime

from document_management.document_management.rag.config import get_rag_config
from document_management.document_management.rag.evaluation import get_latest_report
from document_management.document_management.rag.index import index_status, rebuild_index
from document_management.document_management.rag.service import (
    enforce_rate_limit,
    get_owned_session,
    parse_filters,
    request_cancellation,
)


def _serialize_message(row):
    row.references = json.loads(row.references_json or "[]")
    del row["references_json"]
    return row


@frappe.whitelist()
def create_session(filters=None):
    parsed_filters = parse_filters(filters)
    session = frappe.get_doc(
        {
            "doctype": "Document Chat Session",
            "title": "Nueva conversación",
            "user": frappe.session.user,
            "status": "Active",
            "filters_json": json.dumps(parsed_filters, ensure_ascii=True),
            "last_message_at": now_datetime(),
        }
    )
    session.insert(ignore_permissions=True)
    return session.as_dict()


@frappe.whitelist()
def list_sessions():
    return frappe.get_all(
        "Document Chat Session",
        fields=["name", "title", "status", "filters_json", "last_message_at", "modified"],
        filters={"status": "Active", "user": frappe.session.user},
        order_by="last_message_at desc, modified desc",
        limit_page_length=100,
    )


@frappe.whitelist()
def get_session(session, after_message=None):
    doc = get_owned_session(session)
    filters = {"session": session}
    if after_message:
        creation = frappe.db.get_value("Document Chat Message", after_message, "creation")
        if creation:
            filters["creation"] = [">", creation]
    messages = frappe.get_all(
        "Document Chat Message",
        filters=filters,
        fields=[
            "name",
            "role",
            "status",
            "content",
            "references_json",
            "error_message",
            "model",
            "creation",
            "modified",
        ],
        order_by="creation asc",
        limit_page_length=500,
    )
    return {
        "session": doc.as_dict(),
        "messages": [_serialize_message(row) for row in messages],
    }


@frappe.whitelist()
def get_message(message):
    row = frappe.db.get_value(
        "Document Chat Message",
        message,
        [
            "name",
            "session",
            "role",
            "status",
            "content",
            "references_json",
            "error_message",
            "model",
            "creation",
            "modified",
        ],
        as_dict=True,
    )
    if not row:
        frappe.throw("Message not found.")
    get_owned_session(row.session)
    del row["session"]
    return _serialize_message(row)


@frappe.whitelist()
def ask_question(session, query):
    query = (query or "").strip()
    if not query:
        frappe.throw("Question is required.")
    if len(query) > 4000:
        frappe.throw("Question is too long.")

    session_doc = get_owned_session(session)
    enforce_rate_limit(frappe.session.user)
    frappe.db.sql(
        "SELECT name FROM `tabDocument Chat Session` WHERE name = %s FOR UPDATE",
        (session,),
    )
    active = _get_active_message(session)
    if active:
        frappe.throw("This conversation already has an active generation.")

    user_message = frappe.get_doc(
        {
            "doctype": "Document Chat Message",
            "session": session,
            "role": "user",
            "status": "Completed",
            "content": query,
            "completed_at": now_datetime(),
        }
    ).insert(ignore_permissions=True)
    assistant_message = frappe.get_doc(
        {
            "doctype": "Document Chat Message",
            "session": session,
            "role": "assistant",
            "status": "Queued",
            "content": "",
        }
    ).insert(ignore_permissions=True)

    if session_doc.title == "Nueva conversación":
        session_doc.db_set("title", query[:80], update_modified=False)
    session_doc.db_set("last_message_at", now_datetime(), update_modified=False)

    worker_timeout = max(get_rag_config().timeout * 3 + 120, 900)
    job = frappe.enqueue(
        "document_management.document_management.rag.service.generate_answer",
        queue="long",
        timeout=worker_timeout,
        message_name=assistant_message.name,
        question=query,
        question_message=user_message.name,
        user=frappe.session.user,
        enqueue_after_commit=True,
    )
    return {
        "session": session,
        "user_message": user_message.name,
        "message": assistant_message.name,
        "job_id": getattr(job, "id", None),
    }


@frappe.whitelist()
def cancel_message(message):
    doc = frappe.get_doc("Document Chat Message", message)
    get_owned_session(doc.session)
    if doc.status in {"Queued", "Processing"}:
        request_cancellation(doc.name)
        doc.db_set(
            {
                "status": "Cancelled",
                "cancel_requested": 1,
                "completed_at": now_datetime(),
            }
        )
        references = json.loads(doc.references_json or "[]")
        frappe.publish_realtime(
            f"document_chat:{doc.name}",
            {
                "type": "cancelled",
                "content": doc.content or "",
                "references": references,
            },
            user=frappe.session.user,
        )
        return {
            "status": "Cancelled",
            "content": doc.content or "",
            "references": references,
        }
    return {
        "status": doc.status,
        "content": doc.content or "",
        "references": json.loads(doc.references_json or "[]"),
    }


@frappe.whitelist()
def update_session_filters(session, filters=None):
    doc = get_owned_session(session)
    _throw_if_active(session)
    parsed = parse_filters(filters)
    doc.db_set("filters_json", json.dumps(parsed, ensure_ascii=True))
    return parsed


@frappe.whitelist()
def rename_session(session, title):
    doc = get_owned_session(session)
    title = (title or "").strip()
    if not title:
        frappe.throw("Title is required.")
    doc.db_set("title", title[:140])
    return {"title": title[:140]}


@frappe.whitelist()
def delete_session(session):
    get_owned_session(session)
    _throw_if_active(session)
    frappe.db.delete("Document Chat Message", {"session": session})
    frappe.delete_doc("Document Chat Session", session, ignore_permissions=True)
    return {"deleted": session}


def _throw_if_active(session):
    if _get_active_message(session):
        frappe.throw("Wait for the active response to finish or cancel it first.")


def _get_active_message(session):
    active = frappe.get_all(
        "Document Chat Message",
        filters={
            "session": session,
            "role": "assistant",
            "status": ["in", ["Queued", "Processing"]],
        },
        fields=["name", "status", "creation", "started_at"],
        order_by="creation desc",
        limit=1,
    )
    if not active:
        return None

    message = active[0]
    timeout = max(get_rag_config().timeout * 3 + 120, 900)
    cutoff = add_to_date(now_datetime(), seconds=-timeout)
    activity_time = message.started_at or message.creation
    if activity_time and get_datetime(activity_time) < get_datetime(cutoff):
        frappe.db.set_value(
            "Document Chat Message",
            message.name,
            {
                "status": "Failed",
                "error_message": "Generation expired before completion.",
                "completed_at": now_datetime(),
            },
        )
        return None
    return message.name


@frappe.whitelist()
def rebuild_rag_index():
    if "System Manager" not in frappe.get_roles():
        frappe.throw("System Manager role is required.", frappe.PermissionError)
    job = frappe.enqueue(
        "document_management.document_management.rag.index.rebuild_index",
        queue="long",
        timeout=3600,
        enqueue_after_commit=True,
    )
    return {"job_id": getattr(job, "id", None)}


@frappe.whitelist()
def get_rag_index_status():
    if "System Manager" not in frappe.get_roles():
        frappe.throw("System Manager role is required.", frappe.PermissionError)
    return index_status()


@frappe.whitelist()
def run_rag_evaluation(include_generation=0):
    if "System Manager" not in frappe.get_roles():
        frappe.throw("System Manager role is required.", frappe.PermissionError)
    config = get_rag_config()
    job = frappe.enqueue(
        "document_management.document_management.rag.evaluation.run_evaluation",
        queue="long",
        timeout=max(config.timeout * 20, 3600),
        include_generation=include_generation,
        requested_by=frappe.session.user,
        enqueue_after_commit=True,
    )
    return {"job_id": getattr(job, "id", None)}


@frappe.whitelist()
def get_latest_rag_evaluation():
    if "System Manager" not in frappe.get_roles():
        frappe.throw("System Manager role is required.", frappe.PermissionError)
    return get_latest_report()
