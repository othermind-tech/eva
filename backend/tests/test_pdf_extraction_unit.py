"""
PDF text extraction (utils.file_utils.extract_text_from_pdf_bytes) on pypdf.

PyMuPDF (AGPL) was replaced by pypdf (BSD-3). The behaviours that matter to the
RAG pipeline and chat attachments are pinned here so the swap cannot silently
change them: page text must not fuse across page boundaries, an empty-password
"encrypted" PDF must still read, a genuinely password-protected or corrupt file
must RAISE (callers turn that into a visible error rather than indexing an
empty document), and one bad page must not discard the rest.
"""
import io
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pypdf import PdfReader, PdfWriter

from utils import file_utils
from utils.file_utils import extract_text_from_pdf_bytes, process_file_bytes


def _pdf(*pages: str) -> bytes:
    """A minimal valid PDF with one text line per page (no third-party writer needed)."""
    objs = []
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(len(pages)))
    objs.append("<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>")
    font_id = 3 + 2 * len(pages)
    for i, text in enumerate(pages):
        content_id = 4 + 2 * i
        objs.append(
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Contents {content_id} 0 R /Resources << /Font << /F1 {font_id} 0 R >> >> >>"
        )
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET"
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
    objs.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(f"{n} 0 obj\n{body}\nendobj\n".encode("latin-1"))
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
    return out.getvalue()


def _encrypted(raw: bytes, user_pw: str) -> bytes:
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(raw)))
    writer.encrypt(user_pw, owner_password="owner", algorithm="AES-256")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


class ExtractionTests(unittest.TestCase):
    def test_pages_are_separated_not_fused(self):
        text = extract_text_from_pdf_bytes(_pdf("End of page one 12345", "Start of page two"))
        self.assertIn("12345\nStart of page two", text)
        self.assertNotIn("12345Start", text)

    def test_exact_output_matches_the_previous_extractor_shape(self):
        # One newline after every page that has text -- what PyMuPDF produced.
        self.assertEqual(
            extract_text_from_pdf_bytes(_pdf("alpha", "beta")), "alpha\nbeta\n"
        )

    def test_blank_pdf_gives_empty_text(self):
        writer = PdfWriter()
        writer.add_blank_page(612, 792)
        buf = io.BytesIO()
        writer.write(buf)
        self.assertEqual(extract_text_from_pdf_bytes(buf.getvalue()), "")

    def test_empty_password_encrypted_pdf_is_readable(self):
        text = extract_text_from_pdf_bytes(_encrypted(_pdf("Secret contents"), ""))
        self.assertEqual(text, "Secret contents\n")

    def test_password_protected_pdf_raises(self):
        with self.assertRaises(ValueError) as ctx:
            extract_text_from_pdf_bytes(_encrypted(_pdf("Secret contents"), "hunter2"))
        self.assertIn("password", str(ctx.exception).lower())

    def test_corrupt_input_raises(self):
        with self.assertRaises(Exception):
            extract_text_from_pdf_bytes(b"this is not a pdf")

    def test_one_bad_page_does_not_discard_the_rest(self):
        class _Boom:
            def extract_text(self):
                raise RuntimeError("malformed page")

        class _Ok:
            def __init__(self, t):
                self.t = t

            def extract_text(self):
                return self.t

        class _Reader:
            is_encrypted = False
            pages = [_Ok("first"), _Boom(), _Ok("third")]

            def __init__(self, *_a, **_k):
                pass

        with patch.object(file_utils, "PdfReader", _Reader):
            self.assertEqual(extract_text_from_pdf_bytes(b"ignored"), "first\nthird\n")

    def test_unicode_survives(self):
        # Latin-1 range only: the hand-built PDF uses a standard font.
        text = extract_text_from_pdf_bytes(_pdf("caf\\351"))
        self.assertIn("caf", text)


class PipelineTests(unittest.TestCase):
    def test_process_file_bytes_chunks_a_pdf(self):
        chunks = process_file_bytes(_pdf("one two three"), "doc.pdf")
        self.assertEqual("".join(chunks).strip(), "one two three")

    def test_process_file_bytes_propagates_password_error(self):
        with self.assertRaises(ValueError):
            process_file_bytes(_encrypted(_pdf("x"), "pw"), "locked.pdf")

    def test_agpl_library_is_not_imported_anywhere(self):
        import pathlib
        import re

        root = pathlib.Path(__file__).resolve().parent.parent
        pat = re.compile(r"^\s*(import|from)\s+(fitz|pymupdf)\b", re.M)
        offenders = []
        for sub in ("api", "core", "tools", "utils", "providers", "config"):
            for path in (root / sub).rglob("*.py"):
                if pat.search(path.read_text(encoding="utf-8", errors="ignore")):
                    offenders.append(str(path.relative_to(root)))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
