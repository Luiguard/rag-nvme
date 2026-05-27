"""Sichere Code-Generierung: Regeln + statische Prüfung von LLM-Ausgaben."""
from __future__ import annotations

import re
from dataclasses import dataclass

# Muster, die in generiertem Code nicht vorkommen dürfen
DANGEROUS_PATTERNS: list[tuple[str, str]] = [
    (r"\beval\s*\(", "eval() – beliebige Codeausführung"),
    (r"\bexec\s*\(", "exec() – beliebige Codeausführung"),
    (r"__import__\s*\(", "dynamischer Import um Validierung zu umgehen"),
    (r"pickle\.loads?\s*\(", "unsichere Deserialisierung (pickle)"),
    (r"yaml\.load\s*\([^)]*\)", "yaml.load ohne SafeLoader"),
    (r"subprocess\.[a-z]+\([^)]*shell\s*=\s*True", "subprocess mit shell=True"),
    (r"os\.system\s*\(", "os.system – Shell-Injection-Risiko"),
    (r"child_process\.exec\s*\(", "Node exec – Shell-Injection-Risiko"),
    (r"dangerouslySetInnerHTML", "XSS-Risiko (React innerHTML)"),
    (r"document\.write\s*\(", "XSS-Risiko (document.write)"),
    (r"innerHTML\s*=", "direktes innerHTML ohne Sanitizing"),
    (r"verify\s*=\s*False", "TLS-Zertifikatsprüfung deaktiviert"),
    (r"ssl\._create_unverified_context", "TLS-Verifikation umgangen"),
    (r"chmod\s+777", "unsichere Dateirechte"),
    (r"0o777|0o666", "übermäßig offene Unix-Rechte"),
    (r"password\s*=\s*['\"][^'\"]{3,}['\"]", "hardcodiertes Passwort"),
    (r"api[_-]?key\s*=\s*['\"][^'\"]+['\"]", "hardcodierter API-Key"),
    (r"secret[_-]?key\s*=\s*['\"][^'\"]+['\"]", "hardcodierter Secret-Key"),
    (r"BEGIN (RSA |OPENSSH )?PRIVATE KEY", "privater Schlüssel im Code"),
    (r"backdoor", "Backdoor-Verdacht"),
    (r"disable.*auth|auth.*bypass|skip.*auth", "Auth-Bypass-Verdacht"),
    (r"debug\s*=\s*True.*#.*prod|#.*nur.*test.*production", "Debug in Produktion"),
]

FORBIDDEN_INTENT_KEYWORDS = (
    "rm -rf /", "rm -rf /*", "drop database", "drop table",
    "forkbomb", ":(){ :|:& };:", "format c:", "overwrite system",
    "disable firewall", "exfiltrate", "keylogger", "rootkit",
    "reverse shell", "bind shell", "malware", "ransomware",
)


@dataclass
class SecurityFinding:
    severity: str  # critical | high | medium
    rule: str
    excerpt: str


def audit_text(text: str) -> list[SecurityFinding]:
    findings: list[SecurityFinding] = []
    for pattern, rule in DANGEROUS_PATTERNS:
        for m in re.finditer(pattern, text, re.IGNORECASE | re.MULTILINE):
            start = max(0, m.start() - 40)
            end = min(len(text), m.end() + 40)
            findings.append(
                SecurityFinding(
                    severity="critical" if "eval" in rule or "exec" in rule or "backdoor" in rule.lower() else "high",
                    rule=rule,
                    excerpt=text[start:end].replace("\n", " "),
                )
            )
    return findings


def audit_code_blocks(text: str) -> list[SecurityFinding]:
    """Prüft nur Code-Fences in LLM-Antworten."""
    blocks = re.findall(r"```[\w]*\n(.*?)```", text, re.DOTALL)
    findings: list[SecurityFinding] = []
    for block in blocks:
        findings.extend(audit_text(block))
    return findings or audit_text(text)


def is_safe_user_intent(text: str) -> bool:
    t = text.lower()
    return not any(kw in t for kw in FORBIDDEN_INTENT_KEYWORDS)


def format_findings(findings: list[SecurityFinding]) -> str:
    if not findings:
        return ""
    lines = ["\n⚠️ **Sicherheitsprüfung – Auffälligkeiten (bitte vor Nutzung beheben):**"]
    seen = set()
    for f in findings[:12]:
        key = (f.rule, f.excerpt[:60])
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"- [{f.severity.upper()}] {f.rule}\n  `{f.excerpt[:120]}…`")
    return "\n".join(lines)


SECURE_CODING_RULES = """
## Sicheres Coding (verbindlich)
- Keine Backdoors, versteckten Zugänge, Auth-Bypasses oder „Debug-Only“-Lücken in Produktionscode.
- Keine Secrets im Quellcode (Passwörter, API-Keys, Tokens) – nur Umgebungsvariablen/Secret-Stores.
- Eingaben immer validieren und escapen; SQL nur parametrisiert; keine String-Konkatenation für Queries.
- Kein eval/exec/pickle.loads auf untrusted Data; kein subprocess mit shell=True.
- Fehlerbehandlung ohne Stacktraces/Interna an Endnutzer; Logging ohne PII/Secrets.
- Principle of Least Privilege: minimale Rechte für Prozesse, Dateien, DB-User.
- Abhängigkeiten aktuell halten; bekannte CVEs vermeiden.
- OWASP Top 10 beachten (Injection, XSS, Broken Auth, SSRF, etc.).
"""

BEST_PRACTICES = """
## Best Practices
- Klare Struktur: kleine Funktionen, sprechende Namen, Typ-Hints wo sinnvoll.
- Tests für kritische Pfade; Edge Cases dokumentieren.
- Idempotenz und klare Fehlermeldungen bei CLI/API.
- Konfiguration externalisieren; 12-Factor-App für Services.
"""
