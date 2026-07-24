"""``get_client_ip`` (app/utils/http.py) — the real-visitor-IP resolver used by the
public embed chat endpoint's per-IP rate limiting. Pure unit tests: no DB, no network,
a hand-built Starlette ``Request`` over a minimal ASGI scope.

Fixes a real gap found in review: ``request.client.host`` alone is the reverse proxy's
own address in any deployment fronted by one, collapsing per-IP rate limiting into one
shared bucket for every visitor behind it. These tests pin the fix: X-Forwarded-For is
trusted ONLY when the direct peer is a configured trusted proxy, never otherwise.
"""

from __future__ import annotations

from starlette.requests import Request

from app.config.settings import settings
from app.utils.http import get_client_ip


def _make_request(*, client_host: str | None, forwarded_for: str | None = None) -> Request:
    headers = []
    if forwarded_for is not None:
        headers.append((b"x-forwarded-for", forwarded_for.encode()))
    scope = {
        "type": "http",
        "headers": headers,
        "client": (client_host, 12345) if client_host is not None else None,
    }
    return Request(scope)


def test_no_trusted_proxies_configured_returns_direct_peer(monkeypatch) -> None:
    monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", "")
    req = _make_request(client_host="203.0.113.5", forwarded_for="198.51.100.9")
    assert get_client_ip(req) == "203.0.113.5"


def test_direct_peer_not_in_trusted_list_ignores_forwarded_header(monkeypatch) -> None:
    """A client that connects directly and forges X-Forwarded-For must never be
    trusted — its own peer address isn't one of the configured proxies."""
    monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", "10.0.0.1")
    req = _make_request(client_host="203.0.113.5", forwarded_for="1.2.3.4")
    assert get_client_ip(req) == "203.0.113.5"


def test_trusted_proxy_peer_reads_real_ip_from_forwarded_header(monkeypatch) -> None:
    monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", "10.0.0.1")
    req = _make_request(client_host="10.0.0.1", forwarded_for="198.51.100.9")
    assert get_client_ip(req) == "198.51.100.9"


def test_trusted_proxy_peer_with_no_forwarded_header_falls_back_to_direct_peer(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", "10.0.0.1")
    req = _make_request(client_host="10.0.0.1", forwarded_for=None)
    assert get_client_ip(req) == "10.0.0.1"


def test_multi_hop_chain_returns_first_untrusted_hop_from_the_right(monkeypatch) -> None:
    """X-Forwarded-For reads left-to-right as client -> hop1 -> hop2 -> ... -> us.
    Walking from the right (nearest hop first) and skipping trusted proxies finds the
    real client even through a multi-hop trusted chain."""
    monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", "10.0.0.1,10.0.0.2")
    req = _make_request(client_host="10.0.0.2", forwarded_for="198.51.100.9, 10.0.0.1")
    assert get_client_ip(req) == "198.51.100.9"


def test_multiple_trusted_ips_comma_separated_with_whitespace(monkeypatch) -> None:
    monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", " 10.0.0.1 , 10.0.0.2 ")
    req = _make_request(client_host="10.0.0.2", forwarded_for="198.51.100.9")
    assert get_client_ip(req) == "198.51.100.9"


def test_no_client_on_scope_returns_unknown(monkeypatch) -> None:
    monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", "")
    req = _make_request(client_host=None)
    assert get_client_ip(req) == "unknown"
