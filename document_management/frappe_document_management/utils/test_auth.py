import unittest
import frappe
from document_management.frappe_document_management.utils.auth import preserve_guest_redirect_parameters

class TestAuthRedirect(unittest.TestCase):
    def setUp(self):
        # Cache original request and session user
        self.original_user = frappe.session.user
        self.original_request = getattr(frappe.local, "request", None)
        self.original_redirect_location = getattr(frappe.local.flags, "redirect_location", None)

    def tearDown(self):
        # Restore original request and session user
        frappe.session.user = self.original_user
        frappe.local.request = self.original_request
        frappe.local.flags.redirect_location = self.original_redirect_location

    def test_redirect_for_guest_with_params(self):
        # Mock guest user
        frappe.session.user = "Guest"
        
        # Mock request
        class MockRequest:
            path = "/app/document-management-console"
            query_string = b"Doc=DOC-2026-0004"
            
        frappe.local.request = MockRequest()
        
        with self.assertRaises(frappe.Redirect):
            preserve_guest_redirect_parameters()
            
        self.assertEqual(
            frappe.local.flags.redirect_location,
            "/login?redirect-to=%2Fapp%2Fdocument-management-console%3FDoc%3DDOC-2026-0004"
        )

    def test_no_redirect_for_non_guest(self):
        # Mock logged in user
        frappe.session.user = "Administrator"
        
        # Mock request
        class MockRequest:
            path = "/app/document-management-console"
            query_string = b"Doc=DOC-2026-0004"
            
        frappe.local.request = MockRequest()
        
        # Should NOT raise Redirect
        preserve_guest_redirect_parameters()
        self.assertNotEqual(
            frappe.local.flags.redirect_location,
            "/login?redirect-to=%2Fapp%2Fdocument-management-console%3FDoc%3DDOC-2026-0004"
        )

    def test_no_redirect_without_query_params(self):
        # Mock guest user
        frappe.session.user = "Guest"
        
        # Mock request without query string
        class MockRequest:
            path = "/app/document-management-console"
            query_string = b""
            
        frappe.local.request = MockRequest()
        
        # Should NOT raise Redirect because query string is empty
        preserve_guest_redirect_parameters()
