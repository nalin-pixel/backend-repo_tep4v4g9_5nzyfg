"""
Database Schemas for WhatsApp-style MVP

Each Pydantic model corresponds to a MongoDB collection where the
collection name is the lowercase of the class name.

- User -> user
- Session -> session
- Verification -> verification
- Message -> message
- Contact -> contact
- DeviceKey -> devicekey (optional future use for E2E)
"""

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class User(BaseModel):
    phone: str = Field(..., description="E.164 formatted phone number")
    name: str = Field(..., description="Display name")
    photo_url: Optional[str] = Field(None, description="Profile photo URL")
    about: Optional[str] = Field(None, description="About text")


class Session(BaseModel):
    user_id: str = Field(..., description="Owner user id")
    token: str = Field(..., description="Opaque session token")
    expires_at: datetime = Field(..., description="Expiry timestamp (UTC)")


class Verification(BaseModel):
    phone: str = Field(..., description="Phone being verified")
    code: str = Field(..., description="6-digit OTP code")
    expires_at: datetime = Field(..., description="Expiry timestamp (UTC)")
    attempts: int = Field(0, description="Number of attempts made")


class Message(BaseModel):
    sender_id: str = Field(...)
    recipient_id: str = Field(...)
    text: Optional[str] = Field(None, description="Plaintext body (dev-only)")
    ciphertext: Optional[str] = Field(None, description="Encrypted body (optional)")
    nonce: Optional[str] = Field(None, description="Encryption nonce (optional)")
    status: str = Field("sent", description="sent | delivered | read")
    sent_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    read_at: Optional[datetime] = None


class Contact(BaseModel):
    user_id: str = Field(..., description="Owner user id")
    contact_user_id: str = Field(..., description="The user id of the contact that also uses the app")
    contact_name: Optional[str] = Field(None, description="Name from address book")
    phone: str = Field(..., description="Raw phone in address book")


class DeviceKey(BaseModel):
    user_id: str
    public_key: str = Field(..., description="Client public key for E2E (future)")
    device_id: Optional[str] = Field(None)


# Additional helper response models (not stored directly)
class ChatSummary(BaseModel):
    peer_user_id: str
    last_text: Optional[str] = None
    last_time: Optional[datetime] = None
    unread_count: int = 0
