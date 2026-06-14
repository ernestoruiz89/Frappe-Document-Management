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
            
            const reader = new FileReader();
            reader.onload = function(e) {
                if (is_new) {
                    // It's a new document, save it first, then upload
                    frm.save().then(() => {
                        upload_file_to_existing(frm, d, e.target.result, d.pending_file.name);
                    }).catch(() => {
                        d.get_primary_btn().prop('disabled', false).html('Upload');
                    });
                } else {
                    upload_file_to_existing(frm, d, e.target.result, d.pending_file.name);
                }
            };
            reader.readAsDataURL(d.pending_file);
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

function upload_file_to_existing(frm, dialog, filedata, filename) {
    let args = {
        cmd: "upload_file",
        filedata: filedata,
        filename: filename,
        doctype: frm.doc.doctype,
        docname: frm.doc.name,
        is_private: 1
    };
    if (frm.doc.folder) {
        args.folder = frm.doc.folder;
    }

    frappe.call({
        method: "upload_file",
        args: args,
        callback: function(r) {
            if(!r.exc && r.message) {
                let file_url = r.message.file_url;
                let row = frappe.model.add_child(frm.doc, "Document Version", "versions");
                
                let next_v = 1;
                if (frm.doc.versions && frm.doc.versions.length > 1) { 
                    next_v = frm.doc.versions.length; // Already added 1 to the array
                }
                
                row.version_number = next_v.toString();
                row.release_date = frappe.datetime.get_today();
                row.attachment = file_url;
                row.change_log = next_v === 1 ? "Initial upload" : "Uploaded via Quick Upload";
                
                frm.refresh_field("versions");
                frm.save().then(() => {
                    dialog.hide();
                    frappe.show_alert({message: __('File uploaded successfully'), indicator: 'green'});
                });
            }
        },
        error: function() {
            dialog.get_primary_btn().prop('disabled', false).html('Upload');
        }
    });
}
