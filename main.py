import os
import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from database import db, create_document, get_documents
from schemas import User, Verification, Session, Message, Contact

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def read_root():
    return {"message": "WhatsApp-style MVP Backend Running"}


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }
    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
            response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"
            response["connection_status"] = "Connected"
            try:
                response["collections"] = db.list_collection_names()[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️ Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️ Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"
    return response


# Utilities

def generate_code(n: int = 6) -> str:
    return ''.join(secrets.choice(string.digits) for _ in range(n))


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# Models for requests
class StartVerificationRequest(BaseModel):
    phone: str


class VerifyCodeRequest(BaseModel):
    phone: str
    code: str
    name: Optional[str] = None
    photo_url: Optional[str] = None


class ContactSyncItem(BaseModel):
    name: Optional[str] = None
    phone: str


class SendMessageRequest(BaseModel):
    token: str
    recipient_id: str
    text: Optional[str] = None
    ciphertext: Optional[str] = None
    nonce: Optional[str] = None


class TokenRequest(BaseModel):
    token: str


# In-memory websocket connections (not for persistence)
active_connections: dict[str, WebSocket] = {}


# A. Account & Identity
@app.post("/auth/start")
async def start_verification(req: StartVerificationRequest):
    code = generate_code()
    expires = now_utc() + timedelta(minutes=10)
    v = Verification(phone=req.phone, code=code, expires_at=expires, attempts=0)
    create_document("verification", v)
    # In real life, send SMS. Here we return the code for demo/testing.
    return {"phone": req.phone, "code": code, "expires_at": expires}


@app.post("/auth/verify")
async def verify_code(req: VerifyCodeRequest):
    # find latest verification for phone
    records = get_documents("verification", {"phone": req.phone})
    if not records:
        raise HTTPException(status_code=400, detail="No verification started")
    latest = sorted(records, key=lambda x: x.get("created_at"), reverse=True)[0]

    if latest.get("expires_at") and latest["expires_at"] < now_utc():
        raise HTTPException(status_code=400, detail="Code expired")
    if latest.get("code") != req.code:
        raise HTTPException(status_code=400, detail="Invalid code")

    # upsert user by phone
    users = get_documents("user", {"phone": req.phone})
    if users:
        user_doc = users[0]
        user_id = str(user_doc.get("_id"))
    else:
        user = User(phone=req.phone, name=req.name or "New User", photo_url=req.photo_url)
        user_id = create_document("user", user)

    # create session
    token = secrets.token_urlsafe(32)
    expires_at = now_utc() + timedelta(days=30)
    session = Session(user_id=user_id, token=token, expires_at=expires_at)
    create_document("session", session)

    return {"token": token, "user_id": user_id}


# Helper to get user by token

def require_user(token: str):
    sessions = get_documents("session", {"token": token})
    if not sessions:
        raise HTTPException(status_code=401, detail="Invalid token")
    session = sessions[0]
    if session.get("expires_at") and session["expires_at"] < now_utc():
        raise HTTPException(status_code=401, detail="Session expired")
    return session["user_id"]


# C. Contacts
@app.post("/contacts/sync")
async def sync_contacts(token_req: TokenRequest, contacts: List[ContactSyncItem]):
    user_id = require_user(token_req.token)
    # Find which of the phones are registered
    phones = [c.phone for c in contacts]
    existing_users = get_documents("user", {"phone": {"$in": phones}})
    by_phone = {u["phone"]: u for u in existing_users}
    results = []
    for c in contacts:
        u = by_phone.get(c.phone)
        if u:
            # upsert contact link
            create_document("contact", Contact(user_id=user_id, contact_user_id=str(u["_id"]), contact_name=c.name, phone=c.phone))
            results.append({
                "user_id": str(u["_id"]),
                "name": u.get("name"),
                "phone": u.get("phone"),
                "photo_url": u.get("photo_url"),
            })
    return {"matched": results}


@app.get("/contacts")
async def list_contacts(token: str):
    user_id = require_user(token)
    links = get_documents("contact", {"user_id": user_id})
    contact_ids = [l["contact_user_id"] for l in links]
    if not contact_ids:
        return []
    docs = get_documents("user", {"_id": {"$in": [__import__('bson').ObjectId(cid) for cid in contact_ids]}})
    return [{"user_id": str(d["_id"]), "name": d.get("name"), "phone": d.get("phone"), "photo_url": d.get("photo_url")} for d in docs]


# B. Messaging
@app.post("/messages/send")
async def send_message(req: SendMessageRequest):
    sender_id = require_user(req.token)
    msg = Message(
        sender_id=sender_id,
        recipient_id=req.recipient_id,
        text=req.text,
        ciphertext=req.ciphertext,
        nonce=req.nonce,
        status="sent",
        sent_at=now_utc(),
    )
    msg_id = create_document("message", msg)

    # Push via websocket if recipient online
    ws = active_connections.get(req.recipient_id)
    if ws:
        await ws.send_json({
            "type": "message",
            "_id": msg_id,
            "sender_id": sender_id,
            "recipient_id": req.recipient_id,
            "text": req.text,
            "ciphertext": req.ciphertext,
            "nonce": req.nonce,
            "sent_at": msg.sent_at.isoformat(),
        })
        # mark delivered
        db["message"].update_one({"_id": __import__('bson').ObjectId(msg_id)}, {"$set": {"status": "delivered", "delivered_at": now_utc()}})
    return {"message_id": msg_id}


@app.get("/messages/history")
async def get_history(token: str, peer_user_id: str):
    user_id = require_user(token)
    # Get both directions
    msgs = get_documents("message", {"$or": [
        {"sender_id": user_id, "recipient_id": peer_user_id},
        {"sender_id": peer_user_id, "recipient_id": user_id},
    ]})
    # sort by sent_at/created_at
    def ts(m):
        return m.get("sent_at") or m.get("created_at")
    msgs_sorted = sorted(msgs, key=ts)
    # redact ciphertext server-side not required for MVP; return as-is
    return [
        {
            "_id": str(m["_id"]),
            "sender_id": m.get("sender_id"),
            "recipient_id": m.get("recipient_id"),
            "text": m.get("text"),
            "ciphertext": m.get("ciphertext"),
            "nonce": m.get("nonce"),
            "status": m.get("status"),
            "sent_at": (m.get("sent_at") or m.get("created_at")).isoformat() if (m.get("sent_at") or m.get("created_at")) else None,
            "delivered_at": m.get("delivered_at").isoformat() if m.get("delivered_at") else None,
            "read_at": m.get("read_at").isoformat() if m.get("read_at") else None,
        }
        for m in msgs_sorted
    ]


class ReadReceiptRequest(BaseModel):
    token: str
    message_ids: List[str]


@app.post("/messages/read")
async def mark_read(req: ReadReceiptRequest):
    user_id = require_user(req.token)
    from bson import ObjectId
    ids = [ObjectId(i) for i in req.message_ids]
    db["message"].update_many({"_id": {"$in": ids}, "recipient_id": user_id}, {"$set": {"status": "read", "read_at": now_utc()}})
    # Notify senders if online
    for oid in ids:
        m = db["message"].find_one({"_id": oid})
        if not m:
            continue
        ws = active_connections.get(m.get("sender_id"))
        if ws:
            await ws.send_json({"type": "read", "message_id": str(oid), "read_at": now_utc().isoformat(), "recipient_id": user_id})
    return {"updated": len(ids)}


# Realtime via WebSocket
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    try:
        # Expect token first
        init = await ws.receive_json()
        token = init.get("token")
        user_id = require_user(token)
        active_connections[user_id] = ws
        await ws.send_json({"type": "connected", "user_id": user_id})

        while True:
            # Keep-alive or client messages (not used)
            _ = await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        # Remove on disconnect
        for uid, sock in list(active_connections.items()):
            if sock is ws:
                active_connections.pop(uid, None)
                break


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
