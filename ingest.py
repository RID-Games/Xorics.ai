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
ingest.py - turn datasheet PDFs into the RAG index.

[RECONSTRUCTED 2026-06-17 after data loss. The main() body matches the source
 recovered from our build session; the header/constants are reconstructed.]

Drop PDFs in your datasheets/ folder (XORICS_DATASHEETS) and run:
    python ingest.py
Writes rag_index/vectors.npy and rag_index/chunks.json (a full rebuild).
For incremental web additions use web_datasheets.fetch_datasheet instead.
"""

from __future__ import annotations
import json
import os
from pathlib import Path

import numpy as np
import fitz  # PyMuPDF

from datasheet_rag import embed, INDEX_DIR, DOCUMENT_PREFIX, chunk_text

DATASHEET_DIR = Path(os.environ.get("XORICS_DATASHEETS", Path.home() / "xorics-ai" / "datasheets"))
EMBED_BATCH = 32


def main():
    pdfs = sorted(DATASHEET_DIR.glob("*.pdf"))
    if not pdfs:
        print(f"No PDFs in {DATASHEET_DIR}. Drop datasheets there and re-run.")
        return

    chunks: list[dict] = []
    for pdf in pdfs:
        doc = fitz.open(pdf)
        n_pages = doc.page_count
        for pageno, page in enumerate(doc, 1):
            for piece in chunk_text(page.get_text()):
                chunks.append({"text": piece, "source": pdf.name, "page": pageno})
        doc.close()
        print(f"  {pdf.name}: {n_pages} pages")

    if not chunks:
        print("No extractable text (scanned PDFs? that's the VLM iteration).")
        return

    print(f"Embedding {len(chunks)} chunks via the :8082 server...")
    vecs = []
    for i in range(0, len(chunks), EMBED_BATCH):
        batch = [DOCUMENT_PREFIX + c["text"] for c in chunks[i:i + EMBED_BATCH]]
        vecs.append(embed(batch))
        print(f"  {min(i + EMBED_BATCH, len(chunks))}/{len(chunks)}")
    vectors = np.vstack(vecs).astype(np.float32)

    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    np.save(INDEX_DIR / "vectors.npy", vectors)
    (INDEX_DIR / "chunks.json").write_text(json.dumps(chunks))
    print(f"Done. {len(chunks)} chunks -> {INDEX_DIR}")


if __name__ == "__main__":
    main()
