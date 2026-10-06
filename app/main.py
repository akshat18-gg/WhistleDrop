from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import errors
from app.db import create_tables
from app.routers import auth, moderator, reports


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_tables()
    yield


app = FastAPI(title="WhistleDrop", version="1.0.0", lifespan=lifespan)
errors.register(app)
app.include_router(reports.router)
app.include_router(auth.router)
app.include_router(moderator.router)


@app.get("/health")
def health():
    return {"status": "ok"}
