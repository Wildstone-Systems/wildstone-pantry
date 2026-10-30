#!/usr/bin/env python3
"""Fail if tracked files contain deployment or personal details unfit for a public repo.

Looks for things secret scanners miss: private/Tailscale IPs, tailnet names,
home-directory paths and committed env/key files. Documentation-style
addresses used by tests and ADRs are allowlisted explicitly below.

Usage: python scripts/check_publication_hygiene.py   (exit 1 on findings)
"""

from __future__ import annotations

import hashlib
import logging
import re
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("publication_hygiene")

ROOT = Path(__file__).resolve().parent.parent

PATTERNS: dict[str, re.Pattern[str]] = {
    "private-ipv4": re.compile(
        r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3})\b"
    ),
    "tailscale-cgnat-ip": re.compile(
        r"\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b"
    ),
    "tailnet-name": re.compile(r"\btail[0-9a-f]{4,}\.ts\.net\b", re.IGNORECASE),
    "dynamic-dns": re.compile(r"\b[a-z0-9-]+\.duckdns\.org\b", re.IGNORECASE),
    "home-directory": re.compile(
        r"(?:/home/(?!user\b|runner\b|node\b|app\b)[a-z][a-z0-9_-]+|/Users/[A-Za-z][\w-]+|[A-Za-z]:\\Users\\[^\\\s]+)"
    ),
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
}

# Documentation / fixture values that look private but identify nothing real.
ALLOWED_VALUES = {
    "10.0.0.0",  # RFC 1918 range boundary quoted in SSRF guards and ADRs
    "172.16.0.0",
    "192.168.0.0",
    "100.64.0.0",  # CGNAT range boundary in the SSRF guard and ADR
}

# Exact reviewed lines containing synthetic test addresses. Hashes keep their
# documented fixture values out of scan output and fail closed if a line changes.
ALLOWED_FIXTURE_LINE_HASHES = {
    # Synthetic RFC1918 origins exercise LAN acceptance, not deployment addresses.
    "backend/tests/test_classifier_shadow.py": {
        "f4ac29a7517b35e9b807532642fc457df0fc81fd202dd8f2732bd847ceffe234",
        "8ec0a3f62556085496fd92a50a9fd8663f8e6988edf21f45544aec5d705394c6",
        "e5fc50edd2fb07ab3abc6727f3a3c4ddf41ce2f938de279d8f3acc777889fca9",
        "81e0203d69e346b419950912b4df931a3d874101585d5d5a28a781a7df63c8d8",
    },
    "backend/tests/test_agent_ingest.py": {
        "526a433e39a7874b5866d9ae91ef8e01f5d82374684107c1bf10d49704b3fd13",
        "377bc61359e81cc5ea0be6dc6b3ee742a52cedcceafcd82946a638930ee16914",
    },
    "backend/tests/test_protected_env_credentials.py": {
        "36e0a93bc87bd8ba3526eed1851cc7277cf2f389130a79e0ac889b18040231bc",
    },
    "backend/tests/test_wildstone_integration.py": {
        "6d3f97b4051574d67dce30b202c3937933b3b18b815ff5b3d30c67e9a425d0c7",
        "dded144b0498e7d28792f04c9760ec7eb2427ac87aad4199e282f45dc288cc3d",
        "0ff01c450b5628302352527557e1770bc4c29919687eafa3c0eb70028a2e0ae5",
        "380d99a4619b2a96f30dc3004ff38fdc425bf412b2294cc79bfdcc75de1c9bb5",
    },
}

EXAMPLE_EMAIL_DOMAINS = {
    "example.com", "example.org", "example.test", "pantryos.local", "example.lan",
    "home.local", "else.local", "b.local", "x.com",
    "users.noreply.github.com",
}
RECEIPT_EMAIL_DOMAINS = {
    "kroger.com", "walmart.com", "target.com", "costco.com",
    "amazon.com", "instacart.com", "samsclub.com", "wholefoods.com",
    "traderjoes.com", "aldi.com",
}
RECEIPT_EMAIL_PATHS = {
    "backend/modules/shopping/services/email_receipt_fetcher.py",
    "backend/tests/test_purchase_connectors.py",
}

SENSITIVE_FILENAMES = re.compile(
    r"(?:^|/)(?:\.env(?!\.example$)(?:\..+)?|id_rsa|id_ed25519|kubeconfig|\.htpasswd|\.npmrc|\.pypirc)$"
    r"|\.(?:pem|key|p12|pfx|jks|sqlite3?|db)$"
)

SKIP_SUFFIXES = {
    ".png",
    ".ico",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".woff",
    ".woff2",
    ".ttf",
}
SKIP_FILES = {"frontend/package-lock.json", "scripts/check_publication_hygiene.py"}


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout
    return [p for p in out.split("\0") if p]


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    findings: list[str] = []
    for rel in tracked_files():
        if SENSITIVE_FILENAMES.search(rel):
            findings.append(f"{rel}: sensitive filename is tracked")
        if rel in SKIP_FILES or Path(rel).suffix.lower() in SKIP_SUFFIXES:
            continue
        try:
            text = (ROOT / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            line_hash = hashlib.sha256(line.encode("utf-8")).hexdigest()
            if line_hash in ALLOWED_FIXTURE_LINE_HASHES.get(rel, set()):
                continue
            for name, pattern in PATTERNS.items():
                for match in pattern.finditer(line):
                    if match.group(0) in ALLOWED_VALUES:
                        continue
                    if name == "email":
                        domain = match.group(0).rsplit("@", 1)[-1].lower()
                        if domain in EXAMPLE_EMAIL_DOMAINS or domain.endswith(".example.com"):
                            continue
                        if domain in RECEIPT_EMAIL_DOMAINS and rel in RECEIPT_EMAIL_PATHS:
                            continue
                        if rel == "launcher/src-tauri/tauri.conf.json" and match.group(0).endswith("@2x.png"):
                            continue
                    # Report location and category only; never echo the value.
                    findings.append(f"{rel}:{lineno}: {name}")
    for finding in findings:
        logger.error(finding)
    if findings:
        logger.error(
            "%d publication-hygiene finding(s). Replace with placeholders like <household-host>.",
            len(findings),
        )
        return 1
    logger.info("publication hygiene: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
