from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.migrations import run_migrations
from app.routers import auth, balances, categories, expenses, households, push, users

run_migrations()

app = FastAPI(title="Halves", version=__version__)

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
app.include_router(categories.router)
app.include_router(push.router)


@app.get("/health")
def health():
    return {"status": "ok", "version": __version__}


# Self-hosting stays a single process: serve the static frontend from the
# same app, mounted last so it never shadows the API routes above.
FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
