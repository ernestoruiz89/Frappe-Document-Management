frappe.ui.form.on("Document", {
	setup(frm) {
		frm.set_query("party_type", function() {
			return {
				filters: {
					name: ["in", Object.keys(frappe.boot.party_account_types || {})]
				}
			};
		});
		frm.set_query("folder", function() {
			return {
				filters: {
					is_folder: 1
				}
			};
		});
	},
	refresh(frm) {
        if (!frm.is_new()) {
            frm.add_custom_button(__('Upload New Version'), function() {
                show_upload_version_dialog(frm);
            }).removeClass('btn-default').addClass('btn-primary');
        } else {
            // For new docs, they should just fill the form and add a row, 
            // but to be nice, we can add a custom button that uploads the file and saves.
            frm.page.set_primary_action(__('Save & Upload File'), function() {
                if (!frm.doc.title || !frm.doc.category) {
                    frappe.msgprint(__('Title and Category are required before uploading.'));
                    return;
                }
                show_upload_version_dialog(frm, true);
            });
        }
	}
});

function show_upload_version_dialog(frm, is_new = false) {
    let d = new frappe.ui.Dialog({
        title: is_new ? 'Upload Initial File' : 'Upload New Version',
        fields: [
            {
                fieldname: 'file_upload_area',
                fieldtype: 'HTML',
                label: 'File (Drag & Drop)'
            }
        ],
        primary_action_label: 'Upload',
        primary_action: function() {
            if (!d.pending_file) {
                frappe.msgprint(__('Please select a file first'));
                return;
            }
            d.get_primary_btn().prop('disabled', true).html('<i class="fa fa-spinner fa-spin"></i> Uploading...');

            const method = is_new
                ? 'document_management.frappe_document_management.page.document_management_console.document_management_console.quick_upload'
                : 'document_management.frappe_document_management.page.document_management_console.document_management_console.add_document_version';
            const args = is_new
                ? {
                    title: frm.doc.title,
                    category: frm.doc.category,
                    folder: frm.doc.folder || '',
                    document_code: frm.doc.document_code || '',
                    tags: JSON.stringify((frm.doc.tags || []).map((row) => row.tag)),
                    department: frm.doc.department || '',
                    party_type: frm.doc.party_type || '',
                    party_name: frm.doc.party_name || '',
                    description: frm.doc.description || '',
                    status: frm.doc.status || 'Draft',
                    only_me: frm.doc.only_me ? '1' : '0',
                    roles: JSON.stringify(
                        (frm.doc.roles_with_access || []).map((row) => row.role)
                    )
                }
                : {
                    doc_name: frm.doc.name,
                    folder: frm.doc.folder || ''
                };

            upload_document_file(d.pending_file, method, args)
                .then((result) => {
                if (is_new) {
                    d.hide();
                    frappe.set_route('Form', 'Document', result.docname);
                    return;
                }

                d.hide();
                frappe.show_alert({message: __('File uploaded successfully'), indicator: 'green'});
                return frm.reload_doc();
            }).catch(() => {
                d.get_primary_btn().prop('disabled', false).html(__('Upload'));
            });
        }
    });

    let $wrapper = d.fields_dict.file_upload_area.$wrapper;
    $wrapper.html(`
        <div class="file-drop-zone" style="border: 2px dashed #ccc; border-radius: 8px; padding: 30px; text-align: center; background: #f9f9f9; cursor: pointer; transition: all 0.2s;">
            <div style="font-size: 32px; color: #666; margin-bottom: 10px;"><i class="fa fa-cloud-upload"></i></div>
            <p style="margin: 0; color: #666;">Drag & Drop your file here or click to browse</p>
            <input type="file" id="file-input-hidden" style="display: none;">
        </div>
        <div id="file-preview-area" style="display: none; align-items: center; background: #f0f1f3; padding: 10px 15px; border-radius: 8px; margin-top: 15px; gap: 12px;">
            <i class="fa fa-file-text-o" style="font-size: 24px; color: #007bff;"></i>
            <div id="file-name" style="flex: 1; font-weight: 500; font-size: 14px;"></div>
            <button id="file-remove" class="btn btn-xs btn-danger"><i class="fa fa-times"></i></button>
        </div>
    `);

    let $dropZone = $wrapper.find('.file-drop-zone');
    let $fileInput = $wrapper.find('#file-input-hidden');
    let $previewArea = $wrapper.find('#file-preview-area');
    let $fileName = $wrapper.find('#file-name');
    let $fileRemove = $wrapper.find('#file-remove');

    d.pending_file = null;

    $dropZone.on('dragover', function(e) { e.preventDefault(); e.stopPropagation(); $(this).css({'background': '#eef2ff', 'border-color': '#4f46e5'}); });
    $dropZone.on('dragleave', function(e) { e.preventDefault(); e.stopPropagation(); $(this).css({'background': '#f9f9f9', 'border-color': '#ccc'}); });
    $dropZone.on('drop', function(e) {
        e.preventDefault(); e.stopPropagation(); 
        $(this).css({'background': '#f9f9f9', 'border-color': '#ccc'});
        if (e.originalEvent.dataTransfer.files.length > 0) {
            handle_file(e.originalEvent.dataTransfer.files[0]);
        }
    });
    $dropZone.on('click', function() { $fileInput.click(); });
    $fileInput.on('click', function(e) { e.stopPropagation(); });
    $fileInput.on('change', function() { 
        if (this.files.length > 0) {
            handle_file(this.files[0]); 
        }
    });

    $fileRemove.on('click', function(e) {
        e.preventDefault();
        d.pending_file = null;
        $previewArea.hide();
        $dropZone.show();
        $fileInput.val('');
    });

    function handle_file(f) {
        d.pending_file = f;
        $fileName.text(f.name);
        $dropZone.hide();
        $previewArea.css('display', 'flex');
    }

    d.show();
}

async function upload_document_file(file, method, fields) {
    const form_data = new FormData();
    form_data.append('file', file, file.name);
    form_data.append('is_private', '1');
    form_data.append('method', method);
    Object.entries(fields || {}).forEach(([key, value]) => {
        form_data.append(key, value == null ? '' : value);
    });
    const response = await fetch('/api/method/upload_file', {
        method: 'POST',
        body: form_data,
        credentials: 'same-origin',
        headers: {'X-Frappe-CSRF-Token': frappe.csrf_token}
    });
    const payload = await response.json();
    if (!response.ok || payload.exc || !payload.message) {
        throw new Error(payload.exception || __('File upload failed.'));
    }
    return payload.message;
}
