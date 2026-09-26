"""Komuta merkezi UI. Çalıştır: python -m ui.server  →  http://127.0.0.1:8000"""
import json
import os
import shutil
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from s2agent.config import ROOT
from s2agent.data import get_data

from . import pipeline

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


@app.get("/api/agent/{image_id}")
def get_agent_output(image_id: str):
    """GLM agent output (scripts.run → outputs/<id>.json), trimmed for the UI."""
    f = ROOT / "outputs" / f"{image_id}.json"
    if not f.exists():
        raise HTTPException(404, f"agent çıktısı yok: {image_id}")
    ev = json.loads(f.read_text()).get("evidence", {})
    return {"image_id": image_id, "assessment": ev.get("assessment"), "matches": ev.get("matches"),
            "kinematics": ev.get("kinematics", {}), "report_checks": ev.get("report_checks", {})}


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
