"""Minimal FastAPI service wrapping the demo calculator module.

This is what gets built into the container image (see ../Dockerfile) and
smoke-tested by the deploy stage in ci-pipeline.yml.
"""
from fastapi import FastAPI, HTTPException

from app.calculator import add, divide, multiply

app = FastAPI(title="Outer-Loop Demo App")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/add")
def add_endpoint(a: float, b: float):
    return {"result": add(a, b)}


@app.get("/multiply")
def multiply_endpoint(a: float, b: float):
    return {"result": multiply(a, b)}


@app.get("/divide")
def divide_endpoint(a: float, b: float):
    try:
        return {"result": divide(a, b)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
