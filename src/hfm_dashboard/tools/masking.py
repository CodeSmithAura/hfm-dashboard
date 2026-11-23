"""Mask real identifiers in recorded output before it can enter the repository (hard rule 1).

Every host in the environment is replaced by an invented name derived from its node key
(``web_node_01`` -> ``web-node-01.example.test``); its short name and DNS suffix are replaced too.
Extra values (such as the user name used for a development test) and every IPv4 address are
masked as well. ``leftovers`` re-checks the masked text so nothing is written if anything slipped.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import urlsplit

from hfm_dashboard.domain.environment import Environment

IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
IP_MASK = "<ip-masked>"
FAKE_DOMAIN = "example.test"


def build_masks(env: Environment, extra: Iterable[tuple[str, str]] = ()) -> list[tuple[str, str]]:
    """Return (real, replacement) pairs, longest real value first."""
    pairs: dict[str, str] = {}
    for node_key, node in env.nodes.items():
        fake_short = node_key.replace("_", "-")
        host = node.host.strip()
        pairs.setdefault(host.lower(), f"{fake_short}.{FAKE_DOMAIN}")
        if "." in host and not IPV4.fullmatch(host):
            short, suffix = host.split(".", 1)
            pairs.setdefault(short.lower(), fake_short)
            pairs.setdefault(suffix.lower(), FAKE_DOMAIN)
    lb_url = env.load_balancer.url if env.load_balancer else ""
    for url, fake_short in ((lb_url, "lb"), (env.weblogic.admin_url, "wls-admin")):
        host = (urlsplit(url).hostname or "").strip()
        if host:
            pairs.setdefault(host.lower(), f"{fake_short}.{FAKE_DOMAIN}")
            if "." in host and not IPV4.fullmatch(host):
                short, suffix = host.split(".", 1)
                pairs.setdefault(short.lower(), fake_short)
                pairs.setdefault(suffix.lower(), FAKE_DOMAIN)
    for real, replacement in extra:
        if real:
            pairs.setdefault(real.lower(), replacement)
    # A value that already appears in a replacement is itself invented (e.g. test configs using
    # example.test); masking it would be a no-op and would make ``leftovers`` always fail.
    replacements = set(pairs.values())
    kept = {real: rep for real, rep in pairs.items()
            if not any(_pattern(real).search(r) for r in replacements)}
    return sorted(kept.items(), key=lambda p: len(p[0]), reverse=True)


def _pattern(real: str) -> re.Pattern[str]:
    # Whole-token match, so a short host name never alters a longer word such as a service name.
    return re.compile(rf"(?<![A-Za-z0-9_-]){re.escape(real)}(?![A-Za-z0-9_-])", re.IGNORECASE)


def mask(text: str, pairs: list[tuple[str, str]]) -> str:
    for real, replacement in pairs:
        text = _pattern(real).sub(replacement, text)
    return IPV4.sub(IP_MASK, text)


def leftovers(text: str, pairs: list[tuple[str, str]]) -> list[str]:
    """Kinds of real values still present (never the values themselves)."""
    found = [f"masked value #{i + 1}" for i, (real, _) in enumerate(pairs) if _pattern(real).search(text)]
    if IPV4.search(text):
        found.append("IPv4 address")
    return found
