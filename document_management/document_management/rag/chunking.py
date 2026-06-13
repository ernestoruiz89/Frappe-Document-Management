import hashlib
import re
from dataclasses import dataclass


TOKEN_RE = re.compile(r"\S+")


@dataclass(frozen=True)
class ChunkData:
    page_number: int
    chunk_index: int
    content: str
    content_hash: str


def normalize_text(text):
    return re.sub(r"[ \t]+", " ", (text or "").replace("\x00", "")).strip()


def chunk_pages(page_texts, chunk_size=650, overlap=120):
    chunks = []
    global_index = 0
    step = max(chunk_size - overlap, 1)

    for page_number, page_text in enumerate(page_texts, start=1):
        text = normalize_text(page_text)
        tokens = TOKEN_RE.findall(text)
        if not tokens:
            continue

        for start in range(0, len(tokens), step):
            content = " ".join(tokens[start : start + chunk_size]).strip()
            if not content:
                continue
            chunks.append(
                ChunkData(
                    page_number=page_number,
                    chunk_index=global_index,
                    content=content,
                    content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                )
            )
            global_index += 1
            if start + chunk_size >= len(tokens):
                break

    return chunks
