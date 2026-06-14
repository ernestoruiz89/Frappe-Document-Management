import frappe

from werkzeug.routing import RequestRedirect

def preserve_guest_redirect_parameters():
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
