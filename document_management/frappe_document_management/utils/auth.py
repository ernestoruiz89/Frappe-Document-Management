from urllib.parse import quote

import frappe
from werkzeug.routing import RequestRedirect


def can_access_app():
    return frappe.has_permission("Document", "read")


def preserve_guest_redirect_parameters():
    if frappe.session.user != "Guest":
        return

    request = getattr(frappe.local, "request", None)
    if request is None:
        return

    path = request.path or ""
    is_desk_path = path == "/desk" or path.startswith("/desk/")
    is_legacy_path = path == "/app" or path.startswith("/app/")
    if not (is_desk_path or is_legacy_path):
        return

    query_string = request.query_string or b""
    if isinstance(query_string, bytes):
        query_string = query_string.decode("utf-8")
    if query_string:
        redirect_to = f"{path}?{query_string}"
        raise RequestRedirect(f"/login?redirect-to={quote(redirect_to, safe='')}")
