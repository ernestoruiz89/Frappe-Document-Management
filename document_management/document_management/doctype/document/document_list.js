frappe.listview_settings['Document'] = {
    onload: function(listview) {
        listview.page.add_inner_button(__('Quick Upload'), function() {
            show_quick_upload_dialog(listview);
        });
    }
};

function show_quick_upload_dialog(listview) {
    let d = new frappe.ui.Dialog({
        title: 'Upload Document',
        fields: [
            {
                label: 'Document Code (Optional)',
                fieldname: 'document_code',
                fieldtype: 'Data',
                description: 'Leave blank to auto-generate'
            },
            {
                label: 'Title',
                fieldname: 'title',
                fieldtype: 'Data',
                reqd: 1
            },
            {
                label: 'Category',
                fieldname: 'category',
                fieldtype: 'Link',
                options: 'Document Category',
                reqd: 1
            },
            {
                label: 'Folder',
                fieldname: 'folder',
                fieldtype: 'Link',
                options: 'File',
                get_query: function() {
                    return { filters: { is_folder: 1 } };
                }
            },
            {
                fieldtype: 'Section Break'
            },
            {
                fieldname: 'file_upload_area',
                fieldtype: 'HTML',
                label: 'File (Drag & Drop)'
            }
        ],
        primary_action_label: 'Upload',
        primary_action: function(values) {
            if (!d.pending_file) {
                frappe.msgprint(__('Please select a file first'));
                return;
            }
            
            d.get_primary_btn().prop('disabled', true).html('<i class="fa fa-spinner fa-spin"></i> Uploading...');
            
            const reader = new FileReader();
            reader.onload = function(e) {
                const file_data = {
                    filename: d.pending_file.name,
                    content: e.target.result.split(',')[1]
                };
                
                frappe.call({
                    method: 'document_management.document_management.page.document_management_console.document_management_console.quick_upload',
                    args: {
                        title: values.title,
                        category: values.category,
                        folder: values.folder || null,
                        document_code: values.document_code || null,
                        file_data: JSON.stringify(file_data)
                    },
                    callback: function(r) {
                        d.hide();
                        frappe.show_alert({message: __('Document uploaded successfully'), indicator: 'green'});
                        listview.refresh();
                    },
                    error: function() {
                        d.get_primary_btn().prop('disabled', false).html('Upload');
                    }
                });
            };
            reader.readAsDataURL(d.pending_file);
        }
    });

    // Setup drag and drop
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
        
        // Auto-fill title
        if (!d.get_value('title')) {
            d.set_value('title', f.name.replace(/\.[^/.]+$/, ""));
        }
    }

    d.show();
}
