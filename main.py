# main.py
from fastapi import FastAPI

from app.routes import router

app = FastAPI(title="Quote Showcase")

app.include_router(router)
