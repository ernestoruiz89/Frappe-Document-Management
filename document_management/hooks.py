app_name = "document_management"
app_title = "Document Management"
app_publisher = "Administrator"
app_description = "Standalone Document Management module for Frappe"
app_email = "admin@example.com"
app_license = "mit"

permission_query_conditions = {
    "Document": "document_management.document_management.doctype.document.document.get_permission_query_conditions",
    "Document Category": "document_management.document_management.doctype.document_category.document_category.get_permission_query_conditions",
    "Document Chat Session": "document_management.document_management.doctype.document_chat_session.document_chat_session.get_permission_query_conditions",
    "Document Chat Message": "document_management.document_management.doctype.document_chat_message.document_chat_message.get_permission_query_conditions",
    "Document Saved View": "document_management.document_management.doctype.document_saved_view.document_saved_view.get_permission_query_conditions"
}

has_permission = {
    "Document": "document_management.document_management.doctype.document.document.has_permission",
    "Document Category": "document_management.document_management.doctype.document_category.document_category.has_permission",
    "Document Chat Session": "document_management.document_management.doctype.document_chat_session.document_chat_session.has_permission",
    "Document Chat Message": "document_management.document_management.doctype.document_chat_message.document_chat_message.has_permission",
    "Document Saved View": "document_management.document_management.doctype.document_saved_view.document_saved_view.has_permission"
}

doc_events = {
    "*": {
        "on_update": "document_management.search.indexer.handle_doc_save",
        "on_trash": "document_management.search.indexer.handle_doc_trash"
    }
}

scheduler_events = {
    "daily": [
        "document_management.document_management.utils.ml_tagger.train_tagger_model"
    ]
}
