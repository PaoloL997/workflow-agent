from langchain_text_splitters import MarkdownHeaderTextSplitter
from langchain_core.documents import Document

def split_text(markdown: str) -> list[Document]:
    splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=[("#", "h1"), ("##", "h2"), ("###", "h3")],
        strip_headers=False,
    )
    sections = splitter.split_text(markdown)

    chunks = []
    for s in sections:
        md = s.metadata
        if md.get("h2") == "Indice":
            continue
        breadcrumb = " > ".join(md[k] for k in ("h2", "h3") if k in md)
        chunks.append(Document(
            page_content=f"{breadcrumb}\n\n{s.page_content.strip()}",
            metadata={
                "chapter": md.get("h2", ""),
                "section": md.get("h3", ""),
            },
        ))
    return chunks