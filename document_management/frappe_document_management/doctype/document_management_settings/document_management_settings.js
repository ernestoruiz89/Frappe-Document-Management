frappe.ui.form.on('Document Management Settings', {
    setup(frm) {
        frm.set_query('document_type', 'indexed_doctypes', () => {
            return {
                filters: {
                    istable: 0,
                    issingle: 0
                }
            };
        });
    },

    refresh(frm) {
        render_provider_summary(frm);
    },

    chat_provider(frm) {
        const model = frm.doc.rag_model || '';
        if (
            frm.doc.chat_provider === 'Ollama'
            && (!model || ['gpt-5-mini', 'local-model'].includes(model))
        ) {
            frm.set_value('rag_model', 'llama3.1');
        } else if (
            frm.doc.chat_provider === 'LM Studio'
            && (!model || ['gpt-5-mini', 'llama3.1'].includes(model))
        ) {
            frm.set_value('rag_model', 'local-model');
            if (!frm.doc.chat_endpoint) {
                frm.set_value('chat_endpoint', 'http://localhost:1234/v1');
            }
        } else if (
            ['OpenAI', 'OpenAI Compatible'].includes(frm.doc.chat_provider)
            && (!model || ['llama3.1', 'local-model'].includes(model))
        ) {
            frm.set_value('rag_model', 'gpt-5-mini');
        }
        render_provider_summary(frm);
    },

    chat_endpoint: render_provider_summary,
    rag_model: render_provider_summary,

    embedding_provider(frm) {
        const model = frm.doc.embedding_model || '';
        if (
            ['OpenAI', 'OpenAI Compatible'].includes(frm.doc.embedding_provider)
            && (
                !model
                || [
                    'sentence-transformers/all-MiniLM-L6-v2',
                    'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2',
                    'local-model'
                ].includes(model)
            )
        ) {
            frm.set_value('embedding_model', 'text-embedding-3-small');
        } else if (
            frm.doc.embedding_provider === 'LM Studio'
            && (
                !model
                || [
                    'sentence-transformers/all-MiniLM-L6-v2',
                    'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2',
                    'text-embedding-3-small'
                ].includes(model)
            )
        ) {
            frm.set_value('embedding_model', 'local-model');
            if (!frm.doc.embedding_endpoint) {
                frm.set_value('embedding_endpoint', 'http://localhost:1234/v1');
            }
        } else if (
            frm.doc.embedding_provider === 'Local (sentence-transformers)'
            && (!model || ['text-embedding-3-small', 'local-model'].includes(model))
        ) {
            frm.set_value(
                'embedding_model',
                'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
            );
        }
        render_provider_summary(frm);
    },

    embedding_endpoint: render_provider_summary,
    embedding_model: render_provider_summary,
    enable_openai_for_ocr: render_provider_summary,
    openai_ocr_model: render_provider_summary,
    ocr_mode: render_provider_summary
});

function render_provider_summary(frm) {
    const field = frm.fields_dict.provider_guide_html;
    if (!field || !field.$wrapper) {
        return;
    }

    const escape = (value) => $('<div>').text(value || '').html();
    const endpoint = (value, fallback) => escape(value || fallback);
    const chatIsOpenAI = frm.doc.chat_provider === 'OpenAI';
    const chatIsOllama = frm.doc.chat_provider === 'Ollama';
    const chatIsLMStudio = frm.doc.chat_provider === 'LM Studio';
    const embeddingsAreLocal =
        frm.doc.embedding_provider === 'Local (sentence-transformers)';
    const embeddingsIsOpenAI = frm.doc.embedding_provider === 'OpenAI';
    const embeddingsUseLMStudio = frm.doc.embedding_provider === 'LM Studio';

    let chatDetail;
    if (chatIsOllama) {
        chatDetail = `${__('Ollama server')}: ${endpoint(
            frm.doc.chat_endpoint,
            'http://localhost:11434'
        )}. ${__('No API key is used for chat.')}`;
    } else if (chatIsLMStudio) {
        chatDetail = `${__('LM Studio OpenAI-compatible server')}: ${endpoint(
            frm.doc.chat_endpoint,
            'http://localhost:1234/v1'
        )}. ${__(
            'No API key is required.'
        )}`;
    } else if (chatIsOpenAI) {
        chatDetail = `${__('Official OpenAI API')}. ${__('Uses the OpenAI API Key below.')}`;
    } else {
        chatDetail = `${__('OpenAI-compatible API')}: ${endpoint(
            frm.doc.chat_endpoint,
            __('endpoint required')
        )}. ${__('Uses the OpenAI Compatible API Key below when authentication is required.')}`;
    }

    let embeddingDetail;
    if (embeddingsAreLocal) {
        embeddingDetail = __(
            'Runs Sentence Transformers inside the worker.'
        );
    } else if (embeddingsUseLMStudio) {
        embeddingDetail = `${__('LM Studio embeddings API')}: ${endpoint(
            frm.doc.embedding_endpoint,
            'http://localhost:1234/v1'
        )}. ${__(
            'Requires an embedding model loaded in LM Studio.'
        )}`;
    } else if (embeddingsIsOpenAI) {
        embeddingDetail = `${__('Official OpenAI embeddings API')}. ${__('Uses the OpenAI API Key below.')}`;
    } else {
        embeddingDetail = `${__('OpenAI-compatible embeddings API')}: ${endpoint(
            frm.doc.embedding_endpoint,
            __('endpoint required')
        )}. ${__('Uses the OpenAI Compatible API Key below.')}`;
    }

    const ocrDetail = frm.doc.enable_openai_for_ocr
        ? __(
            'OCRmyPDF runs locally first. OpenAI Vision is called only for pages that still have no extracted text.'
        )
        : __('OCR is local only. OpenAI Vision fallback is disabled.');

    const card = (title, provider, model, detail, tone) => `
        <div style="
            border: 1px solid var(--border-color);
            border-left: 4px solid ${tone};
            border-radius: 8px;
            padding: 12px 14px;
            background: var(--card-bg);
        ">
            <div style="font-size: 11px; color: var(--text-muted);">${title}</div>
            <div style="font-weight: 600; margin: 3px 0;">
                ${escape(provider)}
                <span style="font-weight: 400; color: var(--text-muted);">
                    &middot; ${escape(model)}
                </span>
            </div>
            <div style="font-size: 12px; line-height: 1.45;">${detail}</div>
        </div>
    `;

    field.$wrapper.html(`
        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 10px; margin-bottom: 14px;">
            ${card(
                __('Document Chat'),
                frm.doc.chat_provider || 'OpenAI',
                frm.doc.rag_model || 'gpt-5-mini',
                chatDetail,
                (chatIsOllama || chatIsLMStudio) ? '#16a34a' : '#2563eb'
            )}
            ${card(
                __('Semantic Search'),
                frm.doc.embedding_provider || 'Local (sentence-transformers)',
                frm.doc.embedding_model || 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2',
                embeddingDetail,
                (embeddingsAreLocal || embeddingsUseLMStudio)
                    ? '#16a34a'
                    : '#2563eb'
            )}
            ${card(
                __('OCR'),
                frm.doc.enable_openai_for_ocr
                    ? __('Local OCR + OpenAI fallback')
                    : __('Local OCR only'),
                frm.doc.enable_openai_for_ocr
                    ? (frm.doc.openai_ocr_model || 'gpt-5-mini')
                    : (frm.doc.ocr_mode || 'Auto'),
                ocrDetail,
                frm.doc.enable_openai_for_ocr ? '#d97706' : '#16a34a'
            )}
        </div>
        ${(chatIsLMStudio || embeddingsUseLMStudio) ? `
            <div class="alert alert-warning" style="margin-bottom: 14px;">
                ${__(
                    'LM Studio must be reachable from the Frappe worker. localhost refers to the Frappe server, not the browser computer.'
                )}
            </div>
        ` : ''}
    `);
}
