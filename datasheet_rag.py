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
datasheet_rag.py - local RAG retrieval over the datasheet index.

[RECONSTRUCTED 2026-06-17 after data loss — verify against an undelete-recovered
 original if you can. Structure/behaviour match our build sessions: a search_query:
 prefix, brute-force cosine over a numpy matrix, returns the top-k chunks as text.
 The nomic search_query:/search_document: prefixes are load-bearing — do not strip.]

Embeddings come from the always-on CPU embed server (llama.cpp, nomic-embed-text-v1.5)
at :8082. The index is two files written by ingest.py / web_datasheets.py:
    rag_index/vectors.npy   float32 [N, D]
    rag_index/chunks.json   [{"text","source","page"}, ...]

Wire into xorics.py:
    from datasheet_rag import search_datasheets
    TOOL_IMPLS["search_datasheets"] = search_datasheets
"""

from __future__ import annotations
import json
import os
from pathlib import Path

import numpy as np
from openai import OpenAI

# Index location (override with XORICS_RAG_INDEX). Matches .gitignore's rag_index/.
INDEX_DIR = Path(os.environ.get("XORICS_RAG_INDEX", Path.home() / "xorics-ai" / "rag_index"))

# nomic-embed requires these task prefixes; queries and documents use different ones.
QUERY_PREFIX = "search_query: "
DOCUMENT_PREFIX = "search_document: "

EMBED_MODEL = os.environ.get("XORICS_EMBED_MODEL", "nomic-embed-text-v1.5")
_client = OpenAI(
    base_url=os.environ.get("XORICS_EMBED_URL", "http://127.0.0.1:8082/v1"),
    api_key="not-needed",
)

DEFAULT_K = 5


def embed(texts) -> "np.ndarray":
    """Embed a list of strings via the :8082 server. Returns float32 [len(texts), D]."""
    resp = _client.embeddings.create(model=EMBED_MODEL, input=list(texts))
    return np.array([d.embedding for d in resp.data], dtype=np.float32)


def chunk_text(text: str, size: int = 512, overlap: int = 64) -> list[str]:
    """Split page text into word-windowed chunks with a little overlap."""
    words = text.split()
    if not words:
        return []
    step = max(1, size - overlap)
    out = []
    for i in range(0, len(words), step):
        piece = " ".join(words[i:i + size]).strip()
        if piece:
            out.append(piece)
    return out


def _load_index():
    vpath = INDEX_DIR / "vectors.npy"
    cpath = INDEX_DIR / "chunks.json"
    if not vpath.exists() or not cpath.exists():
        return None, None
    vectors = np.load(vpath)
    chunks = json.loads(cpath.read_text())
    return vectors, chunks


def search_datasheets(query: str, k: int = DEFAULT_K) -> str:
    """
    Return the most relevant datasheet excerpts for a natural-language query.
    Gives a friendly nudge (not a crash) if no index has been built yet.
    """
    vectors, chunks = _load_index()
    if vectors is None:
        return ("No datasheet index yet. Drop PDFs in your datasheets/ folder and run "
                "`python ingest.py`, or use fetch_datasheet to pull one from the web.")

    q = embed([QUERY_PREFIX + query])[0]
    vn = vectors / (np.linalg.norm(vectors, axis=1, keepdims=True) + 1e-9)
    qn = q / (np.linalg.norm(q) + 1e-9)
    sims = vn @ qn
    idx = np.argsort(-sims)[:max(1, k)]

    parts = []
    for i in idx:
        c = chunks[int(i)]
        src = c.get("source", "?")
        page = c.get("page", "?")
        parts.append(f"[{src} p{page}] {c['text']}")
    return "\n\n".join(parts) if parts else "No relevant excerpts found."


if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) or "ESP32-C3 strapping pins"
    print(search_datasheets(q))
