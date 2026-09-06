import asyncio
import os
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from openai import AsyncOpenAI
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from redis.asyncio import Redis
from sqlalchemy import String, Text, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from starlette.responses import Response
from passlib.context import CryptContext

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://app:app@db:5432/aiqa")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
JWT_SECRET = os.getenv("JWT_SECRET", "change-me-in-production")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "60"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "20"))
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "2"))

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

REQUEST_COUNT = Counter("http_requests_total", "HTTP requests", ["method", "path", "status"])
REQUEST_LATENCY = Histogram("http_request_latency_seconds", "HTTP request latency", ["method", "path"])
LLM_COUNT = Counter("llm_requests_total", "LLM requests", ["status"])
LLM_TOKENS = Counter("llm_tokens_total", "LLM tokens", ["type"])

class Base(DeclarativeBase):
    pass

class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(100), index=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)

engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
redis: Redis | None = None
llm_client: AsyncOpenAI | None = None

# Demo users. In production these belong in PostgreSQL/IdP.
USERS = {
    "admin": {"password_hash": pwd_context.hash("Admin@123"), "role": "admin"},
    "user": {"password_hash": pwd_context.hash("User@123"), "role": "user"},
    "readonly": {"password_hash": pwd_context.hash("ReadOnly@123"), "role": "readonly"},
}

def authenticate(username: str, password: str):
    record = USERS.get(username)
    if not record or not pwd_context.verify(password, record["password_hash"]):
        return None
    return {"username": username, "role": record["role"]}

def create_token(subject: str, role: str) -> str:
    now = int(time.time())
    payload = {"sub": subject, "role": role, "iat": now, "exp": now + JWT_EXPIRE_MINUTES * 60}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

async def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_error = HTTPException(status_code=401, detail="Invalid or expired token")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        username = payload.get("sub")
        role = payload.get("role")
        if not username or not role:
            raise credentials_error
        return {"username": username, "role": role}
    except JWTError:
        raise credentials_error

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session

@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis, llm_client
    redis = Redis.from_url(REDIS_URL, decode_responses=True)
    if OPENAI_API_KEY:
        llm_client = AsyncOpenAI(api_key=OPENAI_API_KEY, timeout=LLM_TIMEOUT_SECONDS, max_retries=0)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    if redis:
        await redis.close()
    await engine.dispose()

app = FastAPI(title="Production AI Q&A API", version="1.0.0", lifespan=lifespan)

@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start = time.perf_counter()
    try:
        response = await call_next(request)
        return response
    finally:
        elapsed = time.perf_counter() - start
        path = request.url.path
        REQUEST_COUNT.labels(request.method, path, str(getattr(locals().get("response", None), "status_code", 500))).inc()
        REQUEST_LATENCY.labels(request.method, path).observe(elapsed)

@app.get("/health")
async def health():
    checks = {"api": "ok"}
    try:
        assert redis is not None
        await redis.ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "error"
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "error"
    healthy = all(v == "ok" for v in checks.values())
    return {"status": "ok" if healthy else "degraded", "checks": checks}

@app.get("/metrics")
async def metrics(current_user=Depends(get_current_user)):
    if current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.post("/auth/login")
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    user = authenticate(form_data.username, form_data.password)
    if not user:
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    return {"access_token": create_token(user["username"], user["role"]), "token_type": "bearer", "role": user["role"]}

async def call_llm(question: str) -> tuple[str, int, int]:
    if not llm_client:
        raise RuntimeError("LLM provider is not configured")
    last_error = None
    for attempt in range(LLM_MAX_RETRIES + 1):
        try:
            response = await llm_client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": "You are a concise, accurate assistant. If uncertain, say so."},
                    {"role": "user", "content": question},
                ],
            )
            usage = response.usage
            prompt_tokens = usage.prompt_tokens if usage else 0
            completion_tokens = usage.completion_tokens if usage else 0
            return response.choices[0].message.content or "No answer generated.", prompt_tokens, completion_tokens
        except Exception as exc:
            last_error = exc
            if attempt < LLM_MAX_RETRIES:
                await asyncio.sleep(0.5 * (2 ** attempt))
    raise last_error or RuntimeError("LLM request failed")

@app.post("/chat")
async def chat(payload: dict, current_user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    question = str(payload.get("question", "")).strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")
    if len(question) > 4000:
        raise HTTPException(status_code=413, detail="question is too long")

    # Per-user distributed rate limit: 30 requests/minute.
    try:
        assert redis is not None
        key = f"rate:{current_user['username']}:{int(time.time() // 60)}"
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, 70)
        if count > 30:
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
    except HTTPException:
        raise
    except Exception:
        # Graceful degradation: Redis outage should not make the core API unusable.
        pass

    started = time.perf_counter()
    try:
        answer, prompt_tokens, completion_tokens = await call_llm(question)
        LLM_COUNT.labels("success").inc()
        LLM_TOKENS.labels("prompt").inc(prompt_tokens)
        LLM_TOKENS.labels("completion").inc(completion_tokens)
    except asyncio.TimeoutError:
        LLM_COUNT.labels("timeout").inc()
        raise HTTPException(status_code=504, detail="LLM request timed out")
    except Exception:
        LLM_COUNT.labels("error").inc()
        raise HTTPException(status_code=502, detail="LLM provider unavailable")

    db.add(ChatMessage(username=current_user["username"], question=question, answer=answer))
    await db.commit()
    return {
        "answer": answer,
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
    }
