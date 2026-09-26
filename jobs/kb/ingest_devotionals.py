"""ingest_devotionals.py — Chunk and index the "Five Minute Moments" devotional
PDFs (kb/devotionals/) into the 'sermons' ChromaDB collection, tagged
source_type='devotional'.

Unlike jobs/build_kb.py's ingest_dir(), which reads plain .txt/.md files, this
extracts text from PDFs first (pypdf), since kb/devotionals/ holds only PDFs.
A PDF with no extractable text (e.g. a scanned image with no OCR layer) is
skipped and logged, not treated as an error.

Idempotent: re-running only adds chunks whose id isn't already in the
collection, same convention as build_kb.py.

Usage:
  PYTHONPATH=/home/billyomes/watson python3 jobs/kb/ingest_devotionals.py
"""
import logging
from pathlib import Path

import chromadb
from chromadb.utils import embedding_functions
from pypdf import PdfReader

from jobs.build_kb import chunk_text, CHROMA_DIR

log = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEVOTIONALS_DIR = BASE_DIR / 'kb' / 'devotionals'
COLLECTION_NAME = 'sermons'
SOURCE_TYPE = 'devotional'


def extract_pdf_text(path: Path) -> str:
    reader = PdfReader(str(path))
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or '')
        except Exception as e:
            log.warning('Failed to extract a page from %s: %s', path.name, e)
    return '\n'.join(pages).strip()


def friendly_title(stem: str) -> str:
    if stem.endswith('-web'):
        stem = stem[:-len('-web')]
    return f'Five Minute Moments - {stem}'


def ingest_devotionals() -> dict:
    files = sorted(DEVOTIONALS_DIR.glob('*.pdf'))
    if not files:
        log.warning('No PDF files found in %s', DEVOTIONALS_DIR)
        return {'files_found': 0, 'files_with_new_chunks': 0, 'files_skipped_no_text': 0, 'chunks_added': 0}

    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name='all-MiniLM-L6-v2')
    collection = client.get_or_create_collection(name=COLLECTION_NAME, embedding_function=ef, metadata={'hnsw:space': 'cosine'})
    existing = set(collection.get()['ids'])
    log.info('Found %d PDF files in %s. Existing chunks in DB: %d', len(files), DEVOTIONALS_DIR, len(existing))

    files_with_new_chunks = 0
    files_skipped_no_text = 0
    chunks_added = 0

    for fpath in files:
        title = friendly_title(fpath.stem)
        try:
            text = extract_pdf_text(fpath)
        except Exception as e:
            log.error('Failed to read %s: %s', fpath.name, e)
            continue
        if not text:
            log.warning('No extractable text in %s (likely a scanned/image PDF) -- skipped', fpath.name)
            files_skipped_no_text += 1
            continue

        new_for_file = 0
        for i, chunk in enumerate(chunk_text(text)):
            chunk_id = f'{title}::chunk{i}'
            if chunk_id in existing:
                continue
            collection.add(ids=[chunk_id], documents=[chunk], metadatas=[{'title': title, 'chunk': i, 'source_type': SOURCE_TYPE}])
            existing.add(chunk_id)
            chunks_added += 1
            new_for_file += 1
        if new_for_file:
            files_with_new_chunks += 1

    log.info(
        'Devotionals ingest complete. Files with new chunks: %d, files skipped (no text): %d, new chunks added: %d. Total in DB: %d',
        files_with_new_chunks, files_skipped_no_text, chunks_added, collection.count(),
    )
    return {
        'files_found': len(files),
        'files_with_new_chunks': files_with_new_chunks,
        'files_skipped_no_text': files_skipped_no_text,
        'chunks_added': chunks_added,
    }


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', datefmt='%H:%M:%S')
    log.info('Starting devotionals PDF ingest from %s', DEVOTIONALS_DIR)
    ingest_devotionals()
    log.info('Done.')


if __name__ == '__main__':
    main()
