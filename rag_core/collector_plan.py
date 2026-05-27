"""
Optimale Collector-Strategie: Qualität vor Quantität.
Domänenagnostische Konfiguration – Relevanzfilter aus domains.py.
"""
from .domains import get_domain, ALL_DOMAINS, merged_wiki_keywords

# Ordner, die NICHT in die Prime-DB gehören (Rauschen / irrelevant)
PRIME_EXCLUDE_PREFIXES = (
    "gutenberg",
    "books",
    "processed",
    "dumps",
    "wikisource",
    "wiktionary",
    "wikiquote",
    "wikiversity",
)

# Kuratierte Prime-Quellen (Reihenfolge = Priorität)
PRIME_SOURCE_ROOTS = (
    "custom_docs",
    "manpages",
    "stackoverflow",
    "stackexchange",
    "rfcs",
    "mdn-web-docs/files",
    "tldr-pages/pages",
    "arch-wiki",
    "official-docs",
    "owasp",
    "wikipedia",
    "linux-docs",
    "textbooks",
)

# Stack Exchange: kleine, hochwertige Sites zuerst (SO zuletzt – 15 GB)
STACKEXCHANGE_QUALITY_ORDER = [
    "security.stackexchange.com",
    "serverfault.com",
    "unix.stackexchange.com",
    "dba.stackexchange.com",
    "codereview.stackexchange.com",
    "devops.stackexchange.com",
    "networkengineering.stackexchange.com",
    "datascience.stackexchange.com",
    "ai.stackexchange.com",
    "raspberrypi.stackexchange.com",
    "arduino.stackexchange.com",
    "electronics.stackexchange.com",
    "webmasters.stackexchange.com",
    "ethereum.stackexchange.com",
    "wordpress.stackexchange.com",
    "superuser.com",
    "askubuntu.com",
    "softwareengineering.stackexchange.com",
    "stackoverflow.com",
]

# Optional: volle Wikimedia-Dumps
WIKIMEDIA_DUMP_OPTIONAL = [
    {"name": "Wikibooks DE", "size": "~50 MB",
     "url": "https://dumps.wikimedia.org/dewikibooks/latest/dewikibooks-latest-pages-articles.xml.bz2",
     "file": "dewikibooks-articles.xml.bz2"},
    {"name": "Wikibooks EN", "size": "~400 MB",
     "url": "https://dumps.wikimedia.org/enwikibooks/latest/enwikibooks-latest-pages-articles.xml.bz2",
     "file": "enwikibooks-articles.xml.bz2"},
    {"name": "Wikiversity DE", "size": "~30 MB",
     "url": "https://dumps.wikimedia.org/dewikiversity/latest/dewikiversity-latest-pages-articles.xml.bz2",
     "file": "dewikiversity-articles.xml.bz2"},
    {"name": "Wikiversity EN", "size": "~100 MB",
     "url": "https://dumps.wikimedia.org/enwikiversity/latest/enwikiversity-latest-pages-articles.xml.bz2",
     "file": "enwikiversity-articles.xml.bz2"},
    {"name": "Wikisource DE", "size": "~500 MB",
     "url": "https://dumps.wikimedia.org/dewikisource/latest/dewikisource-latest-pages-articles.xml.bz2",
     "file": "dewikisource-articles.xml.bz2"},
    {"name": "Wikisource EN", "size": "~3 GB",
     "url": "https://dumps.wikimedia.org/enwikisource/latest/enwikisource-latest-pages-articles.xml.bz2",
     "file": "enwikisource-articles.xml.bz2"},
    {"name": "Wiktionary DE", "size": "~250 MB",
     "url": "https://dumps.wikimedia.org/dewiktionary/latest/dewiktionary-latest-pages-articles.xml.bz2",
     "file": "dewiktionary-articles.xml.bz2"},
    {"name": "Wiktionary EN", "size": "~1.5 GB",
     "url": "https://dumps.wikimedia.org/enwiktionary/latest/enwiktionary-latest-pages-articles.xml.bz2",
     "file": "enwiktionary-articles.xml.bz2"},
]

WIKIMEDIA_DUMP_LEGACY_FULL = [
    {"name": "Wikipedia DE", "size": "~7.6 GB",
     "url": "https://dumps.wikimedia.org/dewiki/latest/dewiki-latest-pages-articles-multistream.xml.bz2",
     "file": "dewiki-articles.xml.bz2"},
    {"name": "Wikipedia EN", "size": "~23 GB",
     "url": "https://dumps.wikimedia.org/enwiki/latest/enwiki-latest-pages-articles-multistream.xml.bz2",
     "file": "enwiki-articles.xml.bz2"},
    {"name": "Wikipedia FR", "size": "~9 GB",
     "url": "https://dumps.wikimedia.org/frwiki/latest/frwiki-latest-pages-articles-multistream.xml.bz2",
     "file": "frwiki-articles.xml.bz2"},
    {"name": "Wikipedia ES", "size": "~5 GB",
     "url": "https://dumps.wikimedia.org/eswiki/latest/eswiki-latest-pages-articles-multistream.xml.bz2",
     "file": "eswiki-articles.xml.bz2"},
]


def get_wiki_link_keywords(domain_ids: list[str] | None = None) -> tuple[str, ...]:
    """Dynamische Wiki-Link-Keywords basierend auf aktiven Domänen."""
    if domain_ids:
        domains = [get_domain(d) for d in domain_ids if d in ALL_DOMAINS]
    else:
        try:
            from .quality import get_current_domains
            domains = get_current_domains()
        except Exception:
            domains = [get_domain("it")]
    return merged_wiki_keywords(domains)


# Legacy-Kompatibilität
WIKI_LINK_KEYWORDS = get_wiki_link_keywords(["it"])


def get_wiki_blocked_patterns(domain_ids: list[str] | None = None) -> tuple[str, ...]:
    """Dynamische Blockliste für Wikipedia-Link-Expansion."""
    if domain_ids:
        domains = [get_domain(d) for d in domain_ids if d in ALL_DOMAINS]
    else:
        try:
            from .quality import get_current_domains
            domains = get_current_domains()
        except Exception:
            domains = [get_domain("it")]
    from .domains import merged_blocked_patterns
    return merged_blocked_patterns(domains)


WIKI_MAX_QUEUE = 3_000
WIKI_MAX_DONE = 25_000
SO_MAX_PAGES_PER_TAG = 30

QUALITY_PIPELINE_PHASES = [
    "Kern-Dokumentation (MDN, TLDR, Git-Docs, OWASP, Arch Wiki)",
    "Referenz (RFCs, Manpages, Linux-Docs)",
    "Stack Exchange (IT-foren, qualitätsgefiltert)",
    "Wikipedia API (domänenspezifisch, begrenzt)",
    "Stack Overflow API (Top-Tags)",
    "Bücher & Literatur",
    "Wikipedia Vollarchive (Dumps)",
    "Deine Projekte + Prime-Index",
]
