"""Komuta merkezi UI. Çalıştır: python -m ui.server  →  http://127.0.0.1:8000"""
import os
import shutil
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from s2agent.config import ROOT
from s2agent.data import get_data

from . import live_agent, pipeline

WEB = Path(__file__).parent / "web"
UPLOADS = ROOT / "outputs" / "uploads"

app = FastAPI(title="Stage2 Command Center")


@app.get("/api/layout")
def get_layout():
    return pipeline.layout()


@app.get("/api/images")
def get_images():
    return pipeline.images()


@app.get("/api/images/{image_id}/file")
def get_image_file(image_id: str):
    try:
        return FileResponse(get_data().image_path(image_id))
    except FileNotFoundError:
        raise HTTPException(404, image_id)


@app.get("/api/tracks/{track_id}")
def get_track(track_id: str):
    t = pipeline.track(track_id)
    if t is None:
        raise HTTPException(404, track_id)
    return t


@app.get("/api/agent/{image_id}")
def get_agent_output(image_id: str):
    """Saved GLM agent output (outputs/<id>.json), trimmed for the UI."""
    out = live_agent.load(image_id)
    if out is None:
        raise HTTPException(404, f"agent çıktısı yok: {image_id}")
    return out


@app.post("/api/agent/{image_id}/run")
def run_agent(image_id: str):
    """Run the real GLM agent now; streams NDJSON trace events, ends with {type: done, agent}."""
    if image_id not in get_data().meta:
        raise HTTPException(404, image_id)
    return StreamingResponse(live_agent.stream(image_id), media_type="application/x-ndjson")


@app.post("/api/analyze")
def analyze(file: UploadFile = File(...), image_id: str = Form(""), min_conf: float = Form(pipeline.MIN_CONF)):
    image_id = image_id.strip() or Path(file.filename or "").stem
    UPLOADS.mkdir(parents=True, exist_ok=True)
    dst = UPLOADS / f"{image_id}{Path(file.filename or '.jpg').suffix}"
    with open(dst, "wb") as f:
        shutil.copyfileobj(file.file, f)
    try:
        return pipeline.analyze(dst, image_id, min_conf)
    except pipeline.NeedsMeta:
        raise HTTPException(422, {"needs_meta": True, "image_id": image_id,
                                  "msg": f"'{image_id}' için köşe koordinatı yok — listeden görüntü seç"})


app.mount("/", StaticFiles(directory=WEB, html=True), name="web")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.getenv("UI_HOST", "127.0.0.1"), port=int(os.getenv("UI_PORT", "8000")))
