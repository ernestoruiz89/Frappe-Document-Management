import frappe
from frappe.model.document import Document

from document_management.frappe_document_management.utils.document_access import (
    get_user_departments,
    matches_access_rules,
    sql_access_parts,
)

class DocumentCategory(Document):
	pass

def has_permission(doc, ptype="read", user=None):
    if not user:
        user = frappe.session.user
    if user == "Administrator":
        return True

    user_roles = frappe.get_roles(user)

    if ptype in ["delete", "write"]:
        if "System Manager" in user_roles:
            return True
        if doc.owner != user:
            return False

    if doc.only_me:
        return doc.owner == user

    has_restrictions, allowed = matches_access_rules(
        doc,
        set(user_roles),
        get_user_departments(user),
    )
    if has_restrictions:
        return allowed

    return True

def get_permission_query_conditions(user):
    if not user:
        user = frappe.session.user
    if user == "Administrator":
        return ""

    user_roles = frappe.get_roles(user)
    roles_str = ",".join(frappe.db.escape(role) for role in user_roles)
    if not roles_str:
        roles_str = "''"

    escaped_user = frappe.db.escape(user)
    access = sql_access_parts(
        "`tabDocument Category`",
        "Document Category",
        user,
        roles_str,
    )

    return f"""
        (`tabDocument Category`.only_me = 1 AND `tabDocument Category`.owner = {escaped_user})
        OR
        (`tabDocument Category`.only_me = 0 AND {access["matches"]})
        OR
        (`tabDocument Category`.only_me = 0 AND NOT {access["has_restrictions"]})
    """
