import logging
from pathlib import Path
import chromadb
from chromadb.utils import embedding_functions

log = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent.parent
TRANSCRIPTS_DIR = BASE_DIR / 'kb' / 'documents'
CHROMA_DIR = BASE_DIR / 'data' / 'chroma'
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50

def chunk_text(text, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunk = ' '.join(words[i:i+size])
        chunks.append(chunk)
        i += size - overlap
    return chunks

def ingest_dir(files_dir, collection_name):
    """Chunk and ingest all .txt/.md files in files_dir into the named ChromaDB collection.

    Returns the number of new chunks added.
    """
    files = list(Path(files_dir).glob('*.txt')) + list(Path(files_dir).glob('*.md'))
    if not files:
        log.error('No document files found in %s', files_dir)
        return 0
    log.info('Found %d document files', len(files))
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name='all-MiniLM-L6-v2')
    collection = client.get_or_create_collection(name=collection_name, embedding_function=ef, metadata={'hnsw:space': 'cosine'})
    existing = set(collection.get()['ids'])
    log.info('Existing chunks in DB: %d', len(existing))
    added = 0
    for fpath in files:
        title = fpath.stem
        text = fpath.read_text(encoding='utf-8', errors='ignore').strip()
        if not text:
            log.warning('Empty file: %s', fpath.name)
            continue
        chunks = chunk_text(text)
        for i, chunk in enumerate(chunks):
            chunk_id = f'{title}::chunk{i}'
            if chunk_id in existing:
                continue
            collection.add(ids=[chunk_id], documents=[chunk], metadatas=[{'title': title, 'chunk': i}])
            added += 1
    log.info('Added %d new chunks. Total in DB: %d', added, collection.count())
    return added


def ingest():
    ingest_dir(TRANSCRIPTS_DIR, 'sermons')

def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', datefmt='%H:%M:%S')
    log.info('Building knowledge base...')
    ingest()
    log.info('Done.')

if __name__ == '__main__':
    main()
