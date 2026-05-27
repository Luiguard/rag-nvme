"""Domänen-System: Strukturierte Wissenskategorien statt IT-Hardcoding.

Jede Domäne definiert:
- Relevanz-Signale für Indexierung
- Wikipedia-Link-Keywords für Expansion
- Blocklisten für bekanntes Rauschen
- Source-Boosts für Retrieval-Priorisierung
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class KnowledgeDomain:
    id: str
    name: str
    description: str
    content_signals: tuple[str, ...] = ()
    wiki_keywords: tuple[str, ...] = ()
    wiki_seed_topics: tuple[str, ...] = ()
    blocked_patterns: tuple[str, ...] = ()
    source_boosts: dict[str, float] = field(default_factory=dict)


# ─── Vordefinierte Domänen ───

DOMAIN_IT = KnowledgeDomain(
    id="it",
    name="Informatik & Software",
    description="Programmierung, Netzwerke, Betriebssysteme, Datenbanken, Security",
    content_signals=(
        "programm", "software", "computer", "algorithmus", "algorithm",
        "netzwerk", "network", "linux", "python", "javascript", "java",
        "api", "server", "datenbank", "database", "compiler", "code",
        "docker", "kubernetes", "git", "http", "tcp", "sql", "nginx",
        "betriebssystem", "kernel", "encryption", "kryptograph",
        "framework", "library", "repository", "deployment", "debug",
        "frontend", "backend", "cloud", "container", "virtualisierung",
    ),
    wiki_keywords=(
        "Programmierung", "Software", "Datenbank", "Betriebssystem",
        "Rechnernetz", "Algorithmus", "Compiler", "Informatik",
        "JavaScript", "Python_(Programmiersprache)", "Linux",
        "Kubernetes", "Docker", "API", "REST", "DevOps",
        "Kryptographie", "Verschlüsselung_(Informatik)",
        "Maschinelles_Lernen", "Künstliche_Intelligenz",
    ),
    wiki_seed_topics=(
        "Informatik", "Softwarearchitektur", "Python_(Programmiersprache)",
        "Linux", "Künstliche_Intelligenz", "Netzwerkprotokoll",
        "Maschinelles_Lernen", "Betriebssystem", "Datenbank", "Kryptographie",
        "C++", "Java_(Programmiersprache)", "JavaScript", "HTML", "CSS",
        "Docker_(Software)", "Kubernetes", "Git", "Agile_Softwareentwicklung",
        "Algorithmus", "Datenstruktur", "Compiler", "API", "REST", "GraphQL",
        "Microservices", "Cloud-Computing", "Cybersecurity", "Blockchain", "DevOps",
    ),
    blocked_patterns=(
        "datenschutz", "datenpanne", "vorratsdatenspeicherung",
        "gesundheitsdaten", "personenbezogen", "kirchlicher datenschutz",
        "sozialdatenschutz",
        "kapitalmarkt", "wertpapier", "kapitalanlage", "aktienkapital",
        "sicherheitsventil", "sicherheitsgurt", "verkehrssicherheit",
        "fahrsicherheit", "arbeitssicherheit", "lebensmittelsicherheit",
        "flugsicherheit", "reaktorsicherheit", "kernsicherheit",
        "produktsicherheit", "sicherheitspolizei", "sicherheitsdienst",
        "sicherheitsrat", "innere sicherheit", "soziale sicherheit",
        "pythons", "pythonschlange", "rautenpython", "tigerpython",
        "königspython", "baumpython", "wasserpython", "pythonoidea",
        "monty python", "monty_python",
        "papier", "papierfabrik", "sicherheitsdruck",
    ),
    source_boosts={
        "user:": 1.22,
        "/projects/": 1.20,
        "stackexchange:": 1.17,
        "owasp": 1.16,
        "manpages": 1.18,
        "stackoverflow": 1.15,
        "rfcs": 1.14,
        "arch-wiki": 1.13,
        "official-docs": 1.12,
        "tldr-pages": 1.12,
        "mdn-web-docs/files": 1.10,
        "linux-docs": 1.10,
        "gutenberg": 0.85,
        "wikipedia": 1.0,
        "processed": 1.02,
    },
)

DOMAIN_MEDICAL = KnowledgeDomain(
    id="medical",
    name="Medizin & Gesundheit",
    description="Anatomie, Pharmakologie, Diagnostik, Therapie, Pflege",
    content_signals=(
        "patient", "diagnose", "therapie", "medikament", "symptom",
        "chirurgie", "anästhesie", "pathologie", "radiologie",
        "kardiologie", "neurologie", "onkologie", "pädiatrie",
        "pharmazie", "anatomie", "physiologie", "epidemiologie",
        "klinisch", "ambulant", "stationär", "labor", "befund",
        "prognose", "indikation", "kontraindikation", "dosierung",
        "nebenwirkung", "wirkstoff", "impfung", "hygiene",
        "medical", "clinical", "diagnosis", "treatment", "surgery",
    ),
    wiki_keywords=(
        "Medizin", "Krankheit", "Therapie", "Chirurgie", "Anatomie",
        "Pharmakologie", "Diagnostik", "Pathologie", "Klinisch",
        "Neurologie", "Kardiologie", "Onkologie", "Epidemiologie",
    ),
    wiki_seed_topics=(
        "Medizin", "Anatomie", "Pharmakologie", "Chirurgie",
        "Innere_Medizin", "Neurologie", "Kardiologie", "Onkologie",
        "Pathologie", "Radiologie", "Notfallmedizin", "Allgemeinmedizin",
    ),
    blocked_patterns=(
        "tierhaltung", "veterinär", "tiermedizin",
    ),
    source_boosts={
        "user:": 1.22,
        "/projects/": 1.20,
        "pubmed": 1.18,
        "amboss": 1.16,
        "wikipedia": 1.05,
    },
)

DOMAIN_LAW = KnowledgeDomain(
    id="law",
    name="Recht & Jura",
    description="Gesetze, Urteile, Vertragsrecht, Verwaltungsrecht",
    content_signals=(
        "gesetz", "verordnung", "urteil", "paragraph", "§",
        "rechtsanwalt", "kläger", "beklagter", "gericht",
        "strafrecht", "zivilrecht", "verwaltungsrecht", "bgb",
        "stgb", "grundgesetz", "verfassung", "richtlinie",
        "haftung", "schadenersatz", "vertrag", "klausel",
        "arbeitsgericht", "bundesgerichtshof", "revision",
        "law", "legal", "statute", "regulation", "court",
    ),
    wiki_keywords=(
        "Recht", "Gesetz", "Verordnung", "Gericht", "Strafrecht",
        "Zivilrecht", "Verwaltungsrecht", "Grundgesetz",
        "Europäisches_Recht", "Völkerrecht",
    ),
    wiki_seed_topics=(
        "Bürgerliches_Gesetzbuch", "Strafgesetzbuch", "Grundgesetz",
        "Verwaltungsverfahrensgesetz", "Handelsgesetzbuch",
        "Arbeitsrecht_(Deutschland)", "Europarecht", "Völkerrecht",
    ),
    blocked_patterns=(),
    source_boosts={
        "user:": 1.22,
        "dejure": 1.18,
        "gesetze-im-internet": 1.16,
        "wikipedia": 1.05,
    },
)

DOMAIN_SCIENCE = KnowledgeDomain(
    id="science",
    name="Naturwissenschaften",
    description="Physik, Chemie, Biologie, Mathematik",
    content_signals=(
        "physik", "chemie", "biologie", "mathematik", "formel",
        "experiment", "hypothese", "theorie", "molekül", "atom",
        "reaktion", "gleichung", "energie", "kraft", "welle",
        "spektrum", "evolution", "genetik", "zelle", "enzym",
        "integral", "differential", "statistik", "wahrscheinlichkeit",
        "physics", "chemistry", "biology", "equation", "molecule",
    ),
    wiki_keywords=(
        "Physik", "Chemie", "Biologie", "Mathematik",
        "Quantenmechanik", "Organische_Chemie", "Genetik",
        "Thermodynamik", "Algebra", "Analysis",
    ),
    wiki_seed_topics=(
        "Physik", "Chemie", "Biologie", "Mathematik",
        "Quantenmechanik", "Thermodynamik", "Organische_Chemie",
        "Molekularbiologie", "Genetik", "Statistik",
    ),
    blocked_patterns=(),
    source_boosts={
        "user:": 1.22,
        "arxiv": 1.18,
        "wikipedia": 1.08,
    },
)

DOMAIN_BUSINESS = KnowledgeDomain(
    id="business",
    name="Wirtschaft & Management",
    description="BWL, VWL, Finanzen, Marketing, Projektmanagement",
    content_signals=(
        "umsatz", "gewinn", "bilanz", "buchhaltung", "controlling",
        "marketing", "vertrieb", "strategie", "management",
        "investition", "rendite", "cashflow", "kalkulation",
        "projektmanagement", "geschäftsmodell", "startup",
        "supply chain", "logistik", "qualitätsmanagement",
        "revenue", "profit", "business", "finance", "accounting",
    ),
    wiki_keywords=(
        "Betriebswirtschaftslehre", "Volkswirtschaftslehre",
        "Management", "Marketing", "Projektmanagement",
        "Rechnungswesen", "Controlling", "Finanzwirtschaft",
    ),
    wiki_seed_topics=(
        "Betriebswirtschaftslehre", "Volkswirtschaftslehre",
        "Marketing", "Projektmanagement", "Controlling",
        "Rechnungswesen", "Supply-Chain-Management",
    ),
    blocked_patterns=(),
    source_boosts={
        "user:": 1.22,
        "wikipedia": 1.05,
    },
)

DOMAIN_UNIVERSAL = KnowledgeDomain(
    id="universal",
    name="Allgemeinwissen",
    description="Domänenübergreifend – keine thematische Einschränkung",
    content_signals=(),
    wiki_keywords=(
        "Wissenschaft", "Geschichte", "Geographie", "Kultur", "Technik",
        "Gesellschaft", "Philosophie", "Kunst", "Musik", "Literatur",
        "Politik", "Wirtschaft", "Natur", "Physik", "Chemie", "Biologie",
        "Mathematik", "Medizin", "Recht", "Psychologie", "Soziologie",
        "Informatik", "Architektur", "Religion", "Sport", "Bildung",
    ),
    wiki_seed_topics=(
        # Naturwissenschaften
        "Physik", "Chemie", "Biologie", "Mathematik", "Astronomie",
        "Geologie", "Ökologie", "Evolution", "Genetik", "Quantenmechanik",
        # Technik & IT
        "Informatik", "Elektrotechnik", "Maschinenbau", "Künstliche_Intelligenz",
        "Internet", "Programmiersprache", "Robotik", "Nanotechnologie",
        # Geschichte & Gesellschaft
        "Weltgeschichte", "Philosophie", "Soziologie", "Psychologie",
        "Demokratie", "Menschenrechte", "Vereinte_Nationen", "Europäische_Union",
        # Kultur & Kunst
        "Kunst", "Musik", "Literatur", "Film", "Architektur_(Baukunst)",
        "Theater", "Fotografie",
        # Medizin & Gesundheit
        "Medizin", "Anatomie", "Pharmakologie", "Epidemiologie",
        # Wirtschaft & Recht
        "Volkswirtschaftslehre", "Betriebswirtschaftslehre", "Recht",
        # Geographie & Natur
        "Geographie", "Klimawandel", "Ozean", "Kontinent",
        # Bildung
        "Bildung", "Universität", "Forschung",
    ),
    blocked_patterns=(
        "vorlage:webachiv", "vorlage:toter link", "vorlage:navigationsleiste",
        "diskussion:", "kategorie:", "portal:",
    ),
    source_boosts={
        "user:": 1.22,
        "/projects/": 1.20,
        "wikipedia": 1.05,
    },
)

# ─── Registry ───

ALL_DOMAINS: dict[str, KnowledgeDomain] = {
    d.id: d for d in (
        DOMAIN_IT, DOMAIN_MEDICAL, DOMAIN_LAW,
        DOMAIN_SCIENCE, DOMAIN_BUSINESS, DOMAIN_UNIVERSAL,
    )
}

DEFAULT_DOMAIN_ID = "universal"


def get_domain(domain_id: str) -> KnowledgeDomain:
    return ALL_DOMAINS.get(domain_id, DOMAIN_UNIVERSAL)


def get_active_domains(domain_ids: list[str]) -> list[KnowledgeDomain]:
    return [get_domain(d) for d in domain_ids if d in ALL_DOMAINS]


def merged_content_signals(domains: list[KnowledgeDomain]) -> tuple[str, ...]:
    seen = set()
    result = []
    for d in domains:
        for s in d.content_signals:
            if s not in seen:
                seen.add(s)
                result.append(s)
    return tuple(result)


def merged_blocked_patterns(domains: list[KnowledgeDomain]) -> tuple[str, ...]:
    seen = set()
    result = []
    for d in domains:
        for b in d.blocked_patterns:
            if b not in seen:
                seen.add(b)
                result.append(b)
    return tuple(result)


def merged_wiki_keywords(domains: list[KnowledgeDomain]) -> tuple[str, ...]:
    seen = set()
    result = []
    for d in domains:
        for k in d.wiki_keywords:
            if k not in seen:
                seen.add(k)
                result.append(k)
    return tuple(result)


def merged_source_boosts(domains: list[KnowledgeDomain]) -> dict[str, float]:
    merged: dict[str, float] = {}
    for d in domains:
        for pattern, boost in d.source_boosts.items():
            if pattern not in merged or boost > merged[pattern]:
                merged[pattern] = boost
    return merged
