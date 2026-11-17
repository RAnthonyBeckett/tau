"""App-prep recipes: "Tau, prep for <app>".

A recipe (in the profile) names the services that use the GPU and must stop
first, the brain lane to use while the app runs, and optionally a launch
command. Each step goes through the organ permission check on its own.
"""

from __future__ import annotations

from typing import Any, Dict

from tau.profile import as_list


def find_recipe(profile, app: str) -> Dict[str, Any]:
    a = app.lower().strip()
    for r in as_list(profile.get("app_prep")):
        if isinstance(r, dict) and r.get("app", "").lower() == a:
            return r
    for r in as_list(profile.get("app_prep")):
        if isinstance(r, dict) and a in r.get("app", "").lower():
            return r
    raise LookupError(f"there's no prep recipe for {app} in your profile")


def prep_for(organs, app: str) -> Dict[str, Any]:
    r = find_recipe(organs.profile, app)
    done, waiting, failed = [], [], []
    for svc in as_list(r.get("evict")):
        out = organs.call("services", "stop", via="prep", name=svc)
        (done if out.ok else waiting if out.status == "needs_yes" else failed).append(svc)
    if r.get("lane") and organs.context.get("lanes"):
        organs.context["lanes"].forced = r["lane"]
    if organs.level("gpu") == "act" and r.get("unload_local", True):
        from tau.lanes import request_unload
        request_unload(organs.profile)
    if r.get("launch"):
        from tau.organs.builtin import run_command
        run_command(organs, r["launch"], trusted=True)
    parts = []
    if done:
        parts.append("stopped " + ", ".join(done))
    if waiting:
        parts.append("waiting for your yes on " + ", ".join(waiting))
    if failed:
        parts.append("couldn't stop " + ", ".join(failed))
    if r.get("lane"):
        parts.append(f"using the {r['lane']} lane")
    return {"say": f"Ready for {r['app']}: " + ("; ".join(parts) or "nothing to change") + ".", "ok": not failed}
