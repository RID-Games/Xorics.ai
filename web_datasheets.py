# Xorics — a self-hosted local AI assistant for embedded / PCB engineering.
# Copyright (C) 2026 Zawayix
#
# This file is part of Xorics. Xorics is free software: you can redistribute it
# and/or modify it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Xorics is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
# A PARTICULAR PURPOSE. See the GNU Affero General Public License for details.
#
# You should have received a copy of the GNU Affero General Public License along
# with Xorics. If not, see <https://www.gnu.org/licenses/>.
#
# ADDITIONAL PERMISSION (AGPLv3 section 7): designs and files produced by RUNNING
# Xorics, and any fragments it embeds into that output, are NOT covered by the
# AGPL — you may license your generated designs as you wish. See LICENSE-EXCEPTION.

"""
web_datasheets.py - find a datasheet PDF on the web and add it to the RAG index.

[RECONSTRUCTED 2026-06-17 after data loss — verify against an undelete-recovered
 original if you can. Behaviour matches our build sessions: DuckDuckGo (ddgs, FOSS)
 finds a PDF, PyMuPDF extracts text, it is chunked/embedded and APPENDED to the
 existing index (the append-don't-clobber path was unit-tested when we built it).]

Wire into xorics.py:
    from web_datasheets import fetch_datasheet
    TOOL_IMPLS["fetch_datasheet"] = fetch_datasheet
"""

from __future__ import annotations
import json
import tempfile
import urllib.request
from pathlib import Path

import numpy as np
import fitz  # PyMuPDF

from datasheet_rag import embed, INDEX_DIR, DOCUMENT_PREFIX, chunk_text

try:
    from ddgs import DDGS            # current package name
except ImportError:                  # older name
    from duckduckgo_search import DDGS

_UA = {"User-Agent": "Mozilla/5.0 (Xorics datasheet fetcher)"}
MAX_PAGES = 60


def _append_to_index(chunks: list[dict]) -> int:
    """Embed and append chunks to the index without clobbering what's there."""
    if not chunks:
        return 0
    vecs = embed([DOCUMENT_PREFIX + c["text"] for c in chunks]).astype(np.float32)
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    vpath = INDEX_DIR / "vectors.npy"
    cpath = INDEX_DIR / "chunks.json"
    if vpath.exists() and cpath.exists():
        old_v = np.load(vpath)
        old_c = json.loads(cpath.read_text())
        vecs = np.vstack([old_v, vecs])
        chunks = old_c + chunks
    np.save(vpath, vecs)
    cpath.write_text(json.dumps(chunks))
    return len(chunks)


def _find_pdf_url(query: str) -> str | None:
    with DDGS() as ddgs:
        for r in ddgs.text(f"{query} datasheet filetype:pdf", max_results=8):
            url = r.get("href") or r.get("url") or ""
            if url.lower().endswith(".pdf"):
                return url
    return None


def fetch_datasheet(query: str) -> str:
    """
    Search the web for a datasheet PDF matching `query`, download it, and add its
    text to the local index so search_datasheets can use it. Returns a status line.
    """
    url = _find_pdf_url(query)
    if not url:
        return f"No PDF datasheet found on the web for: {query!r}"

    try:
        req = urllib.request.Request(url, headers=_UA)
        data = urllib.request.urlopen(req, timeout=30).read()
    except Exception as e:  # noqa: BLE001 - report, don't crash the loop
        return f"Found {url} but download failed: {e}"

    name = url.rsplit("/", 1)[-1] or "datasheet.pdf"
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as tf:
        tf.write(data)
        tf.flush()
        chunks: list[dict] = []
        try:
            doc = fitz.open(tf.name)
        except Exception as e:  # noqa: BLE001
            return f"Downloaded {name} but it could not be parsed as PDF: {e}"
        for pageno, page in enumerate(doc, 1):
            if pageno > MAX_PAGES:
                break
            for piece in chunk_text(page.get_text()):
                chunks.append({"text": piece, "source": name, "page": pageno})
        doc.close()

    if not chunks:
        return f"Downloaded {name} but found no extractable text (scanned PDF?)."
    total = _append_to_index(chunks)
    return f"Indexed {name}: added {len(chunks)} chunks (index now {total} total)."


if __name__ == "__main__":
    import sys
    print(fetch_datasheet(" ".join(sys.argv[1:]) or "ATmega328P"))
