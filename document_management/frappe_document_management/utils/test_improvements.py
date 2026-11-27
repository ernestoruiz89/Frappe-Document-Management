import hashlib
import importlib
import os
import sys
import tempfile
import types
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch


REPO_ROOT = Path(__file__).resolve().parents[3]


def _install_frappe_stub_if_missing():
    if "frappe" in sys.modules:
        return False

    class ValidationError(Exception):
        pass

    class PermissionError(Exception):
        pass

    class DoesNotExistError(Exception):
        pass

    class _AttrDict(dict):
        def __getattr__(self, item):
            try:
                return self[item]
            except KeyError as exc:
                raise AttributeError(item) from exc

        def __setattr__(self, key, value):
            self[key] = value

    def _throw(msg, exc=None):
        raise (exc or ValidationError)(msg)

    frappe_mod = types.ModuleType("frappe")
    frappe_mod.ValidationError = ValidationError
    frappe_mod.PermissionError = PermissionError
    frappe_mod.DoesNotExistError = DoesNotExistError
    frappe_mod._dict = _AttrDict
    frappe_mod.throw = _throw
    frappe_mod._ = lambda s: s
    frappe_mod.whitelist = lambda *a, **kw: (lambda fn: fn) if not (a and callable(a[0])) else a[0]
    frappe_mod.local = SimpleNamespace(response=_AttrDict())
    frappe_mod.session = SimpleNamespace(user="Administrator")
    frappe_mod.flags = SimpleNamespace(in_test=True)
    frappe_mod.db = SimpleNamespace(
        get_single_value=lambda dt, field: 1 if field == "enable_file_encryption" else None,
        get_value=lambda *a, **kw: None,
        set_value=MagicMock(),
        exists=lambda *a, **kw: False,
        delete=lambda *a, **kw: None,
        commit=lambda: None,
    )
    frappe_mod.get_all = MagicMock(return_value=[])
    frappe_mod.get_doc = MagicMock()
    frappe_mod.get_single = MagicMock()
    frappe_mod.enqueue = MagicMock()
    frappe_mod.generate_hash = lambda length=16: "0" * int(length)
    frappe_mod.publish_realtime = MagicMock()
    frappe_mod.log_error = MagicMock()
    frappe_mod.logger = lambda name="": SimpleNamespace(info=MagicMock(), warning=MagicMock())
    frappe_mod.get_traceback = lambda: "traceback"

    utils_mod = types.ModuleType("frappe.utils")
    utils_mod.get_site_path = lambda *parts: os.path.join("/tmp/site-a", *parts)
    frappe_mod.get_site_path = utils_mod.get_site_path

    from datetime import date, datetime, timedelta

    def _getdate(val):
        return val if isinstance(val, date) and not isinstance(val, datetime) else date.fromisoformat(str(val).strip()[:10])

    def _get_datetime(val):
        return val if isinstance(val, datetime) else datetime.fromisoformat(str(val).strip())

    utils_mod.getdate = _getdate
    utils_mod.get_datetime = _get_datetime
    utils_mod.now_datetime = lambda: datetime.now()
    utils_mod.today = lambda: date.today().isoformat()
    utils_mod.cint = lambda v: int(v or 0)
    utils_mod.add_days = lambda d, n: d + timedelta(days=int(n))
    utils_mod.add_to_date = lambda d, **kw: d + timedelta(
        days=kw.get("days", 0),
        hours=kw.get("hours", 0),
        minutes=kw.get("minutes", 0),
        seconds=kw.get("seconds", 0),
    )

    fm_mod = types.ModuleType("frappe.utils.file_manager")
    fm_mod.get_file_path = lambda url: url

    pw_mod = types.ModuleType("frappe.utils.password")
    from cryptography.fernet import Fernet

    _test_key = Fernet.generate_key().decode("utf-8")
    pw_mod.get_encryption_key = lambda: _test_key

    model_mod = types.ModuleType("frappe.model")
    doc_mod = types.ModuleType("frappe.model.document")

    class _BaseDoc:
        pass

    doc_mod.Document = _BaseDoc

    frappe_mod.utils = utils_mod
    sys.modules["frappe"] = frappe_mod
    sys.modules["frappe.utils"] = utils_mod
    sys.modules["frappe.utils.file_manager"] = fm_mod
    sys.modules["frappe.utils.password"] = pw_mod
    sys.modules["frappe.model"] = model_mod
    sys.modules["frappe.model.document"] = doc_mod
    if "requests" not in sys.modules:
        req_mod = types.ModuleType("requests")
        req_mod.post = MagicMock()
        sys.modules["requests"] = req_mod
    if "filelock" not in sys.modules:
        fl_mod = types.ModuleType("filelock")
        fl_mod.FileLock = MagicMock()
        fl_mod.Timeout = TimeoutError
        sys.modules["filelock"] = fl_mod
    if "unidecode" not in sys.modules:
        uni_mod = types.ModuleType("unidecode")
        uni_mod.unidecode = lambda s: s
        sys.modules["unidecode"] = uni_mod
    return True


_USING_STUB = _install_frappe_stub_if_missing()


class TestStreamingFileCrypto(TestCase):
    def setUp(self):
        self.file_crypto = importlib.import_module(
            "document_management.frappe_document_management.utils.file_crypto"
        )
        from cryptography.fernet import Fernet

        self.test_key = Fernet.generate_key().decode("utf-8")

    def test_v2_streaming_encrypt_decrypt_multichunk_and_o1_size(self):
        # 2.5 MiB payload spanning 3 AES-256-GCM chunks
        payload = os.urandom(2 * 1024 * 1024 + 512 * 1024 + 37)
        expected_sha = hashlib.sha256(payload).hexdigest()

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "large.pdf"
            path.write_bytes(payload)

            with (
                patch.object(self.file_crypto, "file_encryption_enabled", return_value=True),
                patch.object(self.file_crypto, "get_encryption_key", return_value=self.test_key),
            ):
                self.assertTrue(self.file_crypto.encrypt_path_at_rest(path))
                self.assertTrue(self.file_crypto.is_encrypted_path(path))
                self.assertTrue(path.read_bytes().startswith(self.file_crypto.MAGIC))
                # Idempotent re-encryption check
                self.assertFalse(self.file_crypto.encrypt_path_at_rest(path))

                self.assertEqual(self.file_crypto.plaintext_size(path), len(payload))
                self.assertEqual(self.file_crypto.sha256_path(path), expected_sha)
                self.assertEqual(self.file_crypto.read_path_bytes(path), payload)

                with self.file_crypto.decrypted_temp_file(path=str(path)) as dec_path:
                    self.assertEqual(Path(dec_path).read_bytes(), payload)

    def test_v2_empty_file_and_tamper_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty.txt"
            empty.write_bytes(b"")

            with (
                patch.object(self.file_crypto, "file_encryption_enabled", return_value=True),
                patch.object(self.file_crypto, "get_encryption_key", return_value=self.test_key),
            ):
                self.assertTrue(self.file_crypto.encrypt_path_at_rest(empty))
                self.assertEqual(self.file_crypto.plaintext_size(empty), 0)
                self.assertEqual(self.file_crypto.read_path_bytes(empty), b"")

                # Tamper with ciphertext of a non-empty file
                doc = Path(tmp) / "secret.txt"
                doc.write_bytes(b"top secret payload")
                self.file_crypto.encrypt_path_at_rest(doc)
                corrupted = bytearray(doc.read_bytes())
                corrupted[-1] ^= 0xFF
                doc.write_bytes(bytes(corrupted))
                with self.assertRaises(Exception):
                    self.file_crypto.read_path_bytes(doc)

    def test_legacy_v1_fernet_files_remain_readable(self):
        from cryptography.fernet import Fernet

        plaintext = b"legacy-v1-document-bytes"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy.pdf"
            encrypted = self.file_crypto.LEGACY_MAGIC + Fernet(self.test_key.encode()).encrypt(plaintext)
            path.write_bytes(encrypted)

            with patch.object(self.file_crypto, "get_encryption_key", return_value=self.test_key):
                self.assertTrue(self.file_crypto.is_encrypted_path(path))
                self.assertEqual(self.file_crypto.plaintext_size(path), len(plaintext))
                self.assertEqual(self.file_crypto.read_path_bytes(path), plaintext)
                self.assertEqual(
                    self.file_crypto.sha256_path(path),
                    hashlib.sha256(plaintext).hexdigest(),
                )

    def test_inline_unsafe_extensions_force_attachment(self):
        import frappe

        with tempfile.TemporaryDirectory() as tmp:
            svg = Path(tmp) / "diagram.svg"
            svg.write_bytes(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>")
            with patch.object(self.file_crypto, "get_file_path", return_value=str(svg)):
                self.file_crypto.document_file_response("/private/files/diagram.svg", attachment=False)
                self.assertEqual(frappe.local.response.display_content_as, "attachment")


class TestChunkingImprovements(TestCase):
    def test_structured_paragraphs_and_sentences(self):
        from document_management.frappe_document_management.rag.chunking import chunk_pages

        page = (
            "Section 1: Overview.\n\n"
            "First paragraph explains the payment schedule. It has two sentences.\n\n"
            "Second paragraph covers penalties. Late fees apply after ten days."
        )
        chunks = chunk_pages([page], chunk_size=15, overlap=3)
        self.assertGreaterEqual(len(chunks), 2)
        combined = " ".join(c.content for c in chunks)
        self.assertIn("Overview.", combined)
        self.assertIn("Late fees apply after ten days.", combined)

    def test_existing_word_stream_contracts_hold(self):
        from document_management.frappe_document_management.rag.chunking import chunk_pages

        words = [f"token-{index}" for index in range(1500)]
        chunks = chunk_pages([" ".join(words)], chunk_size=500, overlap=100)
        self.assertEqual(len(chunks), 4)
        self.assertIn("token-1499", chunks[-1].content)

        first_page = " ".join(f"first-{index}" for index in range(12))
        second_page = " ".join(f"second-{index}" for index in range(8))
        chunks2 = chunk_pages([first_page, second_page], chunk_size=10, overlap=2)
        self.assertEqual([c.page_number for c in chunks2], [1, 1, 2])
        self.assertEqual(chunks2[0].content.split()[-2:], chunks2[1].content.split()[:2])
        self.assertTrue(chunks2[2].content.startswith("second-0"))


class TestSecurityAndReliabilityFixes(TestCase):
    def test_loopback_endpoint_check_blocks_non_loopback_internal_ips(self):
        if "requests" not in sys.modules:
            sys.modules["requests"] = types.ModuleType("requests")
        providers = importlib.import_module(
            "document_management.frappe_document_management.rag.providers"
        )
        self.assertTrue(providers._is_loopback_endpoint("http://localhost:11434"))
        self.assertTrue(providers._is_loopback_endpoint("http://127.0.0.1:1234/v1"))
        self.assertFalse(providers._is_loopback_endpoint("http://169.254.169.254/latest/meta-data"))
        self.assertFalse(providers._is_loopback_endpoint("http://10.0.0.5:11434"))

    def test_openai_stream_ignores_empty_choices_chunks(self):
        if "requests" not in sys.modules:
            sys.modules["requests"] = types.ModuleType("requests")
        providers = importlib.import_module(
            "document_management.frappe_document_management.rag.providers"
        )
        events = [
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="hi"))]),
            SimpleNamespace(choices=[]),  # usage-only chunk
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="!"))]),
        ]
        client = MagicMock()
        client.chat.completions.create.return_value = events
        fake_openai = SimpleNamespace(OpenAI=MagicMock(return_value=client))
        cfg = SimpleNamespace(
            chat_provider="OpenAI Compatible",
            chat_model="m",
            chat_endpoint="https://api.example.com/v1",
            chat_api_key="k",
            timeout=30,
            allow_internal_endpoints=True,
        )
        with (
            patch.object(providers, "get_rag_config", return_value=cfg),
            patch.dict(sys.modules, {"openai": fake_openai}),
        ):
            out = "".join(providers.stream_chat([{"role": "user", "content": "hi"}]))
        self.assertEqual(out, "hi!")

    def test_realtime_scopes_only_me_documents_to_owner(self):
        import frappe
        from document_management.frappe_document_management.utils.realtime import (
            DOCUMENT_CHANGE_EVENT,
            publish_document_change,
        )

        private_doc = SimpleNamespace(name="DOC-SECRET", only_me=1, owner="alice@example.com")
        with patch.object(frappe, "publish_realtime") as pub:
            publish_document_change(private_doc)
        pub.assert_called_once_with(
            DOCUMENT_CHANGE_EVENT,
            {"doctype": "Document", "name": "DOC-SECRET", "deleted": False},
            after_commit=True,
            user="alice@example.com",
        )

    def test_ml_tagger_resolves_site_path_dynamically(self):
        import frappe
        from document_management.frappe_document_management.utils import ml_tagger

        with patch.object(
            frappe.utils,
            "get_site_path",
            side_effect=lambda *p: os.path.join("/sites/site-one", *p),
        ):
            dir1, _, _, _ = ml_tagger._model_paths()
        with patch.object(
            frappe.utils,
            "get_site_path",
            side_effect=lambda *p: os.path.join("/sites/site-two", *p),
        ):
            dir2, _, _, _ = ml_tagger._model_paths()
        self.assertNotEqual(dir1, dir2)
        self.assertIn("site-one", dir1)
        self.assertIn("site-two", dir2)

    def test_advanced_date_filter_expands_datetime_bounds(self):
        from document_management.search.advanced import _date_filter

        self.assertEqual(
            _date_filter("creation", "2026-06-01"),
            ["creation", "between", ["2026-06-01 00:00:00", "2026-06-01 23:59:59"]],
        )
        self.assertEqual(
            _date_filter("creation", "<=2026-06-01"),
            ["creation", "<=", "2026-06-01 23:59:59"],
        )
        self.assertEqual(
            _date_filter("creation", ">=2026-06-01"),
            ["creation", ">=", "2026-06-01 00:00:00"],
        )

    def test_document_tag_rejects_invalid_regex(self):
        from document_management.frappe_document_management.doctype.document_tag.document_tag import (
            DocumentTag,
        )

        tag = DocumentTag()
        tag.matching_algorithm = "Regular expression"
        tag.match_pattern = "[unclosed"
        with self.assertRaises(Exception):
            tag.validate()

    def test_no_external_cdn_links_and_obsolete_bundles_removed(self):
        public_js = REPO_ROOT / "document_management" / "public" / "js"
        for old_version in ("7", "8", "9"):
            self.assertFalse((public_js / f"pdf_search_viewer.v5_7_284_{old_version}.js").exists())
        for vendor_file in ("vue.global.prod.js", "marked.min.js", "purify.min.js"):
            self.assertTrue((public_js / "vendor" / vendor_file).is_file())

        for ext in ("*.js", "*.html"):
            for file_path in (REPO_ROOT / "document_management").rglob(ext):
                if "vendor" in file_path.parts or file_path.name.startswith("pdf_search_viewer.v5_"):
                    continue
                text = file_path.read_text(encoding="utf-8")
                self.assertNotIn("unpkg.com", text, f"CDN link left in {file_path}")
                self.assertNotIn("cdn.jsdelivr.net", text, f"CDN link left in {file_path}")


class TestPhase4To6Improvements(TestCase):
    def test_rag_sqlite_chunk_store_write_mutate_and_indexed_queries(self):
        if "filelock" not in sys.modules:
            fl = types.ModuleType("filelock")
            fl.FileLock = MagicMock()
            fl.Timeout = TimeoutError
            sys.modules["filelock"] = fl
        if "requests" not in sys.modules:
            sys.modules["requests"] = types.ModuleType("requests")

        rag_index = importlib.import_module(
            "document_management.frappe_document_management.rag.index"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [
                {
                    "name": "chunk-a-1",
                    "document": "DOC-A",
                    "document_version": "VER-A1",
                    "version_number": "1",
                    "page_number": 1,
                    "chunk_index": 0,
                    "content": "Alpha contract terms",
                    "content_hash": "ha1",
                    "embedding_model": "test-model",
                    "vector_id": "101",
                },
                {
                    "name": "chunk-b-1",
                    "document": "DOC-B",
                    "document_version": "VER-B1",
                    "version_number": "1",
                    "page_number": 2,
                    "chunk_index": 0,
                    "content": "Beta invoice amount",
                    "content_hash": "hb1",
                    "embedding_model": "test-model",
                    "vector_id": "202",
                },
            ]
            rag_index._write_chunk_metadata(rows, root)
            self.assertTrue((root / "chunks.sqlite").is_file())
            self.assertEqual(rag_index._count_chunk_metadata(root), 2)

            by_id = rag_index._query_chunks_by_vector_ids(root, [202])
            self.assertEqual(list(by_id.keys()), [202])
            self.assertEqual(by_id[202]["document"], "DOC-B")

            by_name = rag_index._query_chunks_by_names(root, ["chunk-a-1"])
            self.assertEqual(by_name["chunk-a-1"]["content"], "Alpha contract terms")

            # Incremental mutation: replace DOC-A, keep DOC-B untouched
            new_doc_a = [
                {
                    "name": "chunk-a-2",
                    "document": "DOC-A",
                    "document_version": "VER-A2",
                    "version_number": "2",
                    "page_number": 1,
                    "chunk_index": 0,
                    "content": "Updated alpha contract",
                    "content_hash": "ha2",
                    "embedding_model": "test-model",
                    "vector_id": "303",
                }
            ]
            rag_index._mutate_sqlite_chunks(root, ["DOC-A"], new_doc_a)
            self.assertEqual(rag_index._count_chunk_metadata(root), 2)
            self.assertEqual(rag_index._query_chunks_by_vector_ids(root, [101]), {})
            self.assertEqual(
                rag_index._query_chunks_by_vector_ids(root, [303])[303]["content"],
                "Updated alpha contract",
            )
            self.assertEqual(
                rag_index._query_chunks_by_vector_ids(root, [202])[202]["document"],
                "DOC-B",
            )

    def test_rag_legacy_chunks_json_migrates_seamlessly_to_sqlite(self):
        import json

        if "filelock" not in sys.modules:
            fl = types.ModuleType("filelock")
            fl.FileLock = MagicMock()
            fl.Timeout = TimeoutError
            sys.modules["filelock"] = fl
        rag_index = importlib.import_module(
            "document_management.frappe_document_management.rag.index"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy_rows = [
                {
                    "name": "legacy-1",
                    "document": "DOC-OLD",
                    "document_version": "VER-1",
                    "version_number": "1",
                    "page_number": 1,
                    "chunk_index": 0,
                    "content": "Legacy json chunk",
                    "content_hash": "h1",
                    "embedding_model": "m",
                    "vector_id": "999",
                }
            ]
            (root / "chunks.json").write_text(json.dumps(legacy_rows), encoding="utf-8")
            self.assertEqual(rag_index._count_chunk_metadata(root), 1)
            loaded = rag_index._load_document_chunks_from_store(root, ["DOC-OLD"])
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].name, "legacy-1")
            self.assertTrue((root / "chunks.sqlite").is_file())

    def test_share_link_passcode_hashing_and_rate_limit(self):
        import frappe
        import frappe.utils as fu
        from datetime import datetime, timedelta

        if not hasattr(fu, "add_days"):
            fu.add_days = lambda dt, days: dt + timedelta(days=days)
        if not hasattr(fu, "cint"):
            fu.cint = lambda v: int(v or 0)
        if not hasattr(fu, "now_datetime"):
            fu.now_datetime = lambda: datetime.now()
        if not hasattr(fu, "get_datetime"):
            fu.get_datetime = lambda v: v if isinstance(v, datetime) else datetime.fromisoformat(str(v))

        sharing = importlib.import_module(
            "document_management.frappe_document_management.utils.document_sharing"
        )
        hashed = sharing._passcode_hash("Secret123")
        self.assertTrue(hashed.startswith("pbkdf2_sha256$"))
        self.assertTrue(sharing._verify_passcode(hashed, "Secret123"))
        self.assertFalse(sharing._verify_passcode(hashed, "Wrong"))
        self.assertFalse(sharing._verify_passcode(hashed, ""))

        # Rate limit enforcement
        store = {}
        fake_cache = SimpleNamespace(
            get_value=lambda k: store.get(k),
            set_value=lambda k, v, expires_in_sec=60: store.__setitem__(k, v),
        )
        frappe.cache = lambda: fake_cache
        frappe.local.request_ip = "203.0.113.10"
        store[f"dm_share_rl:{'a'*16}:203.0.113.10"] = sharing.SHARE_RATE_LIMIT_PER_MINUTE
        with self.assertRaises(frappe.PermissionError):
            sharing._enforce_share_rate_limit("a" * 64)

    def test_document_has_permission_restricts_trashed_documents(self):
        import frappe

        doc_mod = importlib.import_module(
            "document_management.frappe_document_management.doctype.document.document"
        )
        trashed = SimpleNamespace(
            name="DOC-TRASH",
            owner="alice@example.com",
            is_deleted=1,
            only_me=0,
            category=None,
            roles_with_access=[],
            departments_with_access=[],
        )
        with patch.object(frappe, "get_roles", return_value=["All"], create=True):
            self.assertTrue(doc_mod.has_permission(trashed, "read", user="alice@example.com"))
            self.assertFalse(doc_mod.has_permission(trashed, "share", user="alice@example.com"))
            self.assertFalse(doc_mod.has_permission(trashed, "read", user="bob@example.com"))
        with patch.object(frappe, "get_roles", return_value=["All", "System Manager"], create=True):
            self.assertTrue(doc_mod.has_permission(trashed, "read", user="admin@example.com"))
            self.assertFalse(doc_mod.has_permission(trashed, "share", user="admin@example.com"))

    def test_folder_ingestion_subfolder_mapping_and_min_age(self):
        import time

        fi = importlib.import_module(
            "document_management.frappe_document_management.utils.folder_ingestion"
        )
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "inbox"
            done = source / "done"
            err = source / "error"
            legal = source / "Legal"
            legal.mkdir(parents=True)
            done.mkdir()
            err.mkdir()

            f1 = source / "root.txt"
            f1.write_bytes(b"hello root")
            f2 = legal / "contract.txt"
            f2.write_bytes(b"hello legal")
            empty = legal / "empty.txt"
            empty.write_bytes(b"")

            # Fresh files skipped when min_file_age_seconds > 0
            skipped = fi._candidate_files(
                source, done, err, 50, min_file_age_seconds=3600, map_subfolders_to_category=True
            )
            self.assertEqual(skipped, [])

            # Older files eligible and subfolder mapped
            old_time = time.time() - 120
            os.utime(f1, (old_time, old_time))
            os.utime(f2, (old_time, old_time))
            found = fi._candidate_files(
                source, done, err, 50, min_file_age_seconds=30, map_subfolders_to_category=True
            )
            self.assertEqual([(p.name, cat) for p, cat in found], [("contract.txt", "Legal"), ("root.txt", None)])

    def test_evaluation_refusal_markers_cover_english_spanish_french(self):
        if "unidecode" not in sys.modules:
            u = types.ModuleType("unidecode")
            u.unidecode = lambda s: s
            sys.modules["unidecode"] = u
        ev = importlib.import_module(
            "document_management.frappe_document_management.rag.evaluation"
        )
        for sample in (
            "Not enough evidence was found in the available documents.",
            "No encontré evidencia suficiente en los documentos disponibles.",
            "Les documents ne contiennent pas assez d'informations.",
        ):
            self.assertTrue(
                any(ev._contains(sample, m) for m in ev.REFUSAL_MARKERS),
                f"Refusal marker did not match: {sample}",
            )

    def test_translations_and_chat_encoding_clean(self):
        es = (REPO_ROOT / "document_management" / "locale" / "es.po").read_text(encoding="utf-8")
        fr = (REPO_ROOT / "document_management" / "locale" / "fr.po").read_text(encoding="utf-8")
        for key in (
            "Check System Health",
            "Advanced Search Syntax",
            "New conversation",
            "Version History",
            "Upload New Version",
            "Passcode Required",
        ):
            self.assertIn(f'msgid "{key}"', es)
            self.assertIn(f'msgid "{key}"', fr)

        chat_js = (
            REPO_ROOT
            / "document_management"
            / "frappe_document_management"
            / "page"
            / "document_chat"
            / "document_chat.js"
        ).read_text(encoding="utf-8")
        self.assertNotIn("conversaciÃ³n", chat_js)


class TestPhase7To9Improvements(TestCase):
    def test_share_link_hmac_cookie_unlock_and_no_passcode_in_urls(self):
        import frappe
        import time

        sharing = importlib.import_module(
            "document_management.frappe_document_management.utils.document_sharing"
        )
        token = "tok-secret-123"
        token_hash = sharing._token_hash(token)
        pass_hash = sharing._passcode_hash("MyPass99")

        cookie = sharing._sign_unlock_cookie(
            token_hash, pass_hash, expires_ts=int(time.time()) + 300
        )
        self.assertTrue(sharing._verify_unlock_cookie(cookie, token_hash, pass_hash))
        self.assertFalse(
            sharing._verify_unlock_cookie(cookie, sharing._token_hash("other"), pass_hash)
        )
        self.assertFalse(
            sharing._verify_unlock_cookie(
                cookie, token_hash, sharing._passcode_hash("OtherPass")
            )
        )
        self.assertFalse(
            sharing._verify_unlock_cookie(
                cookie, token_hash, pass_hash, now_ts=int(time.time()) + 600
            )
        )
        self.assertFalse(sharing._verify_unlock_cookie(cookie + "x", token_hash, pass_hash))

        # Download URL must never carry passcode in query string
        url = sharing._shared_download_url(token, download=1)
        self.assertNotIn("passcode=", url)

        # GET requests to unlock_shared_document must be rejected
        prev_request = getattr(frappe.local, "request", None)
        try:
            frappe.local.request = SimpleNamespace(method="GET", cookies={})
            with self.assertRaises(frappe.PermissionError):
                sharing.unlock_shared_document(token, "MyPass99")
        finally:
            frappe.local.request = prev_request

    def test_share_link_atomic_view_dedup_and_limit(self):
        import frappe

        sharing = importlib.import_module(
            "document_management.frappe_document_management.utils.document_sharing"
        )
        store = {}
        fake_cache = SimpleNamespace(
            get_value=lambda k: store.get(k),
            set_value=lambda k, v, expires_in_sec=60: store.__setitem__(k, v),
        )
        frappe.cache = lambda: fake_cache
        frappe.local.request_ip = "198.51.100.5"
        token_hash = sharing._token_hash("tok-views-1")
        link = SimpleNamespace(name="SL-1", max_views=1, access_count=0, get=lambda k, d=None: {"max_views": 1, "access_count": 0}.get(k, d))

        sql_calls = []

        def fake_sql(query, params=None):
            sql_calls.append(query.strip())
            if "SELECT ROW_COUNT()" in query:
                return [(1,)]
            return None

        with patch.object(frappe.db, "sql", side_effect=fake_sql, create=True):
            # First request consumes 1 view
            self.assertTrue(sharing._consume_share_view(link, token_hash=token_hash))
            self.assertEqual(len(sql_calls), 2)
            # Immediate second sub-request from same IP is deduplicated
            self.assertTrue(sharing._consume_share_view(link, token_hash=token_hash))
            self.assertEqual(len(sql_calls), 2)

        # Different IP when ROW_COUNT() == 0 must raise PermissionError
        frappe.local.request_ip = "198.51.100.99"
        with patch.object(
            frappe.db,
            "sql",
            side_effect=lambda q, p=None: [(0,)] if "ROW_COUNT" in q else None,
            create=True,
        ):
            with self.assertRaises(frappe.PermissionError):
                sharing._consume_share_view(link, token_hash=token_hash)

    def test_document_has_permission_missing_category_fails_closed(self):
        import frappe

        doc_mod = importlib.import_module(
            "document_management.frappe_document_management.doctype.document.document"
        )
        doc = frappe._dict(
            name="DOC-ORPHAN-CAT",
            owner="alice@example.com",
            is_deleted=0,
            only_me=0,
            category="Deleted Category",
            roles_with_access=[],
            departments_with_access=[],
        )
        with (
            patch.object(frappe, "get_roles", return_value=["All"], create=True),
            patch.object(frappe.db, "exists", return_value=False, create=True),
            patch.object(doc_mod, "get_user_departments", return_value=set()),
            patch.object(
                frappe,
                "get_cached_doc",
                side_effect=frappe.DoesNotExistError("missing"),
                create=True,
            ),
        ):
            self.assertFalse(doc_mod.has_permission(doc, "read", user="bob@example.com"))

    def test_storage_prune_empty_parent_dirs(self):
        storage = importlib.import_module(
            "document_management.frappe_document_management.utils.storage_paths"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "private" / "files"
            nested = root / "Legal" / "2026" / "05"
            sibling = root / "Legal" / "2025"
            nested.mkdir(parents=True)
            sibling.mkdir(parents=True)
            keep_file = sibling / "keep.pdf"
            keep_file.write_bytes(b"%PDF-1.4")
            moved_from = nested / "gone.pdf"

            removed = storage.prune_empty_parent_dirs(moved_from, root=root)
            self.assertEqual(len(removed), 2)  # 05 and 2026 removed; Legal has 2025
            self.assertFalse(nested.exists())
            self.assertFalse((root / "Legal" / "2026").exists())
            self.assertTrue((root / "Legal").is_dir())
            self.assertTrue(keep_file.is_file())
            self.assertTrue(root.is_dir())

    def test_search_query_quotes_operators_and_skips_single_term_relaxed_scan(self):
        query_mod = importlib.import_module("document_management.search.query")
        tantivy_mod = importlib.import_module("document_management.search.tantivy_backend")

        class FakeQuery:
            @staticmethod
            def empty_query():
                return ("empty",)

            @staticmethod
            def boost_query(q, w):
                return ("boost", q, w)

            @staticmethod
            def boolean_query(clauses):
                return ("boolean", clauses)

        fake_tantivy = SimpleNamespace(
            Query=FakeQuery,
            Occur=SimpleNamespace(Must="must", Should="should"),
        )
        parsed_inputs = []

        class FakeIndex:
            def parse_query(self, q, fields):
                parsed_inputs.append(q)
                return ("parsed", q, tuple(fields))

        with patch.dict(sys.modules, {"tantivy": fake_tantivy}):
            # Reserved operator word is quoted via JSON string
            query_mod.build_natural_query(FakeIndex(), "OR", {"title": 1.0}, language="es")
            self.assertIn('"or"', parsed_inputs)

            # Pure-stopword query falls back to raw tokens instead of empty_query
            res = query_mod.build_natural_query(FakeIndex(), "de la", {"title": 1.0}, language="es")
            self.assertNotEqual(res, ("empty",))

        # Single-term iter_search runs strict_query only (no redundant relaxed_query)
        search_calls = []

        class FakeSearcher:
            def search(self, query, limit, count=True, offset=0):
                search_calls.append(query)
                return SimpleNamespace(hits=[], count=0)

        fake_idx = SimpleNamespace(reload=lambda: None, searcher=lambda: FakeSearcher())
        with (
            patch.object(tantivy_mod, "get_index", return_value=fake_idx),
            patch.object(
                tantivy_mod,
                "build_natural_query",
                side_effect=lambda index, q, fields, require_all=True: (
                    "strict" if require_all else "relaxed"
                ),
            ),
        ):
            list(tantivy_mod.iter_search("contrato"))
        self.assertEqual(search_calls, ["strict"])

    def test_rag_sqlite_readonly_and_batched_iterator(self):
        import sqlite3

        rag_index = importlib.import_module(
            "document_management.frappe_document_management.rag.index"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [
                {
                    "name": f"chunk-{i}",
                    "document": f"DOC-{i}",
                    "document_version": "VER-1",
                    "version_number": "1",
                    "page_number": 1,
                    "chunk_index": i,
                    "content": f"Sample content {i}",
                    "content_hash": f"h{i}",
                    "embedding_model": "m",
                    "vector_id": str(1000 + i),
                }
                for i in range(5)
            ]
            rag_index._write_chunk_metadata(rows, root)
            db_path = rag_index._chunks_db_path(root)

            # Read-only connection rejects writes
            conn = rag_index._connect_sqlite_ro(db_path)
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    conn.execute("DELETE FROM chunks")
            finally:
                conn.close()

            batches = list(rag_index._iter_sqlite_chunks_batched(db_path, batch_size=2))
            self.assertEqual([len(b) for b in batches], [2, 2, 1])

    def test_expiry_reminders_configurable_and_overdue_support(self):
        import frappe
        import frappe.utils as fu
        from datetime import date, timedelta

        if not hasattr(fu, "today"):
            fu.today = lambda: date.today().isoformat()
        if not hasattr(fu, "add_days"):
            fu.add_days = lambda d, n: d + timedelta(days=n)
        if not hasattr(fu, "add_to_date"):
            fu.add_to_date = lambda d, **kw: d

        lifecycle = importlib.import_module(
            "document_management.frappe_document_management.utils.archive_lifecycle"
        )

        # Disabled when reminder window is 0
        with patch.object(lifecycle, "_expiry_reminder_settings", return_value=(0, False)):
            self.assertEqual(lifecycle.notify_expiring_documents(), [])

        # Overdue + upcoming + optional email
        inserted_logs = []
        sent_emails = []
        yesterday = (date.today() - timedelta(days=2)).isoformat()
        tomorrow = (date.today() + timedelta(days=5)).isoformat()

        def fake_get_all(doctype, filters=None, fields=None, **kw):
            if "expiry_date" in (filters or {}):
                return [{"name": "DOC-EXP", "title": "Old Policy", "owner": "alice@example.com", "expiry_date": yesterday}]
            if "review_date" in (filters or {}):
                return [{"name": "DOC-REV", "title": "Review SOP", "owner": "bob@example.com", "review_date": tomorrow}]
            return []

        with (
            patch.object(lifecycle, "_expiry_reminder_settings", return_value=(30, True)),
            patch.object(frappe, "get_all", side_effect=fake_get_all, create=True),
            patch.object(frappe.db, "exists", return_value=False, create=True),
            patch.object(frappe.db, "get_value", side_effect=lambda dt, n, f: n, create=True),
            patch.object(frappe.db, "commit", create=True),
            patch.object(
                frappe,
                "get_doc",
                side_effect=lambda d: SimpleNamespace(insert=lambda **kw: inserted_logs.append(d)),
                create=True,
            ),
            patch.object(
                frappe,
                "sendmail",
                side_effect=lambda **kw: sent_emails.append(kw),
                create=True,
            ),
        ):
            notified = lifecycle.notify_expiring_documents()

        self.assertEqual(notified, ["DOC-EXP", "DOC-REV"])
        self.assertIn("expired", inserted_logs[0]["subject"])
        self.assertIn("due for review", inserted_logs[1]["subject"])
        self.assertEqual(len(sent_emails), 2)

    def test_shared_pdf_css_workspace_link_count_and_orphan_cleanup(self):
        import json

        css_path = REPO_ROOT / "document_management" / "public" / "css" / "pdf_search_viewer.css"
        self.assertTrue(css_path.is_file())
        css_text = css_path.read_text(encoding="utf-8")
        self.assertIn(".loan-pdf-page-input", css_text)

        hooks_text = (REPO_ROOT / "document_management" / "hooks.py").read_text(encoding="utf-8")
        self.assertIn("/assets/document_management/css/pdf_search_viewer.css", hooks_text)

        share_html = (REPO_ROOT / "document_management" / "www" / "document_share.html").read_text(encoding="utf-8")
        self.assertIn("/assets/document_management/css/pdf_search_viewer.css", share_html)
        self.assertIn(".fa-rotate-right::before", share_html)
        self.assertIn('method="post"', share_html)
        self.assertNotIn('method="get"', share_html)

        ws = json.loads(
            (
                REPO_ROOT
                / "document_management"
                / "frappe_document_management"
                / "workspace"
                / "document_management"
                / "document_management.json"
            ).read_text(encoding="utf-8")
        )
        card = next(link for link in ws["links"] if link.get("type") == "Card Break")
        self.assertEqual(card["link_count"], 7)

        doc_json = json.loads(
            (
                REPO_ROOT
                / "document_management"
                / "frappe_document_management"
                / "doctype"
                / "document"
                / "document.json"
            ).read_text(encoding="utf-8")
        )
        fieldnames = {f["fieldname"] for f in doc_json["fields"]}
        self.assertNotIn("attachment", fieldnames)
        self.assertNotIn("preview_attachment", fieldnames)

        orphan_dir = (
            REPO_ROOT
            / "document_management"
            / "frappe_document_management"
            / "doctype"
            / "document_tag_role"
        )
        self.assertFalse(orphan_dir.exists())


class TestPhase10To12Improvements(TestCase):
    def test_archive_portability_dates_and_zip_bomb_protection(self):
        import frappe
        import zipfile

        portability = importlib.import_module(
            "document_management.frappe_document_management.utils.archive_portability"
        )
        self.assertIn("expiry_date", portability.PARENT_FIELDS)
        self.assertIn("review_date", portability.PARENT_FIELDS)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            private_dir = tmp_path / "private" / "files"
            private_dir.mkdir(parents=True)
            zip_path = tmp_path / "bomb.zip"
            payload = b"A" * 2048
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("documents/DOC-1/1/original/test.txt", payload)

            descriptor = {
                "path": "documents/DOC-1/1/original/test.txt",
                "filename": "test.txt",
                "size": len(payload),
                "checksum": hashlib.sha256(payload).hexdigest(),
            }
            with (
                zipfile.ZipFile(zip_path) as zf,
                patch.object(
                    frappe,
                    "get_site_path",
                    side_effect=lambda *parts: str(tmp_path.joinpath(*parts)),
                ),
            ):
                # Enforce remaining_budget limit before extracting
                with self.assertRaises(frappe.ValidationError):
                    portability._extract_blob(
                        zf, descriptor, "VER-1", "attachment", remaining_budget=512
                    )
                # Ensure no leftover file was written
                self.assertEqual(list(private_dir.iterdir()), [])

                # Simulate forged info.file_size / streaming overshoot during read
                import io

                small_descriptor = {**descriptor, "size": 100}

                class ForgedZip:
                    def getinfo(self, name):
                        return SimpleNamespace(filename=name, file_size=100)

                    def open(self, info):
                        return io.BytesIO(payload)

                with self.assertRaises(frappe.ValidationError):
                    portability._extract_blob(
                        ForgedZip(), small_descriptor, "VER-1", "attachment"
                    )
                self.assertEqual(list(private_dir.iterdir()), [])

    def test_archive_sanity_decryption_failure_unencrypted_and_orphans(self):
        import frappe

        sanity = importlib.import_module(
            "document_management.frappe_document_management.utils.archive_sanity"
        )
        with tempfile.TemporaryDirectory() as tmp:
            plain = Path(tmp) / "plain.pdf"
            plain.write_bytes(b"%PDF-1.4 sample")

            # Unencrypted warning when encryption expected
            issues = []
            with patch.object(sanity, "get_file_path", return_value=str(plain)):
                sanity._check_stored_file(
                    issues,
                    file_url="/private/files/plain.pdf",
                    expected_checksum=hashlib.sha256(b"%PDF-1.4 sample").hexdigest(),
                    document="DOC-1",
                    version="VER-1",
                    kind="original",
                    check_encryption=True,
                )
            self.assertEqual([i["code"] for i in issues], ["original_unencrypted"])

            # Decryption error recorded as issue without aborting audit
            issues2 = []
            with (
                patch.object(sanity, "get_file_path", return_value=str(plain)),
                patch.object(
                    sanity,
                    "_sha256_file",
                    side_effect=frappe.ValidationError("bad encryption key"),
                ),
            ):
                sanity._check_stored_file(
                    issues2,
                    file_url="/private/files/plain.pdf",
                    expected_checksum="abc",
                    document="DOC-1",
                    version="VER-1",
                    kind="original",
                )
            self.assertEqual([i["code"] for i in issues2], ["original_decryption_failed"])

        # Orphaned Document Page & Document Chunk detection
        orphan_issues = []

        def fake_get_all(doctype, **kw):
            if doctype == "Document Page":
                return [
                    frappe._dict(name="PG-1", document="DOC-MISSING", document_version="VER-X")
                ]
            if doctype == "Document Chunk":
                return [frappe._dict(name="CH-1", document="DOC-MISSING")]
            return []

        with (
            patch.object(frappe.db, "exists", return_value=True),
            patch.object(frappe, "get_all", side_effect=fake_get_all),
        ):
            sanity._check_orphaned_derived_records(orphan_issues, {"DOC-1"}, {"VER-1"})
        self.assertEqual(
            [i["code"] for i in orphan_issues],
            ["orphaned_document_page", "orphaned_document_chunk"],
        )

    def test_encrypt_existing_documents_retroactive_job(self):
        import frappe
        from cryptography.fernet import Fernet

        fc = importlib.import_module(
            "document_management.frappe_document_management.utils.file_crypto"
        )
        key = Fernet.generate_key().decode("utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            p1 = Path(tmp) / "plain.pdf"
            p2 = Path(tmp) / "already.pdf"
            p1.write_bytes(b"%PDF-1.4 plain content")
            p2.write_bytes(b"%PDF-1.4 already encrypted")

            with (
                patch.object(fc, "file_encryption_enabled", return_value=True),
                patch.object(fc, "get_encryption_key", return_value=key),
            ):
                fc.encrypt_path_at_rest(p2)
                rows = [
                    frappe._dict(name="F1", file_url=str(p1), is_private=1),
                    frappe._dict(name="F2", file_url=str(p2), is_private=1),
                ]
                with (
                    patch.object(frappe, "get_all", return_value=rows),
                    patch.object(fc, "get_file_path", side_effect=lambda u: u),
                ):
                    result = fc.encrypt_existing_documents()

                self.assertEqual(result["encrypted"], 1)
                self.assertEqual(result["skipped"], 1)
                self.assertEqual(result["failed"], 0)
                self.assertTrue(fc.is_encrypted_path(p1))
                self.assertEqual(fc.read_path_bytes(p1), b"%PDF-1.4 plain content")

    def test_document_access_per_request_cache(self):
        import frappe

        access = importlib.import_module(
            "document_management.frappe_document_management.utils.document_access"
        )
        prev_in_test = frappe.flags.in_test
        try:
            frappe.flags.in_test = False
            access.clear_access_cache()
            with (
                patch.object(frappe.db, "exists", return_value=True) as mock_exists,
                patch.object(frappe, "get_all", return_value=["Finance"]) as mock_get_all,
            ):
                self.assertTrue(access.department_access_enabled())
                self.assertTrue(access.department_access_enabled())
                self.assertEqual(mock_exists.call_count, 2)  # Department + Employee once each

                self.assertEqual(access.get_user_departments("alice@example.com"), {"Finance"})
                self.assertEqual(access.get_user_departments("alice@example.com"), {"Finance"})
                self.assertEqual(mock_get_all.call_count, 1)

                access.clear_access_cache()
                self.assertEqual(access.get_user_departments("alice@example.com"), {"Finance"})
                self.assertEqual(mock_get_all.call_count, 2)
        finally:
            access.clear_access_cache()
            frappe.flags.in_test = prev_in_test

    def test_settings_validation_and_folder_ingestion_health(self):
        import frappe

        settings_mod = importlib.import_module(
            "document_management.frappe_document_management.doctype."
            "document_management_settings.document_management_settings"
        )

        class FakeSettings(settings_mod.DocumentManagementSettings):
            def __init__(self, data):
                self._data = data
                for k, v in data.items():
                    setattr(self, k, v)

            def get(self, key, default=None):
                return self._data.get(key, default)

        base = {
            "indexed_doctypes": [],
            "trash_retention_days": 30,
            "export_retention_hours": 24,
            "expiry_reminder_days": 30,
            "ingestion_min_file_age_seconds": 0,
            "rag_chunk_size": 650,
            "rag_chunk_overlap": 120,
            "chat_rate_limit_per_minute": 10,
            "file_storage_path_template": "",
            "enable_folder_ingestion": 0,
            "ocr_processing_timeout_minutes": 60,
            "chat_provider": "OpenAI",
            "embedding_provider": "Local (sentence-transformers)",
        }
        FakeSettings(base).validate()

        with self.assertRaises(frappe.ValidationError):
            FakeSettings({**base, "expiry_reminder_days": -1}).validate()
        with self.assertRaises(frappe.ValidationError):
            FakeSettings({**base, "rag_chunk_overlap": 650}).validate()
        with self.assertRaises(frappe.ValidationError):
            FakeSettings({**base, "chat_rate_limit_per_minute": 0}).validate()

        with tempfile.TemporaryDirectory() as tmp:
            ok_health = settings_mod._check_folder_ingestion_health(
                frappe._dict(enable_folder_ingestion=1, ingestion_folder_path=tmp)
            )
            self.assertTrue(ok_health["ok"])
            bad_health = settings_mod._check_folder_ingestion_health(
                frappe._dict(
                    enable_folder_ingestion=1,
                    ingestion_folder_path=str(Path(tmp) / "missing_dir"),
                )
            )
            self.assertFalse(bad_health["ok"])

    def test_ocr_worker_stale_recovery_resilience_and_realtime_hooks(self):
        import frappe

        ocr = importlib.import_module(
            "document_management.frappe_document_management.utils.ocr_worker"
        )
        rows = [
            frappe._dict(
                name="VER-ORPHAN",
                parent="DOC-GONE",
                ocr_status="Processing",
                ocr_started_at=None,
                ocr_heartbeat_at=None,
            ),
            frappe._dict(
                name="VER-OK",
                parent="DOC-LIVE",
                ocr_status="Processing",
                ocr_started_at=None,
                ocr_heartbeat_at=None,
            ),
        ]
        live_doc = SimpleNamespace(enqueue_processing=MagicMock())
        with (
            patch.object(frappe, "get_all", return_value=rows),
            patch.object(frappe.db, "sql", create=True),
            patch.object(
                frappe.db,
                "get_value",
                side_effect=lambda dt, name, *a, **kw: next(r for r in rows if r.name == name),
            ),
            patch.object(
                frappe.db,
                "exists",
                side_effect=lambda dt, name: name == "DOC-LIVE",
            ),
            patch.object(frappe.db, "set_value"),
            patch.object(frappe.db, "commit"),
            patch.object(frappe, "get_doc", return_value=live_doc),
        ):
            recovered = ocr.recover_stale_ocr_jobs()
        self.assertEqual(recovered, ["DOC-LIVE"])
        live_doc.enqueue_processing.assert_called_once()

    def test_pdf_viewer_version_name_ci_and_packaging_metadata(self):
        src = (
            REPO_ROOT / "document_management" / "public" / "js" / "pdf_search_viewer.source.js"
        ).read_text(encoding="utf-8")
        bundle = (
            REPO_ROOT / "document_management" / "public" / "js" / "pdf_search_viewer.v5_7_284_10.js"
        ).read_text(encoding="utf-8")
        self.assertIn('version_name', src)
        self.assertIn('version_name', bundle)

        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('"cryptography"', pyproject)

        install_py = (REPO_ROOT / "document_management" / "install.py").read_text(encoding="utf-8")
        self.assertIn('"expiry_reminder_days"', install_py)

        ci_yml = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        for suite in ("test_ocr_worker.py", "test_providers.py", "test_archive_sanity.py", "test_indexer.py"):
            self.assertIn(suite, ci_yml)

        console_py = (
            REPO_ROOT
            / "document_management"
            / "frappe_document_management"
            / "page"
            / "document_management_console"
            / "document_management_console.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn('fields=["count(', console_py)
        self.assertIn('{"COUNT": "*", "as": "total"}', console_py)


class TestIndexedDocTypesSearchUpgrades(TestCase):
    def test_parse_search_syntax_phrases_exclusions_and_hyphenated_ids(self):
        query_mod = importlib.import_module("document_management.search.query")

        parsed = query_mod.parse_search_syntax(
            '"Juan Pérez" INV-2026-001 -cancelado -borrador'
        )
        self.assertEqual(parsed["phrases"], ["juan pérez"])
        self.assertEqual(parsed["excluded"], ["cancelado", "borrador"])
        self.assertIn("INV-2026-001", parsed["positive_text"])
        self.assertNotIn("cancelado", parsed["positive_text"])

    def test_build_natural_query_supports_phrases_exclusions_prefix_and_fuzzy(self):
        query_mod = importlib.import_module("document_management.search.query")

        class FakeQuery:
            @staticmethod
            def empty_query():
                return ("empty",)

            @staticmethod
            def boost_query(q, w):
                return ("boost", q, w)

            @staticmethod
            def boolean_query(clauses):
                return ("boolean", clauses)

            @staticmethod
            def regex_query(schema, field, pattern):
                return ("regex", field, pattern)

            @staticmethod
            def fuzzy_term_query(schema, field, term, distance=1, transposition_cost_one=True, prefix=False):
                return ("fuzzy", field, term, distance)

        fake_tantivy = SimpleNamespace(
            Query=FakeQuery,
            Occur=SimpleNamespace(Must="must", Should="should", MustNot="must_not"),
        )

        class FakeIndex:
            schema = "fake-schema"

            def parse_query(self, q, fields):
                return ("parsed", q, tuple(fields))

        with patch.dict(sys.modules, {"tantivy": fake_tantivy}):
            q = query_mod.build_natural_query(
                FakeIndex(),
                '"Juan Pérez" contrato -anulado',
                {"doc_name": 6.0, "title": 4.0, "content": 1.0},
                language="es",
                require_all=True,
                allow_prefix=True,
            )
            rep = repr(q)
            self.assertIn("must_not", rep)
            self.assertIn('"anulado"', rep)
            self.assertIn('"juan pérez"', rep)
            self.assertIn("'regex', 'doc_name'", rep)

            fq = query_mod.build_natural_query(
                FakeIndex(),
                "fatura",
                {"title": 4.0, "content": 1.0},
                language="es",
                require_all=False,
                allow_prefix=False,
                fuzzy=True,
            )
            self.assertIn("'fuzzy', 'title', 'fatura', 1", repr(fq))

    def test_tantivy_backend_quality_rerank_cache_and_stats(self):
        tb = importlib.import_module("document_management.search.tantivy_backend")

        ranked = tb._rank_batch(
            [
                {"doc_name": "TASK-LONG", "quality": 30, "coverage": 1.0, "score": 25.0},
                {"doc_name": "TASK-EXACT", "quality": 90, "coverage": 1.0, "score": 2.0},
            ]
        )
        self.assertEqual([r["doc_name"] for r in ranked], ["TASK-EXACT", "TASK-LONG"])

        # Handle cache reuses opened Index for same active path
        open_calls = []

        class FakeTantivy:
            class Index:
                @staticmethod
                def open(path):
                    open_calls.append(path)
                    return SimpleNamespace(
                        path=path,
                        reload=lambda: None,
                        searcher=lambda: SimpleNamespace(
                            num_docs=12,
                            search=lambda q, limit, count=True: SimpleNamespace(count=5),
                        ),
                        parse_query=lambda q, fields: (q, fields),
                    )

        tb.clear_index_cache()
        try:
            with (
                patch.dict(sys.modules, {"tantivy": FakeTantivy}),
                patch.object(tb, "get_tantivy_index_path", return_value="/tmp/gen-1/tantivy"),
                patch.object(tb, "_read_pointer", return_value={"generation": "gen-1", "rebuilt_at": "2026-11-26 07:00:00"}),
                patch.object(tb, "_active_generation_name", return_value="gen-1"),
                patch.object(tb, "index_exists", return_value=True),
            ):
                idx1 = tb.get_index()
                idx2 = tb.get_index()
                self.assertIs(idx1, idx2)
                self.assertEqual(len(open_calls), 1)

                stats = tb.get_index_stats(["Customer", "Task"])
                self.assertTrue(stats["ready"])
                self.assertEqual(stats["num_docs"], 12)
                self.assertEqual(stats["by_doctype"], {"Customer": 5, "Task": 5})
                self.assertEqual(stats["rebuilt_at"], "2026-11-26 07:00:00")
        finally:
            tb.clear_index_cache()

    def test_indexer_excluded_fields_attachments_dedup_dates_and_sorting(self):
        import frappe

        idx_mod = importlib.import_module("document_management.search.indexer")
        fields = [
            SimpleNamespace(fieldname="customer", fieldtype="Data", label="Customer", hidden=0, permlevel=0),
            SimpleNamespace(fieldname="internal_note", fieldtype="Data", label="Internal Note", hidden=0, permlevel=0),
        ]
        doc = frappe._dict(customer="Acme", internal_note="Do not index")
        doc.meta = SimpleNamespace(fields=fields)

        content = idx_mod.get_document_content(doc, excluded_fields="internal_note")
        self.assertIn("Customer: Acme", content)
        self.assertNotIn("Do not index", content)

        # Attachment reference detection
        pdf_file = frappe._dict(
            doctype="File",
            is_folder=0,
            file_url="/private/files/invoice.pdf",
            attached_to_doctype="Sales Invoice",
            attached_to_name="SINV-001",
        )
        self.assertEqual(idx_mod._attachment_reference(pdf_file), ("Sales Invoice", "SINV-001"))
        img_file = frappe._dict(**{**pdf_file, "file_url": "/private/files/logo.png"})
        self.assertIsNone(idx_mod._attachment_reference(img_file))

        # Enqueue deduplication
        with patch.object(frappe, "enqueue") as mock_enq:
            idx_mod._enqueue_indexing("Task", "TASK-9")
        _, kwargs = mock_enq.call_args
        self.assertEqual(kwargs["job_id"], "dm_idx:Task:TASK-9")
        self.assertTrue(kwargs["deduplicate"])

        # Date filtering & sorting
        self.assertTrue(idx_mod._matches_date_range("2026-06-15 10:00:00", "2026-06-01", "2026-06-30"))
        self.assertFalse(idx_mod._matches_date_range("2026-05-31 23:59:59", "2026-06-01", "2026-06-30"))
        records = [
            {"title": "Beta", "doc_name": "2", "modified": "2026-01-01 00:00:00"},
            {"title": "Alpha", "doc_name": "1", "modified": "2026-06-01 00:00:00"},
        ]
        self.assertEqual([r["title"] for r in idx_mod._sort_records(records, "modified_desc")], ["Alpha", "Beta"])
        self.assertEqual([r["title"] for r in idx_mod._sort_records(records, "modified_asc")], ["Beta", "Alpha"])
        self.assertEqual([r["title"] for r in idx_mod._sort_records(records, "title_asc")], ["Alpha", "Beta"])

    def test_indexed_search_ui_i18n_and_doctype_schema(self):
        import json

        dt = json.loads(
            (
                REPO_ROOT
                / "document_management"
                / "frappe_document_management"
                / "doctype"
                / "indexed_doctype"
                / "indexed_doctype.json"
            ).read_text(encoding="utf-8")
        )
        names = {f["fieldname"] for f in dt["fields"]}
        self.assertTrue({"document_type", "index_comments", "index_attachments", "excluded_fields"}.issubset(names))

        html = (
            REPO_ROOT
            / "document_management"
            / "frappe_document_management"
            / "page"
            / "indexed_doctypes_search"
            / "indexed_doctypes_search.html"
        ).read_text(encoding="utf-8")
        self.assertIn('{{ __("Indexed DocTypes Search") }}', html)
        self.assertIn('id="indexed-doctypes-search-sort"', html)
        self.assertIn('id="indexed-doctypes-recent-searches"', html)

        js = (
            REPO_ROOT
            / "document_management"
            / "frappe_document_management"
            / "page"
            / "indexed_doctypes_search"
            / "indexed_doctypes_search.js"
        ).read_text(encoding="utf-8")
        self.assertIn("allowedOptionValues", js)
        self.assertIn("get_index_status", js)
        self.assertIn("Rebuild Index Now", js)
        self.assertIn("RECENT_SEARCHES_KEY", js)


class TestIndexedDocTypesSearchPhaseDF(TestCase):
    def test_exclusions_do_not_penalize_match_details_excerpt_or_trigger_relaxed_scan(self):
        query_mod = importlib.import_module("document_management.search.query")
        tb = importlib.import_module("document_management.search.tantivy_backend")

        details = query_mod.search_match_details(
            "factura -cancelado",
            doc_name="SINV-001",
            title="Factura mensual",
            content="Pago aprobado sin problemas",
            language="es",
        )
        self.assertEqual(details["total_terms"], 1)
        self.assertEqual(details["matched_terms"], 1)
        self.assertEqual(details["coverage"], 1.0)
        self.assertEqual(details["match_type"], "all_terms")

        # Excerpt centers on positive term 'factura', not excluded 'cancelado'
        long_text = ("cancelado " * 60) + "factura importante aqui" + (" relleno" * 60)
        excerpt = query_mod.make_excerpt(long_text, "factura -cancelado", maximum=100, language="es")
        self.assertIn("factura importante", excerpt)

        # iter_search sees only 1 positive term for 'factura -cancelado' -> skips relaxed scan
        search_calls = []

        class FakeSearcher:
            def search(self, query, limit, count=True, offset=0):
                search_calls.append(query)
                return SimpleNamespace(hits=[], count=0)

        fake_idx = SimpleNamespace(reload=lambda: None, searcher=lambda: FakeSearcher())
        with (
            patch.object(tb, "get_index", return_value=fake_idx),
            patch.object(
                tb,
                "build_natural_query",
                side_effect=lambda index, q, fields, require_all=True: (
                    "strict" if require_all else "relaxed"
                ),
            ),
        ):
            list(tb.iter_search("factura -cancelado"))
        self.assertEqual(search_calls, ["strict"])

    def test_handle_doc_rename_removes_old_and_reindexes_new(self):
        import frappe

        idx_mod = importlib.import_module("document_management.search.indexer")
        hooks_text = (REPO_ROOT / "document_management" / "hooks.py").read_text(encoding="utf-8")
        self.assertIn('"after_rename": "document_management.search.indexer.handle_doc_rename"', hooks_text)

        doc = frappe._dict(doctype="Customer", name="CUS-NEW")
        queued = []
        with (
            patch.object(
                idx_mod,
                "_get_indexing_config",
                return_value=(True, False, {"Customer"}, {"Customer": {}}),
            ),
            patch.object(
                frappe,
                "enqueue",
                side_effect=lambda method, **kw: queued.append((method, kw)),
            ),
        ):
            idx_mod.handle_doc_rename(doc, old_name="CUS-OLD", new_name="CUS-NEW")

        self.assertEqual(len(queued), 2)
        self.assertTrue(queued[0][0].endswith("remove_from_index_background"))
        self.assertEqual(queued[0][1]["doc_name"], "CUS-OLD")
        self.assertTrue(queued[1][0].endswith("run_indexing_background"))
        self.assertEqual(queued[1][1]["doc_name"], "CUS-NEW")

    def test_attachment_extraction_supports_txt_md_and_respects_char_limit(self):
        import frappe

        idx_mod = importlib.import_module("document_management.search.indexer")
        with tempfile.TemporaryDirectory() as tmp:
            txt_path = Path(tmp) / "notes.txt"
            txt_path.write_text("X" * 200, encoding="utf-8")
            with (
                patch.object(
                    frappe,
                    "get_all",
                    return_value=[frappe._dict(file_url=str(txt_path), name="F1")],
                ),
                patch.object(
                    sys.modules["frappe.utils.file_manager"],
                    "get_file_path",
                    side_effect=lambda u: u,
                ),
                patch.object(idx_mod, "MAX_ATTACHMENT_CHARS", 50),
            ):
                extracted = idx_mod._extract_pdf_attachments_text("Task", "TASK-1")
            self.assertEqual(len(extracted), 50)
            self.assertEqual(extracted, "X" * 50)

    def test_settings_validates_excluded_fields_against_doctype_meta(self):
        import frappe

        settings_mod = importlib.import_module(
            "document_management.frappe_document_management.doctype."
            "document_management_settings.document_management_settings"
        )

        class FakeSettings(settings_mod.DocumentManagementSettings):
            def __init__(self, rows):
                self._rows = rows

            def get(self, key, default=None):
                return self._rows if key == "indexed_doctypes" else default

        fake_meta = SimpleNamespace(
            istable=0,
            issingle=0,
            fields=[
                SimpleNamespace(fieldname="customer_name", fieldtype="Data"),
                SimpleNamespace(fieldname="notes", fieldtype="Text"),
            ],
        )
        frappe.bold = lambda s: str(s)
        with patch.object(frappe, "get_meta", return_value=fake_meta, create=True):
            # Valid excluded field passes
            FakeSettings(
                [SimpleNamespace(document_type="Customer", excluded_fields="notes")]
            )._validate_indexed_doctypes()

            # Unknown excluded field raises ValidationError
            with self.assertRaises(frappe.ValidationError):
                FakeSettings(
                    [SimpleNamespace(document_type="Customer", excluded_fields="nonexistent_field")]
                )._validate_indexed_doctypes()

    def test_indexed_search_frontend_keyboard_nav_docname_highlight_and_help(self):
        js = (
            REPO_ROOT
            / "document_management"
            / "frappe_document_management"
            / "page"
            / "indexed_doctypes_search"
            / "indexed_doctypes_search.js"
        ).read_text(encoding="utf-8")
        self.assertIn("$input.trigger('blur')", js)
        self.assertIn("this.highlightText(row.doc_name)", js)
        self.assertIn("response.fuzzy_fallback", js)
        self.assertIn("lastAllFacets", js)
        self.assertIn("showSyntaxHelp", js)

        # Guard Desk page microtemplates against single quotes or escaped quotes inside {{ ... }}
        import re

        pages_root = (
            REPO_ROOT / "document_management" / "frappe_document_management" / "page"
        )
        for html_file in pages_root.rglob("*.html"):
            content = html_file.read_text(encoding="utf-8")
            for expr in re.findall(r"\{\{(.*?)\}\}", content, flags=re.DOTALL):
                self.assertNotIn(
                    "'",
                    expr,
                    f"Single quote inside microtemplate expression in {html_file}: {expr}",
                )
                self.assertNotIn(
                    '\\"',
                    expr,
                    f"Escaped double quote inside microtemplate expression in {html_file}: {expr}",
                )

    def test_indexed_search_performance_optimizations_and_lazy_hooks(self):
        import frappe

        query_mod = importlib.import_module("document_management.search.query")
        tb = importlib.import_module("document_management.search.tantivy_backend")
        idx_mod = importlib.import_module("document_management.search.indexer")

        # 1. Fast fold_text matches NFKD for ASCII + accented Spanish/French
        self.assertEqual(query_mod.fold_text("INV-2026-001"), "inv-2026-001")
        self.assertEqual(query_mod.fold_text("Período de Nómina — Crédits"), "periodo de nomina — credits")

        # 2. build_natural_query avoids redundant raw+normalized clauses and limits FST regex scans
        regex_calls = []

        class FakeQuery:
            @staticmethod
            def empty_query():
                return ("empty",)

            @staticmethod
            def boost_query(q, w):
                return ("boost", q, w)

            @staticmethod
            def boolean_query(clauses):
                return ("boolean", clauses)

            @staticmethod
            def regex_query(schema, field, pattern):
                regex_calls.append((field, pattern))
                return ("regex", field, pattern)

            @staticmethod
            def fuzzy_term_query(schema, field, term, distance=1, transposition_cost_one=True, prefix=False):
                return ("fuzzy", field, term, distance)

        fake_tantivy = SimpleNamespace(
            Query=FakeQuery,
            Occur=SimpleNamespace(Must="must", Should="should", MustNot="must_not"),
        )

        class FakeIndex:
            schema = "fake-schema"

            def parse_query(self, q, fields):
                return ("parsed", q, tuple(fields))

        with patch.dict(sys.modules, {"tantivy": fake_tantivy}):
            q = query_mod.build_natural_query(
                FakeIndex(),
                "factura cliente",
                tb.SEARCH_FIELDS,
                language="es",
                require_all=True,
                allow_prefix=True,
            )
            rep = repr(q)
            # Non-accented query uses normalized fields only (no redundant raw field queries)
            self.assertIn("content_normalized", rep)
            self.assertNotIn("('content',)", rep)
            # Multi-term prefix regex runs only on doc_name_normalized (2 calls, not 8)
            self.assertEqual(len(regex_calls), 2)
            self.assertTrue(all(f == "doc_name_normalized" for f, _ in regex_calls))

        # 3. iter_search disables count=True (enables Block-Max WAND) and supports deferred excerpts
        seen_count_flags = []

        class FakeSearcher:
            def search(self, query, limit, count=True, offset=0):
                seen_count_flags.append((count, limit))
                return SimpleNamespace(hits=[(5.0, "d1")], count=None)

            def doc(self, address):
                return {
                    "doc_type": ["Customer"],
                    "doc_name": ["CUS-001"],
                    "title": ["Acme"],
                    "content": ["Cliente Acme factura mensual importante " * 20],
                }

        fake_idx = SimpleNamespace(reload=lambda: None, searcher=lambda: FakeSearcher())
        with (
            patch.object(tb, "get_index", return_value=fake_idx),
            patch.object(tb, "build_natural_query", return_value="q"),
        ):
            batches = list(tb.iter_search("factura", batch_size=10, include_excerpt=False))
        self.assertEqual(seen_count_flags, [(False, 11)])
        self.assertEqual(batches[0][0]["excerpt"], "")
        self.assertIn("_raw_content", batches[0][0])

        # 4. indexer.search skips custom has_permission get_doc for Administrator and materializes excerpts lazily
        get_doc_calls = []

        def fake_iter_search(query_str, doctypes=None, batch_size=50, include_excerpt=True):
            yield [
                {
                    "doc_type": "Customer",
                    "doc_name": f"CUS-{i:03d}",
                    "title": f"Customer {i}",
                    "excerpt": "",
                    "_raw_content": ("inicio " * 40) + "factura clave aqui" + (" fin" * 40),
                    "score": 10.0 - i,
                    "quality": 30,
                    "match_type": "all_terms",
                    "matched_terms": 1,
                    "total_terms": 1,
                    "coverage": 1.0,
                }
                for i in range(10)
            ]

        with (
            patch.object(
                idx_mod,
                "_get_indexing_config",
                return_value=(True, False, {"Customer"}, {"Customer": {}}),
            ),
            patch.object(tb, "iter_search", side_effect=fake_iter_search),
            patch.object(
                frappe,
                "get_hooks",
                return_value={"Customer": ["custom.perm"]},
                create=True,
            ),
            patch.object(
                frappe,
                "get_list",
                return_value=[
                    frappe._dict(name=f"CUS-{i:03d}", modified="2026-10-01 00:00:00")
                    for i in range(10)
                ],
                create=True,
            ),
            patch.object(
                frappe,
                "get_doc",
                side_effect=lambda *a, **kw: get_doc_calls.append(a),
            ),
            patch.object(
                frappe,
                "has_permission",
                return_value=True,
                create=True,
            ),
        ):
            frappe.session.user = "Administrator"
            res = idx_mod.search("factura", page=1, page_length=3)

            # Non-Administrator stops calling custom has_permission get_doc after page_length + 1 (4) hits, not all 10
            frappe.session.user = "user@example.com"
            res_user = idx_mod.search("factura", page=1, page_length=3)
            frappe.session.user = "Administrator"

        self.assertEqual(len(res["exact"]), 3)
        self.assertEqual(len(res_user["exact"]), 3)
        self.assertEqual(len(get_doc_calls), 4)
        self.assertIn("elapsed_ms", res)
        self.assertIn("factura clave", res["exact"][0]["excerpt"])
        self.assertNotIn("_raw_content", res["exact"][0])

    def test_get_search_result_preview_doctype_vs_child_table_and_permissions(self):
        import frappe

        idx_mod = importlib.import_module("document_management.search.indexer")

        class FakeDoc(dict):
            def __init__(self, doctype, name, fields, **values):
                super().__init__(values)
                self.doctype = doctype
                self.name = name
                self.modified = values.get("modified", "2026-11-20 10:00:00")
                self.owner = values.get("owner", "admin@example.com")
                self.meta = SimpleNamespace(
                    fields=fields,
                    title_field=values.get("_title_field"),
                    istable=values.get("_istable", False),
                    get_field=lambda fname: next((f for f in fields if f.fieldname == fname), None),
                )
                self.checked_perm = []

            def check_permission(self, ptype="read"):
                self.checked_perm.append(ptype)

        child_fields = [
            SimpleNamespace(fieldname="item_code", fieldtype="Data", label="Item Code", hidden=0, permlevel=0),
            SimpleNamespace(fieldname="description", fieldtype="Text", label="Description", hidden=0, permlevel=0),
            SimpleNamespace(fieldname="secret_cost", fieldtype="Currency", label="Secret Cost", hidden=0, permlevel=1),
        ]
        child_row_1 = FakeDoc(
            "Sales Invoice Item",
            "ROW-001",
            child_fields,
            item_code="SERV-01",
            description="Soporte mensual",
            secret_cost=999,
        )
        child_row_1.idx = 1
        child_row_2 = FakeDoc(
            "Sales Invoice Item",
            "ROW-002",
            child_fields,
            item_code="DIG-900",
            description="Digitalización especial de expedientes",
            secret_cost=500,
        )
        child_row_2.idx = 2

        parent_fields = [
            SimpleNamespace(fieldname="customer_name", fieldtype="Data", label="Customer", hidden=0, permlevel=0),
            SimpleNamespace(fieldname="api_password", fieldtype="Password", label="Password", hidden=0, permlevel=0),
            SimpleNamespace(fieldname="internal_note", fieldtype="Data", label="Internal", hidden=0, permlevel=0),
            SimpleNamespace(fieldname="items", fieldtype="Table", label="Items", options="Sales Invoice Item", hidden=0, permlevel=0),
        ]
        parent_doc = FakeDoc(
            "Sales Invoice",
            "SINV-2026-001",
            parent_fields,
            customer_name="Acme Corp",
            api_password="top-secret-password",
            internal_note="Excluded text",
            items=[child_row_1, child_row_2],
            _title_field="customer_name",
        )

        rules = {
            "Sales Invoice": {
                "index_comments": True,
                "index_attachments": False,
                "excluded_fields": ["internal_note"],
            }
        }
        with (
            patch.object(
                idx_mod,
                "_get_indexing_config",
                return_value=(True, False, {"Sales Invoice"}, rules),
            ),
            patch.object(frappe, "get_doc", return_value=parent_doc),
            patch.object(frappe, "get_all", return_value=[]),
        ):
            # Query matching child table row -> match_source = "child_table", focuses row #2
            child_preview = idx_mod.get_search_result_preview(
                "Sales Invoice",
                "SINV-2026-001",
                query="digitalizacion expedientes",
            )
            self.assertEqual(child_preview["match_source"], "child_table")
            self.assertEqual(len(child_preview["matched_child_rows"]), 1)
            row = child_preview["matched_child_rows"][0]
            self.assertEqual(row["table_fieldname"], "items")
            self.assertEqual(row["row_name"], "ROW-002")
            self.assertEqual(row["idx"], 2)
            row_field_names = {f["fieldname"] for f in row["fields"]}
            self.assertIn("description", row_field_names)
            self.assertNotIn("secret_cost", row_field_names)  # permlevel=1 excluded

            # Query matching parent header -> match_source = "doctype"
            parent_preview = idx_mod.get_search_result_preview(
                "Sales Invoice",
                "SINV-2026-001",
                query="Acme",
            )
            self.assertEqual(parent_preview["match_source"], "doctype")
            parent_field_names = {f["fieldname"] for f in parent_preview["fields"]}
            self.assertIn("customer_name", parent_field_names)
            self.assertNotIn("api_password", parent_field_names)  # Password excluded
            self.assertNotIn("internal_note", parent_field_names)  # excluded_fields honored
            self.assertEqual(parent_doc.checked_perm, ["read", "read"])

            # Unconfigured DocType rejected with PermissionError
            with self.assertRaises(frappe.PermissionError):
                idx_mod.get_search_result_preview("User", "Administrator", query="admin")

        # Verify modal + child row direct open hooks exist in JS
        js = (
            REPO_ROOT
            / "document_management"
            / "frappe_document_management"
            / "page"
            / "indexed_doctypes_search"
            / "indexed_doctypes_search.js"
        ).read_text(encoding="utf-8")
        self.assertIn("get_search_result_preview", js)
        self.assertIn("openResultModal", js)
        self.assertIn("focus_child_row_on_form", js)
        self.assertIn("Open Row in Record", js)
        self.assertNotIn('<a class="search-result"', js)
        self.assertIn("event.stopPropagation()", js)
        self.assertIn("dm-search-preview-dialog", js)
        self.assertIn("size: 'extra-large'", js)
