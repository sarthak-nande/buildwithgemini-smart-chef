"""Minimal FastAPI proxy for a deployed A2A agent with User Auth and Firestore Chat History.

The browser talks ONLY to this proxy (same origin, no CORS, no GCP creds in the
browser). The proxy authenticates with Application Default Credentials, manages
user auth and chat history in Cloud Firestore, and forwards chat to the deployed agent.
"""

import os
import time
import uuid

import google.auth
import google.auth.transport.requests
import httpx
from a2a.client import ClientConfig, ClientFactory
from a2a.types import (
    AgentCard,
    FilePart,
    Message,
    Part,
    Role,
    TaskArtifactUpdateEvent,
    TextPart,
    TransportProtocol,
)
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from google.cloud import firestore

RESOURCE = os.environ["AGENT_ENGINE_RESOURCE_NAME"]
AGENT_DIRECTORY = os.environ.get("AGENT_DIRECTORY", "app")
LOCATION = RESOURCE.split("/locations/")[1].split("/")[0]

FIRESTORE_PROJECT = os.environ.get("FIRESTORE_PROJECT", "qwiklabs-gcp-01-69e8752c22fd")
db = firestore.Client(project=FIRESTORE_PROJECT)

A2A_BASE = (
    f"https://{LOCATION}-aiplatform.googleapis.com/reasoningEngines/v1/"
    f"{RESOURCE}/api/a2a/{AGENT_DIRECTORY}"
)
A2A_CARD_URL = f"{A2A_BASE}/.well-known/agent-card.json"
_A2UI_MIME = "application/json+a2ui"

_creds, _ = google.auth.default(
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
)


def _auth_headers() -> dict[str, str]:
    _creds.refresh(google.auth.transport.requests.Request())
    return {
        "Authorization": f"Bearer {_creds.token}",
        "Content-Type": "application/json",
    }


app = FastAPI()


@app.exception_handler(Exception)
async def _json_errors(request: Request, exc: Exception):
    return JSONResponse(
        status_code=200,
        content={
            "parts": [{"kind": "text", "text": f"Error: {type(exc).__name__}: {exc}"}]
        },
    )


# Context per user_id:chat_id pair
_contexts: dict[str, str] = {}
_card: AgentCard | None = None


async def _get_card(client: httpx.AsyncClient) -> AgentCard:
    global _card
    if _card is None:
        resp = await client.get(A2A_CARD_URL)
        resp.raise_for_status()
        card = AgentCard(**resp.json())
        card.url = A2A_BASE
        _card = card
    return _card


def _extract_parts(parts: list) -> list[dict]:
    out: list[dict] = []
    for p in parts:
        root = getattr(p, "root", p)
        if isinstance(root, TextPart) and getattr(root, "text", None):
            out.append({"kind": "text", "text": root.text})
        elif getattr(root, "data", None) is not None:
            meta = getattr(root, "metadata", None) or {}
            mime = meta.get("mimeType") if isinstance(meta, dict) else None
            if mime == _A2UI_MIME:
                out.append({"kind": "a2ui", "data": root.data})
        elif isinstance(root, FilePart):
            uri = getattr(getattr(root, "file", None), "uri", None)
            if uri:
                out.append({"kind": "text", "text": uri})
    return out


# --- User Authentication Endpoints ---

@app.post("/api/auth/register")
async def register(req: Request):
    body = await req.json()
    username = body.get("username", "").strip().lower()
    password = body.get("password", "").strip()
    name = body.get("name", "").strip() or username.title()

    if not username or not password:
        return JSONResponse({"status": "error", "message": "Username and password required."}, status_code=400)

    user_ref = db.collection("users").document(username)
    if user_ref.get().exists:
        return JSONResponse({"status": "error", "message": "Username already exists. Please login."}, status_code=400)

    user_data = {
        "username": username,
        "password": password,  # simple demonstration auth
        "name": name,
        "created_at": time.time(),
    }
    user_ref.set(user_data)
    return JSONResponse({"status": "success", "username": username, "name": name})


@app.post("/api/auth/login")
async def login(req: Request):
    body = await req.json()
    username = body.get("username", "").strip().lower()
    password = body.get("password", "").strip()

    if not username:
        return JSONResponse({"status": "error", "message": "Username is required."}, status_code=400)

    user_ref = db.collection("users").document(username)
    user_doc = user_ref.get()

    if not user_doc.exists:
        # Auto-create profile for demo user or new login
        name = username.title()
        user_ref.set({
            "username": username,
            "password": password,
            "name": name,
            "created_at": time.time(),
        })
        return JSONResponse({"status": "success", "username": username, "name": name})

    data = user_doc.to_dict()
    if data.get("password") and data.get("password") != password:
        return JSONResponse({"status": "error", "message": "Incorrect password."}, status_code=400)

    return JSONResponse({"status": "success", "username": username, "name": data.get("name", username.title())})


# --- Chat History Endpoints ---

@app.get("/api/chats")
async def list_chats(user_id: str):
    if not user_id:
        return JSONResponse({"sessions": []})
    
    sessions_ref = db.collection("users").document(user_id).collection("sessions")
    docs = sessions_ref.order_by("updated_at", direction=firestore.Query.DESCENDING).stream()
    
    sessions = []
    for doc in docs:
        d = doc.to_dict()
        sessions.append({
            "id": doc.id,
            "title": d.get("title", "New Chat"),
            "created_at": d.get("created_at", 0),
            "updated_at": d.get("updated_at", 0),
        })
    return JSONResponse({"sessions": sessions})


@app.post("/api/chats/new")
async def new_chat(req: Request):
    body = await req.json()
    user_id = body.get("user_id") or "guest"
    chat_id = str(uuid.uuid4())
    now = time.time()

    session_ref = db.collection("users").document(user_id).collection("sessions").document(chat_id)
    session_ref.set({
        "title": "New Chat",
        "created_at": now,
        "updated_at": now,
    })
    return JSONResponse({"chat_id": chat_id, "title": "New Chat"})


@app.get("/api/chats/{chat_id}")
async def get_chat_history(chat_id: str, user_id: str):
    if not user_id or not chat_id:
        return JSONResponse({"messages": []})

    messages_ref = db.collection("users").document(user_id).collection("sessions").document(chat_id).collection("messages")
    docs = messages_ref.order_by("timestamp", direction=firestore.Query.ASCENDING).stream()

    messages = [doc.to_dict() for doc in docs]
    return JSONResponse({"chat_id": chat_id, "messages": messages})


# --- Primary Chat Relay Endpoint ---

@app.post("/chat")
async def chat(req: Request):
    body = await req.json()
    message = body.get("message", "")
    user_id = body.get("user_id") or "guest"
    chat_id = body.get("chat_id") or str(uuid.uuid4())
    context_key = f"{user_id}:{chat_id}"
    now = time.time()

    # 1. Save user message to Firestore
    session_ref = db.collection("users").document(user_id).collection("sessions").document(chat_id)
    session_doc = session_ref.get()

    if not session_doc.exists:
        title = message[:35] + ("..." if len(message) > 35 else "") or "New Chat"
        session_ref.set({
            "title": title,
            "created_at": now,
            "updated_at": now,
        })
    else:
        current_data = session_doc.to_dict()
        if current_data.get("title") == "New Chat" and message:
            title = message[:35] + ("..." if len(message) > 35 else "")
            session_ref.update({"title": title, "updated_at": now})
        else:
            session_ref.update({"updated_at": now})

    msg_id_user = str(uuid.uuid4())
    user_msg_data = {
        "role": "user",
        "parts": [{"kind": "text", "text": message}],
        "timestamp": now,
    }
    session_ref.collection("messages").document(msg_id_user).set(user_msg_data)

    parts: list[dict] = []

    async with httpx.AsyncClient(headers=_auth_headers(), timeout=120) as client:
        card = await _get_card(client)
        factory = ClientFactory(
            ClientConfig(
                supported_transports=[
                    TransportProtocol.jsonrpc,
                    TransportProtocol.http_json,
                ],
                httpx_client=client,
            )
        )
        a2a_client = factory.create(card)

        msg = Message(
            message_id=str(uuid.uuid4()),
            role=Role.user,
            parts=[Part(root=TextPart(text=message))],
            context_id=_contexts.get(context_key),
        )

        last_task = None
        got_artifact_update = False
        async for event in a2a_client.send_message(msg):
            if not isinstance(event, tuple):
                continue
            task, update = event
            if task is not None:
                last_task = task
                if getattr(task, "context_id", None):
                    _contexts[context_key] = task.context_id
            if isinstance(update, TaskArtifactUpdateEvent):
                got_artifact_update = True
                parts.extend(_extract_parts(update.artifact.parts))

        if not got_artifact_update and last_task is not None:
            for artifact in getattr(last_task, "artifacts", None) or []:
                parts.extend(_extract_parts(artifact.parts))

    if not parts:
        parts = [{"kind": "text", "text": "(The agent didn't return a reply.)"}]

    # 2. Save agent response to Firestore
    msg_id_agent = str(uuid.uuid4())
    agent_msg_data = {
        "role": "model",
        "parts": parts,
        "timestamp": time.time(),
    }
    session_ref.collection("messages").document(msg_id_agent).set(agent_msg_data)

    return JSONResponse({"parts": parts, "chat_id": chat_id})


app.mount("/", StaticFiles(directory="static", html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
