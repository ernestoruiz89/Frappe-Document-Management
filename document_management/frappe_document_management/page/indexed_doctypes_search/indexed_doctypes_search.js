frappe.pages['indexed-doctypes-search'].on_page_load = function(wrapper) {
    const page = frappe.ui.make_app_page({
        parent: wrapper,
        title: __('Indexed DocTypes Search'),
        single_column: true
    });
    $(frappe.render_template('indexed_doctypes_search', {})).appendTo(page.main);
    new IndexedDocTypesSearchController(wrapper, page);
};

class IndexedDocTypesSearchController {
    constructor(wrapper, page) {
        this.wrapper = $(wrapper);
        this.page = page;
        this.selectedTypes = new Set();
        this.options = [];
        this.currentQuery = '';
        this.currentTerms = [];
        this.bind();
        this.setupActions();
        this.loadOptions();
    }

    setupActions() {
        if ((frappe.user_roles || []).includes('System Manager')) {
            this.page.add_menu_item(__('Rebuild Indexed DocTypes Search Index'), () => {
                frappe.confirm(
                    __('Rebuild all configured search indexes?'),
                    () => this.call('enqueue_rebuild_index').then(() => {
                        frappe.show_alert({
                            message: __('Search index rebuild queued'),
                            indicator: 'blue'
                        });
                    })
                );
            });
        }
    }

    bind() {
        this.wrapper.find('#indexed-doctypes-search-button').on('click', () => this.search());
        this.wrapper.find('#indexed-doctypes-search-input').on('keydown', (event) => {
            if (event.key === 'Enter') {
                event.preventDefault();
                this.search();
            }
        });
    }

    call(method, args = {}) {
        return new Promise((resolve, reject) => {
            frappe.call({
                method: `document_management.search.indexer.${method}`,
                args,
                callback: (response) => resolve(response.message),
                error: reject
            });
        });
    }

    async loadOptions() {
        const options = await this.call('get_search_options');
        this.options = options.doctypes || [];
        this.renderTypes();
        const modes = [];
        if (options.full_text_enabled) modes.push(__('Full text'));
        let status = modes.length
            ? __('Enabled: {0}', [modes.join(' + ')])
            : __('No search indexes are enabled.');
        if (options.full_text_enabled && !options.generic_index_ready) {
            status += ` | ${__(
                'The operational record index must be rebuilt before first use.'
            )}`;
        }
        this.wrapper.find('#indexed-doctypes-search-status').text(status);
    }

    renderTypes() {
        const container = this.wrapper.find('#indexed-doctypes-search-types').empty();
        const all = $('<button class="search-type active"></button>')
            .text(__('All'))
            .on('click', () => {
                this.selectedTypes.clear();
                this.renderTypes();
            });
        container.append(all);
        this.options.forEach((option) => {
            const button = $('<button class="search-type"></button>')
                .text(option.label)
                .toggleClass('active', this.selectedTypes.has(option.value))
                .on('click', () => {
                    if (this.selectedTypes.has(option.value)) {
                        this.selectedTypes.delete(option.value);
                    } else {
                        this.selectedTypes.add(option.value);
                    }
                    this.renderTypes();
                });
            container.append(button);
        });
        all.toggleClass('active', this.selectedTypes.size === 0);
    }

    async search() {
        const query = this.wrapper.find('#indexed-doctypes-search-input').val().trim();
        if (!query) return;
        this.currentQuery = query;
        const results = this.wrapper.find('#indexed-doctypes-search-results');
        const status = this.wrapper.find('#indexed-doctypes-search-status');
        results.html(
            '<div class="search-empty"><i class="fa fa-spinner fa-spin"></i></div>'
        );
        status.text(__('Searching...'));
        try {
            const response = await this.call('search', {
                query,
                limit: 25,
                doctypes: JSON.stringify(Array.from(this.selectedTypes))
            });
            this.currentTerms = response.terms || [];
            this.renderResults(response);
            const count = (response.exact || []).length;
            status.text(
                response.exact_error
                    ? response.exact_error
                    : __('{0} results', [count])
            ).toggleClass(
                'text-danger',
                Boolean(response.exact_error)
            );
        } catch (error) {
            results.html(
                `<div class="search-empty"><strong>${__(
                    'Search is temporarily unavailable.'
                )}</strong></div>`
            );
            status.empty();
        }
    }

    renderResults(response) {
        const container = this.wrapper.find('#indexed-doctypes-search-results').empty();
        if (response.exact_error) {
            container.append(
                $('<div class="alert alert-warning"></div>').text(
                    response.exact_error
                )
            );
        }
        this.renderSection(
            container,
            __('Indexed Records'),
            response.exact || []
        );
        if (!container.children().length) {
            container.html(
                `<div class="search-empty"><i class="fa fa-search"></i><strong>${
                    __('No permitted results found.')
                }</strong></div>`
            );
        }
    }

    renderSection(container, title, rows) {
        if (!rows.length) return;
        const section = $('<section class="result-section"></section>');
        section.append($('<h5 class="result-section-title"></h5>').text(title));
        const list = $('<div class="result-list"></div>');
        rows.forEach((row) => {
            const card = $('<a class="search-result"></a>')
                .attr('href', this.resultRoute(row));
            const header = $('<div class="result-header"></div>');
            header.append(
                $('<div class="result-title"></div>').append(
                    this.highlightText(row.title)
                )
            );
            header.append(
                $('<div class="result-score"></div>').text(
                    Number(row.score || 0).toFixed(3)
                )
            );
            card.append(header);
            card.append(
                $('<div class="result-meta"></div>').text(
                    `${row.doc_type} | ${row.doc_name}`
                )
            );
            if (row.excerpt) {
                card.append(
                    $('<div class="result-excerpt"></div>').append(
                        this.highlightText(row.excerpt)
                    )
                );
            }
            list.append(card);
        });
        section.append(list);
        container.append(section);
    }

    resultRoute(row) {
        if (row.doc_type !== 'Document') {
            return row.route;
        }
        const params = new URLSearchParams({
            document: row.doc_name,
            query: this.currentQuery
        });
        if (row.page) {
            params.set('page', row.page);
        }
        return `/app/document-management-console?${params.toString()}`;
    }

    highlightText(value) {
        const fragment = document.createDocumentFragment();
        const text = String(value || '');
        const terms = this.highlightTerms();
        if (!terms.length) {
            fragment.append(document.createTextNode(text));
            return fragment;
        }

        const pattern = new RegExp(
            `(${terms.map((term) => this.escapeRegExp(term)).join('|')})`,
            'giu'
        );
        let lastIndex = 0;
        for (const match of text.matchAll(pattern)) {
            if (match.index > lastIndex) {
                fragment.append(
                    document.createTextNode(text.slice(lastIndex, match.index))
                );
            }
            const mark = document.createElement('mark');
            mark.textContent = match[0];
            fragment.append(mark);
            lastIndex = match.index + match[0].length;
        }
        if (lastIndex < text.length) {
            fragment.append(document.createTextNode(text.slice(lastIndex)));
        }
        return fragment;
    }

    highlightTerms() {
        return Array.from(
            new Set(this.currentTerms)
        ).sort(
            (left, right) => right.length - left.length
        );
    }

    escapeRegExp(value) {
        return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    }
}
