#!/usr/bin/env python3
"""Local web interface for the company's departments. Standard library only.

One page lists every department. Departments run by the department engine are configured here;
the software department keeps its own control panel, which this page links to.

It listens on 127.0.0.1 only. Access needs the token stored in data/departments/ui_token;
`dept open` opens /login?token=... once, which sets a cookie. Requests that change anything must
also carry a custom header, which a page on another site cannot send, and the Host header must be
this server.

Usage: server.py [--port 8780] [--no-open] [--print-url]
"""
import argparse
import http.server
import json
import os
import secrets
import socket
import socketserver
import sys
import threading
import urllib.parse
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import dept as engine  # noqa: E402

TOKEN_PATH = os.environ.get("DEPT_TOKEN_FILE") or os.path.join(engine.DATA, "ui_token")
SOFTWARE_PANEL_PORT = int(os.environ.get("PANEL_PORT") or 8770)
SOFTWARE_PANEL_TOKEN = os.path.join(engine.BASE, "data", "panel_token")
COOKIE = "departments"
HEADER = "departments"
MAX_BODY = 1_000_000
ROLE_FIELDS = ("title", "enabled", "model", "fallback", "effort", "tools", "needs", "description")
ROUTING_FIELDS = ("lead", "reviewer", "review", "review_scope", "task_timeout")
apply_lock = threading.Lock()


def load_token():
    if os.path.isfile(TOKEN_PATH):
        with open(TOKEN_PATH, "r", encoding="utf-8") as handle:
            token = handle.read().strip()
        if len(token) >= 32:
            return token
    token = secrets.token_urlsafe(32)
    os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
    descriptor = os.open(TOKEN_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(token)
    return token


def port_open(port):
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def software_info():
    """The software department is not run by this engine; report what is there and where its panel is."""
    present = os.path.isfile(engine.SOFTWARE_TEAM)
    return {"present": present, "roles": max(len(engine.software_roles()) - 1, 0) if present else 0,
            "panel_port": SOFTWARE_PANEL_PORT, "panel_running": present and port_open(SOFTWARE_PANEL_PORT)}


def departments_payload():
    return {"departments": engine.overview(), "software": software_info(), "environment": engine.environment_status()}


def models_payload(department, refresh=False):
    data = engine.load_models(refresh=refresh)
    usable = [m for m in data["models"].values() if m["tools"] and m["price_in"] is not None and m["price_in"] >= 0]
    usable.sort(key=lambda m: m["id"])
    return {"fetched_at": data.get("fetched_at", 0), "stale_reason": data.get("stale_reason"),
            "models": usable, "benchmarks": engine.load_benchmarks(department)}


def state_payload(department):
    config = engine.load_config(department)
    errors, warnings = engine.validate(department, config, engine.load_models().get("models") or None)
    presets = {}
    folder = os.path.join(engine.department_dir(department), "presets")
    for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        if name.endswith(".json"):
            preset = engine.read_json(os.path.join(folder, name))
            if preset:
                presets[name[:-5]] = preset
    return {"department": department, "config": config, "status": engine.status(department, config), "errors": errors,
            "warnings": warnings, "presets": presets,
            "choices": {"efforts": engine.EFFORTS, "review": engine.REVIEW_MODES}}


def clean_config(submitted, current):
    """Build the config to save from what the browser sent: known fields only, existing roles only."""
    if not isinstance(submitted, dict) or not isinstance(submitted.get("roles"), dict):
        raise engine.ConfigError("The request did not contain a configuration")
    config = dict(current)
    config["projects_dir"] = str(submitted.get("projects_dir") or "").strip()
    routing = dict(current.get("routing") or {})
    for key in ROUTING_FIELDS:
        if key in (submitted.get("routing") or {}):
            routing[key] = str(submitted["routing"][key]).strip()
    config["routing"] = routing
    roles = {}
    for name, old in current["roles"].items():
        new = submitted["roles"].get(name)
        if not isinstance(new, dict):
            roles[name] = old
            continue
        role = dict(old)
        for field in ROLE_FIELDS:
            if field not in new:
                continue
            value = new[field]
            if field == "enabled":
                role[field] = bool(value)
            elif field in ("tools", "needs"):
                role[field] = [str(v) for v in value] if isinstance(value, list) else []
            else:
                role[field] = str(value).strip()
        roles[name] = role
    config["roles"] = roles
    return config


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "departments-ui"
    token = ""
    port = 0

    def log_message(self, fmt, *args):
        pass

    # --- plumbing -------------------------------------------------------------
    def send_json(self, payload, code=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def fail(self, code, message):
        self.send_json({"error": message}, code)

    def redirect(self, location, cookie=None):
        self.send_response(303)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def login_help(self):
        """A readable page for a browser that has not been logged in yet."""
        command = os.path.join(engine.BASE, "dept") + " open"
        body = ("""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark"><title>Departments: log in</title><link rel="icon" href="data:,">
<style>body{font:1rem/1.5 system-ui,sans-serif;max-width:38rem;margin:3rem auto;padding:0 1rem}
code{display:block;padding:.7rem .9rem;border:1px solid #8886;border-radius:6px;margin:.6rem 0;overflow-x:auto}</style></head>
<body><h1>This browser is not logged in yet</h1>
<p>The departments page only opens for a browser that was logged in from your own terminal. Run this once:</p>
<code>%s</code>
<p>It opens this page and logs this browser in. After that, this address works on its own for a year.</p>
</body></html>""" % command.replace("&", "&amp;").replace("<", "&lt;")).encode("utf-8")
        self.send_response(401)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def host_ok(self):
        return self.headers.get("Host", "") in ("127.0.0.1:%d" % self.port, "localhost:%d" % self.port)

    def authed(self):
        for part in self.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE and secrets.compare_digest(value, self.token):
                return True
        return False

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            raise ValueError("Missing or oversized request body")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    # --- routes ---------------------------------------------------------------
    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(url.query)
        department = query.get("dept", [""])[0]
        if not self.host_ok():
            return self.fail(403, "Open this page as http://127.0.0.1:%d/" % self.port)
        if url.path == "/login":
            if secrets.compare_digest(query.get("token", [""])[0], self.token):
                # Keep the login for a year, so the plain address keeps working after the browser is restarted.
                return self.redirect("/", "%s=%s; HttpOnly; SameSite=Strict; Path=/; Max-Age=31536000" % (COOKIE, self.token))
            return self.fail(403, "Wrong or missing token. Open the interface with: ./dept open")
        if not self.authed():
            if url.path in ("/", "/index.html"):
                return self.login_help()
            return self.fail(401, "Not logged in. Open the interface with: ./dept open")
        try:
            if url.path in ("/", "/index.html"):
                with open(os.path.join(HERE, "index.html"), "rb") as handle:
                    body = handle.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                                 "script-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
                self.end_headers()
                self.wfile.write(body)
            elif url.path == "/go/software":
                # Log the browser in to the software department's own panel, as its scripts/panel-open does.
                try:
                    with open(SOFTWARE_PANEL_TOKEN, "r", encoding="utf-8") as handle:
                        token = handle.read().strip()
                except OSError:
                    return self.fail(404, "The software department's panel is not set up on this machine.")
                self.redirect("http://127.0.0.1:%d/login?token=%s" % (SOFTWARE_PANEL_PORT, urllib.parse.quote(token)))
            elif url.path == "/api/departments":
                self.send_json(departments_payload())
            elif url.path == "/api/state":
                self.send_json(state_payload(department))
            elif url.path == "/api/models":
                self.send_json(models_payload(department if department in engine.list_departments() else None,
                                              refresh=query.get("refresh", ["0"])[0] == "1"))
            else:
                self.fail(404, "Not found")
        except engine.ConfigError as exc:
            self.fail(400, str(exc))

    def do_POST(self):
        if not self.host_ok() or not self.authed() or self.headers.get("X-Requested-With") != HEADER:
            return self.fail(403, "Request refused")
        try:
            body = self.read_json()
            path = urllib.parse.urlsplit(self.path).path
            department = str(body.get("dept") or "")
            if path == "/api/departments":
                engine.new_department(str(body.get("name") or "").strip(), str(body.get("title") or ""), str(body.get("prefix") or "").strip())
                return self.send_json(dict(departments_payload(), created=str(body.get("name")).strip()))
            if path == "/api/key" and body.get("scope") == "shared":
                scope = None
            else:
                config = engine.load_config(department)  # every other request needs an existing department
                scope = department
                if path == "/api/key" and body.get("scope") == "role":
                    scope = str(body.get("role") or "")
                    if scope not in config["roles"]:
                        return self.fail(400, "Unknown role")
            if path == "/api/config":
                config = clean_config(body.get("config"), engine.load_config(department))
                errors, _ = engine.validate(department, config, engine.load_models().get("models") or None)
                if errors:
                    return self.send_json({"error": "Not saved: " + "; ".join(errors), "errors": errors}, 400)
                engine.save_config(department, config)
                self.send_json(state_payload(department))
            elif path == "/api/key":
                variable = engine.key_var(scope)
                if body.get("remove"):
                    engine.set_env_value(engine.ENV_PATH, variable, None)
                elif body.get("import"):
                    engine.import_key(str(body["import"]), scope)
                else:
                    key = str(body.get("key") or "").strip()
                    if len(key) < 20 or any(c.isspace() for c in key):
                        return self.fail(400, "That does not look like an API key")
                    engine.set_env_value(engine.ENV_PATH, variable, key)
                # Put the change into the agents' profiles straight away, so no separate apply is needed.
                targets = engine.list_departments() if scope is None else [department]
                written = sum(engine.sync_keys(name, engine.load_config(name)) for name in targets)
                payload = state_payload(department) if department else departments_payload()
                self.send_json(dict(payload, profiles_updated=written))
            elif path == "/api/apply":
                if not apply_lock.acquire(blocking=False):
                    return self.fail(409, "An apply is already running")
                try:
                    lines = []
                    ok = engine.apply(department, engine.load_config(department), log=lines.append)
                finally:
                    apply_lock.release()
                self.send_json({"ok": ok, "log": "\n".join(lines), "state": state_payload(department)})
            elif path == "/api/test":
                config = engine.load_config(department)
                role = str(body.get("role") or "")
                if role not in config["roles"]:
                    return self.fail(400, "Unknown role")
                ok, message = engine.test_model(config["roles"][role]["model"], engine.key_for(role, department))
                self.send_json({"ok": ok, "message": message})
            else:
                self.fail(404, "Not found")
        except engine.ConfigError as exc:
            self.fail(400, str(exc))
        except ValueError as exc:
            self.fail(400, "Bad request: %s" % exc)


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    parser = argparse.ArgumentParser(description="Web interface for the company's departments")
    parser.add_argument("--port", type=int, default=int(os.environ.get("DEPT_UI_PORT") or 8780))
    parser.add_argument("--no-open", action="store_true", help="do not open the browser (used by the background service)")
    parser.add_argument("--print-url", action="store_true", help="print the login link and exit")
    args = parser.parse_args()
    Handler.token, Handler.port = load_token(), args.port
    url = "http://127.0.0.1:%d/login?token=%s" % (args.port, Handler.token)
    if args.print_url:
        print(url)
        return
    try:
        server = Server(("127.0.0.1", args.port), Handler)
    except OSError as exc:
        if args.no_open:
            sys.exit("Cannot listen on 127.0.0.1:%d (%s). Is the interface already running? Try --port." % (args.port, exc))
        # Most likely the background service is already serving it: just open the page.
        print("The web interface is already running on port %d. Opening it." % args.port)
        webbrowser.open(url)
        return
    if args.no_open:
        # Background service: its output goes to a log file, so the login token must not be printed.
        print("Departments web interface listening on http://127.0.0.1:%d (open it with: dept open)" % args.port, flush=True)
    else:
        print("Departments: %s" % url, flush=True)
        print("Press Ctrl+C to stop.", flush=True)
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
