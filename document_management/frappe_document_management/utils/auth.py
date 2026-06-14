import frappe
from werkzeug.routing import RequestRedirect

def preserve_guest_redirect_parameters():
    path = frappe.local.request.path if (hasattr(frappe, "local") and hasattr(frappe.local, "request")) else None
    if path and path.startswith("/app"):
        import os
        log_file = "/mnt/c/Users/Ernesto/Desktop/document_management/debug_redirect.log"
        try:
            with open(log_file, "a") as f:
                f.write(f"User: {frappe.session.user if frappe.session else None} | Path: {path} | Query: {frappe.local.request.query_string} | Cookies: {frappe.get_cookies()}\n")
        except Exception as log_err:
            pass

    # Only run for Guest users
    if frappe.session.user == "Guest":
        # Only run if frappe.local.request is set
        if hasattr(frappe, "local") and hasattr(frappe.local, "request") and frappe.local.request:
            path = frappe.local.request.path
            # Check if the requested path is a desk route
            if path and path.startswith("/app"):
                # Check if there are query parameters
                query_string = frappe.local.request.query_string
                if query_string:
                    query_str = query_string.decode("utf-8")
                    if query_str:
                        # Reconstruct the target path including the query parameters
                        redirect_to = f"{path}?{query_str}"
                        
                        from urllib.parse import quote
                        login_url = f"/login?redirect-to={quote(redirect_to, safe='')}"
                        
                        raise RequestRedirect(login_url)
