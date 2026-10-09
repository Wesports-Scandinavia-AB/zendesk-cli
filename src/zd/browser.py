"""
Sign in through a real browser, once, and take the session cookies out of it.

Zendesk's login page sits behind bot protection (a plain HTTP request to
/access/login gets 403) and most people sign in through Microsoft or Google
anyway, so there is no login form to fill in from code. Instead:

  1. Start Chrome (or Edge) with a profile of its own per instance, under
     %LOCALAPPDATA%\\zendesk-cli\\profiles\\<instance>, and nothing else: no
     DevTools, no flags. The profile is kept, so the SSO provider remembers
     you next time.
  2. The person signs in in that window and closes it. Nothing is typed by us.
  3. The same profile is started headless with DevTools and its last session
     restored, and the cookies are read out. Zendesk then says whether they
     belong to a real user.

The DevTools protocol speaks WebSocket. The standard library has no client, so a
minimal one is here: text frames, client masking, fragmented replies. Enough for
a handful of request/response calls to 127.0.0.1, nothing more.
"""

import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from . import ZdError

CANDIDATES = {
    "win32": [
        r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
        r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
        r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
        r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
        r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    ],
    "darwin": [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ],
}


def find_browser():
    env = os.environ.get("ZD_BROWSER")
    if env:
        return env
    for c in CANDIDATES.get(sys.platform, []):
        p = os.path.expandvars(c)
        if os.path.isfile(p):
            return p
    for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge"):
        p = shutil.which(name)
        if p:
            return p
    raise ZdError("Hittar varken Chrome eller Edge. Sätt ZD_BROWSER till sökvägen.")


def profile_dir(instance):
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "zendesk-cli" / "profiles" / instance


# ---- minimal WebSocket client -------------------------------------------------

class _WS:
    def __init__(self, url, timeout=15):
        u = urlparse(url)
        self.sock = socket.create_connection((u.hostname, u.port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((
            f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        self.buf = b""
        while b"\r\n\r\n" not in self.buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("DevTools closed the connection")
            self.buf += chunk
        head, self.buf = self.buf.split(b"\r\n\r\n", 1)
        status = head.split(b"\r\n", 1)[0]
        if b" 101 " not in status + b" ":
            raise ConnectionError(status.decode(errors="replace"))
        self.next_id = 0

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("DevTools closed the connection")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, text):
        data = text.encode()
        n = len(data)
        head = bytes([0x81])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self):
        message = b""
        while True:
            b0, b1 = self._read(2)
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            if b1 & 0x80:
                mask = self._read(4)
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(self._read(n)))
            else:
                payload = self._read(n)
            opcode = b0 & 0x0F
            if opcode == 8:
                raise ConnectionError("DevTools closed the connection")
            if opcode in (0, 1, 2):
                message += payload
                if b0 & 0x80:
                    return message.decode()

    def call(self, method, session_id=None, **params):
        self.next_id += 1
        mid = self.next_id
        msg = {"id": mid, "method": method, "params": params}
        if session_id:
            msg["sessionId"] = session_id
        self.send(json.dumps(msg))
        while True:
            msg = json.loads(self.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise ConnectionError(msg["error"].get("message"))
                return msg.get("result", {})

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


# ---- login ----------------------------------------------------------------------
#
# The window the person signs in in is a plain Chrome: a profile of its own and
# nothing else, no DevTools port, nothing attached. Zendesk sits behind
# Cloudflare, whose challenge says "you are a bot" to a browser that is being
# driven, and the honest fix is to not drive it.
#
# The cookies are read afterwards. Zendesk's session cookies have no expiry date,
# so a normal start of the profile deletes them; started with
# --restore-last-session, Chrome keeps them as it does for "continue where you
# left off". That start is headless, with DevTools, and only reads the cookie
# store; the tabs it restores are never attached to.

def _domain_matches(cookie_domain, host):
    d = cookie_domain.lstrip(".")
    return host == d or host.endswith("." + d)


def read_cookies(instance, host):
    """The cookies the profile holds for `host`, read from a headless start of it."""
    prof = profile_dir(instance)
    port_file = prof / "DevToolsActivePort"
    if port_file.exists():
        port_file.unlink()
    proc = subprocess.Popen(
        [find_browser(), "--headless=new", f"--user-data-dir={prof}", "--remote-debugging-port=0",
         "--no-first-run", "--no-default-browser-check", "--restore-last-session"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 30
        while not port_file.exists():
            if time.time() > deadline or proc.poll() is not None:
                raise ZdError("Profilen gick inte att öppna. Är inloggningsfönstret fortfarande öppet?")
            time.sleep(0.2)
        time.sleep(0.3)
        port, path = port_file.read_text().splitlines()[:2]
        ws = _WS(f"ws://127.0.0.1:{port.strip()}{path.strip()}")
        try:
            cookies = ws.call("Storage.getCookies").get("cookies", [])
            try:
                ws.call("Browser.close")
            except (ConnectionError, OSError):
                pass
        finally:
            ws.close()
    finally:
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    return {c["name"]: c["value"] for c in cookies if _domain_matches(c["domain"], host)}


def login(instance, host, is_signed_in, timeout=900, say=print):
    """Let the person sign in in a plain window, then read the session out of the profile.

    `is_signed_in(cookies)` returns the Zendesk user when the cookies are a real
    session, else None. Returns (cookies, user).
    """
    prof = profile_dir(instance)
    prof.mkdir(parents=True, exist_ok=True)
    started = time.time()
    proc = subprocess.Popen(
        [find_browser(), f"--user-data-dir={prof}", "--no-first-run", "--no-default-browser-check",
         f"https://{host}/agent"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    say(f"Logga in på {host} i webbläsarfönstret som öppnades. STÄNG FÖNSTRET när du ser Zendesk, "
        "så hämtas sessionen.")
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.terminate()
        raise ZdError("Fönstret var öppet i 15 minuter. Inget sparat; kör `zd login` igen.")
    if time.time() - started < 5:
        raise ZdError("Webbläsaren stängdes direkt. Är ett fönster med samma profil redan öppet? Stäng det och försök igen.")
    time.sleep(1)  # let the profile's lock go
    jar = read_cookies(instance, host)
    user = is_signed_in(jar) if jar else None
    if not user:
        raise ZdError("Ingen inloggad session i profilen efter att fönstret stängdes. "
                      "Kom du hela vägen in i Zendesk innan du stängde?")
    return jar, user
