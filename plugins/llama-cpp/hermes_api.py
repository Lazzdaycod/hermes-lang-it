"""Client for the PUBLIC Hermes core routes (``/api/local-models/*``).

The plugin never drives the package manager or the runtime bootstrap through
private internals: server lifecycle and build installs go through the same
HTTP routes the first-party Local Models pane uses. Admission is same-user,
via the two mechanisms core itself documents:

1. Gateway native HTTP — discover the live endpoint over the private control
   socket/pipe and mint a single-use session ticket (the bootstrap every
   ``hermes`` CLI client uses).
2. Legacy ``serve`` backend — the host rendezvous record plus its session
   token (``gateway.host_rendezvous``, the pattern ``plugins_activation``
   uses to reach the dashboard backend).

No credential is ever minted from another tool's login, written into the
process environment, or returned to the frontend.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path


class CoreAPIError(RuntimeError):
    """A public core route could not be reached or answered with an error."""


def _home() -> Path:
    try:
        from hermes_constants import get_hermes_home  # type: ignore
        p = get_hermes_home()
        if p:
            return Path(p)
    except Exception:
        pass
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env)
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "hermes"
    return Path.home() / ".hermes"


def _http_json(url: str, method: str, body: dict | None, headers: dict,
               timeout: float) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    if data is not None:
        headers = {**headers, "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read().decode("utf-8", errors="replace")).get("detail", "")
        except Exception:
            pass
        raise CoreAPIError(f"{method} {url.split('/api/')[-1]}: HTTP {exc.code} {detail}".strip()) from exc
    except urllib.error.URLError as exc:
        raise CoreAPIError(f"{method} {url}: {exc.reason}") from exc
    return json.loads(raw.decode("utf-8")) if raw else {}


def _gateway_call(method: str, path: str, body: dict | None, timeout: float) -> dict:
    """Native-http admission: endpoint discovery + single-use session ticket."""
    from hermes_cli.gateway_runtime import discover_gateway_endpoint
    from hermes_cli.gateway_client import private_control_query

    home = _home()
    disc = discover_gateway_endpoint(home, timeout=5)
    ep = disc.endpoint
    if ep is None or not ep.api_origin:
        raise CoreAPIError(f"gateway non raggiungibile ({disc.state}: {disc.reason_code or 'no endpoint'})")
    ctl = Path(ep.control_home) if ep.control_home else home
    request = json.dumps({
        "protocol": 1, "id": 1, "verb": "session-ticket",
        "params": {"profile_id": ep.profile_id, "instance_id": ep.instance_id,
                   "purpose": "native-http"},
    }).encode() + b"\n"
    answer = json.loads(private_control_query(ctl, request, 15).split(b"\n", 1)[0])
    ticket = ((answer.get("result") or {}).get("ticket")) if isinstance(answer, dict) else None
    if not ticket:
        raise CoreAPIError("il gateway non ha rilasciato un session ticket")
    return _http_json(ep.api_origin + path, method, body,
                      {"x-hermes-gateway-ticket": ticket}, timeout)


def _serve_call(method: str, path: str, body: dict | None, timeout: float) -> dict:
    """Legacy serve/dashboard backend: rendezvous record + session token."""
    from gateway import host_rendezvous as hr

    record = None
    for role in (hr.ROLE_SERVE, hr.ROLE_DESKTOP_SERVE):
        rec = hr.read_record(role)
        if rec is not None and rec.port and hr.record_token_is_consistent(rec):
            record = rec
            break
    if record is None:
        raise CoreAPIError("nessun backend serve attivo (rendezvous record assente)")
    token = hr.read_token(record.role)
    url = f"http://{hr.dial_host(record)}:{record.port}{path}"
    return _http_json(url, method, body, {"X-Hermes-Session-Token": token}, timeout)


def core_request(method: str, path: str, body: dict | None = None,
                 timeout: float = 60.0) -> dict:
    """Call a public core route (e.g. ``/api/local-models/server``).

    Tries the gateway's native-http admission first, then the legacy serve
    rendezvous. Raises CoreAPIError when neither backend answers.
    """
    try:
        return _gateway_call(method, path, body, timeout)
    except CoreAPIError:
        raise
    except Exception as gateway_exc:
        try:
            return _serve_call(method, path, body, timeout)
        except Exception as serve_exc:
            raise CoreAPIError(
                f"core API non raggiungibile (gateway: {gateway_exc}; serve: {serve_exc})"
            ) from serve_exc


def poll_job(job_id: str, on_progress=None, *, timeout: float = 2400.0,
             interval: float = 2.0) -> dict:
    """Poll ``GET /api/local-models/jobs/{job_id}`` until the job settles.

    ``on_progress(view)`` sees every intermediate job view (status/phase/
    detail/percent). Returns the final view (``status`` done/error/paused);
    raises CoreAPIError on transport failure or timeout.
    """
    import time

    deadline = time.monotonic() + timeout
    path = f"/api/local-models/jobs/{job_id}"
    while True:
        view = core_request("GET", path, timeout=30)
        status = view.get("status")
        if status in ("done", "error"):
            return view
        if on_progress is not None:
            try:
                on_progress(view)
            except Exception:
                pass
        if time.monotonic() >= deadline:
            raise CoreAPIError(f"job {job_id} non concluso entro {timeout:.0f}s")
        time.sleep(interval)