"""
The ChromaDB client must be created with telemetry switched off.

ChromaDB's default is anonymized_telemetry=True. EVA tells users nothing leaves their
machine, so the client opts out explicitly instead of relying on a default that
differs between ChromaDB versions.
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.rag import client as rag_client


class TestChromaClientTelemetry(unittest.TestCase):
    def setUp(self):
        self._saved = rag_client._chroma_client
        rag_client._chroma_client = None

    def tearDown(self):
        rag_client._chroma_client = self._saved

    def test_persistent_client_is_created_with_telemetry_disabled(self):
        with patch.object(rag_client.chromadb, "PersistentClient") as fake:
            rag_client.get_chroma_client()
        kwargs = fake.call_args.kwargs
        self.assertIn("settings", kwargs)
        self.assertIs(kwargs["settings"].anonymized_telemetry, False)

    def test_a_real_client_reports_telemetry_disabled(self):
        # ignore_cleanup_errors: on Windows Chroma's SQLite file can stay locked for a moment after
        # the client stops, which would make removing the folder raise PermissionError.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            with patch.object(rag_client.settings.chroma, "CHROMA_PATH", tmp, create=True):
                real = rag_client.get_chroma_client()
                try:
                    self.assertIs(real.get_settings().anonymized_telemetry, False)
                finally:
                    rag_client._chroma_client = None
                    try:
                        real._system.stop()
                    except Exception:
                        pass

    def test_client_is_a_singleton(self):
        with patch.object(rag_client.chromadb, "PersistentClient") as fake:
            a = rag_client.get_chroma_client()
            b = rag_client.get_chroma_client()
        self.assertIs(a, b)
        self.assertEqual(fake.call_count, 1)


if __name__ == "__main__":
    unittest.main()
