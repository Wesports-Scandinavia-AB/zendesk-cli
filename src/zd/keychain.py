"""
The operating system's own keychain, one entry per Zendesk instance.

What is stored is a person's signed-in web session: the cookies Zendesk set when
they logged in. Anyone holding them IS that person in Zendesk until the session
expires. So they belong in that person's keychain, where the OS ties them to the
signed-in user, never in a file next to the code.

  Windows  Credential Manager, through advapi32 via ctypes. Generic credentials
           named `zendesk-cli:<instance>`, encrypted with DPAPI for the Windows user.
  macOS    The login keychain, through /usr/bin/security. Generic passwords with
           service `zendesk-cli` and account `<instance>`.

A Credential Manager blob holds at most 2560 bytes and Zendesk's cookies can be
larger, so a long value is split over `<instance>~1`, `<instance>~2`, ... with
the main entry saying how many parts there are. Callers never see the parts.

ON MACOS THE VALUE NEVER TOUCHES ARGV: the write goes through `security -i` with
the command on stdin and the value hex-encoded (-X).
"""

import json
import subprocess
import sys

from . import ZdError

SERVICE = "zendesk-cli"


def backend():
    if sys.platform == "win32":
        return "Windows Credential Manager"
    if sys.platform == "darwin":
        return "macOS-nyckelringen"
    return None


def _unsupported():
    raise ZdError(f"Ingen nyckelring för {sys.platform}. Använd ZD_SESSION-miljövariabeln.")


# ---- public -----------------------------------------------------------------

PART = 2000  # characters per entry; UTF-8 of ASCII cookies stays under 2560 bytes


def _raw_get(name):
    if sys.platform == "win32":
        return _win_get(name)
    if sys.platform == "darwin":
        return _mac_get(name)
    _unsupported()


def _raw_put(name, raw):
    if sys.platform == "win32":
        _win_put(name, raw)
    elif sys.platform == "darwin":
        _mac_put(name, raw)
    else:
        _unsupported()


def _raw_delete(name):
    if sys.platform == "win32":
        return _win_delete(name)
    if sys.platform == "darwin":
        return _mac_delete(name)
    _unsupported()


def _parts(raw):
    """How many parts a stored main entry says follow it, or 0 for a plain value."""
    if raw and raw.startswith('{"_parts":'):
        return json.loads(raw)["_parts"]
    return 0


def get(name):
    """The stored value for one instance as a dict, or None."""
    raw = _raw_get(name)
    n = _parts(raw)
    if n:
        pieces = [_raw_get(f"{name}~{i}") for i in range(1, n + 1)]
        if any(p is None for p in pieces):
            raise ZdError(f"Nyckelringsposten för {name} är trasig. Kör `zd login {name}` igen.")
        raw = "".join(pieces)
    return json.loads(raw) if raw else None


def put(name, value):
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    old = _parts(_raw_get(name))
    chunks = [raw[i:i + PART] for i in range(0, len(raw), PART)]
    if len(chunks) == 1:
        _raw_put(name, raw)
    else:
        for i, c in enumerate(chunks, 1):
            _raw_put(f"{name}~{i}", c)
        _raw_put(name, json.dumps({"_parts": len(chunks)}))
    for i in range(len(chunks) + 1 if len(chunks) > 1 else 1, old + 1):
        _raw_delete(f"{name}~{i}")


def delete(name):
    """True if something was removed."""
    n = _parts(_raw_get(name))
    for i in range(1, n + 1):
        _raw_delete(f"{name}~{i}")
    return _raw_delete(name)


def names():
    """Every instance stored, sorted."""
    if sys.platform == "win32":
        found = _win_names()
    elif sys.platform == "darwin":
        found = _mac_names()
    else:
        return []
    return sorted(n for n in found if "~" not in n)


# ---- Windows ------------------------------------------------------------------

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    CRED_TYPE_GENERIC = 1
    # Despite the name this is per user: it survives logoff and stays on this
    # machine, where ENTERPRISE would roam with the profile.
    CRED_PERSIST_LOCAL_MACHINE = 2
    ERROR_NOT_FOUND = 1168

    class _CREDENTIAL(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    _PCRED = ctypes.POINTER(_CREDENTIAL)
    _advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    _advapi.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(_PCRED)]
    _advapi.CredReadW.restype = wintypes.BOOL
    _advapi.CredWriteW.argtypes = [_PCRED, wintypes.DWORD]
    _advapi.CredWriteW.restype = wintypes.BOOL
    _advapi.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    _advapi.CredDeleteW.restype = wintypes.BOOL
    _advapi.CredEnumerateW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                       ctypes.POINTER(ctypes.POINTER(_PCRED))]
    _advapi.CredEnumerateW.restype = wintypes.BOOL
    _advapi.CredFree.argtypes = [ctypes.c_void_p]
    _advapi.CredFree.restype = None


def _target(name):
    return f"{SERVICE}:{name}"


def _win_error(what):
    err = ctypes.get_last_error()
    return ZdError(f"Credential Manager: {what} misslyckades ({ctypes.FormatError(err).strip()})")


def _win_get(name):
    p = _PCRED()
    if not _advapi.CredReadW(_target(name), CRED_TYPE_GENERIC, 0, ctypes.byref(p)):
        if ctypes.get_last_error() == ERROR_NOT_FOUND:
            return None
        raise _win_error("läsning")
    try:
        c = p.contents
        return ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize).decode("utf-8")
    finally:
        _advapi.CredFree(p)


def _win_put(name, raw):
    blob = raw.encode("utf-8")
    buf = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
    c = _CREDENTIAL()
    c.Type = CRED_TYPE_GENERIC
    c.TargetName = _target(name)
    c.Comment = "zendesk-cli: Zendesk web session"
    c.CredentialBlobSize = len(blob)
    c.CredentialBlob = ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))
    c.Persist = CRED_PERSIST_LOCAL_MACHINE
    c.UserName = name
    if not _advapi.CredWriteW(ctypes.byref(c), 0):
        raise _win_error("skrivning")


def _win_delete(name):
    if _advapi.CredDeleteW(_target(name), CRED_TYPE_GENERIC, 0):
        return True
    if ctypes.get_last_error() == ERROR_NOT_FOUND:
        return False
    raise _win_error("borttagning")


def _win_names():
    count = wintypes.DWORD()
    creds = ctypes.POINTER(_PCRED)()
    if not _advapi.CredEnumerateW(f"{SERVICE}:*", 0, ctypes.byref(count), ctypes.byref(creds)):
        if ctypes.get_last_error() == ERROR_NOT_FOUND:
            return []
        raise _win_error("listning")
    try:
        prefix = SERVICE + ":"
        return [creds[i].contents.TargetName[len(prefix):] for i in range(count.value)]
    finally:
        _advapi.CredFree(creds)


# ---- macOS ----------------------------------------------------------------------

def _security(args, stdin=None):
    return subprocess.run(["/usr/bin/security", *args], input=stdin, capture_output=True, text=True)


def _mac_get(name):
    r = _security(["find-generic-password", "-s", SERVICE, "-a", name, "-w"])
    if r.returncode == 44:  # errSecItemNotFound
        return None
    if r.returncode:
        raise ZdError(f"Nyckelringen: läsning misslyckades ({r.stderr.strip()})")
    return r.stdout.rstrip("\n")


def _mac_put(name, raw):
    # -U updates an existing item in place. The account name is a slug we
    # validated, so it needs no quoting; the value goes hex-encoded.
    cmd = f"add-generic-password -U -s {SERVICE} -a {name} -X {raw.encode('utf-8').hex()}\n"
    r = _security(["-i"], stdin=cmd)
    if r.returncode or "error" in r.stderr.lower():
        raise ZdError(f"Nyckelringen: skrivning misslyckades ({r.stderr.strip()})")


def _mac_delete(name):
    r = _security(["delete-generic-password", "-s", SERVICE, "-a", name])
    if r.returncode == 44:
        return False
    if r.returncode:
        raise ZdError(f"Nyckelringen: borttagning misslyckades ({r.stderr.strip()})")
    return True


def _mac_names():
    """Attributes only — dump-keychain without -d never decrypts or prompts."""
    r = _security(["dump-keychain"])
    out, acct, svce = [], None, None
    for line in r.stdout.splitlines() + ["keychain:"]:
        line = line.strip()
        if line.startswith("keychain:"):
            if svce == SERVICE and acct:
                out.append(acct)
            acct = svce = None
        elif line.startswith('"acct"<blob>="'):
            acct = line.split('="', 1)[1].rstrip('"')
        elif line.startswith('"svce"<blob>="'):
            svce = line.split('="', 1)[1].rstrip('"')
    return out
