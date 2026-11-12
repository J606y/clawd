"""Panel cookie auth. Hash matches the existing frontend; no nginx dependency."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

SALT = "hermes-panel:v2:"
COOKIE = "hp_auth"
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
RENEW_MAX_AGE = 30 * 24 * 3600
UNSAFE = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def credential_hash(user: str, password: str) -> str:
    normalized = user.strip().lower()
    payload = f"{SALT}{normalized}\n{password}".encode()
    return hashlib.sha256(payload).hexdigest()


def auth_path(home: Path) -> Path:
    return home / "panel" / "auth.json"


def load_hashes(home: Path) -> list[str]:
    path = auth_path(home)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    users = raw.get("users") if isinstance(raw, dict) else None
    if not isinstance(users, list):
        return []
    hashes: list[str] = []
    for item in users:
        if not isinstance(item, dict):
            continue
        digest = item.get("hash")
        if isinstance(digest, str) and HASH_RE.fullmatch(digest):
            hashes.append(digest)
    return hashes


def add_user(home: Path, user: str, password: str) -> None:
    name = user.strip().lower()
    if not name or not password or any(ord(c) < 32 for c in name):
        raise ValueError("user and password are required")
    digest = credential_hash(name, password)
    path = auth_path(home)
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    users: list[dict[str, str]] = []
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("auth store is unreadable") from exc
        existing = raw.get("users") if isinstance(raw, dict) else None
        if isinstance(existing, list):
            users = [item for item in existing if isinstance(item, dict) and item.get("user") != name]
    users.append({"user": name, "hash": digest})
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"users": users}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def cookie_value(header: str | None) -> str:
    if not header:
        return ""
    for part in header.split(";"):
        name, _, value = part.strip().partition("=")
        if name == COOKIE:
            return value.strip()
    return ""


def hash_allowed(presented: str, allowed: list[str]) -> bool:
    if not HASH_RE.fullmatch(presented):
        return False
    return any(hmac.compare_digest(presented, item) for item in allowed)


_FETCH_OK = frozenset({"same-origin", "same-site", "none"})
_LOOPBACK_NAMES = frozenset({"127.0.0.1", "localhost", "::1"})
_ALIAS_SEEN = frozenset({"127.0.0.1", "localhost"})
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_NETLOC_BAD = set(" \t\r\n@\\/?#,%;*\"'<>")


def _header_values(headers: Any, name: str) -> list[str] | None:
    """One header's values. None means the map itself is unusable."""
    if headers is None:
        return []
    getall = getattr(headers, "getall", None)
    raw: list[Any]
    if callable(getall):
        try:
            found = getall(name, [])
        except TypeError:
            try:
                found = getall(name)
            except KeyError:
                return []
            except Exception:
                return None
        except Exception:
            return None
        if found is None:
            raw = []
        elif isinstance(found, str):
            raw = [found]
        else:
            try:
                raw = list(found)
            except TypeError:
                return None
    elif hasattr(headers, "items"):
        raw = []
        try:
            pairs = headers.items()
        except Exception:
            return None
        for key, value in pairs:
            if isinstance(key, str) and key.lower() == name.lower():
                raw.append(value)
    else:
        getter = getattr(headers, "get", None)
        if not callable(getter):
            return None
        try:
            found = getter(name)
        except Exception:
            return None
        raw = [] if found is None else [found]
    values: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            return None
        values.append(item)
    return values


def _canonical_ip(text: str) -> str | None:
    try:
        parsed = ipaddress.ip_address(text)
    except ValueError:
        return None
    mapped = getattr(parsed, "ipv4_mapped", None)
    if mapped is not None:
        parsed = mapped
    return str(parsed)


def _canonical_name(hostname: str) -> str | None:
    if not hostname or any(char in hostname for char in "%[]"):
        return None
    if hostname.endswith(".."):
        return None
    text = hostname[:-1] if hostname.endswith(".") else hostname
    text = text.lower()
    ip = _canonical_ip(text)
    if ip is not None:
        return ip
    if ":" in text or not text or len(text) > 253 or ".." in text:
        return None
    labels = text.split(".")
    if labels[-1].isdigit():
        return None
    if any(_DNS_LABEL.fullmatch(label) is None for label in labels):
        return None
    return text


def _netloc_ok(netloc: str) -> bool:
    if not netloc or len(netloc) > 512 or netloc != netloc.strip():
        return False
    if any(char in _NETLOC_BAD or ord(char) < 33 or ord(char) == 127 for char in netloc):
        return False
    if netloc.startswith("["):
        end = netloc.find("]")
        if end <= 1 or netloc.count("[") != 1 or netloc.count("]") != 1:
            return False
        inner = netloc[1:end]
        if not inner or "%" in inner:
            return False
        try:
            ipaddress.IPv6Address(inner)
        except ValueError:
            return False
        rest = netloc[end + 1:]
        if rest == "":
            return True
        if not rest.startswith(":") or not rest[1:].isdigit():
            return False
        port = int(rest[1:])
        return 1 <= port <= 65535
    if "[" in netloc or "]" in netloc or netloc.count(":") > 1:
        return False
    host, sep, port = netloc.partition(":")
    if not host:
        return False
    if not sep:
        return True
    if not port.isdigit():
        return False
    return 1 <= int(port) <= 65535


def _authority_from_netloc(netloc: str, scheme: str | None) -> tuple[str, int | None, str] | None:
    if not _netloc_ok(netloc):
        return None
    try:
        parsed = urlsplit("//" + netloc)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.netloc != netloc
        or not parsed.hostname
    ):
        return None
    name = _canonical_name(parsed.hostname)
    if name is None or port == 0:
        return None
    if scheme == "http" and port == 80:
        port = None
    elif scheme == "https" and port == 443:
        port = None
    return name, port, scheme or ""


def _authority_from_url(value: str, *, allow_path: bool) -> tuple[str, int | None, str] | None:
    if not isinstance(value, str) or not value or len(value) > 2048 or value != value.strip():
        return None
    if any(ord(char) < 32 or ord(char) == 127 or char in "\\\t " for char in value):
        return None
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"}:
        return None
    if parsed.username is not None or parsed.password is not None or "%" in parsed.netloc:
        return None
    if not allow_path and (parsed.path not in {""} or parsed.query or parsed.fragment):
        return None
    if allow_path and parsed.fragment:
        return None
    return _authority_from_netloc(parsed.netloc, parsed.scheme)


def _hosts_match(host: str, seen: str) -> bool:
    if not host or not seen:
        return False
    if seen == host:
        return True
    return host in _LOOPBACK_NAMES and seen in _ALIAS_SEEN


def _ports_compatible(left: int | None, right: int | None) -> bool:
    # Omitted port stays compatible. Case tests require Host:9 to match an
    # origin that omits the port; do not treat that omission as port 80.
    if left is None or right is None:
        return True
    return left == right


def _endpoint_match(
    host: tuple[str, int | None, str],
    seen: tuple[str, int | None, str],
) -> bool:
    return _hosts_match(host[0], seen[0]) and _ports_compatible(host[1], seen[1])


def _names_compatible(left: str, right: str) -> bool:
    if left == right:
        return True
    return left in _LOOPBACK_NAMES and right in _LOOPBACK_NAMES


def _loopback_peer(peer: str | None) -> bool:
    if not isinstance(peer, str) or not peer or len(peer) > 128 or peer != peer.strip():
        return False
    if any(ord(char) < 33 or ord(char) == 127 or char in "@/\\%," for char in peer):
        return False
    text = peer
    if text.startswith("[") and text.endswith("]") and text.count("[") == 1 and text.count("]") == 1:
        text = text[1:-1]
        if not text:
            return False
    return _canonical_ip(text) in {"127.0.0.1", "::1"}


def origin_allowed(method: str, headers: Any, peer: str | None) -> bool:
    """Reject a mismatched Origin on every method. Loopback may omit it on writes.

    Host is parsed as an authority, not split on the first colon, so ``[::1]``
    and ``[::1]:port`` yield hostname ``::1``. Invalid authority, userinfo, and
    repeated header values fail closed. An omitted port does not by itself
    fail a loopback alias match; two explicit conflicting ports do.
    """
    if not isinstance(method, str) or not method:
        return False
    method_name = method.upper()
    host_values = _header_values(headers, "Host")
    origin_values = _header_values(headers, "Origin")
    referer_values = _header_values(headers, "Referer")
    fetch_values = _header_values(headers, "Sec-Fetch-Site")
    alt_referer = _header_values(headers, "Referrer")
    parsed = (host_values, origin_values, referer_values, fetch_values, alt_referer)
    if any(item is None for item in parsed) or any(len(item) > 1 for item in parsed):
        return False
    if alt_referer and referer_values and alt_referer[0] != referer_values[0]:
        return False

    fetch_site = fetch_values[0] if fetch_values else ""
    if fetch_site and fetch_site not in _FETCH_OK:
        return False

    host = _authority_from_netloc(host_values[0], None) if host_values else None
    if host_values and host is None:
        return False

    origin_raw = origin_values[0] if origin_values else ""
    referer_raw = referer_values[0] if referer_values else ""
    if origin_raw and ("," in origin_raw or origin_raw.strip().lower() == "null"):
        return False
    origin = _authority_from_url(origin_raw, allow_path=False) if origin_raw else None
    if origin_raw and origin is None:
        return False
    referer = _authority_from_url(referer_raw, allow_path=True) if referer_raw else None
    if referer_raw and referer is None and (method_name in UNSAFE or origin is not None):
        return False

    if origin is not None:
        if host is None or not _endpoint_match(host, origin):
            return False
        if fetch_site == "same-origin" and host[0] != origin[0]:
            return False
        if referer_raw:
            if (
                referer is None
                or not _endpoint_match(host, referer)
                or not _names_compatible(origin[0], referer[0])
                or not _ports_compatible(origin[1], referer[1])
                or (origin[2] and referer[2] and origin[2] != referer[2])
            ):
                return False
        return True

    if fetch_site == "same-origin":
        # Browsers omit Origin on same-origin GET/HEAD requests.
        if method_name not in {"GET", "HEAD"}:
            return False
        return bool(host is not None and referer is not None and _endpoint_match(host, referer))
    if method_name in UNSAFE:
        if host is None:
            return False
        if referer_raw:
            return referer is not None and _endpoint_match(host, referer)
        return _loopback_peer(peer)
    return True


def _transport_peer(request: Any) -> str | None:
    """Socket peer only. Forwarding middleware can rewrite request.remote."""
    transport = getattr(request, "transport", None)
    if transport is None:
        return None
    getter = getattr(transport, "get_extra_info", None)
    if not callable(getter):
        return None
    try:
        peer = getter("peername")
    except Exception:
        return None
    if isinstance(peer, str):
        return peer
    if not peer:
        return None
    try:
        host = peer[0]
    except (TypeError, IndexError, KeyError):
        return None
    return host if isinstance(host, str) else None


def _real_tls(request: Any) -> bool:
    """This hop's TLS. A forwarded scheme is not enough."""
    scheme = getattr(request, "scheme", None)
    if scheme == "https" or getattr(request, "secure", None) is True:
        return True
    transport = getattr(request, "transport", None)
    if transport is None:
        return False
    getter = getattr(transport, "get_extra_info", None)
    if not callable(getter):
        return False
    try:
        return getter("sslcontext") is not None or getter("ssl_object") is not None
    except Exception:
        return False


def _strict_https_proto(values: list[str] | None) -> bool:
    """One X-Forwarded-Proto value, exactly https. Commas and repeats fail."""
    return values is not None and len(values) == 1 and values[0] == "https"


def _forwarded_allows_https(values: list[str] | None) -> bool:
    """Absent Forwarded is allowed. Comma, repeat, or non-https proto conflicts."""
    if values is None:
        return False
    if not values:
        return True
    if len(values) != 1:
        return False
    raw = values[0]
    if not isinstance(raw, str) or not raw or len(raw) > 512 or "," in raw:
        return False
    if any(ord(char) < 32 or ord(char) == 127 for char in raw):
        return False
    if '"' in raw or "'" in raw:
        return False
    protos: list[str] = []
    for param in raw.split(";"):
        piece = param.strip()
        if not piece:
            return False
        key, sep, token = piece.partition("=")
        if not sep:
            return False
        if key.strip().lower() != "proto":
            continue
        protos.append(token.strip())
    return not protos or protos == ["https"]


def request_is_https(request: Any) -> bool:
    """Whether hp_auth Set-Cookie should include Secure.

    Real TLS on this hop is true. Otherwise the socket peer — not
    request.remote — must pass _loopback_peer, X-Forwarded-Proto must be one
    exact https, and Forwarded must be absent or not conflict. Loopback alone
    is false. A non-loopback peer cannot make a forwarded header true.
    """
    if _real_tls(request):
        return True
    if not _loopback_peer(_transport_peer(request)):
        return False
    headers = getattr(request, "headers", None)
    if not _strict_https_proto(_header_values(headers, "X-Forwarded-Proto")):
        return False
    return _forwarded_allows_https(_header_values(headers, "Forwarded"))
