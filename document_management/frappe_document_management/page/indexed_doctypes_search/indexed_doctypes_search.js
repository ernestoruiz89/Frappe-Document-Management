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
        this.currentPage = 1;
        this.pageLength = 25;
        this.searchRequest = 0;
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
        this.wrapper.find('#indexed-doctypes-search-button').on('click', () => {
            this.search(1);
        });
        this.wrapper.find('#indexed-doctypes-search-input').on('keydown', (event) => {
            if (event.key === 'Enter') {
                event.preventDefault();
                this.search(1);
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
                if (this.currentQuery) this.search(1);
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
                    if (this.currentQuery) this.search(1);
                });
            container.append(button);
        });
        all.toggleClass('active', this.selectedTypes.size === 0);
    }

    async search(page = 1) {
        const query = this.wrapper.find('#indexed-doctypes-search-input').val().trim();
        if (!query) return;
        const requestId = ++this.searchRequest;
        this.currentPage = Math.max(Number(page) || 1, 1);
        this.currentQuery = query;
        const results = this.wrapper.find('#indexed-doctypes-search-results');
        const status = this.wrapper.find('#indexed-doctypes-search-status');
        const button = this.wrapper.find('#indexed-doctypes-search-button');
        button.prop('disabled', true);
        results.html(
            '<div class="search-empty"><i class="fa fa-spinner fa-spin"></i></div>'
        );
        status.removeClass('text-danger').text(__('Searching...'));
        try {
            const response = await this.call('search', {
                query,
                page: this.currentPage,
                page_length: this.pageLength,
                doctypes: JSON.stringify(Array.from(this.selectedTypes))
            });
            if (requestId !== this.searchRequest) return;
            this.currentTerms = response.terms || [];
            this.renderResults(response);
            const count = (response.exact || []).length;
            const pagination = response.pagination || {};
            status.text(
                response.exact_error
                    ? response.exact_error
                    : count
                        ? __('Results {0}-{1}', [
                            pagination.from || 1,
                            pagination.to || count
                        ])
                        : __('0 results')
            ).toggleClass(
                'text-danger',
                Boolean(response.exact_error)
            );
        } catch (error) {
            if (requestId !== this.searchRequest) return;
            results.html(
                `<div class="search-empty"><strong>${__(
                    'Search is temporarily unavailable.'
                )}</strong></div>`
            );
            status.empty();
        } finally {
            if (requestId === this.searchRequest) {
                button.prop('disabled', false);
            }
        }
    }

    renderResults(response) {
        const container = this.wrapper.find('#indexed-doctypes-search-results').empty();
        const rows = response.exact || [];
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
            rows
        );
        if (!rows.length && !response.exact_error) {
            container.append(
                `<div class="search-empty"><i class="fa fa-search"></i><strong>${
                    __('No permitted results found.')
                }</strong></div>`
            );
        }
        this.renderPagination(container, response.pagination || {});
    }

    renderPagination(container, pagination) {
        if (!pagination.has_previous && !pagination.has_more) return;
        const controls = $('<nav class="search-pagination"></nav>');
        const previous = $('<button class="btn btn-default btn-sm"></button>')
            .text(__('Previous'))
            .prop('disabled', !pagination.has_previous)
            .on('click', () => this.search(this.currentPage - 1));
        const page = $('<span class="search-pagination-page"></span>').text(
            __('Page {0}', [pagination.page || this.currentPage])
        );
        const next = $('<button class="btn btn-default btn-sm"></button>')
            .text(__('Next'))
            .prop('disabled', !pagination.has_more)
            .on('click', () => this.search(this.currentPage + 1));
        controls.append(previous, page, next);
        container.append(controls);
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
                $('<div class="result-match"></div>').text(
                    this.matchLabel(row)
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
        const terms = this.highlightTerms()
            .map((term) => this.foldText(term))
            .filter(Boolean);
        if (!terms.length) {
            fragment.append(document.createTextNode(text));
            return fragment;
        }

        const folded = this.foldTextWithOffsets(text);
        const ranges = [];
        terms.forEach((term) => {
            let searchFrom = 0;
            while (searchFrom < folded.text.length) {
                const index = folded.text.indexOf(term, searchFrom);
                if (index < 0) break;
                const first = folded.offsets[index];
                const last = folded.offsets[index + term.length - 1];
                if (first && last) {
                    ranges.push([first.start, last.end]);
                }
                searchFrom = index + Math.max(term.length, 1);
            }
        });
        ranges.sort((left, right) => left[0] - right[0] || right[1] - left[1]);
        const merged = [];
        ranges.forEach((range) => {
            const previous = merged[merged.length - 1];
            if (previous && range[0] <= previous[1]) {
                previous[1] = Math.max(previous[1], range[1]);
            } else {
                merged.push(range.slice());
            }
        });

        let lastIndex = 0;
        merged.forEach(([start, end]) => {
            if (start > lastIndex) {
                fragment.append(
                    document.createTextNode(text.slice(lastIndex, start))
                );
            }
            const mark = document.createElement('mark');
            mark.textContent = text.slice(start, end);
            fragment.append(mark);
            lastIndex = end;
        });
        if (lastIndex < text.length) {
            fragment.append(document.createTextNode(text.slice(lastIndex)));
        }
        return fragment;
    }

    matchLabel(row) {
        if (row.match_type === 'exact') {
            return __('Exact match');
        }
        if (row.match_type === 'all_terms') {
            return __('All terms');
        }
        const coverage = Math.round(Number(row.coverage || 0) * 100);
        return __('Partial {0}%', [coverage]);
    }

    foldText(value) {
        return String(value || '')
            .normalize('NFKD')
            .replace(/\p{M}/gu, '')
            .toLocaleLowerCase();
    }

    foldTextWithOffsets(value) {
        const source = String(value || '');
        const offsets = [];
        let text = '';
        let sourceIndex = 0;
        for (const character of source) {
            const foldedCharacter = this.foldText(character);
            text += foldedCharacter;
            for (let index = 0; index < foldedCharacter.length; index += 1) {
                offsets.push({
                    start: sourceIndex,
                    end: sourceIndex + character.length
                });
            }
            sourceIndex += character.length;
        }
        return { text, offsets };
    }

    highlightTerms() {
        return Array.from(
            new Set(this.currentTerms)
        ).sort(
            (left, right) => right.length - left.length
        );
    }

}
