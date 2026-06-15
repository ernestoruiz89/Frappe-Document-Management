import frappe


def get_user_departments(user):
    if not user or user == "Guest":
        return set()
    return {
        department
        for department in frappe.get_all(
            "Employee",
            filters={"user_id": user, "status": "Active"},
            pluck="department",
        )
        if department
    }


def matches_access_rules(doc, user_roles, user_departments):
    allowed_roles = {
        row.role for row in (doc.get("roles_with_access") or []) if row.role
    }
    allowed_departments = {
        row.department
        for row in (doc.get("departments_with_access") or [])
        if row.department
    }
    has_restrictions = bool(allowed_roles or allowed_departments)
    allowed = bool(
        allowed_roles.intersection(user_roles)
        or allowed_departments.intersection(user_departments)
    )
    return has_restrictions, allowed


def sql_access_parts(table_alias, parenttype, user, roles_sql):
    escaped_parenttype = frappe.db.escape(parenttype)
    escaped_user = frappe.db.escape(user)
    role_rows = (
        "EXISTS ("
        "SELECT 1 FROM `tabDocument Role Access` access_role "
        f"WHERE access_role.parent = {table_alias}.name "
        f"AND access_role.parenttype = {escaped_parenttype}"
        ")"
    )
    department_rows = (
        "EXISTS ("
        "SELECT 1 FROM `tabDocument Department Access` access_department "
        f"WHERE access_department.parent = {table_alias}.name "
        f"AND access_department.parenttype = {escaped_parenttype}"
        ")"
    )
    role_match = (
        "EXISTS ("
        "SELECT 1 FROM `tabDocument Role Access` access_role "
        f"WHERE access_role.parent = {table_alias}.name "
        f"AND access_role.parenttype = {escaped_parenttype} "
        f"AND access_role.role IN ({roles_sql})"
        ")"
    )
    department_match = (
        "EXISTS ("
        "SELECT 1 FROM `tabDocument Department Access` access_department "
        "INNER JOIN `tabEmployee` access_employee "
        "ON access_employee.department = access_department.department "
        f"WHERE access_department.parent = {table_alias}.name "
        f"AND access_department.parenttype = {escaped_parenttype} "
        f"AND access_employee.user_id = {escaped_user} "
        "AND access_employee.status = 'Active'"
        ")"
    )
    return {
        "has_restrictions": f"({role_rows} OR {department_rows})",
        "matches": f"({role_match} OR {department_match})",
    }
