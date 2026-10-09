import argparse
import html
import json
import re
import sys
from pathlib import Path

from . import ZdError, browser, keychain, session

REPO = "Wesports-Scandinavia-AB/zendesk-cli"


def _dump(data):
    print(json.dumps(data, indent=2, ensure_ascii=False))


def _short(v, n=200):
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return s if len(s) <= n else s[:n - 1] + "…"


def _text(h):
    """Readable text out of Zendesk's HTML: line breaks kept, tags gone."""
    h = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</h\d>", "\n", h or "")
    h = re.sub(r"(?i)<li[^>]*>", "• ", h)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(re.sub(r"<[^>]+>", "", h))).strip()


def _read_json_arg(value):
    """JSON from the argument itself, from @file, or from - (stdin)."""
    if value == "-":
        return json.load(sys.stdin)
    if value.startswith("@"):
        return json.loads(Path(value[1:]).read_text(encoding="utf-8"))
    return json.loads(value)


def _dry(a, what):
    if a.apply:
        return False
    print(f"\nTorrkörning: {what} har inte skickats. Lägg till --apply för att spara.", file=sys.stderr)
    return True


def _diff(old, new):
    """Top-level keys of `new` whose value differs from `old`, as (key, old, new)."""
    return [(k, old.get(k), v) for k, v in new.items() if old.get(k) != v]


def _print_diff(rows):
    if not rows:
        print("Inga ändringar mot det som ligger i Zendesk nu.")
    for k, o, n in rows:
        print(f"  {k}:\n    nu:  {_short(o, 300)}\n    blir: {_short(n, 300)}")


# ---- login and accounts ----------------------------------------------------------

def cmd_login(a):
    if not session.NAME_RE.match(a.name):
        print("Namnet får bara innehålla a–z, 0–9 och bindestreck, t.ex. vartex.", file=sys.stderr)
        return 2
    subdomain = a.subdomain or (keychain.get(a.name) or {}).get("subdomain") or a.name
    host = session.host_for(subdomain)

    def signed_in(jar):
        # Store only Zendesk's own cookies when they are enough, else all of them.
        own = {k: v for k, v in jar.items() if k.startswith("_zendesk")}
        for cookies in (own, jar):
            if cookies:
                user = session.whoami(host, cookies)
                if user:
                    signed_in.cookies = cookies
                    return user
        return None

    _, user = browser.login(a.name, host, signed_in, say=lambda m: print(m, file=sys.stderr))
    session.store_login(a.name, subdomain, signed_in.cookies, user)
    print(f"Inloggad på {host} som {user.get('name')} <{user.get('email')}> ({user.get('role')}). "
          f"Sparat i {keychain.backend()} som {keychain.SERVICE}:{a.name}.")
    return 0


def cmd_logout(a):
    s = None
    entry = keychain.get(a.name)
    if entry:
        s = session.Session(a.name, entry, persist=False)
        try:
            s.request("DELETE", "/api/v2/users/me/logout.json")
        except ZdError:
            pass  # already gone on Zendesk's side; the local copy goes anyway
    removed = keychain.delete(a.name)
    print(f"{a.name}: {'utloggad och borttagen ur nyckelringen' if removed else 'fanns inte i nyckelringen'}.")
    if a.forget_browser:
        import shutil
        shutil.rmtree(browser.profile_dir(a.name), ignore_errors=True)
        print("Webbläsarprofilen borttagen; nästa inloggning börjar från noll.")
    return 0


def cmd_account_list(a):
    rows = []
    for name in session.accounts():
        e = keychain.get(name) or {}
        rows.append({"name": name, "host": session.host_for(e.get("subdomain", name)),
                     "user": (e.get("user") or {}).get("email"), "role": (e.get("user") or {}).get("role"),
                     "saved": e.get("saved")})
    if a.json:
        _dump(rows)
        return 0
    print(f"lagras i  {keychain.backend()}\n")
    for r in rows:
        print(f"{r['name']:<14} {r['host']:<28} {r['user'] or '-':<34} {r['role'] or '-':<8} inloggad {r['saved']}")
    if not rows:
        print("Inga. Kör `zd login <namn>`.")
    return 0


def cmd_whoami(a, s):
    me = s.get("/api/v2/users/me.json")["user"]
    if not me.get("id"):
        raise s._expired()
    me.pop("authenticity_token", None)
    if a.json:
        _dump(me)
    else:
        print(f"{me['name']} <{me['email']}>  roll={me['role']}  id={me['id']}  på {s.host}")
    return 0


# ---- raw api -------------------------------------------------------------------------

def cmd_api(a, s):
    method = a.method.upper()
    body = _read_json_arg(a.data) if a.data else None
    params = dict(p.split("=", 1) for p in a.param or [])
    if method != "GET":
        print(f"{method} https://{s.host}{a.path}", file=sys.stderr)
        if body is not None:
            print(json.dumps(body, indent=2, ensure_ascii=False), file=sys.stderr)
        if _dry(a, "anropet"):
            return 0
    if a.all and method == "GET":
        _dump(s.paginate(a.path, a.all, **params))
    else:
        _dump(s.request(method, a.path, body=body, params=params or None))
    return 0


# ---- tickets ---------------------------------------------------------------------------

def _ticket_rows(tickets):
    for t in tickets:
        print(f"{t['id']:>8}  {t.get('status', ''):<8} {(t.get('updated_at') or '')[:16].replace('T', ' ')}  "
              f"{_short(t.get('subject') or '', 80)}")


def cmd_tickets(a, s):
    if a.view:
        tickets = s.paginate(f"/api/v2/views/{a.view}/tickets.json", "tickets", limit=a.limit)
    else:
        q = ["type:ticket", a.query or "status<solved"]
        if a.mine:
            q.append("assignee:me")
        tickets = s.paginate("/api/v2/search.json", "results", limit=a.limit,
                             query=" ".join(q), sort_by="updated_at", sort_order="desc")
    if a.json:
        _dump(tickets)
    else:
        _ticket_rows(tickets)
    return 0


def cmd_views(a, s):
    views = s.paginate("/api/v2/views/active.json", "views")
    if a.json:
        _dump(views)
        return 0
    counts = {}
    ids = [v["id"] for v in views]
    for i in range(0, len(ids), 100):
        for c in s.get("/api/v2/views/count_many.json", ids=",".join(map(str, ids[i:i + 100]))).get("view_counts", []):
            counts[c["view_id"]] = c.get("pretty") or c.get("value")
    for v in views:
        print(f"{v['id']:>14}  {str(counts.get(v['id'], '')):>6}  {v['title']}")
    return 0


def _users(s, ids):
    out = {}
    ids = [i for i in set(ids) if i]
    for i in range(0, len(ids), 100):
        for u in s.get("/api/v2/users/show_many.json", ids=",".join(map(str, ids[i:i + 100]))).get("users", []):
            out[u["id"]] = u
    return out


def cmd_ticket_show(a, s):
    t = s.get(f"/api/v2/tickets/{a.id}.json")["ticket"]
    comments = s.paginate(f"/api/v2/tickets/{a.id}/comments.json", "comments")
    if a.json:
        _dump({"ticket": t, "comments": comments})
        return 0
    users = _users(s, [t.get("requester_id"), t.get("assignee_id")] + [c.get("author_id") for c in comments])

    def who(uid):
        u = users.get(uid) or {}
        return f"{u.get('name', uid)} <{u.get('email', '')}>" if uid else "-"

    print(f"#{t['id']}  {t.get('subject')}")
    print(f"status={t['status']}  prioritet={t.get('priority') or '-'}  kanal={t.get('via', {}).get('channel')}  "
          f"brand={t.get('brand_id')}  grupp={t.get('group_id')}")
    print(f"beställare  {who(t.get('requester_id'))}\nhandläggare {who(t.get('assignee_id'))}")
    print(f"taggar      {' '.join(t.get('tags') or []) or '-'}")
    print(f"skapad {t['created_at']}  uppdaterad {t['updated_at']}")
    print(f"https://{s.host}/agent/tickets/{t['id']}")
    for c in comments:
        if a.public and not c.get("public"):
            continue
        flag = "" if c.get("public") else "  [INTERN]"
        print(f"\n--- {c['created_at'][:16].replace('T', ' ')}  {who(c.get('author_id'))}{flag}")
        print(c.get("plain_body") or _text(c.get("html_body")) or c.get("body"))
        for f in c.get("attachments") or []:
            print(f"  📎 {f['file_name']} ({f.get('size', 0) // 1024} kB) {f['content_url']}")
    return 0


def cmd_ticket_reply(a, s):
    body = a.text if a.text is not None else Path(a.file).read_text(encoding="utf-8")
    if not body.strip():
        print("Tom text. Inget att skicka.", file=sys.stderr)
        return 2
    t = s.get(f"/api/v2/tickets/{a.id}.json")["ticket"]
    comment = {"public": not a.internal, ("html_body" if a.html else "body"): body}
    upd = {"comment": comment}
    if a.status:
        upd["status"] = a.status
    kind = "intern anteckning" if a.internal else "PUBLIKT SVAR till kunden"
    print(f"#{t['id']} {t.get('subject')}  (status {t['status']}{' -> ' + a.status if a.status else ''})")
    print(f"{kind} som {s.user.get('name')}:\n")
    print(body if not a.html else _text(body))
    if _dry(a, kind):
        return 0
    before = {c["id"] for c in s.paginate(f"/api/v2/tickets/{a.id}/comments.json", "comments")}
    s.request("PUT", f"/api/v2/tickets/{a.id}.json", body={"ticket": upd})
    after = s.paginate(f"/api/v2/tickets/{a.id}/comments.json", "comments")
    new = [c for c in after if c["id"] not in before]
    status = s.get(f"/api/v2/tickets/{a.id}.json")["ticket"]["status"]
    if len(new) != 1 or new[0].get("public") != (not a.internal) or (a.status and status != a.status):
        raise ZdError(f"Skickat, men ärendet ser inte ut som väntat: {len(new)} nya kommentarer, status {status}. Kontrollera i Zendesk.")
    print(f"\nSkickat. Kommentar {new[0]['id']}, status {status}.")
    return 0


def cmd_ticket_set(a, s):
    t = s.get(f"/api/v2/tickets/{a.id}.json")["ticket"]
    upd = {}
    for k in ("status", "priority", "type"):
        if getattr(a, k):
            upd[k] = getattr(a, k)
    if a.assignee:
        upd["assignee_id"] = s.user["id"] if a.assignee == "me" else (
            int(a.assignee) if a.assignee.isdigit() else _user_id(s, a.assignee))
    if a.group:
        upd["group_id"] = int(a.group)
    if a.add_tag:
        upd["additional_tags"] = a.add_tag
    if a.remove_tag:
        upd["remove_tags"] = a.remove_tag
    if not upd:
        print("Inget att ändra. Se `zd ticket set --help`.", file=sys.stderr)
        return 2
    print(f"#{t['id']} {t.get('subject')}")
    plain = {k: v for k, v in upd.items() if k not in ("additional_tags", "remove_tags")}
    _print_diff(_diff(t, plain))
    if a.add_tag:
        print(f"  taggar +: {' '.join(a.add_tag)}")
    if a.remove_tag:
        print(f"  taggar -: {' '.join(a.remove_tag)}")
    if _dry(a, "ändringen"):
        return 0
    s.request("PUT", f"/api/v2/tickets/{a.id}.json", body={"ticket": upd})
    now = s.get(f"/api/v2/tickets/{a.id}.json")["ticket"]
    wrong = [k for k, _, v in _diff(now, plain)]
    wrong += [f"+{x}" for x in a.add_tag or [] if x not in now.get("tags", [])]
    wrong += [f"-{x}" for x in a.remove_tag or [] if x in now.get("tags", [])]
    if wrong:
        raise ZdError(f"Sparat, men Zendesk visar inte allt: {', '.join(wrong)}. En trigger kan ha ändrat tillbaka.")
    print("Sparat och kontrollerat.")
    return 0


def _user_id(s, email):
    found = s.get("/api/v2/users/search.json", query=email).get("users", [])
    if len(found) != 1:
        raise ZdError(f"{email}: {len(found)} användare hittades, väntade exakt en.")
    return found[0]["id"]


# ---- configuration objects: macros, triggers, ... ------------------------------------

# kind -> (collection path, list key, single key)
KINDS = {
    "macros": ("/api/v2/macros", "macros", "macro"),
    "triggers": ("/api/v2/triggers", "triggers", "trigger"),
    "automations": ("/api/v2/automations", "automations", "automation"),
    "views": ("/api/v2/views", "views", "view"),
    "groups": ("/api/v2/groups", "groups", "group"),
    "brands": ("/api/v2/brands", "brands", "brand"),
    "ticket-fields": ("/api/v2/ticket_fields", "ticket_fields", "ticket_field"),
    "ticket-forms": ("/api/v2/ticket_forms", "ticket_forms", "ticket_form"),
    "user-fields": ("/api/v2/user_fields", "user_fields", "user_field"),
    "sla-policies": ("/api/v2/slas/policies", "sla_policies", "sla_policy"),
    "dynamic-content": ("/api/v2/dynamic_content/items", "items", "item"),
    "webhooks": ("/api/v2/webhooks", "webhooks", "webhook"),
}


def _title(o):
    return o.get("title") or o.get("name") or o.get("raw_title") or ""


def cmd_list(a, s):
    path, key, _ = KINDS[a.kind]
    items = s.paginate(f"{path}.json", key)
    if a.find:
        f = a.find.lower()
        items = [o for o in items if f in json.dumps(o, ensure_ascii=False).lower()]
    if a.json:
        _dump(items)
        return 0
    for o in items:
        active = "" if o.get("active", True) else "  (inaktiv)"
        print(f"{o['id']:>14}  {_title(o)}{active}")
    return 0


def cmd_show(a, s):
    path, _, one = KINDS[a.kind]
    _dump(s.get(f"{path}/{a.id}.json")[one])
    return 0


def cmd_update(a, s):
    path, _, one = KINDS[a.kind]
    new = _read_json_arg(a.data)
    new = new.get(one, new)
    old = s.get(f"{path}/{a.id}.json")[one]
    print(f"{a.kind[:-1] if a.kind.endswith('s') else a.kind} {a.id}: {_title(old)}")
    rows = _diff(old, new)
    _print_diff(rows)
    if not rows or _dry(a, "ändringen"):
        return 0
    s.request("PUT", f"{path}/{a.id}.json", body={one: dict((k, n) for k, _, n in rows)})
    now = s.get(f"{path}/{a.id}.json")[one]
    off = _diff(now, {k: n for k, _, n in rows})
    if off:
        print("Sparat, men Zendesk visar annat än det som skickades för:", file=sys.stderr)
        _print_diff(off)
        return 1
    print("Sparat och kontrollerat.")
    return 0


# ---- help center ----------------------------------------------------------------------

def _hc(a, s, path):
    """A Help Center path on the instance, or on another brand's host with --brand."""
    if getattr(a, "brand", None):
        return f"https://{session.host_for(a.brand)}{path}"
    return path


def cmd_hc_articles(a, s):
    if a.find:
        params = {"query": a.find}
        if a.locale:
            params["locale"] = a.locale
        items = s.paginate(_hc(a, s, "/api/v2/help_center/articles/search.json"), "results", limit=a.limit, **params)
    else:
        loc = f"/{a.locale}" if a.locale else ""
        items = s.paginate(_hc(a, s, f"/api/v2/help_center{loc}/articles.json"), "articles", limit=a.limit)
    if a.json:
        _dump(items)
        return 0
    for o in items:
        flag = "  (utkast)" if o.get("draft") else ""
        print(f"{o['id']:>14}  {o.get('locale', ''):<6} {(o.get('updated_at') or '')[:10]}  {o.get('title')}{flag}")
    return 0


def cmd_hc_article(a, s):
    loc = f"/{a.locale}" if a.locale else ""
    art = s.get(_hc(a, s, f"/api/v2/help_center{loc}/articles/{a.id}.json"))["article"]
    if a.json:
        _dump(art)
    elif a.html:
        print(art.get("body") or "")
    else:
        print(f"{art['title']}  ({art['locale']}, {'utkast' if art.get('draft') else 'publicerad'}, "
              f"uppdaterad {art['updated_at'][:10]})\n{art['html_url']}\n")
        print(_text(art.get("body")))
    return 0


def cmd_hc_update(a, s):
    path = _hc(a, s, f"/api/v2/help_center/articles/{a.id}/translations/{a.locale}.json")
    old = s.get(path)["translation"]
    new = {}
    if a.title:
        new["title"] = a.title
    if a.file:
        new["body"] = Path(a.file).read_text(encoding="utf-8")
    if a.draft is not None:
        new["draft"] = a.draft == "yes"
    if not new:
        print("Inget att ändra: ange --title, --file eller --draft.", file=sys.stderr)
        return 2
    print(f"Artikel {a.id} ({a.locale}): {old.get('title')}")
    rows = _diff(old, new)
    _print_diff([(k, o, n) if k != "body" else (k, f"{len(o or '')} tecken", f"{len(n)} tecken") for k, o, n in rows])
    if not rows or _dry(a, "ändringen"):
        return 0
    s.request("PUT", path, body={"translation": {k: n for k, _, n in rows}})
    now = s.get(path)["translation"]
    off = [k for k, _, n in rows if now.get(k) != n]
    if off:
        raise ZdError(f"Sparat, men Zendesk visar annat för: {', '.join(off)}. (Zendesk kan städa HTML; jämför med `zd hc article {a.id} --html`.)")
    print("Sparat och kontrollerat.")
    return 0


# ---- skill and update -------------------------------------------------------------

def _skill_text():
    from importlib.resources import files
    return files("zd").joinpath("skill/SKILL.md").read_text(encoding="utf-8")


def cmd_skill_show(a):
    print(_skill_text())
    return 0


def cmd_skill_install(a):
    target = Path(a.dir).expanduser() if a.dir else Path.home() / ".claude" / "skills" / "zendesk"
    target.mkdir(parents=True, exist_ok=True)
    (target / "SKILL.md").write_text(_skill_text(), encoding="utf-8")
    print(f"Skill installerad: {target / 'SKILL.md'}")
    return 0


def cmd_selfupdate(a):
    """Reinstall from GitHub, then refresh the skill with the NEW code (as e37-cli does)."""
    import shutil
    import subprocess
    from . import __version__
    url = (f"git+https://github.com/{REPO}" if shutil.which("git")
           else f"https://github.com/{REPO}/archive/refs/heads/main.zip")
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "--force-reinstall", "--no-deps",
           "--quiet", "--disable-pip-version-check", f"zendesk-cli @ {url}"]
    if sys.prefix == sys.base_prefix:
        cmd.insert(4, "--user")
    print(f"Hämtar senaste zendesk-cli (har {__version__}) ...", file=sys.stderr)
    if subprocess.run(cmd).returncode:
        print("Uppdateringen misslyckades, se pip-utskriften ovan.", file=sys.stderr)
        return 1
    new = subprocess.run([sys.executable, "-c", "import zd; print(zd.__version__)"],
                         capture_output=True, text=True).stdout.strip()
    subprocess.run([sys.executable, "-m", "zd", "skill", "install"])
    print(f"zendesk-cli {__version__} -> {new or '?'}")
    return 0


# ---- wiring ---------------------------------------------------------------------------

def _parser(sub, name, help, description=None, epilog=None):
    return sub.add_parser(name, help=help, description=description or help, epilog=epilog,
                          formatter_class=argparse.RawDescriptionHelpFormatter)


def _common(p, json_flag=True):
    p.add_argument("-a", "--account", metavar="NAME", help="which stored login (default: ZD_ACCOUNT, or the only one)")
    if json_flag:
        p.add_argument("--json", action="store_true", help="raw JSON")


def _apply(p):
    p.add_argument("--apply", action="store_true", help="actually send it; without this, a dry run")


def _with_session(fn):
    """Open the account's session, run, and write rotated cookies back afterwards."""
    def run(a):
        s = session.open_account(a.account)
        try:
            return fn(a, s)
        finally:
            s.save()
    return run


def main(argv=None):
    from . import __version__
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(
        prog="zd", formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Zendesk from the terminal, signed in as yourself: no API token. `zd login` opens a\n"
                    "browser once; after that everything is HTTPS with your session from the OS keychain.\n"
                    "Every command that writes is a dry run unless given --apply.\n\n"
                    "  login/logout  sign in through the browser / sign out and forget\n"
                    "  account   stored logins          whoami   who the session is\n"
                    "  tickets   list (search or view)  ticket   show, reply, set\n"
                    "  views     your views with counts\n"
                    "  list/show/update  macros, triggers, automations, views, groups, fields, ...\n"
                    "  hc        Help Center articles: articles, article, update\n"
                    "  api       any endpoint the web UI uses, as yourself\n"
                    "  skill     teach Claude how to use this tool\n"
                    "  selfupdate  get the latest version from GitHub",
        epilog="If you are an assistant: run `zd skill show` first. Never ask for a password or cookie\n"
               "in the chat; `zd login NAME` lets the person sign in in a browser window.")
    p.add_argument("--version", action="version", version=f"zendesk-cli {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = _parser(sub, "login", "sign in through a browser window and store the session",
                "Opens Chrome/Edge with a profile of its own for NAME at the agent workspace.\n"
                "Sign in as usual (Microsoft, Google, password + 2FA). The window closes itself\n"
                "once Zendesk knows you, and the session cookies go to the OS keychain.\n"
                "Run it again whenever a command says the session has expired.",
                "examples:\n  zd login vartex\n  zd login wesports --subdomain wesportshelp")
    s.add_argument("name", help="a short name for this Zendesk, e.g. vartex")
    s.add_argument("--subdomain", help="the Zendesk subdomain (default: NAME), or a full host")
    s.set_defaults(fn=cmd_login)

    s = _parser(sub, "logout", "end the session in Zendesk and remove it from the keychain")
    s.add_argument("name")
    s.add_argument("--forget-browser", action="store_true", help="also delete the browser profile (SSO memory)")
    s.set_defaults(fn=cmd_logout)

    ac = _parser(sub, "account", "stored logins")
    acs = ac.add_subparsers(dest="account_cmd", required=True)
    s = _parser(acs, "list", "stored logins: instance, user, when signed in (never the cookies)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_account_list)

    s = _parser(sub, "whoami", "who the stored session is, checked live")
    _common(s)
    s.set_defaults(fn=_with_session(cmd_whoami))

    s = _parser(sub, "api", "call any endpoint as yourself (writes are dry runs without --apply)",
                "The web UI's own endpoints, public (/api/v2/...) and private (Admin Center,\n"
                "Copilot). Prints the JSON reply.",
                "examples:\n  zd api GET /api/v2/tickets/123.json\n"
                "  zd api GET /api/v2/macros.json --all macros\n"
                "  zd api PUT /api/v2/tickets/123.json --data '{\"ticket\":{\"priority\":\"high\"}}' --apply\n"
                "  zd api POST /api/v2/macros.json --data @macro.json")
    s.add_argument("method", choices=["GET", "POST", "PUT", "PATCH", "DELETE", "get", "post", "put", "patch", "delete"])
    s.add_argument("path", help="path on the instance, e.g. /api/v2/users/me.json, or a full https URL")
    s.add_argument("--data", metavar="JSON|@FILE|-", help="request body")
    s.add_argument("--param", action="append", metavar="K=V", help="query parameter, repeatable")
    s.add_argument("--all", metavar="KEY", help="GET: follow pagination and return every item under KEY")
    _common(s, json_flag=False)
    _apply(s)
    s.set_defaults(fn=_with_session(cmd_api))

    s = _parser(sub, "tickets", "list tickets: a search (default: unsolved) or a view",
                epilog="examples:\n  zd tickets --mine\n  zd tickets --query 'status:open tags:retur'\n"
                       "  zd tickets --view 360001234567 --limit 200")
    s.add_argument("--query", help="Zendesk search syntax, added to type:ticket (default status<solved)")
    s.add_argument("--mine", action="store_true", help="only tickets assigned to you")
    s.add_argument("--view", metavar="ID", help="the tickets in a view instead (ids from `zd views`)")
    s.add_argument("--limit", type=int, default=50)
    _common(s)
    s.set_defaults(fn=_with_session(cmd_tickets))

    s = _parser(sub, "views", "your active views with ticket counts")
    _common(s)
    s.set_defaults(fn=_with_session(cmd_views))

    t = _parser(sub, "ticket", "one ticket: show, reply, set")
    ts = t.add_subparsers(dest="ticket_cmd", required=True)
    s = _parser(ts, "show", "the ticket and its whole thread, internal notes included",
                epilog="example:\n  zd ticket show 123456")
    s.add_argument("id", type=int)
    s.add_argument("--public", action="store_true", help="only public comments")
    _common(s)
    s.set_defaults(fn=_with_session(cmd_ticket_show))

    s = _parser(ts, "reply", "reply to the customer, or add an internal note (dry run without --apply)",
                "The comment is written as you. Prints exactly what will be sent; with --apply\n"
                "sends it and checks that one new comment of the right kind appeared.",
                "examples:\n  zd ticket reply 123456 --internal --text 'Kollat med lagret.'\n"
                "  zd ticket reply 123456 --file svar.txt --status solved --apply")
    s.add_argument("id", type=int)
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--text")
    g.add_argument("--file", help="read the text from a file (UTF-8)")
    s.add_argument("--internal", action="store_true", help="internal note, not visible to the customer")
    s.add_argument("--html", action="store_true", help="the text is HTML")
    s.add_argument("--status", choices=["open", "pending", "hold", "solved"])
    _common(s, json_flag=False)
    _apply(s)
    s.set_defaults(fn=_with_session(cmd_ticket_reply))

    s = _parser(ts, "set", "change status, priority, assignee, group or tags (dry run without --apply)",
                epilog="examples:\n  zd ticket set 123456 --status pending --add-tag vantar_lager\n"
                       "  zd ticket set 123456 --assignee me --apply")
    s.add_argument("id", type=int)
    s.add_argument("--status", choices=["new", "open", "pending", "hold", "solved", "closed"])
    s.add_argument("--priority", choices=["low", "normal", "high", "urgent"])
    s.add_argument("--type", choices=["problem", "incident", "question", "task"])
    s.add_argument("--assignee", metavar="me|ID|EMAIL")
    s.add_argument("--group", metavar="ID")
    s.add_argument("--add-tag", action="append", metavar="TAG")
    s.add_argument("--remove-tag", action="append", metavar="TAG")
    _common(s, json_flag=False)
    _apply(s)
    s.set_defaults(fn=_with_session(cmd_ticket_set))

    kinds = ", ".join(KINDS)
    s = _parser(sub, "list", "list configuration: " + kinds,
                epilog="examples:\n  zd list macros --find retur\n  zd list triggers --json")
    s.add_argument("kind", choices=list(KINDS), metavar="KIND")
    s.add_argument("--find", metavar="TEXT", help="only items whose JSON contains TEXT")
    _common(s)
    s.set_defaults(fn=_with_session(cmd_list))

    s = _parser(sub, "show", "one configuration item as JSON", epilog="example:\n  zd show macros 360012345678")
    s.add_argument("kind", choices=list(KINDS), metavar="KIND")
    s.add_argument("id")
    _common(s, json_flag=False)
    s.set_defaults(fn=_with_session(cmd_show))

    s = _parser(sub, "update", "change one configuration item from JSON (dry run without --apply)",
                "Give the fields to change, e.g. what `zd show` printed with your edits. Only\n"
                "fields that differ are sent; afterwards the item is read back and compared.",
                "examples:\n  zd show macros 360012345678 > m.json   # edit m.json\n"
                "  zd update macros 360012345678 --data @m.json\n"
                "  zd update triggers 360099 --data '{\"active\": false}' --apply")
    s.add_argument("kind", choices=list(KINDS), metavar="KIND")
    s.add_argument("id")
    s.add_argument("--data", required=True, metavar="JSON|@FILE|-")
    _common(s, json_flag=False)
    _apply(s)
    s.set_defaults(fn=_with_session(cmd_update))

    hc = _parser(sub, "hc", "Help Center articles: articles, article, update")
    hcs = hc.add_subparsers(dest="hc_cmd", required=True)
    s = _parser(hcs, "articles", "list or search articles",
                epilog="examples:\n  zd hc articles --locale sv --find retur\n  zd hc articles --brand addnature")
    s.add_argument("--find", metavar="TEXT")
    s.add_argument("--locale", help="e.g. sv, en-us")
    s.add_argument("--brand", metavar="SUBDOMAIN", help="another brand's Help Center")
    s.add_argument("--limit", type=int, default=100)
    _common(s)
    s.set_defaults(fn=_with_session(cmd_hc_articles))
    s = _parser(hcs, "article", "one article as text (or --html, --json)")
    s.add_argument("id")
    s.add_argument("--locale")
    s.add_argument("--brand", metavar="SUBDOMAIN")
    s.add_argument("--html", action="store_true")
    _common(s)
    s.set_defaults(fn=_with_session(cmd_hc_article))
    s = _parser(hcs, "update", "change an article's title, body or draft flag in one locale (dry run without --apply)",
                epilog="examples:\n  zd hc article 360001 --locale sv --html > a.html   # edit a.html\n"
                       "  zd hc update 360001 --locale sv --file a.html --apply")
    s.add_argument("id")
    s.add_argument("--locale", required=True)
    s.add_argument("--brand", metavar="SUBDOMAIN")
    s.add_argument("--title")
    s.add_argument("--file", help="the new body, HTML")
    s.add_argument("--draft", choices=["yes", "no"])
    _common(s, json_flag=False)
    _apply(s)
    s.set_defaults(fn=_with_session(cmd_hc_update))

    sk = _parser(sub, "skill", "the Claude skill for zd: install, show")
    sks = sk.add_subparsers(dest="skill_cmd", required=True)
    s = _parser(sks, "install", "write SKILL.md to ~/.claude/skills/zendesk/")
    s.add_argument("--dir")
    s.set_defaults(fn=cmd_skill_install)
    s = _parser(sks, "show", "print the skill")
    s.set_defaults(fn=cmd_skill_show)

    s = _parser(sub, "selfupdate", "get the latest zendesk-cli from GitHub and refresh the Claude skill")
    s.set_defaults(fn=cmd_selfupdate)

    a = p.parse_args(argv)
    try:
        return a.fn(a)
    except ZdError as e:
        print(str(e), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
