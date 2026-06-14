frappe.pages['document-chat'].on_page_load = function(wrapper) {
    const page = frappe.ui.make_app_page({
        parent: wrapper,
        title: __('Document Chat'),
        single_column: true
    });
    $(frappe.render_template('document_chat', {})).appendTo(page.main);
    new DocumentChatController(wrapper, page);
};

class DocumentChatController {
    constructor(wrapper, page) {
        this.wrapper = $(wrapper);
        this.page = page;
        this.session = null;
        this.sessions = [];
        this.messages = new Map();
        this.activeMessage = null;
        this.pollTimer = null;
        this.realtimeEvent = null;
        this.bind();
        this.setupAdminActions();
        this.loadSessions();
    }

    bind() {
        this.wrapper.find('#new-chat').on('click', () => this.createSession());
        this.wrapper.find('#btn-send-chat').on('click', () => this.send());
        this.wrapper.find('#btn-cancel-chat').on('click', () => this.cancel());
        this.wrapper.find('#chat-filters').on('click', () => this.editFilters());
        this.wrapper.find('#rename-chat').on('click', () => this.renameSession());
        this.wrapper.find('#delete-chat').on('click', () => this.deleteSession());
        this.wrapper.find('#chat-input').on('keydown', (event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                this.send();
            }
        });
        this.wrapper.find('#chat-messages').on('click', '.source-card', (event) => {
            if (event.ctrlKey || event.metaKey || event.shiftKey) {
                return;
            }
            event.preventDefault();
            const $card = $(event.currentTarget);
            const source = {
                document: $card.attr('data-document'),
                page: parseInt($card.attr('data-page'), 10) || 1,
                title: $card.attr('data-title')
            };
            this.openPdfModal(source);
        });
    }

    setupAdminActions() {
        if ((frappe.user_roles || []).includes('System Manager')) {
            this.page.add_menu_item(__('RAG Index Status'), () => {
                this.call('get_rag_index_status').then((status) => {
                    frappe.msgprint(`<pre>${this.escape(JSON.stringify(status, null, 2))}</pre>`);
                });
            });
            this.page.add_menu_item(__('Rebuild RAG Index'), () => {
                frappe.confirm(
                    __('This replaces the current RAG index. Continue?'),
                    () => this.call('rebuild_rag_index').then(() => {
                        frappe.show_alert({message: __('RAG rebuild queued'), indicator: 'blue'});
                    })
                );
            });
            this.page.add_menu_item(__('Run RAG Evaluation'), () => {
                const dialog = new frappe.ui.Dialog({
                    title: __('Run RAG Evaluation'),
                    fields: [{
                        fieldname: 'include_generation',
                        fieldtype: 'Check',
                        label: __('Evaluate generated answers and citations'),
                        description: __(
                            'Uses the configured embedding, reranking and chat providers and may incur cost.'
                        )
                    }],
                    primary_action_label: __('Queue Evaluation'),
                    primary_action: (values) => {
                        dialog.hide();
                        this.call('run_rag_evaluation', {
                            include_generation: values.include_generation ? 1 : 0
                        }).then(() => {
                            frappe.show_alert({
                                message: __('RAG evaluation queued'),
                                indicator: 'blue'
                            });
                        });
                    }
                });
                dialog.show();
            });
            this.page.add_menu_item(__('Latest RAG Evaluation'), () => {
                this.call('get_latest_rag_evaluation').then((report) => {
                    if (!report) {
                        frappe.msgprint(__('No RAG evaluation report exists yet.'));
                        return;
                    }
                    const indicator = report.summary && report.summary.passed
                        ? 'green'
                        : 'red';
                    frappe.msgprint({
                        title: __('Latest RAG Evaluation'),
                        indicator,
                        message: `<pre style="max-height: 65vh; overflow: auto;">${
                            this.escape(JSON.stringify(report, null, 2))
                        }</pre>`
                    });
                });
            });
        }
    }

    call(method, args = {}) {
        return new Promise((resolve, reject) => {
            frappe.call({
                method: `document_management.frappe_document_management.page.document_chat.document_chat.${method}`,
                args,
                callback: (response) => resolve(response.message),
                error: reject
            });
        });
    }

    async loadSessions(preferred) {
        this.sessions = await this.call('list_sessions') || [];
        this.renderSessions();
        const target = preferred || (this.session && this.session.name) ||
            (this.sessions[0] && this.sessions[0].name);
        if (target) {
            await this.openSession(target);
        } else {
            await this.createSession();
        }
    }

    async createSession() {
        const session = await this.call('create_session');
        await this.loadSessions(session.name);
    }

    renderSessions() {
        const list = this.wrapper.find('#session-list').empty();
        this.sessions.forEach((session) => {
            const item = $('<button class="session-item"></button>');
            item.attr('data-session', session.name);
            item.toggleClass('active', this.session && this.session.name === session.name);
            item.append($('<span class="session-title"></span>').text(session.title));
            item.append($('<small></small>').text(session.last_message_at || ''));
            item.on('click', () => this.openSession(session.name));
            list.append(item);
        });
        const activeItem = list.find('.session-item.active').get(0);
        if (activeItem) {
            activeItem.scrollIntoView({block: 'nearest'});
        }
    }

    async openSession(name) {
        this.stopPolling();
        this.unsubscribeRealtime();
        const data = await this.call('get_session', {session: name});
        this.session = data.session;
        this.messages.clear();
        this.activeMessage = null;
        this.setBusy(false);
        this.wrapper.find('#chat-title').text(this.session.title);
        this.renderSessions();
        this.renderFilters();
        const container = this.wrapper.find('#chat-messages').empty();
        if (!data.messages.length) {
            container.append(
                $('<div class="empty-chat"></div>').text(
                    __('Ask a question. Only documents you can read will be searched.')
                )
            );
        }
        data.messages.forEach((message) => this.upsertMessage(message));
        const active = data.messages.find((message) =>
            message.role === 'assistant' && ['Queued', 'Processing'].includes(message.status)
        );
        if (active) {
            this.watchMessage(active.name);
        }
        this.scrollBottom();
    }

    upsertMessage(message) {
        this.messages.set(message.name, message);
        let node = this.wrapper.find(`[data-message="${this.selector(message.name)}"]`);
        if (!node.length) {
            this.wrapper.find('.empty-chat').remove();
            node = $('<div class="message-wrapper"></div>')
                .attr('data-message', message.name)
                .addClass(message.role === 'user' ? 'user' : 'ai');
            node.append($('<div class="message-avatar"></div>').html(
                message.role === 'user' ? '<i class="fa fa-user"></i>' : '<i class="fa fa-magic"></i>'
            ));
            const bubble = $('<div class="message-bubble"></div>');
            bubble.append('<div class="message-content"></div>');
            bubble.append('<div class="message-status"></div>');
            bubble.append('<div class="sources-container"></div>');
            node.append(bubble);
            this.wrapper.find('#chat-messages').append(node);
        }
        node.find('.message-content').html(this.renderContent(message.content || ''));
        const statusNode = node.find('.message-status').empty();
        if (['Queued', 'Processing'].includes(message.status)) {
            statusNode.text(__(message.status));
        } else if (message.status === 'Cancelled') {
            statusNode.text(__('Cancelled'));
        } else if (message.status === 'Failed') {
            statusNode.append($('<span></span>').text(message.error_message || __('Failed')));
            const retry = $('<button class="btn btn-xs btn-default ml-2"></button>').text(__('Retry'));
            retry.on('click', () => this.retryMessage(message.name));
            statusNode.append(retry);
        }
        this.renderReferences(node.find('.sources-container'), message.references || []);
        this.scrollBottom();
    }

    renderContent(content) {
        if (window.marked) {
            const rendered = marked.parse(content || '');
            if (window.DOMPurify) {
                return DOMPurify.sanitize(rendered);
            }
            return this.sanitizeHTML(rendered);
        }
        return this.escape(content || '').replace(/\n/g, '<br>');
    }

    sanitizeHTML(html) {
        const template = document.createElement('template');
        template.innerHTML = html;
        const blocked = ['script', 'style', 'iframe', 'object', 'embed', 'form', 'input', 'button'];
        template.content.querySelectorAll(blocked.join(',')).forEach((node) => node.remove());
        template.content.querySelectorAll('*').forEach((node) => {
            Array.from(node.attributes).forEach((attribute) => {
                const name = attribute.name.toLowerCase();
                const value = attribute.value.trim().toLowerCase();
                if (name.startsWith('on') || name === 'style') {
                    node.removeAttribute(attribute.name);
                }
                if ((name === 'href' || name === 'src') && value.startsWith('javascript:')) {
                    node.removeAttribute(attribute.name);
                }
            });
        });
        return template.innerHTML;
    }

    renderReferences(container, references) {
        container.empty();
        if (!references.length) return;
        container.append($('<div class="sources-title"></div>').text(__('Sources')));
        references.forEach((source) => {
            const card = $('<a class="source-card" target="_blank" rel="noopener"></a>');
            card.attr('href', `/app/document/${encodeURIComponent(source.document)}`);
            card.attr('data-document', source.document);
            card.attr('data-page', source.page || 1);
            card.attr('data-title', source.title || '');
            card.append($('<strong></strong>').text(source.title || source.document));
            card.append($('<span></span>').text(
                `${__('Version')} ${source.version || '-'} · ${__('Page')} ${source.page || '-'}`
            ));
            card.append($('<small></small>').text(source.excerpt || ''));
            container.append(card);
        });
    }

    openPdfModal(source) {
        const doc_name = source.document;
        frappe.show_alert({message: __('Loading document preview...'), indicator: 'blue'});

        this.call('get_document_pdf_url', {document: doc_name}).then((file_url) => {
            if (!file_url) {
                frappe.msgprint(__('This document does not have an attached preview or file.'));
                return;
            }

            if (!file_url.toLowerCase().endsWith('.pdf')) {
                frappe.msgprint(__('The document preview is not a PDF file. Opening in a new window instead.'));
                window.open(file_url, '_blank');
                return;
            }

            this.loadPdfViewer(() => {
                const dialog = new frappe.ui.Dialog({
                    title: source.title || doc_name,
                    size: 'extra-large',
                    fields: [
                        {
                            fieldname: 'pdf_viewer_container',
                            fieldtype: 'HTML',
                            options: '<div class="modal-pdf-viewer-app" style="height: 75vh; width: 100%;"></div>'
                        }
                    ]
                });

                dialog.$wrapper.addClass('pdf-viewer-modal');

                let app = null;

                dialog.on_page_show = () => {
                    setTimeout(() => {
                        const container = dialog.$wrapper.find('.modal-pdf-viewer-app')[0];
                        if (!container) return;

                        app = Vue.createApp({
                            template: `
                                <div style="height: 100%; width: 100%;">
                                    <loan-pdf-viewer
                                        :src="src"
                                        :page="page"
                                        :terms="terms">
                                    </loan-pdf-viewer>
                                </div>
                            `,
                            setup() {
                                const src = Vue.ref(file_url);
                                const page = Vue.ref(source.page || 1);
                                const terms = Vue.ref([]);
                                return { src, page, terms };
                            }
                        });

                        app.component(
                            'loan-pdf-viewer',
                            window.LoanManagerPdfSearchViewer.createVueComponent(Vue)
                        );
                        app.mount(container);
                    }, 200);
                };

                dialog.on_hide = () => {
                    if (app) {
                        app.unmount();
                    }
                    dialog.$wrapper.remove();
                };

                dialog.show();
            });
        }).catch((err) => {
            console.error(err);
            frappe.msgprint(__('Failed to load the document preview.'));
        });
    }

    loadPdfViewer(callback) {
        if (typeof Vue === 'undefined') {
            frappe.require("https://unpkg.com/vue@3/dist/vue.global.prod.js", () => {
                this.loadPdfViewerLibrary(callback);
            });
        } else {
            this.loadPdfViewerLibrary(callback);
        }
    }

    loadPdfViewerLibrary(callback) {
        const viewer_version = "5.7.284-7";
        if (window.LoanManagerPdfSearchViewer?.version === viewer_version) {
            callback();
            return;
        }
        frappe.require(
            "/assets/document_management/js/pdf_search_viewer.v5_7_284_7.js",
            () => callback()
        );
    }

    async send() {
        if (!this.session || this.activeMessage) return;
        const input = this.wrapper.find('#chat-input');
        const query = input.val().trim();
        if (!query) return;
        input.val('');
        this.setBusy(true);
        try {
            const result = await this.call('ask_question', {
                session: this.session.name,
                query
            });
            this.upsertMessage({
                name: result.user_message,
                role: 'user',
                status: 'Completed',
                content: query,
                references: []
            });
            this.upsertMessage({
                name: result.message,
                role: 'assistant',
                status: 'Queued',
                content: '',
                references: []
            });
            this.watchMessage(result.message);
            if (this.session.title === __('New conversation') ||
                this.session.title === 'Nueva conversaciÃ³n') {
                this.session.title = query.slice(0, 80);
                this.wrapper.find('#chat-title').text(this.session.title);
                const listed = this.sessions.find((row) => row.name === this.session.name);
                if (listed) listed.title = this.session.title;
                this.renderSessions();
            }
        } catch (error) {
            this.setBusy(false);
        }
    }

    watchMessage(messageName) {
        this.activeMessage = messageName;
        this.setBusy(true);
        this.subscribeRealtime(messageName);
        this.pollTimer = setInterval(() => this.pollMessage(messageName), 1500);
    }

    subscribeRealtime(messageName) {
        this.unsubscribeRealtime();
        this.realtimeEvent = `document_chat:${messageName}`;
        frappe.realtime.on(this.realtimeEvent, (event) => {
            const message = this.messages.get(messageName) || {
                name: messageName, role: 'assistant', content: '', references: []
            };
            if (event.type === 'status') {
                message.status = event.status || 'Processing';
                this.upsertMessage(message);
            } else if (event.type === 'delta') {
                message.status = 'Processing';
                message.content = (message.content || '') + event.content;
                this.upsertMessage(message);
            } else if (event.type === 'complete') {
                message.status = 'Completed';
                message.content = event.content;
                message.references = event.references || [];
                this.upsertMessage(message);
                this.finishWatch();
            } else if (event.type === 'error' || event.type === 'cancelled') {
                message.status = event.type === 'cancelled' ? 'Cancelled' : 'Failed';
                if (event.type === 'cancelled' && event.content !== undefined) {
                    message.content = event.content;
                    message.references = event.references || [];
                }
                message.error_message = event.message || '';
                this.upsertMessage(message);
                this.finishWatch();
            }
        });
    }

    async pollMessage(messageName) {
        if (!this.session) return;
        const message = await this.call('get_message', {message: messageName});
        if (!message) return;
        this.upsertMessage(message);
        if (!['Queued', 'Processing'].includes(message.status)) {
            this.finishWatch();
        }
    }

    async cancel() {
        if (!this.activeMessage) return;
        const messageName = this.activeMessage;
        const button = this.wrapper.find('#btn-cancel-chat');
        button.prop('disabled', true);
        const message = this.messages.get(messageName);
        if (message) {
            const node = this.wrapper.find(
                `[data-message="${this.selector(messageName)}"]`
            );
            node.find('.message-status').text(__('Cancelling...'));
        }
        try {
            const result = await this.call('cancel_message', {message: messageName});
            if (result && result.status === 'Cancelled') {
                const cancelled = this.messages.get(messageName) || {
                    name: messageName,
                    role: 'assistant',
                    references: []
                };
                cancelled.status = 'Cancelled';
                cancelled.content = result.content !== undefined
                    ? result.content
                    : (cancelled.content || '');
                cancelled.references = result.references || [];
                this.upsertMessage(cancelled);
                this.finishWatch();
            }
        } finally {
            button.prop('disabled', false);
        }
    }

    retryMessage(messageName) {
        const ordered = Array.from(this.messages.values());
        const index = ordered.findIndex((message) => message.name === messageName);
        for (let cursor = index - 1; cursor >= 0; cursor--) {
            if (ordered[cursor].role === 'user') {
                this.wrapper.find('#chat-input').val(ordered[cursor].content || '');
                this.send();
                return;
            }
        }
    }

    finishWatch() {
        this.activeMessage = null;
        this.stopPolling();
        this.unsubscribeRealtime();
        this.setBusy(false);
    }

    stopPolling() {
        if (this.pollTimer) clearInterval(this.pollTimer);
        this.pollTimer = null;
    }

    unsubscribeRealtime() {
        if (this.realtimeEvent && frappe.realtime.off) {
            frappe.realtime.off(this.realtimeEvent);
        }
        this.realtimeEvent = null;
    }

    setBusy(busy) {
        this.wrapper.find('#btn-send-chat').prop('disabled', busy);
        this.wrapper.find('#chat-input').prop('disabled', busy);
        this.wrapper.find('#btn-cancel-chat').toggleClass(
            'hidden',
            !busy || !this.activeMessage
        );
    }

    async editFilters() {
        if (!this.session) return;
        const current = this.parseJSON(this.session.filters_json);

        await Promise.all([
            new Promise(resolve => frappe.model.with_doctype('Document Tag Link', resolve)),
            new Promise(resolve => frappe.model.with_doctype('Document Link', resolve))
        ]);

        const dialog = new frappe.ui.Dialog({
            title: __('Document filters'),
            fields: [
                {fieldname: 'category', fieldtype: 'Link', options: 'Document Category', label: __('Category'), default: current.category},
                {fieldname: 'department', fieldtype: 'Link', options: 'Department', label: __('Department'), default: current.department},
                {fieldname: 'party_type', fieldtype: 'Link', options: 'DocType', label: __('Party Type'), default: current.party_type},
                {fieldname: 'party_name', fieldtype: 'Data', label: __('Party Name'), default: current.party_name},
                {
                    fieldname: 'tags',
                    fieldtype: 'Table MultiSelect',
                    options: 'Document Tag Link',
                    label: __('Tags'),
                    default: (current.tags || []).map(t => ({ tag: t }))
                },
                {
                    fieldname: 'documents',
                    fieldtype: 'Table MultiSelect',
                    options: 'Document Link',
                    label: __('Document IDs'),
                    default: (current.documents || []).map(d => ({ document: d }))
                }
            ],
            primary_action_label: __('Apply'),
            primary_action: async (values) => {
                values.tags = (values.tags || []).map(row => row.tag);
                values.documents = (values.documents || []).map(row => row.document);
                const filters = await this.call('update_session_filters', {
                    session: this.session.name,
                    filters: JSON.stringify(values)
                });
                this.session.filters_json = JSON.stringify(filters);
                this.renderFilters();
                dialog.hide();
            }
        });
        dialog.show();
    }

    async renderFilters() {
        if (!this.session) {
            this.wrapper.find('#active-filters').empty().hide();
            return;
        }
        const session_name = this.session.name;
        const filters = this.parseJSON(this.session.filters_json);
        const labels = [];

        const keys = Object.keys(filters);
        for (const key of keys) {
            const val = filters[key];
            if (!val || (Array.isArray(val) && !val.length)) continue;

            if (key === 'documents' && Array.isArray(val)) {
                try {
                    const response = await frappe.call({
                        method: 'frappe.client.get_list',
                        args: {
                            doctype: 'Document',
                            filters: { name: ['in', val] },
                            fields: ['name', 'title'],
                            limit_page_length: val.length
                        }
                    });
                    if (this.session && this.session.name === session_name) {
                        const docs_info = response.message || [];
                        const name_to_title = {};
                        docs_info.forEach(d => {
                            name_to_title[d.name] = d.title;
                        });
                        const formatted = val.map(name => {
                            const title = name_to_title[name];
                            return title ? `${name} (${title})` : name;
                        }).join(', ');
                        labels.push(`${key}: ${formatted}`);
                    }
                } catch (e) {
                    labels.push(`${key}: ${val.join(', ')}`);
                }
            } else {
                const formatted_val = Array.isArray(val) ? val.join(', ') : val;
                labels.push(`${key}: ${formatted_val}`);
            }
        }

        if (this.session && this.session.name === session_name) {
            this.wrapper.find('#active-filters').text(labels.join(' · ')).toggle(!!labels.length);
        }
    }

    renameSession() {
        if (!this.session) return;
        frappe.prompt(
            [{fieldname: 'title', fieldtype: 'Data', label: __('Title'), reqd: 1, default: this.session.title}],
            async (values) => {
                await this.call('rename_session', {session: this.session.name, title: values.title});
                this.session.title = values.title;
                this.wrapper.find('#chat-title').text(values.title);
                this.loadSessions(this.session.name);
            },
            __('Rename conversation')
        );
    }

    deleteSession() {
        if (!this.session) return;
        frappe.confirm(__('Delete this conversation?'), async () => {
            await this.call('delete_session', {session: this.session.name});
            this.session = null;
            await this.loadSessions();
        });
    }

    parseJSON(value) {
        try { return JSON.parse(value || '{}'); } catch (e) { return {}; }
    }

    csv(value) {
        return (value || '').split(',').map((item) => item.trim()).filter(Boolean);
    }

    selector(value) {
        return String(value).replace(/"/g, '\\"');
    }

    escape(value) {
        return $('<div></div>').text(value == null ? '' : String(value)).html();
    }

    scrollBottom() {
        const body = this.wrapper.find('#chat-messages');
        if (body.length) body.scrollTop(body[0].scrollHeight);
    }
}
