from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import Base, engine
from app.routers import auth, balances, expenses, households, users

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


@app.get("/health")
def health():
    return {"status": "ok"}
