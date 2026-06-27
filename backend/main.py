"""
FastAPI backend for the Offline AI Assistant.

Endpoints:
  GET  /              → serves the frontend HTML
  GET  /health        → Ollama status + available models
  POST /chat          → SSE stream of tokens
  POST /command       → execute a slash command
  GET  /conversations → list saved conversations
  POST /conversations/{name}/load → load a conversation
  DELETE /conversations/{name}    → delete a saved conversation
"""

from __future__ import annotations

import logging
import os
import json
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, Depends, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    HTMLResponse,
    StreamingResponse,
    JSONResponse,
    FileResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uuid
import pyotp

from chat_engine import ChatEngine
from commands import is_command, parse_command, handle_command
from config import CONVERSATIONS_DIR, HOST, PORT, DEFAULT_MODEL, AUTH_SECRET

# ──────────────────────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
# App + global state
# ──────────────────────────────────────────────────────────────
app = FastAPI(title="Offline AI Assistant", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# One global ChatEngine per server process (single-user desktop app).
# For multi-user, swap this for a session dict keyed by cookie/token.
engine = ChatEngine(model=DEFAULT_MODEL)

# ──────────────────────────────────────────────────────────────
# Authentication state
# ──────────────────────────────────────────────────────────────
VALID_SESSIONS = set()
LAST_TOTP = None

def verify_auth(request: Request):
    session_id = request.cookies.get("session_id")
    if session_id not in VALID_SESSIONS:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return True

# ──────────────────────────────────────────────────────────────
# Frontend serving
# ──────────────────────────────────────────────────────────────
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

# Mount static files (CSS, JS)
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def serve_index(request: Request):
    session_id = request.cookies.get("session_id")
    if session_id in VALID_SESSIONS:
        index_path = FRONTEND_DIR / "index.html"
        if index_path.exists():
            return HTMLResponse(content=index_path.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>Frontend not found.</h1>", status_code=404)
    else:
        login_path = FRONTEND_DIR / "login.html"
        if login_path.exists():
            return HTMLResponse(content=login_path.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>Login page not found.</h1>", status_code=404)

class LoginRequest(BaseModel):
    code: str

@app.post("/auth/login")
async def login(req: LoginRequest, response: Response):
    global LAST_TOTP
    # Prevent replay attack within 30 seconds
    if req.code == LAST_TOTP:
        raise HTTPException(status_code=401, detail="Code already used. Wait for a new one.")
        
    totp = pyotp.TOTP(AUTH_SECRET)
    if totp.verify(req.code):
        LAST_TOTP = req.code
        session_id = str(uuid.uuid4())
        VALID_SESSIONS.add(session_id)
        # Set HttpOnly cookie for security
        response.set_cookie(key="session_id", value=session_id, httponly=True, max_age=86400)
        return {"success": True}
        
    raise HTTPException(status_code=401, detail="Invalid code")


# ──────────────────────────────────────────────────────────────
# Health check
# ──────────────────────────────────────────────────────────────
@app.get("/health", dependencies=[Depends(verify_auth)])
async def health():
    running = await ChatEngine.is_api_reachable()
    models = await ChatEngine.list_models() if running else []
    return {
        "api_reachable": running,
        "current_model": engine.model,
        "available_models": models,
        "message_count": engine.memory.message_count,
        "long_term_memory": engine.memory.long.available,
    }


# ──────────────────────────────────────────────────────────────
# Chat (SSE streaming)
# ──────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    message: str
    model: Optional[str] = None


async def _token_stream(message: str):
    """Async generator that yields SSE-formatted token events."""
    async for token in engine.stream_response(message):
        # Escape newlines so SSE stays on one data: line
        safe_token = token.replace("\n", "\\n")
        yield f"data: {safe_token}\n\n"
    yield "data: [DONE]\n\n"


@app.post("/chat", dependencies=[Depends(verify_auth)])
async def chat(req: ChatRequest):
    # Allow per-request model override
    if req.model and req.model != engine.model:
        engine.model = req.model
        logger.info("Model switched to: %s", engine.model)

    # Handle slash commands inline
    if is_command(req.message):
        cmd, arg = parse_command(req.message)
        result = handle_command(cmd, arg, engine)

        async def command_stream():
            msg = result["message"].replace("\n", "\\n")
            yield f"data: {msg}\n\n"
            yield "data: [DONE]\n\n"

            # If a conversation was loaded, send its history
            if result["action"] == "loaded" and result.get("data"):
                loaded_data = json.dumps(result["data"]).replace("\n", "\\n")
                yield f"data: [LOAD]{loaded_data}\n\n"

        return StreamingResponse(command_stream(), media_type="text/event-stream")

    return StreamingResponse(
        _token_stream(req.message),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ──────────────────────────────────────────────────────────────
# Conversation management
# ──────────────────────────────────────────────────────────────
class SaveRequest(BaseModel):
    name: Optional[str] = None


@app.get("/conversations", dependencies=[Depends(verify_auth)])
async def list_conversations():
    save_dir = Path(CONVERSATIONS_DIR)
    if not save_dir.exists():
        return {"conversations": []}
    files = sorted(save_dir.glob("*.json"))
    result = []
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            count = len(data.get("messages", []))
        except Exception:
            count = 0
        result.append({"name": f.stem, "messages": count})
    return {"conversations": result}


@app.post("/conversations/save", dependencies=[Depends(verify_auth)])
async def save_conversation(req: SaveRequest):
    from commands import _cmd_save
    result = _cmd_save(req.name, engine)
    return result


@app.post("/conversations/{name}/load", dependencies=[Depends(verify_auth)])
async def load_conversation(name: str):
    from commands import _cmd_load
    result = _cmd_load(name, engine)
    return result


@app.delete("/conversations/{name}", dependencies=[Depends(verify_auth)])
async def delete_conversation(name: str):
    safe = "".join(c for c in name if c.isalnum() or c in ("_", "-"))
    path = Path(CONVERSATIONS_DIR) / f"{safe}.json"
    if not path.exists():
        return JSONResponse({"error": "Not found"}, status_code=404)
    path.unlink()
    return {"message": f"Deleted `{safe}`."}


# ──────────────────────────────────────────────────────────────
# Clear
# ──────────────────────────────────────────────────────────────
@app.post("/clear", dependencies=[Depends(verify_auth)])
async def clear_chat():
    engine.memory.clear()
    return {"message": "Memory cleared."}


# ──────────────────────────────────────────────────────────────
# Model management
# ──────────────────────────────────────────────────────────────
@app.get("/models", dependencies=[Depends(verify_auth)])
async def get_models():
    models = await ChatEngine.list_models()
    return {"models": models, "current": engine.model}


class ModelRequest(BaseModel):
    model: str


@app.post("/models/switch", dependencies=[Depends(verify_auth)])
async def switch_model(req: ModelRequest):
    old = engine.model
    engine.model = req.model
    return {"previous": old, "current": engine.model}


# ──────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    display_host = "localhost" if HOST == "0.0.0.0" else HOST
    logger.info("🚀 Starting Offline AI Assistant at http://%s:%d", display_host, PORT)
    logger.info("📦 Default model: %s", DEFAULT_MODEL)
    uvicorn.run("main:app", host=HOST, port=PORT, reload=False, log_level="info")
