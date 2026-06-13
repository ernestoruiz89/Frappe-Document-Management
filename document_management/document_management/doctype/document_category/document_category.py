import frappe
from frappe.model.document import Document

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
        
    if doc.roles_with_access:
        doc_roles = [d.role for d in doc.roles_with_access]
        if set(user_roles).intersection(set(doc_roles)):
            return True
        return False
        
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
        
    return f"""
        (`tabDocument Category`.only_me = 1 AND `tabDocument Category`.owner = {frappe.db.escape(user)})
        OR
        (`tabDocument Category`.only_me = 0 AND EXISTS (SELECT 1 FROM `tabDocument Role Access` c_ra WHERE c_ra.parent = `tabDocument Category`.name AND c_ra.parenttype = 'Document Category' AND c_ra.role IN ({roles_str})))
        OR
        (`tabDocument Category`.only_me = 0 AND NOT EXISTS (SELECT 1 FROM `tabDocument Role Access` c_ra WHERE c_ra.parent = `tabDocument Category`.name AND c_ra.parenttype = 'Document Category'))
    """
