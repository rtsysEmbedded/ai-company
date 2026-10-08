"""End-to-end MCP test: start scripts/kb-mcp over stdio, list tools, call each one."""
import asyncio
import os
import shutil
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


async def main():
    params = StdioServerParameters(command=os.path.join(ROOT, "scripts", "kb-mcp"),
                                   env=dict(os.environ, KB_ROLE="tester"))
    created = []
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = sorted(t.name for t in (await s.list_tools()).tools)
            print("tools:", tools)

            async def call(name, **kw):
                res = await s.call_tool(name, kw)
                return res.content[0].text

            refused = await call("kb_add", title="MCP test lesson watchdog", body="Kick the IWDG every 100 ms.")
            print("kb_add without the form refused:", refused.startswith("Not saved") and "## Next time" in refused)
            print("kb_template:", "## Symptom" in await call("kb_template", kind="error") and "## Sources" in await call("kb_template"))
            out = await call("kb_add", title="MCP test lesson watchdog", project="MCP Test Rig", task="t_abcd1234",
                             body="## What was the job\nWatchdog bring-up.\n\n## What worked\nKick the IWDG every 100 ms from the idle task.\n\n"
                                  "## What went wrong\nKicking it from an ISR hid a hang.\n\n## Next time\nNever kick from an ISR.",
                             tags="test watchdog")
            print("kb_add:", out); created.append(out.split(": ", 1)[-1])
            hit = await call("kb_search", query="how often should the watchdog be kicked", k=1)
            print("kb_search finds lesson:", "MCP test lesson watchdog" in hit)
            got = await call("kb_get", path=created[0])
            print("kb_get:", "IWDG" in got and "project: MCP Test Rig" in got and "task: t_abcd1234" in got)
            mine = await call("kb_search", query="watchdog", k=3, project="MCP Test Rig")
            other = await call("kb_search", query="watchdog", k=3, project="some other project")
            print("project filter:", "MCP test lesson watchdog" in mine and "MCP test lesson watchdog" not in other)
            err = await call("kb_add", title="MCP test error flash write", kind="error", tags="test",
                             body="## Symptom\nFlash write fails.\n## Cause\nClock off.\n## Fix\nEnable the clock.\n## How to check\nRead back.")
            print("kb_add error note:", err.startswith("Saved")); created.append(err.split(": ", 1)[-1])
            form = "## Question\nWhich diode?\n## Findings\n1N5822.\n## Sources\n%s\n## Open points\nnone"
            print("research note without links refused:", "links themselves" in await call("kb_add", title="MCP test research diode", kind="research", body=form % "See the project file."))
            res = await call("kb_add", title="MCP test research diode", kind="research", body=form % "https://example.com/1n5822.pdf")
            print("research note with links saved:", res.startswith("Saved")); created.append(res.split(": ", 1)[-1])
            print("kb_get traversal blocked:", "outside knowledge" in await call("kb_get", path="../.env"))
            print("kb_add escape blocked:", (await call("kb_add", title="../../etc/evil", body="x", kind="module")).startswith("Saved and indexed: knowledge/lessons/"))
            created.append((await call("kb_search", query="evil", k=1)).split("] ", 1)[-1].split(" >", 1)[0])
            print("kb_add secret refused:", "Refused" in await call("kb_add", title="k", body="key sk-or-v1-abc", kind="module"))
            dec = await call("kb_add", title="MCP test decision gearbox bus", kind="decision",
                             body="## Context\nGearbox bus, 8 nodes.\n## Decision\nUse CAN-FD for the gearbox bus.\n## Why\nNeeds 64-byte frames.\n## Consequences\nNew transceivers.",
                             files="motor/src/can.c", links=f"related_to {created[0]}")
            print("kb_add decision:", dec); created.append(dec.split(": ", 1)[-1])
            print("kb_add bad kind refused:", "Unknown kind" in await call("kb_add", title="x", body="x", kind="junk"))
            print("kb_add bad link refused:", "not found" in await call("kb_add", title="x", body="x", kind="module", links="depends_on ../.env"))
            only = await call("kb_search", query="gearbox bus watchdog", k=5, note_kind="decision")
            print("note_kind filter:", "MCP test decision" in only and "MCP test lesson" not in only)
            print("backlink:", f"related_to <- {created[-1]}" in await call("kb_get", path=created[0]))
            ret = await call("kb_retire", path=created[-1], reason="test cleanup")
            print("kb_retire:", ret); created[-1] = ret.split(": ", 1)[-1]
            gone = await call("kb_search", query="gearbox bus CAN-FD", k=5, note_kind="decision")
            print("retired hidden from search:", "MCP test decision" not in gone)
            print("retired kept with reason:", "retired_reason: test cleanup" in await call("kb_get", path=created[-1]))
            print("kb_retire outside lessons refused:", "Not found" in await call("kb_retire", path="policies/global.md", reason="x"))
            print("kb_policies:", "Global policy" in await call("kb_policies"))
            print("kb_status:", (await call("kb_status")).splitlines()[0])
    # clean up test lessons and re-sync index
    for p in created:
        f = os.path.join(ROOT, p)
        if p.startswith("knowledge/lessons/") and os.path.exists(f):
            os.remove(f)
    sys.path.insert(0, os.path.join(ROOT, "services", "kb"))
    from kb import store
    from kb.config import LESSONS
    print("cleanup:", store.ingest(LESSONS, log=lambda *_: None))


asyncio.run(main())
