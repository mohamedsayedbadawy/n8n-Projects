import os
import re
import sqlite3
import hashlib
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, UploadFile, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
import pdfplumber

# ─── Config ───────────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).parent.parent
UPLOAD_DIR = BASE_DIR / "uploads"
DATA_DIR   = BASE_DIR / "data"
DB_PATH    = DATA_DIR / "knowledge.db"
STATIC_DIR = BASE_DIR / "static"

UPLOAD_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)

# ─── Database Setup ────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS documents (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            filename  TEXT NOT NULL,
            file_hash TEXT UNIQUE NOT NULL,
            pages     INTEGER,
            chunks    INTEGER,
            uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts
        USING fts5(
            doc_id    UNINDEXED,
            chunk_id  UNINDEXED,
            page_num  UNINDEXED,
            content,
            tokenize  = "porter ascii"
        );

        CREATE TABLE IF NOT EXISTS chunks (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id   INTEGER NOT NULL,
            page_num INTEGER,
            chunk_id INTEGER,
            content  TEXT NOT NULL,
            FOREIGN KEY (doc_id) REFERENCES documents(id)
        );
    """)
    conn.commit()
    conn.close()

init_db()

# ─── Chunking ──────────────────────────────────────────────────────────────────
def chunk_text(text: str, chunk_size: int = 400, overlap: int = 80):
    """Split text into overlapping chunks by sentence boundaries."""
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    chunks, current, current_len = [], [], 0

    for sent in sentences:
        sent_len = len(sent)
        if current_len + sent_len > chunk_size and current:
            chunks.append(" ".join(current))
            # keep overlap
            overlap_sents, overlap_len = [], 0
            for s in reversed(current):
                if overlap_len + len(s) < overlap:
                    overlap_sents.insert(0, s)
                    overlap_len += len(s)
                else:
                    break
            current = overlap_sents
            current_len = overlap_len
        current.append(sent)
        current_len += sent_len

    if current:
        chunks.append(" ".join(current))
    return chunks

# ─── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="AVA Knowledge Base", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Upload PDF ────────────────────────────────────────────────────────────────
@app.post("/upload")
async def upload_pdf(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are accepted.")

    content = await file.read()
    file_hash = hashlib.md5(content).hexdigest()

    # Check duplicate
    conn = get_db()
    existing = conn.execute(
        "SELECT id, filename FROM documents WHERE file_hash = ?", (file_hash,)
    ).fetchone()
    if existing:
        conn.close()
        return {"status": "duplicate", "message": f"This PDF was already uploaded as '{existing['filename']}'", "doc_id": existing["id"]}

    # Save file
    save_path = UPLOAD_DIR / file.filename
    save_path.write_bytes(content)

    # Parse PDF
    all_chunks, page_count = [], 0
    try:
        with pdfplumber.open(save_path) as pdf:
            page_count = len(pdf.pages)
            for page_num, page in enumerate(pdf.pages, 1):
                text = page.extract_text() or ""
                text = re.sub(r'\s+', ' ', text).strip()
                if text:
                    for chunk in chunk_text(text):
                        if len(chunk.strip()) > 30:
                            all_chunks.append((page_num, chunk.strip()))
    except Exception as e:
        save_path.unlink(missing_ok=True)
        raise HTTPException(500, f"PDF parsing failed: {e}")

    if not all_chunks:
        save_path.unlink(missing_ok=True)
        raise HTTPException(400, "No text could be extracted from this PDF.")

    # Store in DB
    cur = conn.execute(
        "INSERT INTO documents (filename, file_hash, pages, chunks) VALUES (?,?,?,?)",
        (file.filename, file_hash, page_count, len(all_chunks))
    )
    doc_id = cur.lastrowid

    for chunk_id, (page_num, content) in enumerate(all_chunks):
        conn.execute(
            "INSERT INTO chunks (doc_id, page_num, chunk_id, content) VALUES (?,?,?,?)",
            (doc_id, page_num, chunk_id, content)
        )
        conn.execute(
            "INSERT INTO knowledge_fts (doc_id, chunk_id, page_num, content) VALUES (?,?,?,?)",
            (doc_id, chunk_id, page_num, content)
        )

    conn.commit()
    conn.close()

    return {
        "status": "success",
        "doc_id": doc_id,
        "filename": file.filename,
        "pages": page_count,
        "chunks_indexed": len(all_chunks)
    }

# ─── Search (AVA calls this) ───────────────────────────────────────────────────
@app.get("/search")
def search(
    q: str = Query(..., min_length=2, description="Search query from AVA"),
    limit: int = Query(3, le=10),
    doc_id: Optional[int] = Query(None, description="Limit to specific document")
):
    if not q.strip():
        raise HTTPException(400, "Query cannot be empty.")

    conn = get_db()

    # Build FTS query — wrap each word for better matching
    fts_query = " OR ".join(q.strip().split())

    try:
        if doc_id:
            rows = conn.execute("""
                SELECT f.doc_id, f.chunk_id, f.page_num, f.content,
                       d.filename,
                       bm25(knowledge_fts) AS score
                FROM knowledge_fts f
                JOIN documents d ON d.id = f.doc_id
                WHERE knowledge_fts MATCH ? AND f.doc_id = ?
                ORDER BY score
                LIMIT ?
            """, (fts_query, doc_id, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT f.doc_id, f.chunk_id, f.page_num, f.content,
                       d.filename,
                       bm25(knowledge_fts) AS score
                FROM knowledge_fts f
                JOIN documents d ON d.id = f.doc_id
                WHERE knowledge_fts MATCH ?
                ORDER BY score
                LIMIT ?
            """, (fts_query, limit)).fetchall()
    except Exception:
        conn.close()
        return {"results": [], "answer": "I don't have information about that in my knowledge base."}

    conn.close()

    if not rows:
        return {
            "results": [],
            "answer": "I don't have specific information about that. Would you like me to transfer you to a human agent?"
        }

    # Build a clean answer for AVA to speak
    results = [{"page": r["page_num"], "source": r["filename"], "content": r["content"]} for r in rows]
    combined = " | ".join(r["content"][:300] for r in rows[:2])

    return {
        "results": results,
        "answer": combined,   # AVA speaks this directly
        "source_count": len(rows)
    }

# ─── Documents list ────────────────────────────────────────────────────────────
@app.get("/documents")
def list_documents():
    conn = get_db()
    docs = conn.execute(
        "SELECT id, filename, pages, chunks, uploaded_at FROM documents ORDER BY uploaded_at DESC"
    ).fetchall()
    conn.close()
    return {"documents": [dict(d) for d in docs]}

# ─── Delete document ───────────────────────────────────────────────────────────
@app.delete("/documents/{doc_id}")
def delete_document(doc_id: int):
    conn = get_db()
    doc = conn.execute("SELECT filename FROM documents WHERE id=?", (doc_id,)).fetchone()
    if not doc:
        conn.close()
        raise HTTPException(404, "Document not found.")

    conn.execute("DELETE FROM knowledge_fts WHERE doc_id=?", (doc_id,))
    conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
    conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
    conn.commit()
    conn.close()

    # Remove file if exists
    (UPLOAD_DIR / doc["filename"]).unlink(missing_ok=True)
    return {"status": "deleted", "doc_id": doc_id}

# ─── Stats ─────────────────────────────────────────────────────────────────────
@app.get("/stats")
def stats():
    conn = get_db()
    total_docs   = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    total_chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    total_pages  = conn.execute("SELECT SUM(pages) FROM documents").fetchone()[0] or 0
    conn.close()
    return {"total_documents": total_docs, "total_chunks": total_chunks, "total_pages": total_pages}

# ─── Health ────────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "service": "AVA Knowledge Base"}

# ─── Serve UI ──────────────────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/")
def serve_ui():
    return FileResponse(str(STATIC_DIR / "index.html"))
