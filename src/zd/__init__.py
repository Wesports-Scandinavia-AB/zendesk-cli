"""zd: Zendesk from the terminal, signed in as yourself.

No API token. `zd login` opens a browser once, you sign in the way you always do
(Microsoft, Google, password and 2FA), and the session cookies go to your OS
keychain. Every command after that is plain HTTPS against the same endpoints
Zendesk's own web UI calls, with exactly your permissions.

    from zd import session
    s = session.open_account("vartex")
    s.get("/api/v2/tickets/123.json")
"""

__version__ = "0.1.0"


class ZdError(Exception):
    """Anything the user can act on: no session, expired session, refused request."""
