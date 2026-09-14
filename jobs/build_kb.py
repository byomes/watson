import logging
import re
from pathlib import Path
import chromadb
from chromadb.utils import embedding_functions

log = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent.parent
TRANSCRIPTS_DIR = BASE_DIR / 'kb' / 'documents'
CHROMA_DIR = BASE_DIR / 'data' / 'chroma'
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50

# jobs/generate.py stamps every transcript filename with a leading date (the
# sermon's own preached date when known, else the ingestion date — see bug
# #165), so the year it was preached can be read straight off the title with
# no separate lookup table. Used by jobs/ask.py to weight search results.
_DATE_PREFIX_RE = re.compile(r'^(\d{2,4})-(\d{2})-(\d{2,4})-?')


# Bill trusts his post-2022 sermons' theology more than his earlier material
# (more education, richer theology since). Not a hard filter — a soft
# discount on cosine distance so 2022+ chunks rank above equally-relevant
# older ones, while a clearly more relevant older chunk still wins and older
# sermons still surface for topics only they cover. Shared by jobs/ask.py and
# jobs/skills/kb_search.py so both "sermons" collection query paths agree.
TRUSTED_YEAR_CUTOFF = 2022
TRUSTED_YEAR_DISTANCE_FACTOR = 0.85

# AI-ghostwritten material (Bill's own ideas expanded by AI, not his original
# words) is tagged with this source_type and must never be silently blended
# into a normal or "expanded" search -- only surfaced when explicitly
# requested. Shared by jobs/ask.py and jobs/skills/kb_search.py.
GHOSTWRITTEN_SOURCE_TYPE = "ai-ghostwritten"


def boosted_distance(distance: float, year) -> float:
    """Discount cosine distance for sermons preached TRUSTED_YEAR_CUTOFF or later."""
    if year is not None and year >= TRUSTED_YEAR_CUTOFF:
        return distance * TRUSTED_YEAR_DISTANCE_FACTOR
    return distance


def year_from_title(title: str) -> int | None:
    """Parse the leading YYYY-MM-DD or MM-DD-YYYY date out of a KB title and
    return its year, or None if the title has no leading date."""
    m = _DATE_PREFIX_RE.match(title)
    if not m:
        return None
    a, _, c = m.groups()
    year_str = a if len(a) == 4 else c if len(c) == 4 else None
    if not year_str:
        return None
    try:
        return int(year_str)
    except ValueError:
        return None

def chunk_text(text, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunk = ' '.join(words[i:i+size])
        chunks.append(chunk)
        i += size - overlap
    return chunks

def ingest_dir(files_dir, collection_name, source_type="transcript"):
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
            meta = {'title': title, 'chunk': i, 'source_type': source_type}
            year = year_from_title(title)
            if year is not None:
                meta['year'] = year
            collection.add(ids=[chunk_id], documents=[chunk], metadatas=[meta])
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
