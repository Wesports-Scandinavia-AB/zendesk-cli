"""
A signed-in Zendesk web session, used over plain HTTPS.

Zendesk's agent workspace and Admin Center call the same `/api/v2/...` endpoints
the public API documents, and a few private ones, authenticated by the session
cookie. Reads need nothing more. Writes also need `X-CSRF-Token`, which Zendesk
hands to a signed-in session as `authenticity_token` on `/api/v2/users/me.json`.

One keychain entry per instance:

    {"subdomain": "vartex", "cookies": {"_zendesk_shared_session": "...", ...},
     "user": {"id": 1, "name": "...", "email": "...", "role": "admin"},
     "saved": "2026-10-09T10:00:00"}

Zendesk rotates cookies as a session is used. Each response's Set-Cookie is
taken in, and `save()` writes the jar back when something changed, so a session
that keeps being used keeps sliding forward the way it does in a browser.

For CI or a one-off script, `ZD_SESSION` with the same JSON goes before the
keychain.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from http.cookies import SimpleCookie

from . import ZdError, __version__, keychain

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
UA = f"zendesk-cli/{__version__} (+https://github.com/Wesports-Scandinavia-AB/zendesk-cli)"


def host_for(subdomain):
    return subdomain if "." in subdomain else f"{subdomain}.zendesk.com"


def _request(host, cookies, method, path, body=None, headers=None, timeout=60):
    """One HTTP round trip. Returns (status, headers, parsed body or text)."""
    url = path if path.startswith("https://") else f"https://{host}{path}"
    data = None
    h = {"User-Agent": UA, "Accept": "application/json",
         "Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items())}
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status, hdrs, raw = r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        status, hdrs, raw = e.code, e.headers, e.read()
    except urllib.error.URLError as e:
        raise ZdError(f"Når inte {host}: {e.reason}")
    text = raw.decode("utf-8", errors="replace")
    try:
        parsed = json.loads(text) if text else None
    except ValueError:
        parsed = text
    return status, hdrs, parsed


def whoami(host, cookies):
    """The signed-in user for these cookies, or None for an anonymous session."""
    status, _, body = _request(host, cookies, "GET", "/api/v2/users/me.json")
    user = (body or {}).get("user") if status == 200 and isinstance(body, dict) else None
    return user if user and user.get("id") else None


class Session:
    def __init__(self, name, entry, persist=True):
        self.name = name
        self.subdomain = entry["subdomain"]
        self.host = host_for(self.subdomain)
        self.cookies = dict(entry["cookies"])
        self.user = entry.get("user") or {}
        self.entry = entry
        self.persist = persist
        self.changed = False
        self._csrf = None

    # ---- transport ----

    def _take_cookies(self, hdrs):
        for line in hdrs.get_all("Set-Cookie") or []:
            jar = SimpleCookie()
            try:
                jar.load(line)
            except Exception:
                continue
            for k, morsel in jar.items():
                if k not in self.cookies:
                    continue  # only keep the ones the session is made of
                expired = morsel["max-age"] == "0" or not morsel.value
                if not expired and self.cookies[k] != morsel.value:
                    self.cookies[k] = morsel.value
                    self.changed = True

    def csrf(self):
        if not self._csrf:
            status, _, body = _request(self.host, self.cookies, "GET", "/api/v2/users/me.json")
            user = (body or {}).get("user") or {} if isinstance(body, dict) else {}
            if not user.get("id"):
                raise self._expired()
            self._csrf = user.get("authenticity_token")
        return self._csrf

    def _expired(self):
        return ZdError(f"Sessionen för {self.name} har gått ut eller loggats ut. Kör `zd login {self.name}`.")

    def request(self, method, path, body=None, params=None, ok=(200, 201, 204)):
        method = method.upper()
        if params:
            path += ("&" if "?" in path else "?") + urllib.parse.urlencode(params, doseq=True)
        headers = {}
        if method not in ("GET", "HEAD"):
            headers["X-CSRF-Token"] = self.csrf()
            headers["X-Requested-With"] = "XMLHttpRequest"
        for attempt in range(4):
            status, hdrs, data = _request(self.host, self.cookies, method, path, body, headers)
            self._take_cookies(hdrs)
            if status == 429 and attempt < 3:
                wait = int(hdrs.get("Retry-After") or 10)
                print(f"Zendesk ber oss vänta {wait} s (429).", file=sys.stderr)
                time.sleep(min(wait, 120))
                continue
            break
        if status == 401:
            raise self._expired()
        if status not in ok:
            detail = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
            raise ZdError(f"{method} {path.split('?')[0]} gav {status}: {(detail or '')[:500]}")
        return data

    def get(self, path, **params):
        return self.request("GET", path, params=params or None)

    def paginate(self, path, key, limit=None, **params):
        """Every item under `key`, following cursor (links.next) or offset (next_page) paging."""
        out = []
        data = self.get(path, **params)
        while True:
            out.extend(data.get(key) or [])
            if limit and len(out) >= limit:
                return out[:limit]
            nxt = None
            if (data.get("meta") or {}).get("has_more"):
                nxt = (data.get("links") or {}).get("next")
            elif data.get("next_page"):
                nxt = data["next_page"]
            if not nxt:
                return out
            data = self.request("GET", nxt)

    def save(self):
        if self.persist and self.changed:
            self.entry["cookies"] = self.cookies
            keychain.put(self.name, self.entry)
            self.changed = False


# ---- accounts ---------------------------------------------------------------------

def accounts():
    return keychain.names()


def open_account(name=None):
    """The session for `name`; with no name, ZD_SESSION, ZD_ACCOUNT, or the only one stored."""
    env = os.environ.get("ZD_SESSION")
    if env and not name:
        entry = json.loads(env)
        return Session(entry.get("subdomain", "env"), entry, persist=False)
    name = name or os.environ.get("ZD_ACCOUNT")
    if not name:
        stored = keychain.names()
        if len(stored) == 1:
            name = stored[0]
        elif not stored:
            raise ZdError("Ingen inloggning sparad. Kör `zd login <namn>`, t.ex. `zd login vartex`.")
        else:
            raise ZdError(f"Flera instanser sparade ({', '.join(stored)}). Välj med -a NAMN eller ZD_ACCOUNT.")
    entry = keychain.get(name)
    if not entry:
        raise ZdError(f"Ingen inloggning för {name}. Kör `zd login {name}`.")
    return Session(name, entry)


def store_login(name, subdomain, cookies, user):
    entry = {
        "subdomain": subdomain,
        "cookies": cookies,
        "user": {k: user.get(k) for k in ("id", "name", "email", "role")},
        "saved": datetime.now().isoformat(timespec="seconds"),
    }
    keychain.put(name, entry)
    return entry
