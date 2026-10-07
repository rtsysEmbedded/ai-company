#!/usr/bin/env python3
"""End-to-end test of the department engine against a throwaway Hermes home.

It creates real Hermes profiles with the real `hermes` CLI, but inside a temporary HERMES_HOME and
with a temporary copy of the departments folder, so your own profiles, keys and configuration are
never touched. OpenRouter is replaced by a local stub.

Usage: python3 departments/engine/tests/test_departments.py        (needs `hermes` on PATH)
Exit code 0 means every check passed.
"""
import http.server
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEPTS = os.path.dirname(ENGINE)
BASE = os.path.dirname(DEPTS)
failures = []


def check(label, condition, detail=""):
    print("  %s  %s%s" % ("ok  " if condition else "FAIL", label, "" if condition else "  -> %s" % (str(detail)[:600],)))
    if not condition:
        failures.append(label)


# --- a stand-in for OpenRouter ---------------------------------------------------
def model(model_id, tools=True, image=True, pdf=True):
    return {"id": model_id, "name": model_id, "context_length": 1000000, "created": 1,
            "pricing": {"prompt": "0.000002", "completion": "0.00001"},
            "architecture": {"input_modalities": ["text"] + (["image"] if image else []) + (["file"] if pdf else [])},
            "supported_parameters": ["tools", "reasoning"] if tools else ["temperature"]}


class Stub(http.server.BaseHTTPRequestHandler):
    models = []

    def log_message(self, *args):
        pass

    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send(200, {"data": self.models})

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.headers.get("Authorization") == "Bearer good-key-0000000000000000":
            self._send(200, {"model": "stub/model", "choices": [{"message": {"content": "OK"}}]})
        else:
            self._send(401, {"error": {"message": "User not found."}})


def ids_in(path):
    with open(path) as handle:
        data = json.load(handle)
    return {r[f] for r in data["roles"].values() for f in ("model", "fallback") if r.get(f)}


def main():
    if not shutil.which("hermes"):
        print("hermes is not on PATH; cannot run this test.")
        return 2
    tmp = tempfile.mkdtemp(prefix="departments-test-")
    stub = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=stub.serve_forever, daemon=True).start()
    stub_url = "http://127.0.0.1:%d" % stub.server_address[1]

    depts = os.path.join(tmp, "departments")
    os.makedirs(depts)
    for folder in ("hardware", "_template"):
        shutil.copytree(os.path.join(DEPTS, folder), os.path.join(depts, folder))
    ids = ids_in(os.path.join(depts, "hardware", "department.json")) | ids_in(os.path.join(depts, "_template", "department.json"))
    for name in os.listdir(os.path.join(depts, "hardware", "presets")):
        ids |= ids_in(os.path.join(depts, "hardware", "presets", name))
    Stub.models = [model(i, image=i != "z-ai/glm-5.3", pdf=i != "z-ai/glm-5.3") for i in sorted(ids)] + [
        model("test/no-tools", tools=False), model("test/text-only", image=False, pdf=False)]

    env_file = os.path.join(tmp, "dept.env")
    os.environ.update({
        "HERMES_HOME": os.path.join(tmp, "hermes"), "XDG_DATA_HOME": os.path.join(tmp, "share"),
        "DEPT_ROOT": depts, "DEPT_ENV_FILE": env_file, "DEPT_DATA": os.path.join(tmp, "data"), "DEPT_NO_ALIAS": "1",
        "DEPT_TOKEN_FILE": os.path.join(tmp, "token"),
        "DEPT_MODELS_URL": stub_url + "/models", "DEPT_CHAT_URL": stub_url + "/chat",
    })
    os.environ.pop("OPENROUTER_API_KEY", None)
    os.makedirs(os.environ["HERMES_HOME"])
    # The MCP servers as their installers would place them (only the server files are needed to register them).
    for package in ("kicad-mcp", "freecad-mcp"):
        shutil.copytree(os.path.join(BASE, "packages", package, "server"), os.path.join(tmp, "share", package))

    sys.path.insert(0, ENGINE)
    sys.path.insert(0, os.path.join(ENGINE, "ui"))
    import dept as engine
    import server as ui

    # A pretend software department, so name clashes with it can be tested without the real one.
    engine.SOFTWARE_TEAM = os.path.join(tmp, "team.yaml")
    with open(engine.SOFTWARE_TEAM, "w") as handle:
        handle.write("provider: openrouter\nroles:\n  manager:\n    hermes_profile: default\n  backend:\n    model: a/b\nrouting:\n  review: code\n")

    def hermes(*args):
        return subprocess.run(["hermes"] + list(args), capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout

    def profile(name, *parts):
        return os.path.join(os.environ["HERMES_HOME"], "profiles", name, *parts)

    quiet = lambda line: None  # noqa: E731

    try:
        print("Configuration")
        models = engine.load_models(refresh=True)["models"]
        check("model list loads from the (stub) OpenRouter", len(models) == len(Stub.models) and models["test/no-tools"]["tools"] is False, len(models))
        check("the hardware department is found", engine.list_departments() == ["hardware"], engine.list_departments())
        config = engine.load_config("hardware")
        errors, _ = engine.validate("hardware", config, models)
        check("the shipped hardware department is valid", not errors, errors)
        for name in sorted(os.listdir(os.path.join(depts, "hardware", "presets"))):
            preset = engine.read_json(os.path.join(depts, "hardware", "presets", name))
            trial = json.loads(json.dumps(config))
            for role, values in preset["roles"].items():
                trial["roles"][role].update(values)
            check("preset %s names existing roles and is valid" % name, set(preset["roles"]) == set(config["roles"]) and not engine.validate("hardware", trial, models)[0])
        check("every role has a rules file", all(os.path.isfile(os.path.join(depts, "hardware", "roles", r + ".md")) for r in config["roles"]))

        def broken(change):
            trial = json.loads(json.dumps(config))
            change(trial)
            found = engine.validate("hardware", trial, models)
            return " | ".join(found[0] + ["W:" + w for w in found[1]])

        check("a model without tool calling is an error", "does not support tool calling" in broken(lambda c: c["roles"]["hw-layout"].update(model="test/no-tools")))
        check("a text-only model on a role that needs images is a warning", "W:role hw-layout: test/text-only cannot see images" in broken(lambda c: c["roles"]["hw-layout"].update(model="test/text-only")))
        check("an unknown model id is a warning", "W:role hw-docs: model nobody/nothing is not in OpenRouter" in broken(lambda c: c["roles"]["hw-docs"].update(model="nobody/nothing")))
        check("a lead without the task board is an error", "needs the 'kanban' tool" in broken(lambda c: c["roles"]["hw-lead"].update(tools=["kicad"])))
        check("a disabled reviewer while review is on is an error", "routing.reviewer" in broken(lambda c: c["roles"]["hw-review"].update(enabled=False)))
        check("bad effort, tool, role name and timeout are errors", all(text in broken(lambda c: (
            c["roles"]["hw-docs"].update(effort="extreme", tools=["hammer"]), c["roles"].update({"Bad Name": dict(c["roles"]["hw-docs"])}),
            c["routing"].update(task_timeout="soon"))) for text in ("effort must be", "unknown tools hammer", "names are lowercase", "task_timeout must look like")))
        check("a role named like a department is an error", "cannot have the same name as a department" in broken(lambda c: c["roles"].update({"hardware": dict(c["roles"]["hw-docs"])})))
        check("a role named like a software-department role is an error",
              "role backend: that name is already used by the software department" in broken(lambda c: c["roles"].update({"backend": dict(c["roles"]["hw-docs"])})))

        print("Apply to Hermes (temporary Hermes home)")
        report = engine.status("hardware", config)
        check("before apply, no profile exists and nothing is in sync", not report["in_sync"] and not any(r["exists"] for r in report["roles"].values()))
        log = []
        check("apply succeeds", engine.apply("hardware", config, log=log.append), log)
        report = engine.status("hardware", config)
        check("after apply, all 8 profiles exist and are in sync", report["in_sync"] and all(r["exists"] for r in report["roles"].values()), report["roles"])
        check("Hermes itself reports the lead's model, fallback and effort",
              hermes("-p", "hw-lead", "config", "get", "model.default").strip() == config["roles"]["hw-lead"]["model"]
              and config["roles"]["hw-lead"]["fallback"] in hermes("-p", "hw-lead", "config", "get", "fallback_providers")
              and hermes("-p", "hw-lead", "config", "get", "agent.reasoning_effort").strip() == "high")
        check("Hermes lists the KiCad tools on the layout profile and none on the researcher",
              "kicad" in hermes("-p", "hw-layout", "mcp", "list") and "kicad" not in hermes("-p", "hw-research", "mcp", "list"))
        check("the lead has the task-board toolset and still has file and terminal tools",
              all("enabled  %s" % t in hermes("-p", "hw-lead", "tools", "list").replace("✓ ", "") for t in ("kanban", "file", "terminal")))
        check("Hermes lists the kicad skill on the schematic profile", "kicad" in hermes("-p", "hw-schematic", "skills", "list"))
        soul = open(profile("hw-layout", "SOUL.md")).read()
        check("role rules are in SOUL.md: shared rules, role rules and the reviewer's name",
              all(text in soul for text in ("Hardware department rules", "# Role: PCB layout engineer", "reviewer=hw-review", engine.BEGIN, engine.END)))
        lead_soul = open(profile("hw-lead", "SOUL.md")).read()
        check("the lead's rules list the other 7 roles, the task tag and how to delegate",
              all("**%s**" % r in lead_soul for r in config["roles"] if r != "hw-lead") and "kanban_create" in lead_soul
              and "`[hw] <project>:`" in lead_soul and "max_runtime_seconds: 3600" in lead_soul
              and "produces or changes a design (schematic, board, CAD part, firmware)" in lead_soul)
        check("the projects folder is written as an absolute path", os.path.expanduser("~/Hardware_Projects") in soul and "{projects_dir}" not in soul)
        log = []
        engine.apply("hardware", config, log=log.append)
        check("a second apply changes nothing", sum("already in sync" in line for line in log) == 8, log)

        print("Changing the configuration")
        with open(profile("hw-layout", "SOUL.md"), "w") as handle:
            handle.write("MY OWN NOTE.\n\n<!-- hardware-dept:begin (old) -->\nold generated text\n<!-- hardware-dept:end -->\n")
        config["roles"]["hw-layout"].update(model="test/text-only", tools=[])
        config["roles"]["hw-firmware"]["enabled"] = False
        config["routing"]["task_timeout"] = "30m"
        engine.save_config("hardware", config)
        differences = engine.status("hardware", config)["roles"]["hw-layout"]["differences"]
        check("status says exactly what apply would change", any("model is" in d for d in differences) and "kicad tools should be removed" in differences, differences)
        log = []
        check("apply after the change succeeds", engine.apply("hardware", config, log=log.append), log)
        check("the new model is in Hermes and the KiCad tools are gone",
              hermes("-p", "hw-layout", "config", "get", "model.default").strip() == "test/text-only" and "kicad" not in hermes("-p", "hw-layout", "mcp", "list"))
        new_soul = open(profile("hw-layout", "SOUL.md")).read()
        check("text the user wrote in SOUL.md survives; a block from the earlier stand-alone version is replaced",
              "MY OWN NOTE" in new_soul and "old generated text" not in new_soul and new_soul.count(engine.BEGIN) == 1)
        check("the lead's rules follow the new time limit", "max_runtime_seconds: 1800" in open(profile("hw-lead", "SOUL.md")).read())
        check("a disabled role is skipped, not deleted", any("hw-firmware: disabled" in line for line in log) and os.path.isdir(profile("hw-firmware")))

        print("A second department")
        engine.new_department("research", "Research", "rs")
        check("a new department is created from the template", engine.list_departments() == ["hardware", "research"]
              and os.path.isfile(os.path.join(depts, "research", "roles", "rs-lead.md"))
              and "{{" not in open(os.path.join(depts, "research", "department.json")).read())
        research = engine.load_config("research")
        check("the new department is valid as created", not engine.validate("research", research, models)[0], engine.validate("research", research, models))
        for label, args, text in (("a prefix already in use", ("sales", "Sales", "rs"), "is taken"),
                                  ("an existing name", ("research", "Research 2", "rx"), "already exists"),
                                  ("a bad name", ("Bad Name", "X", "bn"), "lowercase"),
                                  ("a reserved name", ("engine", "Engine", "en"), "lowercase")):
            try:
                engine.new_department(*args)
                check("creating a department with %s is refused" % label, False, "it was created")
            except engine.ConfigError as exc:
                check("creating a department with %s is refused" % label, text in str(exc), exc)
        check("a role name used by another department is an error",
              "already used by the research department" in broken(lambda c: c["roles"].update({"rs-lead": dict(c["roles"]["hw-docs"])})))
        check("apply creates the new department's profiles", engine.apply("research", research, log=quiet) and engine.status("research", research)["in_sync"])
        rs_soul = open(profile("rs-lead", "SOUL.md")).read()
        check("its rules come from its own files, not from hardware's",
              "Research department rules" in rs_soul and "Hardware department rules" not in rs_soul and "**rs-specialist**" in rs_soul and "`[rs] <project>:`" in rs_soul)
        check("the hardware department is unaffected", engine.status("hardware", config)["in_sync"])
        check("the overview lists both departments", [(d["name"], d["enabled"], d["in_sync"]) for d in engine.overview()] == [("hardware", 7, True), ("research", 3, True)], engine.overview())

        print("API keys")
        check("without a key, status reports that no role has one", not any(r["has_key"] for r in engine.status("hardware", config)["roles"].values()))
        engine.set_env_value(env_file, engine.KEY_VAR, "good-key-0000000000000000")
        engine.set_env_value(env_file, engine.key_var("research"), "research-key-000000000000")
        engine.set_env_value(env_file, engine.key_var("hw-review"), "review-only-key-000000000")
        check("the departments .env is readable only by its owner", stat.S_IMODE(os.stat(env_file).st_mode) == 0o600)
        check("status notices the key is not in the profiles yet", "API key not written to the profile" in engine.status("hardware", config)["roles"]["hw-lead"]["differences"])
        engine.apply("hardware", config, log=quiet)
        engine.apply("research", research, log=quiet)
        value = lambda name: engine.read_env(profile(name, ".env")).get(engine.KEY_VAR)  # noqa: E731
        check("the most specific key wins: role, then department, then shared",
              (value("hw-lead"), value("hw-review"), value("rs-lead")) == ("good-key-0000000000000000", "review-only-key-000000000", "research-key-000000000000"))
        check("profile .env files are readable only by their owner", stat.S_IMODE(os.stat(profile("hw-lead", ".env")).st_mode) == 0o600)
        check("a working key and model pass the model test", engine.test_model("stub/model", "good-key-0000000000000000")[0])
        ok, message = engine.test_model("stub/model", "wrong-key")
        check("a rejected key fails the model test with the provider's message", not ok and "401" in message and "User not found" in message, message)
        check("no key fails the model test without a network call", engine.test_model("stub/model", "") == (False, "No OpenRouter key is set"))

        print("Web interface")
        ui.Handler.token, web = ui.load_token(), ui.Server(("127.0.0.1", 0), ui.Handler)
        ui.Handler.port = web.server_address[1]
        ui.SOFTWARE_PANEL_TOKEN = os.path.join(tmp, "panel_token")
        with open(ui.SOFTWARE_PANEL_TOKEN, "w") as handle:
            handle.write("software-panel-token")
        threading.Thread(target=web.serve_forever, daemon=True).start()
        base = "http://127.0.0.1:%d" % ui.Handler.port
        cookie = "%s=%s" % (ui.COOKIE, ui.Handler.token)

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None

        opener = urllib.request.build_opener(NoRedirect)

        def call(path, body=None, headers=None):
            request = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None, headers=headers or {})
            try:
                with opener.open(request, timeout=120) as response:
                    return response.status, json.loads(response.read() or b"{}")
            except urllib.error.HTTPError as exc:
                raw = exc.read()
                return exc.code, (json.loads(raw) if raw else {"location": exc.headers.get("Location")})

        get = {"Cookie": cookie}
        post = {"Cookie": cookie, "X-Requested-With": ui.HEADER, "Content-Type": "application/json"}
        check("no cookie is refused", call("/api/departments")[0] == 401)
        check("a wrong token is refused", call("/login?token=wrong")[0] == 403)
        check("a request for another host name is refused", call("/api/departments", headers={"Cookie": cookie, "Host": "evil.example"})[0] == 403)
        check("a change without the custom header is refused", call("/api/apply", {"dept": "hardware"}, {"Cookie": cookie, "Content-Type": "application/json"})[0] == 403)
        code, listing = call("/api/departments", headers=get)
        check("the overview lists the departments and the software department",
              code == 200 and [d["name"] for d in listing["departments"]] == ["hardware", "research"] and listing["software"]["present"] and listing["software"]["roles"] == 2, listing.get("software"))
        code, answer = call("/go/software", headers=get)
        check("the software panel link logs in with that panel's own token", code == 303 and answer["location"].endswith("/login?token=software-panel-token"), answer)
        code, state = call("/api/state?dept=hardware", headers=get)
        check("a department's state is served, and never contains a key",
              code == 200 and len(state["config"]["roles"]) == 8 and "good-key" not in json.dumps(state) and state["status"]["key"]["shared"] is True)
        check("an unknown department is a clean error", call("/api/state?dept=../etc", headers=get)[0] == 400 and call("/api/state?dept=nope", headers=get)[0] == 400)
        code, listing = call("/api/models?dept=hardware", headers=get)
        check("the model list offers only tool-capable models, with the department's benchmarks",
              code == 200 and "test/no-tools" not in [m["id"] for m in listing["models"]] and set(listing["benchmarks"]["benchmarks"]) == {"eebench", "agentic"})
        check("another department gets only the general benchmarks", set(call("/api/models?dept=research", headers=get)[1]["benchmarks"]["benchmarks"]) == {"agentic"})
        edited = json.loads(json.dumps(state["config"]))
        edited["roles"]["hw-docs"]["model"] = "test/no-tools"
        code, answer = call("/api/config", {"dept": "hardware", "config": edited}, post)
        check("an invalid configuration is rejected and not written", code == 400 and "tool calling" in answer["error"] and engine.load_config("hardware")["roles"]["hw-docs"]["model"] != "test/no-tools", answer)
        edited["roles"]["hw-docs"].update(model="test/text-only", description="Changed in the browser")
        edited["roles"]["sneaky"] = dict(edited["roles"]["hw-docs"])
        edited["hacked"] = True
        code, answer = call("/api/config", {"dept": "hardware", "config": edited}, post)
        saved = engine.load_config("hardware")
        check("a valid edit is saved; unknown roles and fields are ignored",
              code == 200 and saved["roles"]["hw-docs"]["description"] == "Changed in the browser" and "sneaky" not in saved["roles"] and "hacked" not in saved)
        check("a key that is too short is refused", call("/api/key", {"dept": "hardware", "scope": "shared", "key": "short"}, post)[0] == 400)
        code, answer = call("/api/key", {"dept": "hardware", "scope": "department", "key": "hardware-key-00000000000000"}, post)
        names = sorted(engine.read_env(env_file))
        check("a department key is stored under the department's name", code == 200 and "OPENROUTER_API_KEY_HARDWARE" in names and answer["status"]["key"]["department"] is True, names)
        code, answer = call("/api/key", {"dept": "hardware", "scope": "department", "remove": True}, post)
        check("removing it leaves the shared key in place", code == 200 and answer["status"]["key"] ["department"] is False and answer["status"]["key"]["shared"] is True)
        code, answer = call("/api/key", {"dept": "hardware", "scope": "role", "role": "hw-docs", "key": "docs-only-key-0000000000000"}, post)
        check("a role's own key is stored under the role's name and written to its profile at once",
              code == 200 and answer["profiles_updated"] == 1 and value("hw-docs") == "docs-only-key-0000000000000"
              and answer["status"]["roles"]["hw-docs"]["key_source"] == "role" and "OPENROUTER_API_KEY_HW_DOCS" in engine.read_env(env_file), answer.get("profiles_updated"))
        check("other roles keep their key, and the reply still contains no key",
              value("hw-lead") == "good-key-0000000000000000" and answer["status"]["roles"]["hw-lead"]["key_source"] == "shared" and "only-key" not in json.dumps(answer))
        check("a key for an unknown role is refused", call("/api/key", {"dept": "hardware", "scope": "role", "role": "nobody", "key": "x" * 30}, post)[0] == 400)
        code, answer = call("/api/key", {"dept": "hardware", "scope": "role", "role": "hw-docs", "remove": True}, post)
        check("removing a role's key puts the shared key back into its profile",
              code == 200 and value("hw-docs") == "good-key-0000000000000000" and answer["status"]["roles"]["hw-docs"]["key_source"] == "shared")
        code, answer = call("/api/test", {"dept": "hardware", "role": "hw-lead"}, post)
        check("testing a role's model through the interface works", code == 200 and answer["ok"] is True, answer)
        code, answer = call("/api/apply", {"dept": "hardware"}, post)
        check("apply through the interface brings Hermes in sync", code == 200 and answer["ok"] and answer["state"]["status"]["in_sync"], answer.get("log"))
        code, answer = call("/api/departments", {"name": "sales", "title": "Sales", "prefix": "sl"}, post)
        check("a department can be created through the interface", code == 200 and answer["created"] == "sales" and "sales" in engine.list_departments(), answer)
        check("a clashing one is refused with a reason", call("/api/departments", {"name": "sales2", "title": "Sales", "prefix": "sl"}, post)[1].get("error", "").startswith("The prefix 'sl' is taken"))
        web.shutdown()

        print("Offline and removal")
        os.environ["DEPT_MODELS_URL"] = engine.MODELS_URL = "http://127.0.0.1:1/models"
        stale = engine.load_models(refresh=True)
        check("when OpenRouter is unreachable, the cached list is used and says so", len(stale["models"]) == len(Stub.models) and "Could not refresh" in stale.get("stale_reason", ""))
        check("remove deletes one department's profiles and leaves the other's",
              engine.remove(engine.load_config("research"), log=quiet) and not os.path.isdir(profile("rs-lead")) and os.path.isdir(profile("hw-lead")))
    finally:
        stub.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
    return 1 if failures else 0


if __name__ == "__main__":
    code = main()
    print("\n%s" % ("FAILED: %d check(s)" % len(failures) if failures else ("All checks passed." if code == 0 else "")))
    sys.exit(code)
