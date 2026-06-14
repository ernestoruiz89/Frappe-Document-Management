frappe.pages['document-management-console'].on_page_load = function(wrapper) {
    var page = frappe.ui.make_app_page({
        parent: wrapper,
        title: __('Document Management Console'),
        single_column: true
    });

    wrapper.document_management_console = new DocumentManagementConsole(wrapper, page);
};

class DocumentManagementConsole {
    constructor(wrapper, page) {
        this.wrapper = $(wrapper).find('.layout-main-section');
        this.page = page;
        
        if (typeof Vue === 'undefined') {
            frappe.require("https://unpkg.com/vue@3/dist/vue.global.prod.js", () => {
                this.load_pdf_viewer();
            });
        } else {
            this.load_pdf_viewer();
        }
    }

    load_pdf_viewer() {
        const viewer_version = "5.7.284-7";
        if (window.LoanManagerPdfSearchViewer?.version === viewer_version) {
            this.init_vue();
            return;
        }
        frappe.require(
            "/assets/document_management/js/pdf_search_viewer.v5_7_284_7.js",
            () => this.init_vue(),
        );
    }

    init_vue() {
        const template = `
            <div class="paperless-app" :class="{'sidebar-open': selected_doc}">
                <!-- Toolbar -->
                <div class="paperless-toolbar">
                    <div class="search-box">
                        <i class="fa fa-search search-icon"></i>
                        <input type="text" :placeholder="'${__('Search documents...')}'" v-model="filters.search" @input="debounce_fetch" />
                    </div>
                    <div class="filter-box categories-dropdown-container">
                        <button class="categories-dropdown-btn" @click.stop="toggle_category_dropdown">
                            <i class="fa fa-folder-open-o"></i>
                            <span v-if="filters.categories.length === 0">${__('All Categories')}</span>
                            <span v-else>${__('Categories')}: {{ filters.categories.length }}</span>
                            <i class="fa fa-caret-down"></i>
                        </button>
                        <div class="categories-dropdown-menu" v-if="show_category_dropdown" @click.stop>
                            <div class="categories-dropdown-header">
                                <span>${__('Select Categories')}</span>
                                <button v-if="filters.categories.length" class="btn-clear-categories" @click="clear_categories">${__('Clear')}</button>
                            </div>
                            <div class="categories-dropdown-list">
                                <label v-for="cat in categories" :key="cat.name" class="category-filter-item">
                                    <input type="checkbox" :value="cat.name" v-model="filters.categories" @change="fetch_documents" />
                                    <span class="category-name">{{ cat.name }}</span>
                                </label>
                            </div>
                        </div>
                    </div>
                    <div class="filter-box statuses-dropdown-container">
                        <button class="statuses-dropdown-btn" @click.stop="toggle_status_dropdown">
                            <i class="fa fa-info-circle"></i>
                            <span v-if="filters.statuses.length === 0">${__('All Statuses')}</span>
                            <span v-else>${__('Statuses')}: {{ filters.statuses.length }}</span>
                            <i class="fa fa-caret-down"></i>
                        </button>
                        <div class="statuses-dropdown-menu" v-if="show_status_dropdown" @click.stop>
                            <div class="statuses-dropdown-header">
                                <span>${__('Select Statuses')}</span>
                                <button v-if="filters.statuses.length" class="btn-clear-statuses" @click="clear_statuses">${__('Clear')}</button>
                            </div>
                            <div class="statuses-dropdown-list">
                                <label class="status-filter-item">
                                    <input type="checkbox" value="Draft" v-model="filters.statuses" @change="fetch_documents" />
                                    <span class="status-name">${__('Draft')}</span>
                                </label>
                                <label class="status-filter-item">
                                    <input type="checkbox" value="Published" v-model="filters.statuses" @change="fetch_documents" />
                                    <span class="status-name">${__('Published')}</span>
                                </label>
                                <label class="status-filter-item">
                                    <input type="checkbox" value="Obsolete" v-model="filters.statuses" @change="fetch_documents" />
                                    <span class="status-name">${__('Obsolete')}</span>
                                </label>
                            </div>
                        </div>
                    </div>
                    <div class="filter-box tags-dropdown-container">
                        <button class="tags-dropdown-btn" @click.stop="toggle_tag_dropdown">
                            <i class="fa fa-tags"></i>
                            <span v-if="filters.tags.length === 0">${__('All Tags')}</span>
                            <span v-else>${__('Tags')}: {{ filters.tags.length }}</span>
                            <i class="fa fa-caret-down"></i>
                        </button>
                        <div class="tags-dropdown-menu" v-if="show_tag_dropdown" @click.stop>
                            <div class="tags-dropdown-header">
                                <span>${__('Select Tags')}</span>
                                <button v-if="filters.tags.length" class="btn-clear-tags" @click="clear_tags">${__('Clear')}</button>
                            </div>
                            <div class="tags-dropdown-list">
                                <label v-for="tag in tags" :key="tag.name" class="tag-filter-item">
                                    <input type="checkbox" :value="tag.name" v-model="filters.tags" @change="fetch_documents" />
                                    <span class="tag-color-indicator" :style="{ backgroundColor: tag.color || '#ccc' }"></span>
                                    <span class="tag-name">{{ tag.name }}</span>
                                </label>
                            </div>
                        </div>
                    </div>
                    <div class="filter-box saved-view-box">
                        <select v-model="selected_saved_view" @change="apply_saved_view">
                            <option value="">${__('Saved views')}</option>
                            <option v-for="view in saved_views" :value="view.name">{{ view.view_name }}</option>
                        </select>
                        <button class="btn-refresh" @click="save_view" :title="'${__('Save current view')}'">
                            <i class="fa fa-bookmark-o"></i>
                        </button>
                        <button v-if="selected_saved_view" class="btn-refresh" @click="delete_view" :title="'${__('Delete saved view')}'">
                            <i class="fa fa-times"></i>
                        </button>
                    </div>
                    <button class="btn-refresh" @click="fetch_documents">
                        <i class="fa fa-refresh"></i>
                    </button>
                    <button class="btn-refresh" :class="{active: trash_mode}" @click="toggle_trash">
                        <i class="fa fa-trash"></i> {{ trash_mode ? '${__('Documents')}' : '${__('Trash')}' }}
                    </button>
                    <span class="doc-count" :style="{ visibility: loading ? 'hidden' : 'visible' }">{{ documents.length }} ${__('documents')}</span>
                    <div class="view-toggles">
                        <button class="btn" :class="{active: view_mode==='grid'}" @click="view_mode='grid'" :title="'${__('Grid View')}'"><i class="fa fa-th"></i></button>
                        <button class="btn" :class="{active: view_mode==='large'}" @click="view_mode='large'" :title="'${__('List Cards')}'"><i class="fa fa-th-list"></i></button>
                        <button class="btn" :class="{active: view_mode==='list'}" @click="view_mode='list'" :title="'${__('Table View')}'"><i class="fa fa-list"></i></button>
                    </div>
                    <button v-if="!trash_mode" class="btn-upload" @click="show_upload_modal = true" style="margin-left: 10px;">
                        <i class="fa fa-cloud-upload"></i> ${__('Upload')}
                    </button>
                </div>

                <div class="bulk-toolbar" v-if="selected_count">
                    <strong>{{ selected_count }} ${__('selected')}</strong>
                    <button v-if="!trash_mode" class="btn btn-sm btn-default" @click="show_bulk_edit">
                        <i class="fa fa-pencil"></i> ${__('Edit')}
                    </button>
                    <button v-if="!trash_mode" class="btn btn-sm btn-danger" @click="trash_selected">
                        <i class="fa fa-trash"></i> ${__('Move to trash')}
                    </button>
                    <button v-if="trash_mode" class="btn btn-sm btn-primary" @click="restore_selected">
                        <i class="fa fa-undo"></i> ${__('Restore')}
                    </button>
                    <button v-if="trash_mode && can_purge" class="btn btn-sm btn-danger" @click="purge_selected">
                        <i class="fa fa-times"></i> ${__('Delete permanently')}
                    </button>
                    <button class="btn btn-sm btn-default" @click="clear_selection">${__('Clear')}</button>
                </div>

                <div class="paperless-main">
                    <!-- Left: Document List/Grid -->
                    <div class="paperless-content">

                        <!-- Skeleton Loading -->
                        <div v-if="loading" class="skeleton-grid">
                            <div v-for="n in 8" class="skeleton-card">
                                <div class="skeleton-item skeleton-circle"></div>
                                <div class="skeleton-item skeleton-line w-80"></div>
                                <div class="skeleton-item skeleton-line w-60"></div>
                                <div class="skeleton-item skeleton-line w-40"></div>
                            </div>
                        </div>

                        <!-- Empty State -->
                        <div v-else-if="documents.length === 0" class="empty-state">
                            <div class="empty-icon"><i class="fa fa-folder-open-o"></i></div>
                            <h5>${__('No documents found')}</h5>
                            <p>${__('Try adjusting your search or filters to find what you are looking for.')}</p>
                        </div>
                        
                        <!-- Grid View (Small Cards) -->
                        <div v-else-if="view_mode === 'grid'" class="paperless-grid">
                            <div v-for="doc in documents" :key="doc.name" 
                                 class="paperless-card" 
                                 :class="{active: selected_doc && selected_doc.name === doc.name, selected: is_selected(doc.name)}"
                                 :data-type="get_file_type(doc)"
                                 @click="select_doc(doc)">
                                <div class="doc-icon-wrapper" :class="get_icon_class(doc)">
                                    <i class="fa" :class="get_icon(doc)"></i>
                                </div>
                                <div class="doc-title">{{ doc.document_name }} <small class="text-muted" style="font-weight: normal;">({{ doc.name }})</small></div>
                                <div style="font-size: 10px; color: var(--pc-primary); margin-top: 2px;" v-if="doc.ocr_status === 'Pending' || doc.ocr_status === 'Processing'">
                                    <i class="fa fa-spinner fa-spin"></i> ${__('Processing OCR')}
                                </div>
                                <div class="doc-meta" v-if="doc.document_code">{{ doc.document_code }}</div>
                                <div class="search-excerpt" v-if="filters.search && doc.search_excerpt">
                                    <span v-if="doc.search_page">${__('Page')} {{ doc.search_page }}: </span><span v-html="highlight_text(doc.search_excerpt, doc.search_terms)"></span>
                                </div>
                                <div style="margin-top: 8px; display: flex; gap: 4px; flex-wrap: wrap; justify-content: center;">
                                    <span v-if="doc.party_name" class="keyword-badge" style="font-size: 10px; padding: 1px 6px; margin: 0; background: var(--pc-primary-faded); color: var(--pc-primary-dark); border-color: var(--pc-primary-light);">
                                        <i class="fa fa-handshake-o" style="margin-right: 3px;"></i>{{ doc.party_name }}
                                    </span>
                                    <span v-if="doc.tags" v-for="tag in doc.tags.slice(0, 2)" class="keyword-badge" :style="{fontSize: '10px', padding: '1px 6px', margin: '0', backgroundColor: tag.color, color: get_contrast_color(tag.color)}">{{ tag.name }}</span>
                                </div>
                            </div>
                        </div>

                        <!-- Large Cards View -->
                        <div v-else-if="view_mode === 'large'" class="paperless-large-cards">
                            <div v-for="doc in documents" :key="doc.name"
                                 class="paperless-large-card"
                                 :class="{active: selected_doc && selected_doc.name === doc.name, selected: is_selected(doc.name)}"
                                 :data-type="get_file_type(doc)"
                                 @click="select_doc(doc)">
                                <input class="document-selector inline" type="checkbox" :checked="is_selected(doc.name)" @click.stop="toggle_selection(doc.name)" />
                                <div class="doc-icon-wrapper" :class="get_icon_class(doc)">
                                    <i class="fa" :class="get_icon(doc)"></i>
                                </div>
                                <div class="large-card-body">
                                    <div class="doc-title">{{ doc.document_name }} <small class="text-muted" style="font-weight: normal;">({{ doc.name }})</small></div>
                                    <div class="doc-meta-row">
                                        <span v-if="doc.document_code" class="doc-meta-item"><i class="fa fa-hashtag"></i> {{ doc.document_code }}</span>
                                        <span v-if="doc.ocr_status === 'Pending' || doc.ocr_status === 'Processing'" class="doc-meta-item" style="color: var(--pc-primary);"><i class="fa fa-spinner fa-spin"></i> ${__('OCR')}</span>
                                        <span class="doc-meta-item"><i class="fa fa-tag"></i> {{ doc.category }}</span>
                                        <span v-if="doc.party_type && doc.party_name" class="doc-meta-item"><i class="fa fa-handshake-o"></i> {{ doc.party_name }}</span>
                                        <span v-for="tag in doc.tags" class="keyword-badge" :style="{fontSize: '10px', padding: '1px 6px', margin: '0', backgroundColor: tag.color, color: get_contrast_color(tag.color)}">{{ tag.name }}</span>
                                    </div>
                                    <div class="search-excerpt" v-if="filters.search && doc.search_excerpt">
                                        <span v-if="doc.search_page">${__('Page')} {{ doc.search_page }}: </span><span v-html="highlight_text(doc.search_excerpt, doc.search_terms)"></span>
                                    </div>
                                </div>
                                <div class="large-card-actions">
                                    <button class="btn-action" @click.stop="open_doc(doc.name)" :title="'${__('Open in Frappe')}'"><i class="fa fa-external-link"></i></button>
                                    <a v-if="doc.original_file || doc.document_file" class="btn-action" :href="doc.original_file || doc.document_file" target="_blank" @click.stop :title="'${__('Download')}'"><i class="fa fa-download"></i></a>
                                </div>
                            </div>
                        </div>

                        <!-- List View -->
                        <div v-else class="paperless-list">
                            <table class="table table-hover">
                                <thead>
                                    <tr>
                                        <th style="width: 36px"><input type="checkbox" :checked="all_selected" @change="toggle_all" /></th>
                                        <th style="width: 44px"></th>
                                        <th>${__('Title')}</th>
                                        <th>${__('Category')}</th>
                                        <th>${__('Keywords')}</th>
                                        <th>${__('Date')}</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    <tr v-for="doc in documents" :key="doc.name" 
                                        :class="{active: selected_doc && selected_doc.name === doc.name, selected: is_selected(doc.name)}"
                                        @click="select_doc(doc)">
                                        <td><input type="checkbox" :checked="is_selected(doc.name)" @click.stop="toggle_selection(doc.name)" /></td>
                                        <td><span class="list-icon" :class="get_icon_class(doc)"><i class="fa" :class="get_icon(doc)"></i></span></td>
                                        <td>
                                            <strong>{{ doc.document_name }} <small class="text-muted" style="font-weight: normal;">({{ doc.name }})</small></strong>
                                            <span v-if="doc.ocr_status === 'Pending' || doc.ocr_status === 'Processing'" style="display: block; font-size: 11px; margin-top: 2px; color: var(--pc-primary);"><i class="fa fa-spinner fa-spin"></i> ${__('Processing OCR')}</span>
                                            <span v-if="doc.document_code" class="text-muted" style="display: block; font-size: 11px; margin-top: 2px;"><i class="fa fa-hashtag"></i> {{ doc.document_code }}</span>
                                            <span v-if="doc.party_name" class="text-muted" style="display: block; font-size: 11px; margin-top: 2px;"><i class="fa fa-handshake-o"></i> {{ doc.party_name }}</span>
                                            <span v-if="filters.search && doc.search_excerpt" class="search-excerpt">
                                                <span v-if="doc.search_page">${__('Page')} {{ doc.search_page }}: </span><span v-html="highlight_text(doc.search_excerpt, doc.search_terms)"></span>
                                            </span>
                                        </td>
                                        <td>{{ doc.category }}</td>
                                        <td>
                                             <div style="display: flex; gap: 4px; flex-wrap: wrap;">
                                                 <span v-for="tag in doc.tags.slice(0, 3)" class="keyword-badge" :style="{fontSize: '10px', padding: '1px 6px', margin: '0', whiteSpace: 'nowrap', backgroundColor: tag.color, color: get_contrast_color(tag.color)}">{{ tag.name }}</span>
                                             </div>
                                         </td>
                                        <td style="white-space: nowrap">{{ format_date(doc.document_date) }}</td>
                                    </tr>
                                </tbody>
                            </table>
                        </div>
                    </div>

                    <!-- Right: Sidebar -->
                    <div class="paperless-sidebar" v-if="selected_doc" :key="selected_doc.name">
                        <div class="sidebar-header">
                            <h4>{{ selected_doc.document_name }}</h4>
                            <button class="btn-close-sidebar" @click="selected_doc = null"><i class="fa fa-times"></i></button>
                        </div>
                        <div class="sidebar-body">
                            <!-- Preview (inline) -->
                            <div class="doc-preview" v-if="selected_doc.document_file && !is_fullscreen">
                                <button class="fullscreen-btn" @click="is_fullscreen = true" :title="'${__('Toggle Full Screen')}'">
                                    <i class="fa fa-expand"></i>
                                </button>

                                <!-- PDF Viewer -->
                                <div v-if="is_pdf(selected_doc.document_file)" style="width: 100%; height: 100%; display: flex; flex-direction: column; min-width: 0;">
                                    <div v-if="is_office(selected_doc.original_file)" class="alert alert-warning" style="flex-shrink: 0;">
                                        <i class="fa fa-info-circle"></i> 
                                        <strong>${__('Preview Notice:')}</strong> ${__('You are viewing an automatically generated preview from an Office file. Some complex formatting or design elements might not display with 100% accuracy. For perfect fidelity, we recommend downloading the original file from the sidebar.')}
                                    </div>
                                    <loan-pdf-viewer
                                        :src="selected_doc.document_file"
                                        :query="filters.search"
                                        :terms="selected_doc.search_terms || []"
                                        :page="selected_doc.search_page || 1">
                                    </loan-pdf-viewer>
                                </div>
                                <img v-else-if="is_img(selected_doc.document_file)" :src="selected_doc.document_file" />
                                
                                <!-- Office Formats (No PDF generated yet) -->
                                <div v-else-if="is_office(selected_doc.document_file)" class="office-placeholder">
                                    <div class="placeholder-icon"><i class="fa fa-file-text-o"></i></div>
                                    <h5>${__('Office Preview')}</h5>
                                    <p>${__('The PDF file for secure preview has not been generated yet or is processing.')}</p>
                                    <p style="margin-top: 6px;"><strong>${__('Please wait a few seconds and refresh the page, or download the original file using the sidebar button.')}</strong></p>
                                </div>
                                <div v-else class="office-placeholder">
                                    <div class="placeholder-icon"><i class="fa fa-file-o"></i></div>
                                    <h5>${__('Preview not available')}</h5>
                                    <p>${__('This file type cannot be previewed. Please download it to view.')}</p>
                                </div>
                            </div>
                            <!-- Placeholder when fullscreen is active -->
                            <div v-else-if="selected_doc.document_file && is_fullscreen" class="doc-preview" style="cursor: pointer;" @click="is_fullscreen = false">
                                <div class="office-placeholder">
                                    <div class="placeholder-icon"><i class="fa fa-expand"></i></div>
                                    <h5>${__('Fullscreen mode active')}</h5>
                                    <p>${__('Click here to exit fullscreen.')}</p>
                                </div>
                            </div>
                            <div v-else class="office-placeholder" style="min-height: 200px;">
                                <div class="placeholder-icon"><i class="fa fa-ban"></i></div>
                                <h5>${__('No file attached')}</h5>
                                <p>${__('This document does not have any file attached yet.')}</p>
                            </div>

                            <!-- Fullscreen Teleport (renders on body to escape sidebar transform) -->
                            <teleport to="body">
                                <div v-if="is_fullscreen && selected_doc && selected_doc.document_file" class="pc-fullscreen-overlay" @click.self="is_fullscreen = false">
                                    <button class="pc-fullscreen-close" @click="is_fullscreen = false" :title="'${__('Close Fullscreen')}'">
                                        <i class="fa fa-times"></i>
                                    </button>
                                    <div class="pc-fullscreen-content">
                                        <div v-if="is_pdf(selected_doc.document_file)" class="pc-fullscreen-pdf-wrapper">
                                            <div v-if="is_office(selected_doc.original_file)" class="alert alert-warning" style="margin-bottom: 12px; flex-shrink: 0; width: 100%;">
                                                <i class="fa fa-info-circle"></i> 
                                                <strong>${__('Preview Notice:')}</strong> ${__('You are viewing an automatically generated preview from an Office file. Some complex formatting or design elements might not display with 100% accuracy. For perfect fidelity, we recommend downloading the original file from the sidebar.')}
                                            </div>
                                            <loan-pdf-viewer
                                                :src="selected_doc.document_file"
                                                :query="filters.search"
                                                :terms="selected_doc.search_terms || []"
                                                :page="selected_doc.search_page || 1">
                                            </loan-pdf-viewer>
                                        </div>
                                        <img v-else-if="is_img(selected_doc.document_file)" :src="selected_doc.document_file" />
                                        <div v-else-if="is_office(selected_doc.document_file)" class="office-placeholder">
                                            <div class="placeholder-icon"><i class="fa fa-file-text-o"></i></div>
                                            <h5>${__('Office Preview')}</h5>
                                            <p>${__('The PDF file for secure preview has not been generated yet or is processing.')}</p>
                                            <p style="margin-top: 6px;"><strong>${__('Please wait a few seconds and refresh the page, or download the original file using the sidebar button.')}</strong></p>
                                        </div>
                                        <div v-else class="office-placeholder">
                                            <div class="placeholder-icon"><i class="fa fa-file-o"></i></div>
                                            <h5>${__('Preview not available')}</h5>
                                            <p>${__('This file type cannot be previewed. Please download it to view.')}</p>
                                        </div>
                                    </div>
                                </div>
                            </teleport>

                            <!-- Metadata -->
                            <div class="doc-metadata">
                                <div class="meta-item">
                                    <div class="meta-icon"><i class="fa fa-id-card-o"></i></div>
                                    <div class="meta-content">
                                        <div class="meta-label">${__('Document #')}</div>
                                        <div class="meta-value" style="font-family: monospace;">{{ selected_doc.name }}</div>
                                    </div>
                                </div>
                                <div class="meta-item" v-if="selected_doc.document_code">
                                    <div class="meta-icon"><i class="fa fa-hashtag"></i></div>
                                    <div class="meta-content">
                                        <div class="meta-label">${__('Code')}</div>
                                        <div class="meta-value">{{ selected_doc.document_code }}</div>
                                    </div>
                                </div>
                                <div class="meta-item">
                                    <div class="meta-icon"><i class="fa fa-tag"></i></div>
                                    <div class="meta-content">
                                        <div class="meta-label">${__('Category')}</div>
                                        <div class="meta-value">{{ selected_doc.category }}</div>
                                    </div>
                                </div>
                                <div class="meta-item" v-if="selected_doc.folder">
                                    <div class="meta-icon"><i class="fa fa-folder"></i></div>
                                    <div class="meta-content">
                                        <div class="meta-label">${__('Folder')}</div>
                                        <div class="meta-value">{{ selected_doc.folder }}</div>
                                    </div>
                                </div>
                                <div class="meta-item" v-if="selected_doc.department">
                                    <div class="meta-icon"><i class="fa fa-building-o"></i></div>
                                    <div class="meta-content">
                                        <div class="meta-label">${__('Department')}</div>
                                        <div class="meta-value">{{ selected_doc.department }}</div>
                                    </div>
                                </div>
                                <div class="meta-item">
                                    <div class="meta-icon"><i class="fa fa-circle"></i></div>
                                    <div class="meta-content">
                                        <div class="meta-label">${__('Status')}</div>
                                        <div class="meta-value"><span class="status-pill" :class="status_class(selected_doc.status)">{{ selected_doc.status }}</span></div>
                                    </div>
                                </div>
                                <div class="meta-item">
                                    <div class="meta-icon"><i class="fa fa-code-fork"></i></div>
                                    <div class="meta-content">
                                        <div class="meta-label">${__('Version')}</div>
                                        <div class="meta-value">{{ selected_doc.version || '-' }}</div>
                                    </div>
                                </div>
                                <div class="meta-item" v-if="selected_doc.party_type && selected_doc.party_name">
                                    <div class="meta-icon"><i class="fa fa-handshake-o"></i></div>
                                    <div class="meta-content">
                                        <div class="meta-label">${__('Correspondent')}</div>
                                        <div class="meta-value">
                                            <a href="#" @click.prevent="open_party(selected_doc.party_type, selected_doc.party_name)">
                                                {{ __(selected_doc.party_type) }}: {{ selected_doc.party_name }}
                                            </a>
                                        </div>
                                    </div>
                                </div>
                                <div class="meta-item" v-if="selected_doc.tags && selected_doc.tags.length > 0">
                                    <div class="meta-icon"><i class="fa fa-tags"></i></div>
                                    <div class="meta-content">
                                        <div class="meta-label">${__('Tags')}</div>
                                        <div class="meta-value" style="margin-top: 4px; display: flex; flex-wrap: wrap; gap: 4px;">
                                            <span v-for="tag in selected_doc.tags" class="keyword-badge" :style="{backgroundColor: tag.color, color: get_contrast_color(tag.color)}">{{ tag.name }}</span>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div class="sidebar-footer">
                            <button v-if="!trash_mode" class="btn btn-default" @click="reprocess_ocr(selected_doc.name)" :disabled="['Pending', 'Processing'].includes(selected_doc.ocr_status)">
                                <i class="fa fa-file-text-o"></i> ${__('Reprocess OCR')}
                            </button>
                            <button v-if="!trash_mode && is_office(selected_doc.original_file) && !is_pdf(selected_doc.document_file)" class="btn btn-generate" @click="force_pdf(selected_doc.name)">
                                <i class="fa fa-refresh"></i> ${__('Generate PDF (LibreOffice)')}
                            </button>
                            <button v-if="!trash_mode" class="btn btn-open" @click="open_doc(selected_doc.name)">
                                <i class="fa fa-external-link"></i> ${__('Open in Frappe')}
                            </button>
                            <button v-if="!trash_mode" class="btn btn-danger" @click="trash_one(selected_doc.name)">
                                <i class="fa fa-trash"></i> ${__('Move to trash')}
                            </button>
                            <button v-if="trash_mode" class="btn btn-primary" @click="restore_one(selected_doc.name)">
                                <i class="fa fa-undo"></i> ${__('Restore')}
                            </button>
                            <button v-if="trash_mode && can_purge" class="btn btn-danger" @click="purge_one(selected_doc.name)">
                                <i class="fa fa-times"></i> ${__('Delete permanently')}
                            </button>
                            <a v-if="selected_doc.original_file || selected_doc.document_file" :href="selected_doc.original_file || selected_doc.document_file" target="_blank" class="btn btn-download">
                                <i class="fa fa-download"></i> ${__('Download Original File')}
                            </a>
                        </div>
                    </div>
                    </div>
                </div>

                <!-- Upload Modal -->
                <teleport to="body">
                    <div v-if="show_upload_modal" class="pc-modal-overlay" @click.self="close_upload_modal">
                        <div class="pc-modal">
                            <div class="pc-modal-header">
                                <h4>${__('Upload Document')}</h4>
                                <button class="pc-modal-close" @click="close_upload_modal"><i class="fa fa-times"></i></button>
                            </div>
                            <div class="pc-modal-body">
                                <div class="pc-drop-zone" 
                                     @dragover.prevent="dragover = true" 
                                     @dragleave.prevent="dragover = false" 
                                     @drop.prevent="handle_drop"
                                     @click="$refs.fileInput.click()"
                                     :class="{ dragover: dragover }"
                                     v-if="!upload_form.file">
                                    <div class="pc-drop-icon"><i class="fa fa-cloud-upload"></i></div>
                                    <p class="pc-drop-text">${__('Drag & Drop your file here or click to browse')}</p>
                                    <input type="file" ref="fileInput" style="display: none" @change="handle_file_select">
                                </div>
                                <div v-else class="pc-file-preview">
                                    <i class="fa fa-file-text-o"></i>
                                    <div class="pc-file-name">{{ upload_form.file.name }}</div>
                                    <button class="pc-file-remove" @click.prevent="remove_file"><i class="fa fa-times"></i></button>
                                </div>
                                
                                <div class="pc-form-group">
                                    <label>${__('Document Code (Optional)')}</label>
                                    <input type="text" class="pc-form-control" v-model="upload_form.document_code" placeholder="${__('Leave blank to auto-generate')}">
                                </div>
                                <div class="pc-form-group">
                                    <label>${__('Title *')}</label>
                                    <input type="text" class="pc-form-control" v-model="upload_form.title" placeholder="${__('Document Title')}" required>
                                </div>
                                <div class="pc-form-group">
                                    <label>${__('Category *')}</label>
                                    <select class="pc-form-control" v-model="upload_form.category" required>
                                        <option value="" disabled>${__('Select Category')}</option>
                                        <option v-for="cat in categories" :value="cat.name">{{ cat.name }}</option>
                                    </select>
                                </div>
                                <div class="pc-form-group">
                                    <label>${__('Folder')} <span style="font-weight:normal;color:#888;">(Optional)</span></label>
                                    <select v-model="upload_form.folder" class="pc-input">
                                        <option value="">${__('Home/Attachments (Default)')}</option>
                                        <option v-for="fld in folders" :value="fld.folder_name">{{ fld.file_name }}</option>
                                    </select>
                                </div>
                                <!-- Tags simplified as comma separated for now -->
                            </div>
                            <div class="pc-modal-footer">
                                <button class="btn btn-default" @click="close_upload_modal">${__('Cancel')}</button>
                                <button class="btn-upload" @click="submit_upload" :disabled="!upload_form.file || !upload_form.title || !upload_form.category || is_uploading">
                                    <i class="fa fa-spinner fa-spin" v-if="is_uploading"></i>
                                    <i class="fa fa-upload" v-else></i>
                                    {{ is_uploading ? '${__('Uploading...')}' : '${__('Upload & Save')}' }}
                                </button>
                            </div>
                        </div>
                    </div>
                </teleport>

            </div>
        `;

        this.wrapper.html(`<div id="document-management-vue-app">${template}</div>`);

        const { createApp, ref, reactive, computed, onMounted, onBeforeUnmount } = Vue;

        const app = createApp({
            setup() {
                const route_params = new URLSearchParams(window.location.search);
                let requested_document = route_params.get('document');
                const requested_query = (route_params.get('query') || '').trim();
                const requested_page = Number(route_params.get('page')) || null;
                const documents = ref([]);
                const categories = ref([]);
                const tags = ref([]);
                const selected_doc = ref(null);
                const view_mode = ref('grid');
                const loading = ref(false);
                const trash_mode = ref(false);
                const saved_views = ref([]);
                const selected_saved_view = ref('');
                const selected_names = ref(new Set());
                const can_purge = (frappe.user_roles || []).includes('System Manager');
                const selected_count = computed(() => selected_names.value.size);
                const all_selected = computed(() =>
                    documents.value.length > 0 &&
                    documents.value.every((doc) => selected_names.value.has(doc.name))
                );
                const filters = reactive({
                    search: requested_query,
                    categories: [],
                    statuses: [],
                    tags: []
                });

                const show_category_dropdown = ref(false);
                const show_status_dropdown = ref(false);
                const show_tag_dropdown = ref(false);

                const toggle_category_dropdown = () => {
                    show_category_dropdown.value = !show_category_dropdown.value;
                    show_status_dropdown.value = false;
                    show_tag_dropdown.value = false;
                };
                const toggle_status_dropdown = () => {
                    show_status_dropdown.value = !show_status_dropdown.value;
                    show_category_dropdown.value = false;
                    show_tag_dropdown.value = false;
                };
                const toggle_tag_dropdown = () => {
                    show_tag_dropdown.value = !show_tag_dropdown.value;
                    show_category_dropdown.value = false;
                    show_status_dropdown.value = false;
                };

                const clear_categories = () => {
                    filters.categories = [];
                    fetch_documents();
                };
                const clear_statuses = () => {
                    filters.statuses = [];
                    fetch_documents();
                };
                const clear_tags = () => {
                    filters.tags = [];
                    fetch_documents();
                };

                const handle_window_click = (e) => {
                    if (show_tag_dropdown.value && !e.target.closest('.tags-dropdown-container')) {
                        show_tag_dropdown.value = false;
                    }
                    if (show_category_dropdown.value && !e.target.closest('.categories-dropdown-container')) {
                        show_category_dropdown.value = false;
                    }
                    if (show_status_dropdown.value && !e.target.closest('.statuses-dropdown-container')) {
                        show_status_dropdown.value = false;
                    }
                };

                const show_upload_modal = ref(false);
                const is_uploading = ref(false);
                const dragover = ref(false);
                const upload_form = reactive({
                    file: null,
                    title: '',
                    category: '',
                    folder: '',
                    document_code: ''
                });

                const folders = ref([]);

                const close_upload_modal = () => {
                    show_upload_modal.value = false;
                    upload_form.file = null;
                    upload_form.title = '';
                    upload_form.category = '';
                    upload_form.folder = '';
                    upload_form.document_code = '';
                };

                const handle_drop = (e) => {
                    dragover.value = false;
                    const files = e.dataTransfer.files;
                    if (files.length > 0) {
                        set_file(files[0]);
                    }
                };

                const handle_file_select = (e) => {
                    const files = e.target.files;
                    if (files.length > 0) {
                        set_file(files[0]);
                    }
                };

                const set_file = (f) => {
                    upload_form.file = f;
                    if (!upload_form.title) {
                        upload_form.title = f.name.replace(/\.[^/.]+$/, "");
                    }
                };

                const remove_file = () => {
                    upload_form.file = null;
                };

                const submit_upload = () => {
                    if (!upload_form.file || !upload_form.title || !upload_form.category) return;
                    is_uploading.value = true;
                    
                    const reader = new FileReader();
                    reader.onload = function(e) {
                        const file_data = {
                            filename: upload_form.file.name,
                            content: e.target.result.split(',')[1]
                        };
                        
                        frappe.call({
                            method: 'document_management.frappe_document_management.page.document_management_console.document_management_console.quick_upload',
                            args: {
                                title: upload_form.title,
                                category: upload_form.category,
                                folder: upload_form.folder || null,
                                document_code: upload_form.document_code || null,
                                file_data: JSON.stringify(file_data)
                            },
                            callback: function(r) {
                                is_uploading.value = false;
                                if (!r.exc) {
                                    frappe.show_alert({message: __('Document uploaded successfully'), indicator: 'green'});
                                    close_upload_modal();
                                    fetch_documents();
                                }
                            },
                            error: function() {
                                is_uploading.value = false;
                            }
                        });
                    };
                    reader.readAsDataURL(upload_form.file);
                };

                let debounceTimer;

                const fetch_documents = () => {
                    loading.value = true;
                    frappe.call({
                        method: 'document_management.frappe_document_management.page.document_management_console.document_management_console.get_documents',
                        args: {
                            search_text: filters.search,
                            categories: filters.categories.length ? JSON.stringify(filters.categories) : null,
                            statuses: filters.statuses.length ? JSON.stringify(filters.statuses) : null,
                            tags: filters.tags.length ? JSON.stringify(filters.tags) : null,
                            trash: trash_mode.value ? 1 : 0
                        },
                        callback: (r) => {
                            documents.value = r.message || [];
                            selected_names.value = new Set();
                            if (requested_document) {
                                const requested = documents.value.find(
                                    (doc) => doc.name === requested_document
                                );
                                if (requested) {
                                    if (requested_page && !requested.search_page) {
                                        requested.search_page = requested_page;
                                    }
                                    selected_doc.value = requested;
                                }
                                requested_document = null;
                            }
                            if (selected_doc.value &&
                                !documents.value.some((doc) => doc.name === selected_doc.value.name)) {
                                selected_doc.value = null;
                            }
                            loading.value = false;
                        },
                        error: () => {
                            loading.value = false;
                            frappe.msgprint(__('Failed to load documents. Please try again.'));
                        }
                    });
                };

                const fetch_categories = () => {
                    frappe.call({
                        method: 'document_management.frappe_document_management.page.document_management_console.document_management_console.get_categories',
                        callback: (r) => {
                            categories.value = r.message || [];
                        }
                    });
                };

                const fetch_saved_views = () => {
                    frappe.call({
                        method: 'document_management.frappe_document_management.page.document_management_console.document_management_console.list_saved_views',
                        callback: (r) => {
                            saved_views.value = r.message || [];
                        }
                    });
                };

                const apply_saved_view = () => {
                    const view = saved_views.value.find(
                        (row) => row.name === selected_saved_view.value
                    );
                    if (!view) return;
                    let values = {};
                    try {
                        values = JSON.parse(view.filters_json || '{}');
                    } catch (e) {
                        frappe.msgprint(__('Saved view is invalid.'));
                        return;
                    }
                    filters.search = values.search || '';
                    if (values.categories) {
                        filters.categories = values.categories;
                    } else if (values.category) {
                        filters.categories = [values.category];
                    } else {
                        filters.categories = [];
                    }

                    if (values.statuses) {
                        filters.statuses = values.statuses;
                    } else if (values.status) {
                        filters.statuses = [values.status];
                    } else {
                        filters.statuses = [];
                    }
                    filters.tags = values.tags || [];
                    trash_mode.value = Boolean(values.trash);
                    if (values.view_mode) {
                        view_mode.value = values.view_mode;
                    }
                    selected_doc.value = null;
                    fetch_documents();
                };

                const save_view = () => {
                    frappe.prompt(
                        [
                            {
                                fieldname: 'view_name',
                                fieldtype: 'Data',
                                label: __('View name'),
                                reqd: 1
                            },
                            {
                                fieldname: 'view_mode',
                                fieldtype: 'Select',
                                label: __('View Type'),
                                options: [
                                    { value: 'grid', label: __('Grid') },
                                    { value: 'large', label: __('List Card') },
                                    { value: 'list', label: __('Table') }
                                ],
                                default: view_mode.value,
                                reqd: 1
                            }
                        ],
                        (values) => {
                            frappe.call({
                                method: 'document_management.frappe_document_management.page.document_management_console.document_management_console.save_current_view',
                                args: {
                                    view_name: values.view_name,
                                    filters: JSON.stringify({
                                        search: filters.search,
                                        categories: filters.categories,
                                        statuses: filters.statuses,
                                        tags: filters.tags,
                                        view_mode: values.view_mode,
                                        trash: trash_mode.value ? 1 : 0
                                    })
                                },
                                callback: (r) => {
                                    if (!r.exc) {
                                        selected_saved_view.value = r.message.name;
                                        fetch_saved_views();
                                        frappe.show_alert({
                                            message: __('View saved'),
                                            indicator: 'green'
                                        });
                                    }
                                }
                            });
                        },
                        __('Save current view')
                    );
                };

                const delete_view = () => {
                    const name = selected_saved_view.value;
                    if (!name) return;
                    frappe.confirm(__('Delete this saved view?'), () => {
                        frappe.call({
                            method: 'document_management.frappe_document_management.page.document_management_console.document_management_console.delete_saved_view',
                            args: {view: name},
                            callback: (r) => {
                                if (!r.exc) {
                                    selected_saved_view.value = '';
                                    fetch_saved_views();
                                }
                            }
                        });
                    });
                };

                const debounce_fetch = () => {
                    clearTimeout(debounceTimer);
                    debounceTimer = setTimeout(fetch_documents, 300);
                };

                const select_doc = (doc) => {
                    selected_doc.value = doc;
                };

                const is_selected = (name) => selected_names.value.has(name);
                const toggle_selection = (name) => {
                    const next = new Set(selected_names.value);
                    next.has(name) ? next.delete(name) : next.add(name);
                    selected_names.value = next;
                };
                const toggle_all = () => {
                    selected_names.value = all_selected.value
                        ? new Set()
                        : new Set(documents.value.map((doc) => doc.name));
                };
                const clear_selection = () => {
                    selected_names.value = new Set();
                };
                const selected_list = () => Array.from(selected_names.value);

                const run_bulk_action = (method, args, message) => {
                    return new Promise((resolve, reject) => {
                        frappe.call({
                            method: `document_management.frappe_document_management.page.document_management_console.document_management_console.${method}`,
                            args,
                            freeze: true,
                            callback: (r) => {
                                if (!r.exc) {
                                    frappe.show_alert({message, indicator: 'green'});
                                    selected_doc.value = null;
                                    fetch_documents();
                                    resolve(r.message);
                                }
                            },
                            error: reject
                        });
                    });
                };

                const toggle_trash = () => {
                    trash_mode.value = !trash_mode.value;
                    selected_doc.value = null;
                    clear_selection();
                    fetch_documents();
                };

                const show_bulk_edit = () => {
                    const dialog = new frappe.ui.Dialog({
                        title: __('Edit selected documents'),
                        fields: [
                            {fieldname: 'status', fieldtype: 'Select', label: __('Status'), options: '\nDraft\nPublished\nObsolete'},
                            {fieldname: 'category', fieldtype: 'Link', options: 'Document Category', label: __('Category')},
                            {fieldname: 'add_tags', fieldtype: 'Data', label: __('Add tags (comma separated)')},
                            {fieldname: 'remove_tags', fieldtype: 'Data', label: __('Remove tags (comma separated)')}
                        ],
                        primary_action_label: __('Apply'),
                        primary_action: (values) => {
                            dialog.hide();
                            run_bulk_action('bulk_update_documents', {
                                documents: JSON.stringify(selected_list()),
                                status: values.status || null,
                                category: values.category || null,
                                add_tags: JSON.stringify(csv(values.add_tags)),
                                remove_tags: JSON.stringify(csv(values.remove_tags))
                            }, __('Documents updated'));
                        }
                    });
                    dialog.show();
                };

                const trash_selected = () => {
                    frappe.confirm(__('Move selected documents to trash?'), () =>
                        run_bulk_action('move_documents_to_trash', {
                            documents: JSON.stringify(selected_list())
                        }, __('Documents moved to trash'))
                    );
                };
                const restore_selected = () => {
                    run_bulk_action('restore_documents', {
                        documents: JSON.stringify(selected_list())
                    }, __('Documents restored'));
                };
                const purge_selected = () => {
                    frappe.confirm(__('Permanently delete selected documents and their versions?'), () =>
                        run_bulk_action('permanently_delete_documents', {
                            documents: JSON.stringify(selected_list())
                        }, __('Documents permanently deleted'))
                    );
                };
                const trash_one = (name) => {
                    selected_names.value = new Set([name]);
                    trash_selected();
                };
                const restore_one = (name) => {
                    selected_names.value = new Set([name]);
                    restore_selected();
                };
                const purge_one = (name) => {
                    selected_names.value = new Set([name]);
                    purge_selected();
                };
                const csv = (value) => (value || '')
                    .split(',')
                    .map((item) => item.trim())
                    .filter(Boolean);

                const open_doc = (name) => {
                    frappe.set_route('Form', 'Document', name);
                };

                const status_class = (status) => {
                    if (status === 'Published') return 'published';
                    if (status === 'Draft') return 'draft';
                    if (status === 'Obsolete') return 'obsolete';
                    return '';
                };

                const is_pdf = (file) => file && file.toLowerCase().endsWith('.pdf');
                const is_img = (file) => file && file.match(/\.(jpeg|jpg|gif|png|webp|svg)$/i) != null;
                const is_office = (file) => file && file.match(/\.(doc|docx|ppt|pptx|xls|xlsx)$/i) != null;

                const escape_html = (value) => String(value || '')
                    .replace(/&/g, '&amp;')
                    .replace(/</g, '&lt;')
                    .replace(/>/g, '&gt;')
                    .replace(/"/g, '&quot;')
                    .replace(/'/g, '&#039;');

                const highlight_text = (value, terms) => {
                    const text = String(value || '');
                    terms = Array.from(new Set(terms || []))
                        .sort((left, right) => right.length - left.length);
                    if (!terms.length) return escape_html(text);
                    const escaped_terms = terms.map(
                        (term) => term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
                    );
                    const pattern = new RegExp(`(${escaped_terms.join('|')})`, 'giu');
                    let html = '';
                    let last_index = 0;
                    for (const match of text.matchAll(pattern)) {
                        html += escape_html(text.slice(last_index, match.index));
                        html += `<mark>${escape_html(match[0])}</mark>`;
                        last_index = match.index + match[0].length;
                    }
                    return html + escape_html(text.slice(last_index));
                };

                const get_file_type = (doc) => {
                    const file_path = doc.original_file || doc.document_file;
                    if (!file_path) return 'generic';
                    if (is_pdf(file_path)) return 'pdf';
                    if (is_img(file_path)) return 'image';
                    if (file_path.match(/\.(doc|docx)$/i)) return 'word';
                    if (file_path.match(/\.(xls|xlsx)$/i)) return 'excel';
                    if (file_path.match(/\.(ppt|pptx)$/i)) return 'ppt';
                    return 'generic';
                };

                const get_icon_class = (doc) => {
                    return get_file_type(doc);
                };

                const get_icon = (doc) => {
                    const file_path = doc.original_file || doc.document_file;
                    if (is_pdf(file_path)) return 'fa-file-pdf-o';
                    if (is_img(file_path)) return 'fa-file-image-o';
                    if (file_path && file_path.match(/\.(doc|docx)$/i)) return 'fa-file-word-o';
                    if (file_path && file_path.match(/\.(xls|xlsx)$/i)) return 'fa-file-excel-o';
                    if (file_path && file_path.match(/\.(ppt|pptx)$/i)) return 'fa-file-powerpoint-o';
                    if (file_path) return 'fa-file-o';
                    return 'fa-ban';
                };

                const get_contrast_color = (hex) => {
                    if (!hex) return '#000000';
                    if (hex.indexOf('#') === 0) hex = hex.slice(1);
                    if (hex.length === 3) hex = hex[0] + hex[0] + hex[1] + hex[1] + hex[2] + hex[2];
                    if (hex.length !== 6) return '#000000';
                    
                    const r = parseInt(hex.slice(0, 2), 16);
                    const g = parseInt(hex.slice(2, 4), 16);
                    const b = parseInt(hex.slice(4, 6), 16);
                    
                    const yiq = ((r * 299) + (g * 587) + (b * 114)) / 1000;
                    return (yiq >= 128) ? '#000000' : '#ffffff';
                };

                const is_fullscreen = ref(false);

                const force_pdf = (doc_name) => {
                    frappe.call({
                        method: 'document_management.frappe_document_management.page.document_management_console.document_management_console.force_generate_pdf',
                        args: { doc_name: doc_name },
                        callback: function(r) {
                            if (!r.exc) {
                                frappe.msgprint(r.message);
                                setTimeout(() => {
                                    fetch_documents();
                                }, 1500);
                            }
                        }
                    });
                };

                const reprocess_ocr = (doc_name) => {
                    frappe.confirm(
                        __('Reprocess OCR from the original file and rebuild page-level text?'),
                        () => {
                            frappe.call({
                                method: 'document_management.frappe_document_management.page.document_management_console.document_management_console.reprocess_ocr',
                                args: {doc_name},
                                callback: (r) => {
                                    if (!r.exc) {
                                        selected_doc.value.ocr_status = 'Pending';
                                        frappe.show_alert({
                                            message: __('OCR reprocessing queued'),
                                            indicator: 'blue'
                                        });
                                    }
                                }
                            });
                        }
                    );
                };

                const format_date = (date_str) => {
                    if (!date_str) return '';
                    return frappe.datetime.str_to_user(date_str.split(' ')[0]);
                };

                const open_party = (party_type, party_name) => {
                    frappe.set_route('Form', party_type, party_name);
                };

                const fetch_folders = () => {
                    frappe.call({
                        method: 'document_management.frappe_document_management.page.document_management_console.document_management_console.get_folders',
                        callback: (r) => {
                            folders.value = r.message || [];
                        }
                    });
                };

                const fetch_tags = () => {
                    frappe.call({
                        method: 'document_management.frappe_document_management.page.document_management_console.document_management_console.get_tags',
                        callback: (r) => {
                            tags.value = r.message || [];
                        }
                    });
                };

                onMounted(() => {
                    fetch_categories();
                    fetch_folders();
                    fetch_saved_views();
                    fetch_tags();
                    fetch_documents();
                    window.addEventListener('click', handle_window_click);
                });

                onBeforeUnmount(() => {
                    window.removeEventListener('click', handle_window_click);
                });

                return {
                    documents, categories, folders, tags, selected_doc, view_mode, loading, filters, is_fullscreen,
                    trash_mode, selected_count, all_selected, can_purge,
                    saved_views, selected_saved_view,
                    show_category_dropdown, toggle_category_dropdown, clear_categories,
                    show_status_dropdown, toggle_status_dropdown, clear_statuses,
                    show_tag_dropdown, toggle_tag_dropdown, clear_tags,
                    fetch_documents, debounce_fetch, select_doc, open_doc, status_class, force_pdf,
                    apply_saved_view, save_view, delete_view,
                    reprocess_ocr,
                    is_selected, toggle_selection, toggle_all, clear_selection, toggle_trash,
                    show_bulk_edit, trash_selected, restore_selected, purge_selected,
                    trash_one, restore_one, purge_one,
                    is_pdf, is_img, is_office, highlight_text,
                    get_icon, get_icon_class, get_file_type, get_contrast_color,
                    format_date, open_party,
                    show_upload_modal, upload_form, dragover, is_uploading, close_upload_modal, handle_drop, handle_file_select, remove_file, submit_upload
                };
            }
        });

        app.component(
            'loan-pdf-viewer',
            window.LoanManagerPdfSearchViewer.createVueComponent(Vue)
        );
        app.mount('#document-management-vue-app');
    }
}
