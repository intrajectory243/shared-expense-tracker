from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.database import Base, engine
from app.routers import auth, balances, expenses, households, push, users

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Shared Expense Tracker")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,  # auth uses a Bearer token, not cookies, so wildcard origins are safe
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(households.router)
app.include_router(users.router)
app.include_router(expenses.router)
app.include_router(balances.router)
app.include_router(push.router)


@app.get("/health")
def health():
    return {"status": "ok"}


# Self-hosting stays a single process: serve the static frontend from the
# same app, mounted last so it never shadows the API routes above.
FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
