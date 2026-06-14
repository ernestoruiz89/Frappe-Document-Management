import frappe
from frappe.model.document import Document


class DocumentChatSession(Document):
    def before_insert(self):
        self.user = frappe.session.user


def has_permission(doc, ptype="read", user=None):
    user = user or frappe.session.user
    return doc.user == user


def get_permission_query_conditions(user=None):
    user = user or frappe.session.user
    return f"`tabDocument Chat Session`.`user` = {frappe.db.escape(user)}"
