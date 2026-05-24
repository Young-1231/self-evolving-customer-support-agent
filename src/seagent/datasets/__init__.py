"""Open-source dataset adapters for KB augmentation.

Each module under this package wraps one public corpus and converts it into
this project's KB doc schema ``{doc_id, title, topic, text}``. Adapters never
touch ``data/kb/``; they write under ``data/kb_expanded/`` so the original
30-doc NimbusFlow KB stays a clean baseline.
"""

from .bitext import (
    BITEXT_REPO,
    BITEXT_FILENAME,
    BITEXT_LICENSE,
    download_bitext,
    load_bitext_rows,
    bitext_to_kb_docs,
)

__all__ = [
    "BITEXT_REPO",
    "BITEXT_FILENAME",
    "BITEXT_LICENSE",
    "download_bitext",
    "load_bitext_rows",
    "bitext_to_kb_docs",
]
