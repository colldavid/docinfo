"""
FastAPI backend — stubbed for phase 1. CLI is the primary interface.
Routes will wrap the same pipeline logic used by the CLI.
"""
from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(
    title="DocInfo",
    description="Document intelligence and classification API",
    version="0.1.0",
)


@app.get("/health")
def health():
    return {"status": "ok"}


# Phase 2: add POST /classify, GET /results, GET /results/{id}
