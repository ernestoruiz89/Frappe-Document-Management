from document_management.document_management.rag.chunking import chunk_pages


def test_chunking_preserves_late_document_content():
    words = [f"token-{index}" for index in range(1500)]
    chunks = chunk_pages([" ".join(words)], chunk_size=500, overlap=100)

    assert len(chunks) == 4
    assert "token-1499" in chunks[-1].content
    assert chunks[-1].page_number == 1


def test_chunking_tracks_pages_and_overlap():
    first_page = " ".join(f"first-{index}" for index in range(12))
    second_page = " ".join(f"second-{index}" for index in range(8))

    chunks = chunk_pages([first_page, second_page], chunk_size=10, overlap=2)

    assert [chunk.page_number for chunk in chunks] == [1, 1, 2]
    assert chunks[0].content.split()[-2:] == chunks[1].content.split()[:2]
    assert chunks[2].content.startswith("second-0")


def test_empty_pages_are_ignored():
    chunks = chunk_pages(["", "   ", "available evidence"], chunk_size=10, overlap=2)

    assert len(chunks) == 1
    assert chunks[0].page_number == 3
    assert chunks[0].content == "available evidence"
