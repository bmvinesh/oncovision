"""Local service: health, explanations (cached, live re-run) and the static viewer from one origin.

    ONCOVISION_DATA_DIR=~/data/oncovision/processed/viewer python -m oncovision.api.app --port 8090

Nothing here calls out: the only network peer is the local Ollama server.  Request bodies and generated text
are never written to the log; the explanation cache holds only claim-derived text.

Environment: ``ONCOVISION_CASES`` (whatif_cases.json; default ``<data dir>/whatif_cases.json`` else the
committed fixture), ``ONCOVISION_DATA_DIR`` (served at ``/data`` so the viewer finds frames),
``ONCOVISION_MODEL`` (default nemotron3:33b), ``ONCOVISION_CACHE`` (explanation cache directory).
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import urllib.request
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from oncovision.llm import debate as D
from oncovision.llm import narrative as N

REPO_WEB = Path(__file__).resolve().parents[3] / "web"


class NarrativeRequest(BaseModel):
    uid: str | None = None
    kind: str = "case"          # "case" or "results"
    rerun: bool = False         # True: ignore stored and cached text and run the agents now


def _ollama(path: str, url: str = N.OLLAMA_URL, timeout: float = 1.5):
    try:
        with urllib.request.urlopen(f"{url}{path}", timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def create_app(cases_path: str | None = None, model: str | None = None, cache_dir: str | None = None,
               web_dir: str | None = None, data_dir: str | None = None, caller=None,
               timeout: float = 180.0) -> FastAPI:
    model = model or os.environ.get("ONCOVISION_MODEL", N.DEFAULT_MODEL)
    data_dir = data_dir or os.environ.get("ONCOVISION_DATA_DIR")
    web = Path(web_dir or REPO_WEB)
    if cases_path is None:
        cases_path = os.environ.get("ONCOVISION_CASES")
    if cases_path is None:
        for cand in ([Path(data_dir).expanduser() / "whatif_cases.json"] if data_dir else []) + [
                web / "data" / "whatif_cases.json", web / "data-fixture" / "whatif_cases.json"]:
            if cand.exists():
                cases_path = str(cand)
                break
    if cases_path is None:
        raise FileNotFoundError("no whatif_cases.json found; set ONCOVISION_CASES")
    cache_dir = cache_dir or os.environ.get("ONCOVISION_CACHE") or str(Path.home() / ".cache" / "oncovision" / "debate")

    doc = json.loads(Path(cases_path).read_text())
    effects = (doc.get("effects") or {}).get("values") or {}
    by_uid = {c["uid"]: c for c in doc.get("cases", []) if c.get("uid")}
    llm_lock = threading.Lock()          # one generation at a time: a single local model, a single GPU
    run_caller = caller or D.ollama_caller

    app = FastAPI(title="OncoVision local service", docs_url=None, redoc_url=None)

    def find_case(uid: str):
        if uid in by_uid:
            return by_uid[uid]
        hits = [c for u, c in by_uid.items() if u.split(":")[-1] == uid]
        return hits[0] if len(hits) == 1 else None

    @app.get("/api/health")
    def health():
        tags = _ollama("/api/tags")
        ps = _ollama("/api/ps")
        device = None
        try:
            import torch
            device = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu"
        except Exception:
            pass
        return {"status": "ok", "model": model, "device": device, "cases": len(by_uid),
                "llm": {"reachable": tags is not None,
                        "installed": [m.get("name") for m in (tags or {}).get("models", [])],
                        "loaded": [m.get("name") for m in (ps or {}).get("models", [])]},
                "explanations": {"protocol": D.PROTOCOL, "judge": "rule-based"}}

    @app.post("/api/narrative")
    def narrative(req: NarrativeRequest):
        if req.kind == "results":
            mc = doc.get("model_card")
            if not mc:
                raise HTTPException(404, "no model card in this data file")
            if mc.get("explainer") and not req.rerun:
                return mc["explainer"]
            ledger, kind = D.results_ledger(mc, effects), "results"
        elif req.kind == "case":
            case = find_case(req.uid or "")
            if case is None:
                raise HTTPException(404, "unknown patient")
            if case.get("narrative") and not req.rerun:
                return case["narrative"]
            ledger, kind = D.case_ledger(case, effects), "case"
        else:
            raise HTTPException(422, "kind must be 'case' or 'results'")
        with llm_lock:
            return D.debate(ledger, kind, model, cache_dir, timeout, caller=run_caller, rerun=req.rerun)

    if data_dir and Path(data_dir).expanduser().is_dir():
        app.mount("/data", StaticFiles(directory=str(Path(data_dir).expanduser())), name="data")
    if web.is_dir():
        app.mount("/", StaticFiles(directory=str(web), html=True), name="web")
    return app


def main(argv=None):
    import uvicorn
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--cases", default=None)
    ap.add_argument("--model", default=None)
    a = ap.parse_args(argv)
    uvicorn.run(create_app(cases_path=a.cases, model=a.model), host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
