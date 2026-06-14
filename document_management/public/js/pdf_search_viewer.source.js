import {
    getDocument,
} from "pdfjs-dist/legacy/build/pdf.mjs";
import {
    WorkerMessageHandler,
} from "pdfjs-dist/legacy/build/pdf.worker.mjs";
import {
    EventBus,
    PDFFindController,
    PDFLinkService,
    PDFViewer,
} from "pdfjs-dist/web/pdf_viewer.mjs";


const VIEWER_VERSION = "5.7.284-7";

// Keep the worker implementation in this bundle so deployments do not depend
// on web-server MIME configuration for PDF.js module workers.
globalThis.pdfjsWorker = {WorkerMessageHandler};


class PdfSearchViewer {
    constructor(container, options = {}) {
        this.container = container;
        this.source = options.source || "";
        this.query = this.normalizeQuery(options.query);
        this.initialPage = Number(options.page) || 1;
        this.loadingTask = null;
        this.pdf = null;
        this.lastFindQuery = null;

        this.maxScale = Number(options.maxScale) || 1.5;

        this.eventBus = new EventBus();
        this.linkService = new PDFLinkService({eventBus: this.eventBus});
        this.findController = new PDFFindController({
            eventBus: this.eventBus,
            linkService: this.linkService,
            updateMatchesCountOnProgress: true,
        });
        this.findController.onIsPageVisible = () => true;

        this.viewerElement = document.createElement("div");
        this.viewerElement.className = "pdfViewer";
        this.container.replaceChildren(this.viewerElement);
        this.viewer = new PDFViewer({
            container: this.container,
            viewer: this.viewerElement,
            eventBus: this.eventBus,
            linkService: this.linkService,
            findController: this.findController,
            textLayerMode: 1,
            removePageBorders: true,
        });
        this.linkService.setViewer(this.viewer);

        this.onPagesInit = () => {
            this.goToPage(this.initialPage);
            this.viewer.currentScaleValue = "page-width";
            if (this.viewer.currentScale > this.maxScale) {
                this.viewer.currentScale = this.maxScale;
            }
            options.onPageChange?.(
                this.viewer.currentPageNumber,
                this.viewer.pagesCount,
            );
            this.dispatchFind(true);
        };
        this.onPageRendered = () => this.dispatchFind(false);
        this.onPageChanging = ({pageNumber}) => {
            options.onPageChange?.(pageNumber, this.viewer.pagesCount);
        };
        this.onMatchesCount = ({matchesCount}) => {
            options.onMatchesChange?.(
                matchesCount?.current || 0,
                matchesCount?.total || 0,
            );
        };
        this.onScaleChanging = ({scale}) => {
            options.onScaleChange?.(Math.round((scale || 1) * 100));
        };
        this.eventBus.on("pagesinit", this.onPagesInit);
        this.eventBus.on("pagerendered", this.onPageRendered);
        this.eventBus.on("pagechanging", this.onPageChanging);
        this.eventBus.on("updatefindmatchescount", this.onMatchesCount);
        this.eventBus.on("updatefindcontrolstate", this.onMatchesCount);
        this.eventBus.on("scalechanging", this.onScaleChanging);

        this.resizeObserver = new ResizeObserver(() => {
            if (this.viewer.pagesCount) {
                this.viewer.currentScaleValue = "page-width";
                if (this.viewer.currentScale > this.maxScale) {
                    this.viewer.currentScale = this.maxScale;
                }
            }
        });
        this.resizeObserver.observe(this.container);
        this.load().catch((error) => options.onError?.(error));
    }

    async load() {
        if (!this.source) {
            return;
        }
        this.loadingTask = getDocument({
            url: this.source,
            withCredentials: true,
        });
        this.pdf = await this.loadingTask.promise;
        this.linkService.setDocument(this.pdf);
        this.viewer.setDocument(this.pdf);
    }

    setQuery(query) {
        const normalized = this.normalizeQuery(query);
        if (JSON.stringify(normalized) === JSON.stringify(this.query)) {
            return;
        }
        this.query = normalized;
        this.lastFindQuery = null;
        this.dispatchFind(true);
    }

    setPage(page) {
        this.initialPage = Number(page) || 1;
        this.goToPage(this.initialPage);
    }

    goToPage(page) {
        if (!this.viewer.pagesCount) {
            return;
        }
        this.viewer.currentPageNumber = Math.min(
            Math.max(Math.trunc(Number(page) || 1), 1),
            this.viewer.pagesCount,
        );
    }

    nextMatch(previous = false) {
        if (!this.hasQuery()) {
            return;
        }
        this.eventBus.dispatch("find", this.findOptions("again", previous));
    }

    zoomIn() {
        this.viewer.increaseScale({steps: 1});
    }

    zoomOut() {
        this.viewer.decreaseScale({steps: 1});
    }

    fitWidth() {
        if (this.viewer.pagesCount) {
            this.viewer.currentScaleValue = "page-width";
        }
    }

    dispatchFind(force) {
        if (!this.viewer.pagesCount) {
            return;
        }
        if (!force && this.query === this.lastFindQuery) {
            return;
        }
        this.lastFindQuery = this.query;
        this.eventBus.dispatch("find", this.findOptions("", false));
    }

    findOptions(type, findPrevious) {
        return {
            source: this,
            type,
            query: this.query,
            caseSensitive: false,
            entireWord: false,
            findPrevious,
            highlightAll: this.hasQuery(),
            matchDiacritics: false,
            phraseSearch: true,
        };
    }

    normalizeQuery(query) {
        if (Array.isArray(query)) {
            return Array.from(
                new Set(query.map((term) => String(term || "").trim()).filter(Boolean))
            );
        }
        return String(query || "").trim();
    }

    hasQuery() {
        return Array.isArray(this.query)
            ? this.query.length > 0
            : Boolean(this.query);
    }

    destroy() {
        this.resizeObserver?.disconnect();
        this.eventBus.off("pagesinit", this.onPagesInit);
        this.eventBus.off("pagerendered", this.onPageRendered);
        this.eventBus.off("pagechanging", this.onPageChanging);
        this.eventBus.off("updatefindmatchescount", this.onMatchesCount);
        this.eventBus.off("updatefindcontrolstate", this.onMatchesCount);
        this.eventBus.off("scalechanging", this.onScaleChanging);
        this.loadingTask?.destroy();
        this.viewer?.cleanup();
        this.container.replaceChildren();
    }
}


function createVueComponent(Vue) {
    return {
        props: {
            src: {type: String, required: true},
            query: {type: String, default: ""},
            terms: {type: Array, default: () => []},
            page: {type: Number, default: 1},
            maxScale: {type: Number, default: 1.5},
        },
        setup(props) {
            const root = Vue.ref(null);
            const viewer = Vue.shallowRef(null);
            const currentPage = Vue.ref(props.page || 1);
            const pageCount = Vue.ref(0);
            const currentMatch = Vue.ref(0);
            const totalMatches = Vue.ref(0);
            const scalePercent = Vue.ref(100);
            const error = Vue.ref("");
            const initialQuery = () => props.terms.length
                ? props.terms.join(" ")
                : props.query;
            const searchQuery = Vue.ref(initialQuery());

            Vue.onMounted(() => {
                viewer.value = new PdfSearchViewer(root.value, {
                    source: props.src,
                    query: props.terms.length ? props.terms : props.query,
                    page: props.page,
                    maxScale: props.maxScale,
                    onPageChange: (current, total) => {
                        currentPage.value = current;
                        pageCount.value = total;
                    },
                    onMatchesChange: (current, total) => {
                        currentMatch.value = current;
                        totalMatches.value = total;
                    },
                    onScaleChange: (percent) => {
                        scalePercent.value = percent;
                    },
                    onError: (loadError) => {
                        console.error("Unable to load PDF preview", loadError);
                        const detail = loadError?.message || String(loadError || "");
                        error.value = detail
                            ? `${__("Unable to load PDF preview.")} ${detail}`
                            : __("Unable to load PDF preview.");
                    },
                });
            });
            Vue.watch(
                () => [props.query, props.terms],
                () => {
                    searchQuery.value = initialQuery();
                    viewer.value?.setQuery(
                        props.terms.length ? props.terms : props.query
                    );
                },
                {deep: true},
            );
            Vue.watch(() => props.page, (value) => viewer.value?.setPage(value));
            Vue.onBeforeUnmount(() => viewer.value?.destroy());

            return {
                root,
                currentPage,
                pageCount,
                currentMatch,
                totalMatches,
                scalePercent,
                searchQuery,
                error,
                hasSearch: Vue.computed(
                    () => Boolean(searchQuery.value.trim())
                ),
                runSearch: () => viewer.value?.setQuery(searchQuery.value),
                clearSearch: () => {
                    searchQuery.value = "";
                    viewer.value?.setQuery("");
                },
                previousMatch: () => viewer.value?.nextMatch(true),
                nextMatch: () => viewer.value?.nextMatch(false),
                previousPage: () => viewer.value?.goToPage(currentPage.value - 1),
                nextPage: () => viewer.value?.goToPage(currentPage.value + 1),
                zoomOut: () => viewer.value?.zoomOut(),
                zoomIn: () => viewer.value?.zoomIn(),
                fitWidth: () => viewer.value?.fitWidth(),
            };
        },
        template: `
            <div class="loan-pdf-shell">
                <div class="loan-pdf-toolbar">
                    <button type="button" @click="previousPage" :disabled="currentPage <= 1"
                        title="${__("Previous page")}">
                        <i class="fa fa-chevron-left"></i>
                    </button>
                    <span>{{ currentPage }} / {{ pageCount || "..." }}</span>
                    <button type="button" @click="nextPage"
                        :disabled="!pageCount || currentPage >= pageCount"
                        title="${__("Next page")}">
                        <i class="fa fa-chevron-right"></i>
                    </button>
                    <span class="loan-pdf-toolbar-divider"></span>
                    <button type="button" @click="zoomOut" title="${__("Zoom out")}">
                        <i class="fa fa-search-minus"></i>
                    </button>
                    <button type="button" class="loan-pdf-scale" @click="fitWidth"
                        title="${__("Fit to width")}">
                        {{ scalePercent }}%
                    </button>
                    <button type="button" @click="zoomIn" title="${__("Zoom in")}">
                        <i class="fa fa-search-plus"></i>
                    </button>
                    <div class="loan-pdf-search">
                        <i class="fa fa-search"></i>
                        <input v-model="searchQuery" type="search"
                            placeholder="${__("Search in document")}"
                            @input="runSearch" @keyup.enter="nextMatch">
                        <button v-if="hasSearch" type="button" class="loan-pdf-clear"
                            @click="clearSearch" title="${__("Clear search")}">
                            <i class="fa fa-times"></i>
                        </button>
                    </div>
                    <span v-if="hasSearch" class="loan-pdf-match-count">
                        {{ currentMatch }} / {{ totalMatches }}
                    </span>
                    <button v-if="hasSearch" type="button" @click="previousMatch"
                        title="${__("Previous match")}">
                        <i class="fa fa-arrow-up"></i>
                    </button>
                    <button v-if="hasSearch" type="button" @click="nextMatch"
                        title="${__("Next match")}">
                        <i class="fa fa-arrow-down"></i>
                    </button>
                </div>
                <div class="loan-pdf-viewport">
                    <div v-if="error" class="loan-pdf-error">{{ error }}</div>
                    <div v-show="!error" ref="root" class="loan-pdf-container"></div>
                </div>
            </div>
        `,
    };
}


window.LoanManagerPdfSearchViewer = {
    version: VIEWER_VERSION,
    PdfSearchViewer,
    createVueComponent,
};
