import frappe
from frappe.model.document import Document


class DocumentSavedView(Document):
    def before_insert(self):
        self.user = frappe.session.user

    def validate(self):
        if self.user != frappe.session.user and frappe.session.user != "Administrator":
            frappe.throw(
                "Saved document views are private.",
                frappe.PermissionError,
            )


def has_permission(doc, ptype="read", user=None):
    user = user or frappe.session.user
    return user == "Administrator" or doc.user == user


def get_permission_query_conditions(user):
    user = user or frappe.session.user
    if user == "Administrator":
        return ""
    return f"`tabDocument Saved View`.user = {frappe.db.escape(user)}"

