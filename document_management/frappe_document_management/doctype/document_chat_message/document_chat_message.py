import frappe
from frappe.model.document import Document


class DocumentChatMessage(Document):
    def before_insert(self):
        user = frappe.session.user
        if user != "Administrator" and not _owns_session(self.session, user):
            frappe.throw(
                "You do not have access to this conversation.",
                frappe.PermissionError,
            )


def _owns_session(session, user):
    return frappe.db.get_value("Document Chat Session", session, "user") == user


def has_permission(doc, ptype="read", user=None):
    user = user or frappe.session.user
    return _owns_session(doc.session, user)


def get_permission_query_conditions(user=None):
    user = user or frappe.session.user
    escaped_user = frappe.db.escape(user)
    return (
        "EXISTS (SELECT 1 FROM `tabDocument Chat Session` session "
        "WHERE session.name = `tabDocument Chat Message`.`session` "
        f"AND session.`user` = {escaped_user})"
    )
