frappe.ui.form.on("Document Category", {
	refresh(frm) {
		apply_department_capabilities(frm);
	}
});

function apply_department_capabilities(frm) {
	frappe.call({
		method: 'document_management.frappe_document_management.utils.document_access.get_document_access_capabilities',
		callback: (r) => {
			const capabilities = r.message || {};
			const visible = Boolean(capabilities.department);
			if (frm.fields_dict.departments_with_access) {
				frm.toggle_display('departments_with_access', visible);
				frm.toggle_enable('departments_with_access', visible);
			}
		}
	});
}
