import os
import ipaddress
import base64
import gzip
import hashlib
import html
import mimetypes
import json
import math
import re
import socket
import secrets
import smtplib
import struct
import threading
import time
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from functools import wraps
from uuid import uuid4

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, text
from sqlalchemy.orm import joinedload, selectinload
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "nexaline-dev-secret")
os.makedirs(app.instance_path, exist_ok=True)

database_url = os.environ.get("DATABASE_URL")
if not database_url:
    fallback_db = os.environ.get(
        "SQLITE_PATH",
        "/tmp/nexaline.db" if os.environ.get("RENDER") else os.path.join(app.instance_path, "nexaline.db"),
    )
    database_url = "sqlite:///" + fallback_db.replace("\\", "/")

if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading", max_http_buffer_size=10_000_000)


@app.after_request
def gzip_text_responses(response):
    accept_encoding = request.headers.get("Accept-Encoding", "").lower()
    mime_type = (response.mimetype or "").lower()
    compressible_types = {
        "application/javascript",
        "application/json",
        "application/manifest+json",
        "application/xml",
    }
    if (
        "gzip" not in accept_encoding
        or request.method == "HEAD"
        or request.headers.get("Range")
        or request.path.startswith("/socket.io/")
        or response.status_code < 200
        or response.status_code in {204, 304}
        or response.headers.get("Content-Encoding")
        or mime_type == "text/event-stream"
        or not (mime_type.startswith("text/") or mime_type in compressible_types)
    ):
        return response

    if response.direct_passthrough:
        response.direct_passthrough = False
    body = response.get_data()
    if len(body) < 1024:
        return response

    compressed = gzip.compress(body, compresslevel=6)
    if len(compressed) >= len(body):
        return response

    response.set_data(compressed)
    response.headers["Content-Encoding"] = "gzip"
    response.headers["Content-Length"] = str(len(compressed))
    vary = [item.strip() for item in response.headers.get("Vary", "").split(",") if item.strip()]
    if "Accept-Encoding" not in vary:
        vary.append("Accept-Encoding")
    response.headers["Vary"] = ", ".join(vary)
    return response


connections = {}
typing_users = {}
DEV_ADMIN_TOKEN = "NexaLineAdmin2026!"
FULL_USER_DATA_RESET_KEY = "full_user_data_reset_2026_06_04"
MAX_BOOTSTRAP_MESSAGES = max(20, int(os.environ.get("MAX_BOOTSTRAP_MESSAGES", "35")))
ADMIN_MESSAGE_LIMIT_PER_CHAT = max(20, int(os.environ.get("ADMIN_MESSAGE_LIMIT_PER_CHAT", "40")))
ADMIN_ARCHIVE_MESSAGE_LIMIT = max(10, int(os.environ.get("ADMIN_ARCHIVE_MESSAGE_LIMIT", "30")))
ADMIN_ATTACHMENT_INLINE_LIMIT = max(20_000, int(os.environ.get("ADMIN_ATTACHMENT_INLINE_LIMIT", "160000")))
SCHEDULE_POLL_SECONDS = max(5, int(os.environ.get("SCHEDULE_POLL_SECONDS", "15")))
MAX_SCHEDULE_DAYS = max(1, int(os.environ.get("MAX_SCHEDULE_DAYS", "7")))
MAX_ATTACHMENT_DATA_URL_CHARS = max(250_000, int(os.environ.get("MAX_ATTACHMENT_DATA_URL_CHARS", "5_500_000")))
MAX_AI_AUDIO_DATA_URL_CHARS = max(80_000, int(os.environ.get("MAX_AI_AUDIO_DATA_URL_CHARS", "4_500_000")))
RECENT_MESSAGE_SCAN_LIMIT = max(MAX_BOOTSTRAP_MESSAGES * 2, int(os.environ.get("RECENT_MESSAGE_SCAN_LIMIT", "120")))
AI_TIMEOUT_SECONDS = max(4, int(os.environ.get("AI_TIMEOUT_SECONDS", "12")))
AI_MAX_CONTEXT_MESSAGES = max(8, int(os.environ.get("AI_MAX_CONTEXT_MESSAGES", "16")))
AI_MAX_CHATS = max(5, int(os.environ.get("AI_MAX_CHATS", "16")))
AI_MEMORY_MAX_ITEMS = max(40, int(os.environ.get("AI_MEMORY_MAX_ITEMS", "160")))
AI_RELEVANT_CHAT_LIMIT = max(3, int(os.environ.get("AI_RELEVANT_CHAT_LIMIT", "8")))
AI_RELEVANT_CHAT_MESSAGES = max(8, int(os.environ.get("AI_RELEVANT_CHAT_MESSAGES", "32")))
QR_LOGIN_TTL_SECONDS = max(60, int(os.environ.get("QR_LOGIN_TTL_SECONDS", "60")))
TWO_FACTOR_RESEND_SECONDS = max(45, int(os.environ.get("TWO_FACTOR_RESEND_SECONDS", "45")))
PUBLIC_SITE_URL = os.environ.get("PUBLIC_SITE_URL", os.environ.get("APP_PUBLIC_URL", "https://nexalineapp.xyz")).rstrip("/")
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "").strip()
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
DEFAULT_SMTP_HOST = "smtp.gmail.com"
DEFAULT_SMTP_PORT = 587
DEFAULT_SMTP_USERNAME = "nexalineapp@gmail.com"
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", f"mailto:{os.environ.get('SMTP_USERNAME', DEFAULT_SMTP_USERNAME)}").strip()
POINT_RULES = {
    "daily_login": 10,
    "message": 1,
    "friend_invite": 3,
    "friend_accept": 10,
    "group_join": 20,
    "story": 5,
    "voice_room_join": 50,
    "voice_room_10min": 5,
    "community_join": 20,
    "ai_chat": 10,
    "quest_daily": 50,
    "quest_weekly": 120,
    "quest_special": 200,
}
AVATAR_GRADIENTS = {
    "linear-gradient(135deg,#2ED3C6,#2F80FF)",
    "linear-gradient(135deg,#2F80FF,#E5485D)",
    "linear-gradient(135deg,#7B4DFF,#2ED3C6)",
    "linear-gradient(135deg,#F7C948,#E5485D)",
    "linear-gradient(135deg,#111827,#2F80FF)",
}
POINT_MILESTONES = [
    {"id": "first_light", "threshold": 2500, "title": "İlk Işık", "reward": "Başlangıç rozeti"},
    {"id": "nexa_explorer", "threshold": 5000, "title": "Nexa Kaşifi", "reward": "Kaşif rozeti"},
    {"id": "chat_master_level", "threshold": 15000, "title": "Sohbet Ustası", "reward": "Usta rozeti"},
    {"id": "nexa_elite", "threshold": 50000, "title": "Nexa Eliti", "reward": "Elit profil çerçevesi"},
    {"id": "nexa_legend", "threshold": 150000, "title": "Nexa Efsanesi", "reward": "Efsane profil çerçevesi"},
]
scheduled_delivery_lock = threading.Lock()
qr_login_lock = threading.Lock()
qr_login_sessions = {}
voice_room_lock = threading.Lock()
voice_rooms = {
    "general": {"id": "general", "title": "Nexa Meydan", "topic": "Herkese açık sohbet odası", "participants": {}},
    "study": {"id": "study", "title": "Odak Odası", "topic": "Sessiz çalışma ve kısa molalar", "participants": {}},
    "music": {"id": "music", "title": "Müzik Köşesi", "topic": "Şarkı, sohbet ve keşif", "participants": {}},
}


class IPv4SMTP(smtplib.SMTP):
    def _get_socket(self, host, port, timeout):
        addresses = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
        last_error = None
        for family, socktype, proto, _, address in addresses:
            try:
                sock = socket.socket(family, socktype, proto)
                sock.settimeout(timeout)
                sock.connect(address)
                return sock
            except OSError as error:
                last_error = error
                try:
                    sock.close()
                except OSError:
                    pass

        if last_error:
            raise last_error
        return super()._get_socket(host, port, timeout)


class User(db.Model):
    username = db.Column(db.String(80), primary_key=True)
    password_hash = db.Column(db.String(255), nullable=False)
    display_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), nullable=True)
    email_normalized = db.Column(db.String(255), nullable=True, index=True)
    email_verified = db.Column(db.Boolean, nullable=False, default=False)
    phone = db.Column(db.String(24), nullable=True)
    phone_normalized = db.Column(db.String(24), nullable=True, index=True, unique=True)
    phone_verified = db.Column(db.Boolean, nullable=False, default=False)
    profile_image = db.Column(db.Text, nullable=True)
    avatar = db.Column(db.String(8), nullable=False)
    avatar_gradient = db.Column(db.String(160), nullable=True)
    hide_last_seen = db.Column(db.Boolean, nullable=False, default=False)
    hide_online = db.Column(db.Boolean, nullable=False, default=False)
    disable_read_receipts = db.Column(db.Boolean, nullable=False, default=False)
    hide_email = db.Column(db.Boolean, nullable=False, default=True)
    privacy_settings = db.Column(db.JSON, nullable=True)
    points = db.Column(db.Integer, nullable=False, default=0)
    two_factor_enabled = db.Column(db.Boolean, nullable=False, default=False)
    theme_preference = db.Column(db.String(20), nullable=False, default="dark")
    font_size_preference = db.Column(db.String(20), nullable=False, default="medium")
    notification_sound = db.Column(db.String(40), nullable=False, default="classic")
    ai_settings = db.Column(db.JSON, nullable=True)
    about = db.Column(db.String(255), nullable=False, default="NexaLine kullanıyorum.")
    nearby_enabled = db.Column(db.Boolean, nullable=False, default=False)
    last_lat = db.Column(db.Float, nullable=True)
    last_lng = db.Column(db.Float, nullable=True)
    vault_pin_hash = db.Column(db.String(255), nullable=True)
    vault_failed_attempts = db.Column(db.Integer, nullable=False, default=0)
    vault_locked_until = db.Column(db.DateTime, nullable=True)
    last_daily_login = db.Column(db.DateTime, nullable=True)
    profile_bonus_awarded = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    last_seen = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


class DeviceSession(db.Model):
    id = db.Column(db.String(80), primary_key=True)
    username = db.Column(db.String(80), db.ForeignKey("user.username"), nullable=False, index=True)
    label = db.Column(db.String(120), nullable=False, default="NexaLine cihazi")
    user_agent = db.Column(db.Text, nullable=True)
    ip_address = db.Column(db.String(80), nullable=True)
    last_seen = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    revoked_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship("User")


class PushSubscription(db.Model):
    id = db.Column(db.String(40), primary_key=True)
    username = db.Column(db.String(80), db.ForeignKey("user.username"), nullable=False, index=True)
    endpoint = db.Column(db.Text, nullable=False, unique=True)
    subscription = db.Column(db.JSON, nullable=False)
    user_agent = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    user = db.relationship("User")


class Chat(db.Model):
    id = db.Column(db.String(140), primary_key=True)
    type = db.Column(db.String(20), nullable=False)
    title = db.Column(db.String(160), nullable=False)
    image = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    members = db.relationship("ChatMember", backref="chat", cascade="all, delete-orphan")
    messages = db.relationship("Message", backref="chat", cascade="all, delete-orphan", order_by="Message.created_at")


class ChatMember(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    chat_id = db.Column(db.String(140), db.ForeignKey("chat.id"), nullable=False)
    username = db.Column(db.String(80), db.ForeignKey("user.username"), nullable=False)
    is_admin = db.Column(db.Boolean, nullable=False, default=False)

    user = db.relationship("User")
    __table_args__ = (db.UniqueConstraint("chat_id", "username", name="unique_chat_member"),)


class Message(db.Model):
    id = db.Column(db.String(40), primary_key=True)
    chat_id = db.Column(db.String(140), db.ForeignKey("chat.id"), nullable=False)
    sender = db.Column(db.String(80), db.ForeignKey("user.username"), nullable=False)
    body = db.Column(db.Text, nullable=False, default="")
    attachment = db.Column(db.JSON, nullable=True)
    reply_to = db.Column(db.JSON, nullable=True)
    read_by = db.Column(db.JSON, nullable=False, default=list)
    reactions = db.Column(db.JSON, nullable=False, default=dict)
    versions = db.Column(db.JSON, nullable=False, default=list)
    deleted_for = db.Column(db.JSON, nullable=False, default=list)
    deleted_at = db.Column(db.DateTime, nullable=True)
    deleted_by = db.Column(db.String(80), nullable=True)
    edited_at = db.Column(db.DateTime, nullable=True)
    expires_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    sender_user = db.relationship("User")


class ScheduledMessage(db.Model):
    id = db.Column(db.String(40), primary_key=True)
    chat_id = db.Column(db.String(140), db.ForeignKey("chat.id"), nullable=False)
    sender = db.Column(db.String(80), db.ForeignKey("user.username"), nullable=False)
    body = db.Column(db.Text, nullable=False, default="")
    attachment = db.Column(db.JSON, nullable=True)
    reply_to = db.Column(db.JSON, nullable=True)
    expires_in_seconds = db.Column(db.Integer, nullable=True)
    send_at = db.Column(db.DateTime, nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    chat = db.relationship("Chat")
    sender_user = db.relationship("User")


class Story(db.Model):
    id = db.Column(db.String(40), primary_key=True)
    username = db.Column(db.String(80), db.ForeignKey("user.username"), nullable=False)
    body = db.Column(db.Text, nullable=False, default="")
    attachment = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    expires_at = db.Column(db.DateTime, nullable=False)

    user = db.relationship("User")
    views = db.relationship("StoryView", cascade="all, delete-orphan", back_populates="story")


class StoryView(db.Model):
    id = db.Column(db.String(40), primary_key=True)
    story_id = db.Column(db.String(40), db.ForeignKey("story.id"), nullable=False, index=True)
    viewer_username = db.Column(db.String(80), db.ForeignKey("user.username"), nullable=False, index=True)
    viewed_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    story = db.relationship("Story", back_populates="views")
    viewer = db.relationship("User")

    __table_args__ = (
        db.UniqueConstraint("story_id", "viewer_username", name="uq_story_viewer_once"),
    )


class UpdatePost(db.Model):
    id = db.Column(db.String(40), primary_key=True)
    username = db.Column(db.String(80), db.ForeignKey("user.username"), nullable=False, index=True)
    body = db.Column(db.Text, nullable=False, default="")
    media = db.Column(db.JSON, nullable=False, default=list)
    liked_by = db.Column(db.JSON, nullable=False, default=list)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)
    edited_at = db.Column(db.DateTime, nullable=True)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)

    user = db.relationship("User")


class CallLog(db.Model):
    id = db.Column(db.String(40), primary_key=True)
    chat_id = db.Column(db.String(140), db.ForeignKey("chat.id"), nullable=False)
    caller = db.Column(db.String(80), db.ForeignKey("user.username"), nullable=False)
    kind = db.Column(db.String(20), nullable=False, default="audio")
    status = db.Column(db.String(20), nullable=False, default="ended")
    seen_by = db.Column(db.JSON, nullable=False, default=list)
    duration_seconds = db.Column(db.Integer, nullable=False, default=0)
    started_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    ended_at = db.Column(db.DateTime, nullable=True)

    chat = db.relationship("Chat")
    caller_user = db.relationship("User")


class EmailVerification(db.Model):
    id = db.Column(db.String(40), primary_key=True)
    purpose = db.Column(db.String(40), nullable=False)
    username = db.Column(db.String(80), nullable=True)
    email = db.Column(db.String(255), nullable=False)
    email_normalized = db.Column(db.String(255), nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=True)
    code_hash = db.Column(db.String(255), nullable=False)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    expires_at = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


class PhoneVerification(db.Model):
    id = db.Column(db.String(40), primary_key=True)
    purpose = db.Column(db.String(40), nullable=False)
    username = db.Column(db.String(80), nullable=True)
    phone = db.Column(db.String(24), nullable=False)
    phone_normalized = db.Column(db.String(24), nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=True)
    code_hash = db.Column(db.String(255), nullable=False)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    provider = db.Column(db.String(32), nullable=False, default="local")
    provider_message_id = db.Column(db.String(120), nullable=True)
    expires_at = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


class SupportRequest(db.Model):
    id = db.Column(db.String(40), primary_key=True)
    username = db.Column(db.String(80), nullable=False, index=True)
    remembered_email = db.Column(db.String(255), nullable=True)
    remembered_email_normalized = db.Column(db.String(255), nullable=True, index=True)
    description = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="open")
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


class BlockedUser(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    blocker = db.Column(db.String(80), db.ForeignKey("user.username"), nullable=False)
    blocked = db.Column(db.String(80), db.ForeignKey("user.username"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (db.UniqueConstraint("blocker", "blocked", name="unique_blocked_user"),)


class ContactRequest(db.Model):
    id = db.Column(db.String(40), primary_key=True)
    from_username = db.Column(db.String(80), db.ForeignKey("user.username"), nullable=False)
    to_username = db.Column(db.String(80), db.ForeignKey("user.username"), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending")
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    responded_at = db.Column(db.DateTime, nullable=True)


class GroupInvite(db.Model):
    id = db.Column(db.String(40), primary_key=True)
    chat_id = db.Column(db.String(140), db.ForeignKey("chat.id"), nullable=False)
    inviter = db.Column(db.String(80), db.ForeignKey("user.username"), nullable=False)
    invitee = db.Column(db.String(80), db.ForeignKey("user.username"), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending")
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    responded_at = db.Column(db.DateTime, nullable=True)

    chat = db.relationship("Chat")


class HiddenChat(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), db.ForeignKey("user.username"), nullable=False)
    chat_id = db.Column(db.String(140), nullable=False)
    hidden_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (db.UniqueConstraint("username", "chat_id", name="unique_hidden_chat"),)


class ChatArchive(db.Model):
    id = db.Column(db.String(40), primary_key=True)
    username = db.Column(db.String(80), db.ForeignKey("user.username"), nullable=False)
    chat_id = db.Column(db.String(140), nullable=False)
    chat_title = db.Column(db.String(160), nullable=False)
    chat_type = db.Column(db.String(20), nullable=False)
    reason = db.Column(db.String(20), nullable=False, default="deleted")
    messages = db.Column(db.JSON, nullable=False, default=list)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    expires_at = db.Column(db.DateTime, nullable=False)


class PointLedger(db.Model):
    id = db.Column(db.String(40), primary_key=True)
    username = db.Column(db.String(80), db.ForeignKey("user.username"), nullable=False, index=True)
    amount = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.String(40), nullable=False, default="bonus")
    meta = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    user = db.relationship("User")


class AiTask(db.Model):
    id = db.Column(db.String(40), primary_key=True)
    username = db.Column(db.String(80), db.ForeignKey("user.username"), nullable=False, index=True)
    title = db.Column(db.String(180), nullable=False)
    description = db.Column(db.Text, nullable=True)
    repeat = db.Column(db.String(30), nullable=False, default="none")
    remind_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    user = db.relationship("User")


class AiMemory(db.Model):
    id = db.Column(db.String(40), primary_key=True)
    username = db.Column(db.String(80), db.ForeignKey("user.username"), nullable=False, index=True)
    chat_id = db.Column(db.String(140), nullable=True, index=True)
    role = db.Column(db.String(20), nullable=False)
    content = db.Column(db.Text, nullable=False, default="")
    provider = db.Column(db.String(40), nullable=True)
    meta = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)

    user = db.relationship("User")


class Community(db.Model):
    id = db.Column(db.String(40), primary_key=True)
    title = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text, nullable=True)
    category = db.Column(db.String(80), nullable=False, default="Genel")
    image = db.Column(db.Text, nullable=True)
    owner = db.Column(db.String(80), db.ForeignKey("user.username"), nullable=False, index=True)
    privacy = db.Column(db.String(30), nullable=False, default="public")
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    owner_user = db.relationship("User")
    members = db.relationship("CommunityMember", backref="community", cascade="all, delete-orphan")
    announcements = db.relationship("CommunityAnnouncement", backref="community", cascade="all, delete-orphan")


class CommunityMember(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    community_id = db.Column(db.String(40), db.ForeignKey("community.id"), nullable=False)
    username = db.Column(db.String(80), db.ForeignKey("user.username"), nullable=False)
    role = db.Column(db.String(30), nullable=False, default="member")
    joined_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    user = db.relationship("User")
    __table_args__ = (db.UniqueConstraint("community_id", "username", name="unique_community_member"),)


class CommunityAnnouncement(db.Model):
    id = db.Column(db.String(40), primary_key=True)
    community_id = db.Column(db.String(40), db.ForeignKey("community.id"), nullable=False)
    author = db.Column(db.String(80), db.ForeignKey("user.username"), nullable=False)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    author_user = db.relationship("User")


class VaultItem(db.Model):
    id = db.Column(db.String(40), primary_key=True)
    username = db.Column(db.String(80), db.ForeignKey("user.username"), nullable=False, index=True)
    kind = db.Column(db.String(30), nullable=False, default="note")
    title = db.Column(db.String(140), nullable=False)
    payload = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    user = db.relationship("User")


class AppSetting(db.Model):
    key = db.Column(db.String(80), primary_key=True)
    value = db.Column(db.JSON, nullable=False, default=dict)
    updated_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


NEXA_PLAY_GAMES = {"chess", "solitaire", "2048", "block-blast"}


def nexa_play_setting_key(username):
    return f"games:{(username or '').strip().lower()}"[:80]


def nexa_play_state(username):
    row = db.session.get(AppSetting, nexa_play_setting_key(username))
    saved = dict(row.value or {}) if row else {}
    return {
        "scores": dict(saved.get("scores") or {}),
        "sessions": dict(saved.get("sessions") or {}),
        "updatedAt": saved.get("updatedAt"),
    }


def save_nexa_play_state(username, payload):
    key = nexa_play_setting_key(username)
    row = db.session.get(AppSetting, key)
    current = nexa_play_state(username)
    game_id = str(payload.get("game") or "").strip().lower()
    if game_id not in NEXA_PLAY_GAMES:
        raise ValueError("Desteklenmeyen oyun.")

    score = max(0, min(10_000_000, int(payload.get("score") or 0)))
    session = payload.get("session")
    if not isinstance(session, dict):
        session = {}
    current["scores"][game_id] = max(score, int(current["scores"].get(game_id) or 0))
    current["sessions"][game_id] = session
    current["updatedAt"] = datetime.now(timezone.utc).isoformat()
    if row:
        row.value = current
        row.updated_at = datetime.now(timezone.utc)
    else:
        db.session.add(AppSetting(key=key, value=current))
    db.session.commit()
    return current


DEFAULT_DESIGN_SETTINGS = {
    "brandName": "NexaLine",
    "logoUrl": "/static/nexaline-logo.png",
    "colors": {
        "background": "#070a0f",
        "panel": "#0f141f",
        "panel2": "#161d2b",
        "panel3": "#202a3d",
        "line": "#26334f",
        "text": "#eef1f6",
        "muted": "#8b95a6",
        "blue": "#2f80ff",
        "red": "#e53945",
        "green": "#2ed3c6",
        "incoming": "#202631",
        "outgoing": "#0c5d4e",
        "chatBackground": "#11151d",
    },
    "text": {
        "chats": "Sohbetler",
        "groups": "Gruplar",
        "stories": "Akış",
        "explore": "Keşfet",
        "calls": "Aramalar",
        "friends": "Arkadaşlarım",
        "contacts": "Kişiler",
        "archives": "Arşiv",
        "searchPlaceholder": "Aratın veya yeni sohbet başlatın",
        "messagePlaceholder": "Bir mesaj yazın",
        "newChat": "Yeni sohbet",
        "newGroup": "Yeni grup",
        "newStory": "Durum ekle",
        "newCall": "Yeni arama",
        "noCalls": "Arama kaydı yok",
        "attachment": "Dosya",
        "emoji": "Emoji",
        "voice": "Sesli mesaj",
        "location": "Konum",
        "timed": "Zamanla",
        "send": "Gönder",
    },
    "desktop": {
        "fontSize": 16,
        "messageFontSize": 15,
        "sidebarWidth": 430,
        "railWidth": 82,
        "bubbleRadius": 14,
        "iconSize": 40,
        "listItemRadius": 16,
        "composerRadius": 34,
        "headerHeight": 64,
        "showNavLabels": True,
        "navOrder": ["chats", "calls", "explore", "groups", "stories", "friends", "contacts"],
        "composerOrder": ["attach", "emoji", "input", "voice", "send"],
    },
    "mobile": {
        "fontSize": 15,
        "messageFontSize": 14,
        "iconSize": 40,
        "bubbleRadius": 14,
        "listItemRadius": 16,
        "composerRadius": 34,
        "headerHeight": 64,
        "showNavLabels": True,
        "navOrder": ["stories", "calls", "chats", "contacts", "explore"],
        "composerOrder": ["attach", "emoji", "input", "voice", "send"],
    },
    # Legacy flat keys are kept so older deployed clients can still read a sane design.
    "fontSize": 16,
    "messageFontSize": 15,
    "sidebarWidth": 430,
    "bubbleRadius": 14,
    "iconSize": 40,
    "blue": "#2f80ff",
    "red": "#e53945",
    "green": "#2ed3c6",
}


DESIGN_SECTIONS = ("desktop", "mobile")
DESIGN_NAV_KEYS = ("chats", "calls", "explore", "groups", "stories", "friends", "contacts", "settings")
DESIGN_COMPOSER_KEYS = ("attach", "emoji", "input", "voice", "send", "location", "timed")


def deep_merge_dict(base, override):
    result = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge_dict(result[key], value)
        else:
            result[key] = value
    return result


def design_settings():
    row = db.session.get(AppSetting, "design")
    data = row.value if row and isinstance(row.value, dict) else {}
    merged = deep_merge_dict(DEFAULT_DESIGN_SETTINGS, data)
    for section in DESIGN_SECTIONS:
        screen = merged.get(section, {})
        order = [item for item in screen.get("navOrder", []) if item in DESIGN_NAV_KEYS]
        screen["navOrder"] = order + [item for item in DESIGN_NAV_KEYS if item not in order]
    return merged


def sanitize_design_settings(data):
    data = data if isinstance(data, dict) else {}
    current = design_settings()
    next_settings = deep_merge_dict(DEFAULT_DESIGN_SETTINGS, current)

    def clamp_number(source, key, minimum, maximum, fallback):
        try:
            return max(minimum, min(maximum, int(source.get(key))))
        except (AttributeError, TypeError, ValueError):
            return fallback

    def clean_color(value, fallback):
        value = str(value or "").strip()
        return value if re.fullmatch(r"#[0-9a-fA-F]{6}", value) else fallback

    def clean_text(value, fallback, limit=80):
        value = re.sub(r"\s+", " ", str(value or "")).strip()
        return value[:limit] or fallback

    if "brandName" in data:
        next_settings["brandName"] = clean_text(data.get("brandName"), DEFAULT_DESIGN_SETTINGS["brandName"], 40)
    if "logoUrl" in data:
        logo = str(data.get("logoUrl") or "").strip()
        if logo.startswith("/static/") or logo.startswith("data:image/"):
            next_settings["logoUrl"] = logo[:700_000]

    color_data = data.get("colors") if isinstance(data.get("colors"), dict) else data
    for key, fallback in DEFAULT_DESIGN_SETTINGS["colors"].items():
        if key in color_data:
            next_settings["colors"][key] = clean_color(color_data.get(key), fallback)

    text_data = data.get("text") if isinstance(data.get("text"), dict) else {}
    for key, fallback in DEFAULT_DESIGN_SETTINGS["text"].items():
        if key in text_data:
            next_settings["text"][key] = clean_text(text_data.get(key), fallback, 90)

    screen_ranges = {
        "fontSize": (12, 22),
        "messageFontSize": (12, 22),
        "sidebarWidth": (360, 820),
        "railWidth": (56, 112),
        "bubbleRadius": (0, 24),
        "iconSize": (30, 58),
        "listItemRadius": (0, 24),
        "composerRadius": (8, 40),
        "headerHeight": (54, 96),
    }
    for section in DESIGN_SECTIONS:
        source = data.get(section) if isinstance(data.get(section), dict) else {}
        for key, (minimum, maximum) in screen_ranges.items():
            if key in source and key in next_settings[section]:
                next_settings[section][key] = clamp_number(source, key, minimum, maximum, next_settings[section][key])
        if "showNavLabels" in source:
            next_settings[section]["showNavLabels"] = bool(source.get("showNavLabels"))
        if isinstance(source.get("navOrder"), list):
            order = [item for item in source["navOrder"] if item in DESIGN_NAV_KEYS]
            next_settings[section]["navOrder"] = order + [item for item in DESIGN_NAV_KEYS if item not in order]
        if isinstance(source.get("composerOrder"), list):
            order = [item for item in source["composerOrder"] if item in DESIGN_COMPOSER_KEYS]
            next_settings[section]["composerOrder"] = order + [item for item in DESIGN_COMPOSER_KEYS if item not in order]

    desktop = next_settings["desktop"]
    next_settings["fontSize"] = desktop["fontSize"]
    next_settings["messageFontSize"] = desktop["messageFontSize"]
    next_settings["sidebarWidth"] = desktop["sidebarWidth"]
    next_settings["bubbleRadius"] = desktop["bubbleRadius"]
    next_settings["iconSize"] = desktop["iconSize"]
    next_settings["blue"] = next_settings["colors"]["blue"]
    next_settings["red"] = next_settings["colors"]["red"]
    next_settings["green"] = next_settings["colors"]["green"]
    return next_settings


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def to_iso(value):
    if value is None:
        return now_iso()

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    return value.isoformat()


def is_past(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value < datetime.now(timezone.utc)


def parse_expiry_seconds(value):
    try:
        seconds = int(value or 0)
    except (TypeError, ValueError):
        return None

    if seconds <= 0:
        return None

    return min(seconds, MAX_SCHEDULE_DAYS * 24 * 60 * 60)


def user_points(username):
    user = db.session.get(User, username)
    if not user:
        return 0
    return max(0, int(user.points or 0))


def historical_points(username):
    message_points = Message.query.filter_by(sender=username).count()
    received_points = (
        Message.query.join(ChatMember, ChatMember.chat_id == Message.chat_id)
        .filter(ChatMember.username == username, Message.sender != username)
        .count()
    )
    story_points = Story.query.filter_by(username=username).count() * POINT_RULES["story"]
    friend_points = ContactRequest.query.filter(
        ContactRequest.status == "accepted",
        db.or_(ContactRequest.from_username == username, ContactRequest.to_username == username),
    ).count() * POINT_RULES["friend_accept"]
    group_points = (
        ChatMember.query.join(Chat, Chat.id == ChatMember.chat_id)
        .filter(ChatMember.username == username, Chat.type == "group")
        .count()
        * POINT_RULES["group_join"]
    )
    community_points = CommunityMember.query.filter_by(username=username).count() * POINT_RULES["community_join"]
    return message_points + received_points + story_points + friend_points + group_points + community_points


def point_level(points):
    points = max(0, int(points or 0))
    unlocked = [item for item in POINT_MILESTONES if points >= item["threshold"]]
    level = len(unlocked) + 1
    current_floor = unlocked[-1]["threshold"] if unlocked else 0
    next_milestone = next((item for item in POINT_MILESTONES if points < item["threshold"]), None)
    next_floor = next_milestone["threshold"] if next_milestone else current_floor
    progress = 100 if next_floor == current_floor else int(((points - current_floor) / (next_floor - current_floor)) * 100)
    return {
        "level": level,
        "title": unlocked[-1]["title"] if unlocked else "Yeni Üye",
        "current": points,
        "next": next_floor,
        "remaining": max(0, next_floor - points),
        "progress": max(0, min(100, progress)),
    }


def point_milestone_badges(points, unlocked_only=False):
    points = max(0, int(points or 0))
    badges = []
    for item in POINT_MILESTONES:
        unlocked = points >= item["threshold"]
        if unlocked_only and not unlocked:
            continue
        badges.append({
            **item,
            "current": points,
            "target": item["threshold"],
            "description": f"{item['threshold']:,} puana ulaş.",
            "progress": min(100, int((points / item["threshold"]) * 100)),
            "unlocked": unlocked,
        })
    return badges


def point_ledger_to_dict(row):
    return {
        "id": row.id,
        "username": row.username,
        "amount": row.amount,
        "reason": row.reason,
        "meta": row.meta or {},
        "createdAt": to_iso(row.created_at),
    }


def point_ledger_for(username, limit=40):
    rows = (
        PointLedger.query.filter_by(username=username)
        .order_by(PointLedger.created_at.desc())
        .limit(max(1, min(120, int(limit or 40))))
        .all()
    )
    return [point_ledger_to_dict(row) for row in rows]


def add_points(usernames, amount, reason="bonus", meta=None):
    if isinstance(usernames, str):
        usernames = [usernames]
    amount = int(amount or 0)
    if amount <= 0:
        return
    for username in sorted({name for name in usernames if name}):
        user = db.session.get(User, username)
        if user:
            user.points = max(0, int(user.points or 0)) + amount
            db.session.add(PointLedger(id=uuid4().hex, username=username, amount=amount, reason=reason, meta=meta or {}))


def add_points_once(username, amount, reason, unique_key, meta=None):
    if not username or amount <= 0:
        return False
    exists = PointLedger.query.filter_by(username=username, reason=reason).filter(PointLedger.meta["uniqueKey"].as_string() == unique_key).first()
    if exists:
        return False
    add_points(username, amount, reason, {**(meta or {}), "uniqueKey": unique_key})
    return True


def maybe_award_daily_login(user):
    if not user:
        return False
    now = datetime.now(timezone.utc)
    last = user.last_daily_login
    if last and last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    if last and last.date() == now.date():
        return False
    user.last_daily_login = now
    add_points(user.username, POINT_RULES["daily_login"], "daily_login")
    return True


def maybe_award_profile_completion(user):
    return False


def distance_km(lat1, lng1, lat2, lng2):
    radius = 6371
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lam = math.radians(lng2 - lng1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lam / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def nearby_users_for(username, limit=30):
    user = db.session.get(User, username)
    if not user or not user.nearby_enabled or user.last_lat is None or user.last_lng is None:
        return []
    rows = User.query.filter(
        User.username != username,
        User.nearby_enabled.is_(True),
        User.last_lat.isnot(None),
        User.last_lng.isnot(None),
    ).all()
    result = []
    for row in rows:
        if is_blocked_between(username, row.username):
            continue
        km = distance_km(float(user.last_lat), float(user.last_lng), float(row.last_lat), float(row.last_lng))
        if km <= 50:
            item = public_user(row.username, username)
            item["distanceKm"] = round(km, 1)
            result.append(item)
    return sorted(result, key=lambda item: item["distanceKm"])[:limit]


VOICE_ROOM_DEFAULTS = {
    "category": "Genel",
    "privacy": "public",
    "limit": 50,
    "joinMode": "open",
    "talkMode": "request",
    "commentsEnabled": True,
    "aiModeration": True,
    "recording": False,
    "owner": "",
    "createdAt": None,
    "participants": {},
    "requests": {},
    "comments": [],
    "bans": [],
}


def normalize_voice_room(room):
    for key, value in VOICE_ROOM_DEFAULTS.items():
        if key not in room:
            room[key] = value.copy() if isinstance(value, (dict, list)) else value
    if not room.get("createdAt"):
        room["createdAt"] = now_iso()
    return room


def clamp_voice_room_limit(value, default=50):
    try:
        limit = int(value)
    except (TypeError, ValueError):
        limit = default
    return max(2, min(200, limit))


def can_manage_voice_room(room, username):
    if not room or not username:
        return False
    participant = (room.get("participants") or {}).get(username) or {}
    return room.get("owner") == username or participant.get("role") in {"founder", "admin", "moderator"}


def voice_room_public(room, viewer=None):
    normalize_voice_room(room)
    participants = []
    for username, data in room["participants"].items():
        participant = public_user(username, viewer)
        participant.update({
            "muted": bool(data.get("muted")),
            "speaking": bool(data.get("speaking")),
            "role": data.get("role") or ("founder" if room.get("owner") == username else "listener"),
            "joinedAt": data.get("joinedAt"),
            "handRaised": bool(data.get("handRaised")),
        })
        participants.append(participant)
    return {
        "id": room["id"],
        "title": room["title"],
        "topic": room.get("topic") or "",
        "category": room.get("category") or "Genel",
        "privacy": room.get("privacy") or "public",
        "limit": int(room.get("limit") or 50),
        "joinMode": room.get("joinMode") or "open",
        "talkMode": room.get("talkMode") or "request",
        "commentsEnabled": bool(room.get("commentsEnabled", True)),
        "aiModeration": bool(room.get("aiModeration", True)),
        "recording": bool(room.get("recording")),
        "owner": public_user(room.get("owner"), viewer) if room.get("owner") else None,
        "participants": participants,
        "requests": [public_user(name, viewer) for name in (room.get("requests") or {})],
        "comments": (room.get("comments") or [])[-80:],
        "bans": list(room.get("bans") or []),
        "createdAt": room.get("createdAt"),
        "count": len(participants),
    }


def voice_rooms_state(viewer=None):
    with voice_room_lock:
        return [voice_room_public(room, viewer) for room in voice_rooms.values()]


PRIVACY_SCOPE_VALUES = {"everyone", "friends", "contacts", "nobody"}
DEFAULT_PRIVACY_SCOPES = {
    "lastSeen": "everyone",
    "online": "everyone",
    "email": "friends",
    "about": "friends",
    "photo": "everyone",
    "calls": "friends",
    "groups": "friends",
}


def privacy_scopes_for(user):
    if not user:
        return dict(DEFAULT_PRIVACY_SCOPES)

    if isinstance(user.privacy_settings, dict):
        scopes = {**DEFAULT_PRIVACY_SCOPES, **user.privacy_settings}
    else:
        scopes = {
            **DEFAULT_PRIVACY_SCOPES,
            "lastSeen": "nobody" if user.hide_last_seen else "everyone",
            "online": "nobody" if user.hide_online else "everyone",
            "email": "nobody" if user.hide_email else "friends",
        }

    normalized = {}
    for key, fallback in DEFAULT_PRIVACY_SCOPES.items():
        value = str(scopes.get(key) or fallback).strip()
        normalized[key] = value if value in PRIVACY_SCOPE_VALUES else fallback
    return normalized


def can_view_user_scope(user, viewer, scope_key):
    if not user:
        return False
    if viewer == user.username:
        return True
    if viewer and is_blocked_between(viewer, user.username):
        return False
    scope = privacy_scopes_for(user).get(scope_key, DEFAULT_PRIVACY_SCOPES.get(scope_key, "friends"))
    if scope == "everyone":
        return True
    if scope == "nobody":
        return False
    return bool(viewer and accepted_contact(viewer, user.username))


def public_user(username, viewer=None):
    user = db.session.get(User, username)
    is_self = viewer == username
    blocked = bool(viewer and viewer != username and is_blocked_between(viewer, username))
    online = any(name == username for name in connections.values())
    show_online = not blocked and can_view_user_scope(user, viewer, "online")
    show_last_seen = not blocked and can_view_user_scope(user, viewer, "lastSeen")
    show_email = not blocked and can_view_user_scope(user, viewer, "email")
    show_about = not blocked and can_view_user_scope(user, viewer, "about")
    show_photo = not blocked and can_view_user_scope(user, viewer, "photo")
    points = 0 if blocked else user_points(username) if user else 0
    privacy_scopes = privacy_scopes_for(user)
    return {
        "username": username,
        "displayName": user.display_name if user else username,
        "avatar": user.avatar if user else username[:2].upper(),
        "avatarGradient": user.avatar_gradient if user else None,
        "profileImage": user.profile_image if user and show_photo else None,
        "about": user.about if user and show_about else "",
        "createdAt": to_iso(user.created_at) if user else now_iso(),
        "points": points,
        "pointLevel": point_level(points),
        "pointBadges": point_milestone_badges(points, unlocked_only=True),
        "email": user.email if user and show_email else None,
        "online": online if show_online else False,
        "lastSeen": now_iso() if online and show_online else to_iso(user.last_seen) if user and show_last_seen else None,
        "blocked": blocked,
        "privacy": {
            "lastSeenHidden": bool(user.hide_last_seen) if user else False,
            "onlineHidden": bool(user.hide_online) if user else False,
            "readReceiptsOff": bool(user.disable_read_receipts) if user else False,
            "emailHidden": bool(user.hide_email) if user else True,
        },
        "privacyScopes": privacy_scopes,
    }


def private_user(username):
    user = db.session.get(User, username)
    data = public_user(username, username)
    if user:
        data["email"] = user.email
        data["emailVerified"] = user.email_verified
        data["phone"] = user.phone
        data["phoneVerified"] = user.phone_verified
        data["privacy"] = {
            "lastSeenHidden": bool(user.hide_last_seen),
            "onlineHidden": bool(user.hide_online),
            "readReceiptsOff": bool(user.disable_read_receipts),
            "emailHidden": bool(user.hide_email),
        }
        data["privacyScopes"] = privacy_scopes_for(user)
        data["twoFactorEnabled"] = bool(user.two_factor_enabled)
        data["preferences"] = {
            "theme": user.theme_preference or "dark",
            "fontSize": user.font_size_preference or "medium",
            "notificationSound": user.notification_sound or "classic",
        }
        data["nearbyEnabled"] = bool(user.nearby_enabled)
        data["vaultReady"] = bool(user.vault_pin_hash)
        data["vaultLockedUntil"] = to_iso(user.vault_locked_until) if user.vault_locked_until else None
        data["pointLedger"] = point_ledger_for(username, 12)
    return data


def public_users_for(viewer):
    return [public_user(user.username, viewer) for user in User.query.order_by(User.created_at.desc()).all()]


def story_to_dict(story, viewer=None):
    data = {
        "id": story.id,
        "username": story.username,
        "user": public_user(story.username, viewer),
        "body": story.body,
        "attachment": story.attachment,
        "createdAt": to_iso(story.created_at),
        "expiresAt": to_iso(story.expires_at),
    }
    if viewer:
        data["viewed"] = any(row.viewer_username == viewer for row in story.views)
        if story.username == viewer:
            data["viewCount"] = len(story.views)
    return data


def active_stories(viewer=None):
    now = datetime.now(timezone.utc)
    expired_ids = [
        row[0]
        for row in db.session.query(Story.id).filter(Story.expires_at <= now).all()
    ]
    if expired_ids:
        StoryView.query.filter(StoryView.story_id.in_(expired_ids)).delete(synchronize_session=False)
    Story.query.filter(Story.expires_at <= now).delete(synchronize_session=False)
    db.session.commit()
    stories = Story.query.filter(Story.expires_at > now).order_by(Story.created_at.desc()).all()
    if viewer:
        viewed_story_ids = {
            row.story_id
            for row in StoryView.query.filter_by(viewer_username=viewer).all()
        }
        stories = [
            story
            for story in stories
            if story.username == viewer or story.id not in viewed_story_ids
        ]
    return [story_to_dict(story, viewer) for story in stories]


def update_post_to_dict(post, viewer=None):
    liked_by = list(post.liked_by or [])
    owner = public_user(post.username, viewer)
    return {
        "id": post.id,
        "username": post.username,
        "displayName": owner["displayName"],
        "body": post.body or "",
        "media": list(post.media or [])[:4],
        "createdAt": to_iso(post.created_at),
        "editedAt": to_iso(post.edited_at) if post.edited_at else None,
        "expiresAt": to_iso(post.expires_at),
        "likes": len(liked_by),
        "userLiked": bool(viewer and viewer in liked_by),
        "source": "server",
    }


def active_update_posts(viewer=None):
    now = datetime.now(timezone.utc)
    UpdatePost.query.filter(UpdatePost.expires_at <= now).delete(synchronize_session=False)
    db.session.commit()
    rows = (
        UpdatePost.query.filter(UpdatePost.expires_at > now)
        .order_by(UpdatePost.created_at.desc())
        .limit(80)
        .all()
    )
    return [update_post_to_dict(row, viewer) for row in rows]


def attachment_error(attachment):
    if not attachment:
        return None
    if not isinstance(attachment, dict):
        return "Dosya bilgisi okunamadı."

    data_url = attachment.get("dataUrl") or ""
    if data_url and len(str(data_url)) > MAX_ATTACHMENT_DATA_URL_CHARS:
        return "Dosya çok büyük. Daha küçük dosya seç veya resmi sıkıştır."

    if attachment.get("type") == "bundle":
        items = attachment.get("items") or []
        if not isinstance(items, list) or not 1 <= len(items) <= 4:
            return "Bir ile dört arasında dosya seçebilirsin."
        total_size = 0
        for item in items:
            if not isinstance(item, dict):
                return "Dosya paketi okunamadı."
            nested_error = attachment_error(item)
            if nested_error:
                return nested_error
            total_size += len(str(item.get("dataUrl") or ""))
        if total_size > MAX_ATTACHMENT_DATA_URL_CHARS:
            return "Seçtiğin dosyaların toplam boyutu çok büyük."

    return None


def recent_visible_messages(chat_id, username=None, limit=MAX_BOOTSTRAP_MESSAGES):
    limit = max(1, int(limit or MAX_BOOTSTRAP_MESSAGES))
    scan_limit = min(RECENT_MESSAGE_SCAN_LIMIT, max(limit, limit * 2))
    rows = (
        Message.query.options(joinedload(Message.sender_user))
        .filter_by(chat_id=chat_id)
        .order_by(Message.created_at.desc())
        .limit(scan_limit)
        .all()
    )
    visible = [
        message
        for message in rows
        if not username or username not in (message.deleted_for or [])
    ]
    return list(reversed(visible[:limit]))


def visible_message_count(chat_id, username=None, exact=False):
    if not username:
        return Message.query.filter_by(chat_id=chat_id).count()

    if not exact:
        return Message.query.filter_by(chat_id=chat_id).count()

    rows = Message.query.filter_by(chat_id=chat_id).with_entities(Message.deleted_for).all()
    return sum(1 for (deleted_for,) in rows if username not in (deleted_for or []))


def message_to_dict(message):
    deleted_at = to_iso(message.deleted_at) if message.deleted_at else None
    return {
        "id": message.id,
        "chatId": message.chat_id,
        "sender": message.sender,
        "senderName": message.sender_user.display_name if message.sender_user else message.sender,
        "body": "" if message.deleted_at else message.body,
        "attachment": None if message.deleted_at else message.attachment,
        "replyTo": message.reply_to,
        "createdAt": to_iso(message.created_at),
        "editedAt": to_iso(message.edited_at) if message.edited_at else None,
        "expiresAt": to_iso(message.expires_at) if message.expires_at else None,
        "status": "sent",
        "readBy": message.read_by or [],
        "reactions": message.reactions or {},
        "versions": message.versions or [],
        "deletedAt": deleted_at,
        "deletedBy": message.deleted_by,
        "deletedFor": message.deleted_for or [],
    }


def is_view_once_attachment(attachment):
    if not isinstance(attachment, dict):
        return False
    attachment_type = str(attachment.get("type") or "")
    if attachment_type == "bundle":
        return any(is_view_once_attachment(item) for item in (attachment.get("items") or []))
    return bool(
        attachment.get("viewOnce")
        or attachment_type == "view_once_text"
        or (attachment.get("viewOnceId") and (attachment_type.startswith("image/") or attachment_type.startswith("video/")))
    )


def compact_attachment_for_admin(attachment):
    if not attachment or not isinstance(attachment, dict):
        return attachment

    compact = dict(attachment)
    data_url = str(compact.get("dataUrl") or "")
    if data_url:
        compact.setdefault("dataUrlLength", len(data_url))
        if len(data_url) > ADMIN_ATTACHMENT_INLINE_LIMIT:
            compact.pop("dataUrl", None)
            compact["dataUrlOmitted"] = True
            compact["previewMessage"] = "Dosya çok büyük olduğu için admin özetinde önizleme kapatıldı."
    return compact


def admin_message_to_dict(message):
    data = message_to_dict(message)
    data["attachment"] = compact_attachment_for_admin(data.get("attachment"))
    return data


def compact_archive_messages_for_admin(messages):
    compacted = []
    for message in (messages or [])[-ADMIN_ARCHIVE_MESSAGE_LIMIT:]:
        item = dict(message or {})
        item["attachment"] = compact_attachment_for_admin(item.get("attachment"))
        compacted.append(item)
    return compacted


def admin_story_to_dict(story):
    data = story_to_dict(story)
    data["attachment"] = compact_attachment_for_admin(data.get("attachment"))
    return data


def admin_scheduled_message_to_dict(row):
    data = scheduled_message_to_dict(row)
    data["attachment"] = compact_attachment_for_admin(data.get("attachment"))
    return data


def admin_ai_memory_to_dict(row):
    return {
        "id": row.id,
        "username": row.username,
        "chatId": row.chat_id,
        "role": row.role,
        "content": row.content,
        "provider": row.provider,
        "meta": row.meta or {},
        "createdAt": to_iso(row.created_at),
    }


def admin_nexa_play_states():
    rows = AppSetting.query.filter(AppSetting.key.like("games:%")).order_by(AppSetting.updated_at.desc()).all()
    return [
        {
            "username": row.key.split(":", 1)[1],
            "scores": dict((row.value or {}).get("scores") or {}),
            "sessions": dict((row.value or {}).get("sessions") or {}),
            "updatedAt": (row.value or {}).get("updatedAt") or to_iso(row.updated_at),
        }
        for row in rows
    ]


def admin_activity_feed(limit=240):
    events = []

    def add(kind, username, title, detail, created_at, entity_id=None):
        if not created_at:
            return
        events.append(
            {
                "kind": kind,
                "username": username,
                "title": title,
                "detail": detail,
                "entityId": entity_id,
                "createdAt": to_iso(created_at),
                "_sort": created_at,
            }
        )

    for row in User.query.order_by(User.created_at.desc()).limit(80).all():
        add("user", row.username, "Yeni kullanıcı", row.display_name, row.created_at, row.username)
    for row in Message.query.order_by(Message.created_at.desc()).limit(120).all():
        detail = row.body or (row.attachment or {}).get("name") or (row.attachment or {}).get("type") or "Dosya"
        add("message", row.sender, "Mesaj gönderdi", detail[:240], row.created_at, row.id)
    for row in UpdatePost.query.order_by(UpdatePost.created_at.desc()).limit(80).all():
        first_media = (row.media or [None])[0] if isinstance(row.media, list) else None
        media_name = first_media.get("name") if isinstance(first_media, dict) else ""
        detail = row.body or media_name or "Güncelleme"
        add("update", row.username, "Güncelleme paylaştı", detail[:240], row.created_at, row.id)
    for row in Story.query.order_by(Story.created_at.desc()).limit(80).all():
        detail = row.body or (row.attachment or {}).get("name") or "Tek görüntülemelik paylaşım"
        add("story", row.username, "Durum paylaştı", detail[:240], row.created_at, row.id)
    for row in CallLog.query.order_by(CallLog.started_at.desc()).limit(80).all():
        add("call", row.caller, f"{'Görüntülü' if row.kind == 'video' else 'Sesli'} arama", row.status, row.started_at, row.id)
    for row in AiMemory.query.filter_by(role="user").order_by(AiMemory.created_at.desc()).limit(100).all():
        add("ai", row.username, "Nexa AI kullandı", row.content[:240], row.created_at, row.id)
    for row in PointLedger.query.order_by(PointLedger.created_at.desc()).limit(100).all():
        add("points", row.username, f"{row.amount:+d} Nexa Puan", row.reason, row.created_at, row.id)
    for row in AiTask.query.order_by(AiTask.created_at.desc()).limit(80).all():
        add("task", row.username, "AI görevi oluşturdu", row.title, row.created_at, row.id)

    events.sort(key=lambda item: item["_sort"], reverse=True)
    for item in events:
        item.pop("_sort", None)
    return events[: max(1, min(500, int(limit or 240)))]


def scheduled_message_to_dict(row):
    return {
        "id": row.id,
        "chatId": row.chat_id,
        "chatTitle": row.chat.title if row.chat else "Sohbet",
        "sender": row.sender,
        "senderName": row.sender_user.display_name if row.sender_user else row.sender,
        "body": row.body,
        "attachment": row.attachment,
        "replyTo": row.reply_to,
        "sendAt": to_iso(row.send_at),
        "createdAt": to_iso(row.created_at),
        "expiresInSeconds": row.expires_in_seconds,
    }


def expire_due_messages(notify=True):
    now = datetime.now(timezone.utc)
    rows = Message.query.filter(Message.expires_at.isnot(None), Message.expires_at <= now).all()
    if not rows:
        return []

    expired_by_chat = {}
    for message in rows:
        expired_by_chat.setdefault(message.chat_id, []).append(message.id)
        db.session.delete(message)
    db.session.commit()

    if notify:
        for chat_id, message_ids in expired_by_chat.items():
            socketio.emit("message:expired", {"chatId": chat_id, "messageIds": message_ids}, room=chat_id)

    return list(expired_by_chat)


def purge_expired_archives():
    ChatArchive.query.filter(ChatArchive.expires_at <= datetime.now(timezone.utc)).delete(synchronize_session=False)
    db.session.commit()


def archive_to_summary(archive):
    return {
        "id": archive.id,
        "chatId": archive.chat_id,
        "chatTitle": archive.chat_title,
        "chatType": archive.chat_type,
        "reason": archive.reason,
        "messageCount": len(archive.messages or []),
        "createdAt": to_iso(archive.created_at),
        "expiresAt": to_iso(archive.expires_at),
    }


def archives_with_messages(username):
    purge_expired_archives()
    rows = ChatArchive.query.filter_by(username=username).order_by(ChatArchive.created_at.desc()).all()
    return [
        {
            **archive_to_summary(row),
            "messages": row.messages or [],
        }
        for row in rows
    ]


def visible_archives(username):
    purge_expired_archives()
    rows = ChatArchive.query.filter_by(username=username).order_by(ChatArchive.created_at.desc()).all()
    return [archive_to_summary(row) for row in rows]


def visible_scheduled_messages(username):
    rows = (
        ScheduledMessage.query.filter_by(sender=username)
        .order_by(ScheduledMessage.send_at.asc())
        .limit(80)
        .all()
    )
    return [
        scheduled_message_to_dict(row)
        for row in rows
        if row.chat and user_can_see_chat(row.chat, username)
    ]


def verify_user_password(username, password):
    user = db.session.get(User, username)
    return bool(user and password and check_password_hash(user.password_hash, password))


def visible_messages_for_archive(chat, username):
    return [
        message_to_dict(message)
        for message in chat.messages
        if username not in (message.deleted_for or [])
    ]


def hide_chat_messages_for_user(chat, username):
    for message in chat.messages:
        deleted_for = list(message.deleted_for or [])
        if username not in deleted_for:
            deleted_for.append(username)
            message.deleted_for = deleted_for

    hidden = HiddenChat.query.filter_by(username=username, chat_id=chat.id).first()
    if hidden:
        hidden.hidden_at = datetime.now(timezone.utc)
    else:
        db.session.add(HiddenChat(username=username, chat_id=chat.id))


def chat_has_visible_messages_after(chat, username, hidden_at):
    if hidden_at.tzinfo is None:
        hidden_at = hidden_at.replace(tzinfo=timezone.utc)
    rows = (
        Message.query.filter_by(chat_id=chat.id)
        .filter(Message.created_at > hidden_at)
        .order_by(Message.created_at.desc())
        .limit(80)
        .all()
    )
    for message in rows:
        if username in (message.deleted_for or []):
            continue
        return True
    return False


def archive_chat_for_user(chat, username, reason="deleted"):
    messages = visible_messages_for_archive(chat, username)
    if not messages:
        hide_chat_messages_for_user(chat, username)
        return None

    archive = ChatArchive(
        id=uuid4().hex,
        username=username,
        chat_id=chat.id,
        chat_title=chat_for_user(chat, username)["title"],
        chat_type=chat.type,
        reason=reason,
        messages=messages,
        expires_at=datetime.now(timezone.utc) + timedelta(days=3),
    )
    db.session.add(archive)
    hide_chat_messages_for_user(chat, username)
    return archive


def restore_archive_for_user(archive):
    chat = db.session.get(Chat, archive.chat_id)
    if not chat or not user_can_see_chat(chat, archive.username):
        return None

    archived_ids = {message.get("id") for message in (archive.messages or []) if message.get("id")}
    for message in chat.messages:
        if message.id not in archived_ids:
            continue
        deleted_for = [item for item in (message.deleted_for or []) if item != archive.username]
        message.deleted_for = deleted_for

    HiddenChat.query.filter_by(username=archive.username, chat_id=archive.chat_id).delete(synchronize_session=False)
    db.session.delete(archive)
    return chat


def call_log_to_dict(log, username):
    chat = db.session.get(Chat, log.chat_id)
    seen_by = list(log.seen_by or [])
    return {
        "id": log.id,
        "chatId": log.chat_id,
        "chatTitle": chat_for_user(chat, username)["title"] if chat and user_can_see_chat(chat, username) else "Arama",
        "caller": log.caller,
        "callerName": log.caller_user.display_name if log.caller_user else log.caller,
        "kind": log.kind,
        "status": log.status,
        "seen": username in seen_by,
        "durationSeconds": log.duration_seconds,
        "startedAt": to_iso(log.started_at),
        "endedAt": to_iso(log.ended_at),
    }


def ensure_lobby():
    lobby = db.session.get(Chat, "lobby")
    if lobby:
        if lobby.title != "Genel Grup":
            lobby.title = "Genel Grup"
            db.session.commit()
        return

    db.session.add(Chat(id="lobby", type="group", title="Genel Grup"))
    db.session.commit()


def direct_chat_id(first_username, second_username):
    return "dm:" + ":".join(sorted([first_username, second_username]))


def find_direct_chat(first_username, second_username):
    wanted = sorted([first_username, second_username])
    for chat in Chat.query.filter_by(type="direct").all():
        if chat_member_names(chat) == wanted:
            return chat
    return None


def ensure_direct_chat(first_username, second_username):
    existing = find_direct_chat(first_username, second_username)
    if existing:
        return existing

    chat_id = direct_chat_id(first_username, second_username)
    chat = db.session.get(Chat, chat_id)

    if chat:
        return chat

    chat = Chat(id=chat_id, type="direct", title=second_username)
    db.session.add(chat)
    db.session.add(ChatMember(chat_id=chat_id, username=first_username))
    db.session.add(ChatMember(chat_id=chat_id, username=second_username))
    db.session.commit()
    return chat


def chat_member_names(chat):
    return sorted(member.username for member in chat.members)


def user_can_see_chat(chat, username):
    return any(member.username == username for member in chat.members)


def add_chat_member(chat_id, username):
    if not db.session.get(User, username):
        return False

    exists = ChatMember.query.filter_by(chat_id=chat_id, username=username).first()
    if exists:
        return True

    db.session.add(ChatMember(chat_id=chat_id, username=username))
    return True


def is_group_admin(chat, username):
    if not chat or chat.type != "group":
        return False

    member = ChatMember.query.filter_by(chat_id=chat.id, username=username).first()
    return bool(member and member.is_admin)


def promote_fallback_group_admin(chat):
    if not chat or chat.type != "group" or chat.id == "lobby":
        return

    if any(member.is_admin for member in chat.members):
        return

    first_member = next(iter(chat.members), None)
    if first_member:
        first_member.is_admin = True


def general_group_state(username):
    ensure_lobby()
    lobby = db.session.get(Chat, "lobby")
    members = [public_user(member, username) for member in chat_member_names(lobby)]
    joined = any(member["username"] == username for member in members)

    return {
        "id": lobby.id,
        "title": lobby.title,
        "members": members,
        "joined": joined,
    }


def chat_for_user(chat, username):
    visible_messages = recent_visible_messages(chat.id, username, MAX_BOOTSTRAP_MESSAGES + 1)
    has_more_messages = len(visible_messages) > MAX_BOOTSTRAP_MESSAGES
    if has_more_messages:
        visible_messages = visible_messages[-MAX_BOOTSTRAP_MESSAGES:]
    last_message = message_to_dict(visible_messages[-1]) if visible_messages else None
    messages = [message_to_dict(message) for message in visible_messages]
    member_names = chat_member_names(chat)
    title = chat.title
    send_error = chat_send_error(username, chat)

    if chat.type == "direct":
        other_users = [member for member in member_names if member != username]
        title = public_user(other_users[0], username)["displayName"] if other_users else "Kişisel sohbet"

    return {
        "id": chat.id,
        "type": chat.type,
        "title": title,
        "image": chat.image,
        "members": [
            {**public_user(member, username), "isAdmin": is_group_admin(chat, member)}
            for member in member_names
        ],
        "lastMessage": last_message,
        "messages": messages,
        "messageCount": len(messages) + (1 if has_more_messages else 0),
        "sendBlockedReason": send_error,
    }


def visible_chats(username):
    ensure_lobby()
    expire_due_messages(notify=False)
    purge_expired_archives()
    result = []
    hidden_by_chat = {
        row.chat_id: row.hidden_at
        for row in HiddenChat.query.filter_by(username=username).all()
    }
    chats = (
        Chat.query.join(ChatMember, ChatMember.chat_id == Chat.id)
        .filter(ChatMember.username == username)
        .options(selectinload(Chat.members).selectinload(ChatMember.user))
        .order_by(Chat.created_at)
        .all()
    )

    for chat in chats:
        hidden_at = hidden_by_chat.get(chat.id)
        if hidden_at and not chat_has_visible_messages_after(chat, username, hidden_at):
            continue
        chat_data = chat_for_user(chat, username)
        if chat_data:
            result.append(chat_data)

    return sorted(result, key=lambda item: item["lastMessage"]["createdAt"] if item["lastMessage"] else "", reverse=True)


def ai_task_to_dict(row):
    return {
        "id": row.id,
        "title": row.title,
        "description": row.description or "",
        "repeat": row.repeat or "none",
        "remindAt": to_iso(row.remind_at) if row.remind_at else None,
        "completedAt": to_iso(row.completed_at) if row.completed_at else None,
        "createdAt": to_iso(row.created_at),
        "updatedAt": to_iso(row.updated_at),
    }


def ai_tasks_for(username):
    rows = AiTask.query.filter_by(username=username).order_by(AiTask.completed_at.isnot(None), AiTask.created_at.desc()).limit(120).all()
    return [ai_task_to_dict(row) for row in rows]


def default_ai_settings():
    return {
        "enabled": True,
        "name": "Nexa AI",
        "image": "",
        "autoApprove": False,
        "voice": "warm",
        "responseLength": "medium",
        "persona": "",
        "saveHistory": True,
        "notifications": True,
        "censorEnabled": True,
    }


def ai_settings_for_user(user):
    stored = user.ai_settings if isinstance(user.ai_settings, dict) else {}
    settings = {**default_ai_settings(), **stored}
    settings["name"] = re.sub(r"\s+", " ", str(settings.get("name") or "Nexa AI")).strip()[:40] or "Nexa AI"
    settings["theme"] = user.theme_preference or "dark"
    settings["fontSize"] = user.font_size_preference or "medium"
    settings["notificationSound"] = user.notification_sound or "classic"
    return settings


def ai_memory_message_to_dict(row):
    meta = row.meta if isinstance(row.meta, dict) else {}
    actions = meta.get("actions")
    return {
        "id": row.id,
        "role": "user" if row.role == "user" else "assistant",
        "text": row.content or "",
        "actions": actions if isinstance(actions, list) and actions and isinstance(actions[0], dict) else [],
        "provider": row.provider or "",
        "chatId": row.chat_id,
        "createdAt": to_iso(row.created_at),
    }


def ai_memory_messages_for(username):
    rows = (
        AiMemory.query.filter_by(username=username)
        .order_by(AiMemory.created_at.desc())
        .limit(80)
        .all()
    )
    return [ai_memory_message_to_dict(row) for row in reversed(rows)]


def ai_task_plan_to_dict(row):
    base = ai_task_to_dict(row)
    remind_at = row.remind_at or row.created_at
    return {
        **base,
        "hint": row.description or row.repeat or "",
        "time": remind_at.strftime("%H:%M") if remind_at else "09:00",
        "active": row.completed_at is None,
        "attempts": 0,
        "failedAt": "",
        "nextAt": to_iso(row.remind_at) if row.remind_at and row.completed_at is None else "",
    }


def ai_full_state_for(username):
    user = db.session.get(User, username)
    if not user:
        return None
    return {
        "memory": ai_memory_messages_for(username),
        "tasks": [
            ai_task_plan_to_dict(row)
            for row in AiTask.query.filter_by(username=username).order_by(AiTask.created_at.desc()).limit(120).all()
        ],
        "settings": ai_settings_for_user(user),
        "syncedAt": datetime.now(timezone.utc).isoformat(),
    }


def community_to_dict(row, viewer=None):
    member_rows = CommunityMember.query.filter_by(community_id=row.id).all()
    members = [public_user(member.username, viewer) for member in member_rows[:24]]
    joined = any(member.username == viewer for member in member_rows) if viewer else False
    announcements = (
        CommunityAnnouncement.query.filter_by(community_id=row.id)
        .order_by(CommunityAnnouncement.created_at.desc())
        .limit(8)
        .all()
    )
    return {
        "id": row.id,
        "title": row.title,
        "description": row.description or "",
        "category": row.category or "Genel",
        "image": row.image,
        "owner": public_user(row.owner, viewer),
        "privacy": row.privacy or "public",
        "createdAt": to_iso(row.created_at),
        "memberCount": len(member_rows),
        "onlineCount": sum(1 for member in member_rows if any(name == member.username for name in connections.values())),
        "joined": joined,
        "role": next((member.role for member in member_rows if member.username == viewer), None),
        "members": members,
        "announcements": [
            {
                "id": item.id,
                "body": item.body,
                "author": public_user(item.author, viewer),
                "createdAt": to_iso(item.created_at),
            }
            for item in announcements
        ],
    }


def communities_for(username):
    rows = Community.query.order_by(Community.created_at.desc()).limit(80).all()
    return [community_to_dict(row, username) for row in rows]


def app_state_for_user(username):
    return {
        "user": private_user(username),
        "users": public_users_for(username),
        "chats": visible_chats(username),
        "generalGroup": general_group_state(username),
        "stories": active_stories(username),
        "updatesFeed": active_update_posts(username),
        "callLogs": visible_call_logs(username),
        "contactRequests": visible_contact_requests(username),
        "groupInvites": visible_group_invites(username),
        "blockedUsers": blocked_users_for(username),
        "blockedProfiles": [public_user(item, username) for item in blocked_users_for(username)],
        "devices": active_device_sessions(username),
        "archives": visible_archives(username),
        "scheduledMessages": visible_scheduled_messages(username),
        "pointLedger": point_ledger_for(username),
        "voiceRooms": voice_rooms_state(username),
        "communities": communities_for(username),
        "aiTasks": ai_tasks_for(username),
        "nearbyUsers": nearby_users_for(username),
        "nexaPlay": nexa_play_state(username),
    }


def visible_call_logs(username):
    logs = (
        CallLog.query.join(Chat, CallLog.chat_id == Chat.id)
        .join(ChatMember, ChatMember.chat_id == Chat.id)
        .filter(ChatMember.username == username)
        .order_by(CallLog.started_at.desc())
        .limit(80)
        .all()
    )
    return [call_log_to_dict(log, username) for log in logs]


def is_blocked_between(first_username, second_username):
    return (
        BlockedUser.query.filter_by(blocker=first_username, blocked=second_username).first()
        or BlockedUser.query.filter_by(blocker=second_username, blocked=first_username).first()
    )


def contact_request_between(first_username, second_username):
    return (
        ContactRequest.query.filter_by(from_username=first_username, to_username=second_username).order_by(ContactRequest.created_at.desc()).first()
        or ContactRequest.query.filter_by(from_username=second_username, to_username=first_username).order_by(ContactRequest.created_at.desc()).first()
    )


def accepted_contact(first_username, second_username):
    request_row = contact_request_between(first_username, second_username)
    if request_row and request_row.status == "accepted":
        return True

    legacy_chat = db.session.get(Chat, direct_chat_id(first_username, second_username))
    return bool(legacy_chat and legacy_chat.messages)


def chat_send_error(username, chat):
    if not username or not chat or not user_can_see_chat(chat, username):
        return "Sohbet bulunamadÄ±."

    if chat.type != "direct":
        return None

    others = [member for member in chat_member_names(chat) if member != username]
    if any(is_blocked_between(username, other) for other in others):
        return "Bu kiÅŸiyle mesajlaÅŸma veya arama engellenmiÅŸ."
    if any(not accepted_contact(username, other) for other in others):
        return "Mesaj gÃ¶ndermek iÃ§in Ã¶nce istek kabul edilmeli."

    return None


def contact_request_to_dict(request_row, viewer=None):
    return {
        "id": request_row.id,
        "from": public_user(request_row.from_username, viewer),
        "to": public_user(request_row.to_username, viewer),
        "status": request_row.status,
        "createdAt": to_iso(request_row.created_at),
        "respondedAt": to_iso(request_row.responded_at) if request_row.responded_at else None,
    }


def visible_contact_requests(username):
    rows = ContactRequest.query.filter(
        db.or_(ContactRequest.from_username == username, ContactRequest.to_username == username)
    ).order_by(ContactRequest.created_at.desc()).all()
    return [contact_request_to_dict(row, username) for row in rows]


def group_invite_to_dict(invite, viewer=None):
    return {
        "id": invite.id,
        "chatId": invite.chat_id,
        "chatTitle": invite.chat.title if invite.chat else "Grup",
        "chatImage": invite.chat.image if invite.chat else None,
        "inviter": public_user(invite.inviter, viewer),
        "invitee": public_user(invite.invitee, viewer),
        "status": invite.status,
        "createdAt": to_iso(invite.created_at),
        "respondedAt": to_iso(invite.responded_at) if invite.responded_at else None,
    }


def visible_group_invites(username):
    rows = GroupInvite.query.filter(
        db.or_(GroupInvite.inviter == username, GroupInvite.invitee == username)
    ).order_by(GroupInvite.created_at.desc()).all()
    return [group_invite_to_dict(row, username) for row in rows]


def blocked_users_for(username):
    return [row.blocked for row in BlockedUser.query.filter_by(blocker=username).all()]


def connected_sids_for(username):
    return [sid for sid, connected_user in connections.items() if connected_user == username]


def push_payload_for_chat(chat_id, title, message, notification_type="message", url=None, call_kind=None):
    target_url = url or f"/chat/{chat_id}"
    return {
        "title": title or "NexaLine",
        "message": message or "Yeni bildirim",
        "type": notification_type or "message",
        "chatId": chat_id,
        "callKind": call_kind,
        "url": target_url,
        "tag": f"{notification_type or 'message'}-{chat_id}",
        "icon": "/static/icons/icon-192-3d.png",
        "badge": "/static/icons/icon-192-3d.png",
    }


def send_web_push_subscription(subscription_row, payload):
    if not (VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY):
        return False
    try:
        from pywebpush import WebPushException, webpush
    except Exception:
        return False

    try:
        webpush(
            subscription_info=subscription_row.subscription,
            data=json.dumps(payload, ensure_ascii=False),
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims={"sub": VAPID_SUBJECT},
        )
        return True
    except WebPushException as error:
        status_code = getattr(getattr(error, "response", None), "status_code", None)
        if status_code in {404, 410}:
            db.session.delete(subscription_row)
            db.session.commit()
        app.logger.warning("Web Push bildirimi gonderilemedi: %s", error)
        return False
    except Exception as error:
        app.logger.warning("Web Push bildirimi hazirlanamadi: %s", error)
        return False


def send_push_notification(
    chat_id,
    title,
    message,
    notification_type="message",
    sender=None,
    target_usernames=None,
    url=None,
    call_kind=None,
    emit_socket=True,
):
    chat = db.session.get(Chat, chat_id)
    if not chat:
        return {"ok": False, "message": "Sohbet bulunamadı.", "sent": 0, "socket": 0}

    if target_usernames is None:
        targets = chat_member_names(chat)
    else:
        targets = [str(username).strip().lower() for username in target_usernames if username]

    targets = sorted({username for username in targets if username and username != sender and user_can_see_chat(chat, username)})
    payload = push_payload_for_chat(chat_id, title, message, notification_type, url, call_kind)
    web_push_sent = 0
    socket_sent = 0

    for username in targets:
        for subscription in PushSubscription.query.filter_by(username=username).all():
            if send_web_push_subscription(subscription, payload):
                web_push_sent += 1
        if emit_socket:
            for sid in connected_sids_for(username):
                socketio.emit("push:notification", payload, room=sid)
                socket_sent += 1

    return {"ok": True, "payload": payload, "sent": web_push_sent, "socket": socket_sent, "targets": targets}


def device_label(user_agent):
    value = (user_agent or "").lower()
    if "android" in value:
        return "Android"
    if "iphone" in value or "ipad" in value:
        return "iPhone/iPad"
    if "windows" in value:
        return "Windows Web"
    if "mac" in value:
        return "Mac Web"
    if "linux" in value:
        return "Linux Web"
    return "NexaLine Web"


def normalize_device_id(value):
    value = re.sub(r"[^a-zA-Z0-9:_-]", "", str(value or ""))[:80]
    return value or uuid4().hex


def upsert_device_session(username, device_id=None):
    if not username or not db.session.get(User, username):
        return None
    device_id = normalize_device_id(device_id)
    user_agent = request.headers.get("User-Agent", "")[:600]
    row = db.session.get(DeviceSession, device_id)
    if row and row.username != username:
        device_id = uuid4().hex
        row = None
    if not row:
        row = DeviceSession(id=device_id, username=username)
        db.session.add(row)
    row.label = device_label(user_agent)
    row.user_agent = user_agent
    row.ip_address = request_ip()
    row.last_seen = datetime.now(timezone.utc)
    row.revoked_at = None
    return row


def device_session_to_dict(row):
    return {
        "id": row.id,
        "label": row.label,
        "platform": device_label(row.user_agent or row.label),
        "ipAddress": row.ip_address,
        "userAgent": row.user_agent,
        "createdAt": to_iso(row.created_at),
        "lastSeen": to_iso(row.last_seen),
        "revokedAt": to_iso(row.revoked_at) if row.revoked_at else None,
    }


def active_device_sessions(username):
    rows = (
        DeviceSession.query.filter_by(username=username, revoked_at=None)
        .order_by(DeviceSession.last_seen.desc())
        .all()
    )
    return [device_session_to_dict(row) for row in rows]


def device_revoked(username, device_id):
    if not device_id:
        return False
    row = db.session.get(DeviceSession, normalize_device_id(device_id))
    return bool(row and row.username == username and row.revoked_at)


def broadcast_presence():
    for sid, username in connections.items():
        socketio.emit("presence:update", public_users_for(username), room=sid, namespace="/")


def broadcast_stories():
    for sid, username in connections.items():
        socketio.emit("stories:update", active_stories(username), room=sid, namespace="/")


def broadcast_update_posts():
    for sid, username in connections.items():
        socketio.emit("updates:feed", active_update_posts(username), room=sid, namespace="/")


def emit_general_group_updates():
    for sid, username in connections.items():
        socketio.emit("general:update", general_group_state(username), room=sid)


def emit_social_updates(*usernames):
    for username in {name for name in usernames if name}:
        for sid in connected_sids_for(username):
            socketio.emit(
                "social:update",
                {
                    "user": private_user(username),
                    "contactRequests": visible_contact_requests(username),
                    "groupInvites": visible_group_invites(username),
                    "blockedUsers": blocked_users_for(username),
                    "blockedProfiles": [public_user(item, username) for item in blocked_users_for(username)],
                    "devices": active_device_sessions(username),
                    "archives": visible_archives(username),
                    "scheduledMessages": visible_scheduled_messages(username),
                    "pointLedger": point_ledger_for(username),
                    "voiceRooms": voice_rooms_state(username),
                    "nearbyUsers": nearby_users_for(username),
                },
                room=sid,
            )


def admin_token_from_request():
    data = request.get_json(silent=True) or {}
    return request.headers.get("X-Admin-Token") or data.get("token") or request.args.get("token")


def request_ip():
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    raw_ip = forwarded_for.split(",", 1)[0].strip() or request.remote_addr or ""
    if raw_ip.startswith("::ffff:"):
        raw_ip = raw_ip.removeprefix("::ffff:")
    return raw_ip


def is_local_admin_request():
    try:
        ip = ipaddress.ip_address(request_ip())
    except ValueError:
        return False

    if ip.is_loopback:
        return True

    try:
        local_ips = {
            item[4][0]
            for item in socket.getaddrinfo(socket.gethostname(), None)
            if item[4] and item[4][0]
        }
    except socket.gaierror:
        local_ips = set()

    return str(ip) in local_ips


def require_admin():
    accepted_tokens = {token for token in (os.environ.get("ADMIN_TOKEN"), os.environ.get("ADMIN_BOOTSTRAP_TOKEN"), DEV_ADMIN_TOKEN) if token}
    if is_local_admin_request():
        return None

    if admin_token_from_request() not in accepted_tokens:
        return jsonify({"ok": False, "message": "Yönetici token hatalı."}), 401

    return None


def reset_all_user_data():
    StoryView.query.delete(synchronize_session=False)
    Story.query.delete(synchronize_session=False)
    CallLog.query.delete(synchronize_session=False)
    ScheduledMessage.query.delete(synchronize_session=False)
    Message.query.delete(synchronize_session=False)
    BlockedUser.query.delete(synchronize_session=False)
    ContactRequest.query.delete(synchronize_session=False)
    GroupInvite.query.delete(synchronize_session=False)
    HiddenChat.query.delete(synchronize_session=False)
    ChatArchive.query.delete(synchronize_session=False)
    PointLedger.query.delete(synchronize_session=False)
    AiTask.query.delete(synchronize_session=False)
    AiMemory.query.delete(synchronize_session=False)
    CommunityAnnouncement.query.delete(synchronize_session=False)
    CommunityMember.query.delete(synchronize_session=False)
    Community.query.delete(synchronize_session=False)
    VaultItem.query.delete(synchronize_session=False)
    PushSubscription.query.delete(synchronize_session=False)
    DeviceSession.query.delete(synchronize_session=False)
    EmailVerification.query.delete(synchronize_session=False)
    SupportRequest.query.delete(synchronize_session=False)
    ChatMember.query.delete(synchronize_session=False)

    for chat in Chat.query.filter(Chat.id != "lobby").all():
        db.session.delete(chat)

    lobby = db.session.get(Chat, "lobby")
    if lobby:
        lobby.title = "Genel Grup"
        lobby.image = None
    else:
        db.session.add(Chat(id="lobby", type="group", title="Genel Grup"))

    User.query.delete(synchronize_session=False)
    db.session.commit()

    connections.clear()
    typing_users.clear()
    qr_login_sessions.clear()
    with voice_room_lock:
        for room in voice_rooms.values():
            room["participants"] = {}
            room["requests"] = {}
            room["comments"] = []
            room["bans"] = []


def reset_user_data_once(reset_key=FULL_USER_DATA_RESET_KEY):
    if db.session.get(AppSetting, reset_key):
        return False

    reset_all_user_data()
    db.session.add(
        AppSetting(
            key=reset_key,
            value={
                "completedAt": now_iso(),
                "scope": "users, chats, messages, calls, devices, archives, points, vault, AI tasks, verification records",
            },
        )
    )
    db.session.commit()
    app.logger.warning("One-time NexaLine user data reset completed: %s", reset_key)
    return True


def password_error(username, password):
    if len(password) < 10:
        return "Şifre en az 10 karakter olmalı."

    if username and username in password.lower():
        return "Şifre kullanıcı adını içermemeli."

    has_lower = any(char.islower() for char in password)
    has_upper = any(char.isupper() for char in password)
    has_digit = any(char.isdigit() for char in password)
    has_symbol = any(not char.isalnum() for char in password)

    if not (has_lower and has_upper and has_digit and has_symbol):
        return "Şifre büyük harf, küçük harf, sayı ve özel karakter içermeli."

    return None


def display_name_exists(display_name):
    return User.query.filter(db.func.lower(User.display_name) == display_name.lower()).first() is not None


TR_LOWER_MAP = str.maketrans("IİĞÜŞÖÇ", "ıiğüşöç")
TR_UPPER_MAP = str.maketrans("iıüğşöç", "İIÜĞŞÖÇ")


def tr_lower(value):
    return (value or "").translate(TR_LOWER_MAP).lower()


def tr_upper(value):
    return (value or "").translate(TR_UPPER_MAP).upper()


def title_case_name(value):
    parts = []
    for token in re.split(r"([ \-'])", re.sub(r"\s+", " ", (value or "").strip())):
        if token and re.match(r"[^\W\d_]", token[0], re.UNICODE):
            lowered = tr_lower(token)
            token = tr_upper(lowered[0]) + lowered[1:]
        parts.append(token)
    return "".join(parts).strip()


def normalize_display_name(data):
    first_name = title_case_name(data.get("firstName") or "")
    last_name = tr_upper(re.sub(r"\s+", " ", (data.get("lastName") or "").strip()))

    if not first_name or not last_name:
        display_name = re.sub(r"\s+", " ", (data.get("displayName") or "").strip())
        pieces = display_name.split(" ")
        if len(pieces) >= 2:
            first_name = title_case_name(" ".join(pieces[:-1]))
            last_name = tr_upper(pieces[-1])

    display_name = f"{first_name} {last_name}".strip()
    if len(first_name) < 2 or len(last_name) < 2:
        return None, "İsim ve soyisim ayrı ayrı en az 2 karakter olmalı."
    if len(display_name) > 80:
        return None, "İsim ve soyisim toplam 80 karakteri geçmemeli."

    return display_name, None


def username_error(username):
    if len(username) < 3:
        return "Kullanıcı adı en az 3 karakter olmalı."

    if len(username) > 20:
        return "Kullanıcı adı en fazla 20 karakter olmalı."

    if not re.fullmatch(r"[a-z0-9_.]+", username):
        return "Kullanıcı adı sadece küçük harf, sayı, nokta ve alt çizgi içerebilir."

    return None


def user_by_login_identifier(identifier):
    value = (identifier or "").strip().lower()
    if not value:
        return None
    _, phone_normalized = normalize_phone(value)
    if phone_normalized:
        return User.query.filter(User.phone_normalized == phone_normalized).first()
    if "@" in value:
        _, email_normalized = normalize_email(value)
        if not email_normalized:
            return None
        return User.query.filter(db.func.lower(User.email_normalized) == email_normalized.lower()).first()
    return db.session.get(User, value)


def username_edit_distance_at_most_one(left, right):
    if left == right:
        return True
    if abs(len(left) - len(right)) > 1:
        return False

    if len(left) > len(right):
        left, right = right, left

    short_index = 0
    long_index = 0
    edits = 0
    while short_index < len(left) and long_index < len(right):
        if left[short_index] == right[long_index]:
            short_index += 1
            long_index += 1
            continue
        edits += 1
        if edits > 1:
            return False
        if len(left) == len(right):
            short_index += 1
        long_index += 1

    return True


def user_by_username_typo(identifier, password):
    value = (identifier or "").strip().lower()
    if not value or "@" in value or normalize_phone(value)[1]:
        return None

    candidates = [
        user
        for user in User.query.filter(db.func.length(User.username).between(len(value) - 1, len(value) + 1)).all()
        if username_edit_distance_at_most_one(value, user.username)
        and check_password_hash(user.password_hash, password)
    ]
    return candidates[0] if len(candidates) == 1 else None


def normalize_phone(phone):
    raw = (phone or "").strip()
    if not raw or not re.fullmatch(r"[+\d\s().-]+", raw):
        return None, None
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("0090"):
        digits = digits[4:]
    elif digits.startswith("90") and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    if len(digits) != 10 or not digits.startswith("5"):
        return None, None
    normalized = f"+90{digits}"
    return normalized, normalized


def mask_phone(phone):
    _, normalized = normalize_phone(phone)
    if not normalized:
        return ""
    return f"+90 5** *** ** {normalized[-2:]}"


def phone_error(phone):
    original, normalized = normalize_phone(phone)
    if not original or not normalized:
        return "Geçerli bir Türkiye cep telefonu numarası yazmalısın."
    return None


def phone_exists(phone_normalized, except_username=None):
    if not phone_normalized:
        return False
    query = User.query.filter(User.phone_normalized == phone_normalized)
    if except_username:
        query = query.filter(User.username != except_username)
    return query.first() is not None


def normalize_email(email):
    email = (email or "").strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return None, None

    local, domain = email.rsplit("@", 1)
    if domain in {"gmail.com", "googlemail.com"}:
        local = local.split("+", 1)[0].replace(".", "")
        domain = "gmail.com"

    return email, f"{local}@{domain}"


def mask_email(email):
    email = (email or "").strip()
    if "@" not in email:
        return ""
    local, domain = email.split("@", 1)
    if len(local) <= 2:
        masked_local = f"{local[:1]}***"
    else:
        masked_local = f"{local[:2]}{'*' * min(6, max(3, len(local) - 2))}"
    return f"{masked_local}@{domain}"


def email_error(email):
    original, normalized = normalize_email(email)
    if not original or not normalized:
        return "Geçerli bir Gmail adresi yazmalısın."

    if not normalized.endswith("@gmail.com"):
        return "Şimdilik sadece Gmail adresi kabul ediliyor."

    return None


def email_exists(email_normalized, except_username=None):
    if not email_normalized:
        return False
    query = User.query.filter(db.func.lower(User.email_normalized) == email_normalized.lower())
    if except_username:
        query = query.filter(User.username != except_username)
    return query.first() is not None


def verification_code():
    return f"{secrets.randbelow(900000) + 100000}"


def send_sms_via_iletimerkezi(phone_normalized, body):
    api_key = os.environ.get("ILETIMERKEZI_API_KEY", "").strip()
    api_hash = os.environ.get("ILETIMERKEZI_API_HASH", "").strip()
    sender = os.environ.get("ILETIMERKEZI_SENDER", "").strip()
    if not api_key or not api_hash or not sender:
        return False, None

    number = phone_normalized.lstrip("+")
    response = requests.post(
        "https://api.iletimerkezi.com/v1/send-sms/json",
        headers={"Content-Type": "application/json"},
        json={
            "request": {
                "authentication": {"key": api_key, "hash": api_hash},
                "order": {
                    "sender": sender,
                    "iys": "0",
                    "message": {
                        "text": body,
                        "receipents": {"number": [number]},
                    },
                },
            }
        },
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    provider_response = payload.get("response") or {}
    status = provider_response.get("status") or {}
    if int(status.get("code") or 0) != 200:
        raise RuntimeError(status.get("message") or "SMS sağlayıcısı gönderimi reddetti.")
    return True, str((provider_response.get("order") or {}).get("id") or "") or None


def twilio_verify_config():
    api_key_sid = os.environ.get("TWILIO_API_KEY_SID", "").strip()
    api_key_secret = os.environ.get("TWILIO_API_KEY_SECRET", "").strip()
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
    service_sid = os.environ.get("TWILIO_VERIFY_SERVICE_SID", "").strip()
    auth_username = api_key_sid or account_sid
    auth_password = api_key_secret or auth_token
    if not auth_username or not auth_password or not service_sid:
        return None
    return auth_username, auth_password, service_sid


def start_twilio_verification(phone_normalized):
    config = twilio_verify_config()
    if not config:
        return False, None
    account_sid, auth_token, service_sid = config
    response = requests.post(
        f"https://verify.twilio.com/v2/Services/{service_sid}/Verifications",
        auth=(account_sid, auth_token),
        data={"To": phone_normalized, "Channel": "sms", "Locale": "tr"},
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") not in {"pending", "approved"}:
        raise RuntimeError(payload.get("message") or "Twilio dogrulama SMS'ini baslatamadi.")
    return True, str(payload.get("sid") or "") or None


def check_twilio_verification(phone_normalized, code):
    config = twilio_verify_config()
    if not config:
        raise RuntimeError("Twilio Verify ayarlari eksik.")
    account_sid, auth_token, service_sid = config
    response = requests.post(
        f"https://verify.twilio.com/v2/Services/{service_sid}/VerificationCheck",
        auth=(account_sid, auth_token),
        data={"To": phone_normalized, "Code": code},
        timeout=15,
    )
    if response.status_code == 404:
        return False
    response.raise_for_status()
    return response.json().get("status") == "approved"


def send_phone_code(phone_normalized, code, purpose):
    purpose_labels = {
        "register": "kayıt",
        "forgot": "şifre sıfırlama",
        "login_2fa": "giriş",
        "phone_change": "telefon değiştirme",
    }
    label = purpose_labels.get(purpose, "doğrulama")
    body = f"NexaLine {label} kodun: {code}. Kod 10 dakika geçerlidir."
    try:
        if twilio_verify_config():
            sent, provider_message_id = start_twilio_verification(phone_normalized)
            return sent, provider_message_id, "twilio"
        sent, provider_message_id = send_sms_via_iletimerkezi(phone_normalized, body)
        return sent, provider_message_id, "iletimerkezi" if sent else "local"
    except Exception:
        app.logger.exception("Doğrulama SMS'i gönderilemedi")
        return False, None, "local"


def create_phone_verification(purpose, phone, phone_normalized, username=None, password_hash=None):
    previous = PhoneVerification.query.filter_by(
        purpose=purpose,
        username=username,
        phone_normalized=phone_normalized,
    ).order_by(PhoneVerification.created_at.desc()).first()
    wait_seconds = verification_resend_wait_seconds(previous)
    if wait_seconds:
        return None, None, False, wait_seconds

    PhoneVerification.query.filter_by(
        purpose=purpose,
        username=username,
        phone_normalized=phone_normalized,
    ).delete(synchronize_session=False)
    code = verification_code()
    verification = PhoneVerification(
        id=uuid4().hex,
        purpose=purpose,
        username=username,
        phone=phone,
        phone_normalized=phone_normalized,
        password_hash=password_hash,
        code_hash=generate_password_hash(code),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    db.session.add(verification)
    db.session.commit()
    sent, provider_message_id, provider = send_phone_code(phone_normalized, code, purpose)
    verification.provider = provider
    if provider_message_id:
        verification.provider_message_id = provider_message_id
    db.session.commit()
    return verification, code, sent, 0


def phone_verification_response(message, code=None, sent=True, retry_after=0):
    if retry_after:
        return jsonify({"ok": False, "message": f"Yeni kod için {retry_after} saniye bekle.", "retryAfter": retry_after}), 429
    response = {
        "ok": True,
        "requiresVerification": True,
        "verificationChannel": "phone",
        "message": message,
        "smsSent": bool(sent),
        "resendAfter": TWO_FACTOR_RESEND_SECONDS,
    }
    if not sent:
        if expose_verification_codes():
            response["message"] += " SMS servisi hazır olmadığı için kod geliştirme modunda gösteriliyor."
            response["devCode"] = code
        else:
            response["ok"] = False
            response["message"] += " SMS gönderilemedi; lütfen yeniden dene."
    return jsonify(response), (200 if response["ok"] else 503)


def email_subject(purpose):
    labels = {
        "register": "NexaLine kayıt doğrulama kodun",
        "forgot": "NexaLine şifre sıfırlama kodun",
        "email_change": "NexaLine Gmail değiştirme kodun",
        "login_2fa": "NexaLine giriş doğrulama kodun",
    }
    return labels.get(purpose, "NexaLine doğrulama kodun")


def email_body(code):
    return (
        f"NexaLine doğrulama kodun: {code}\n\n"
        "Bu kod 10 dakika geçerlidir. Bu işlemi sen yapmadıysan bu mesajı yok sayabilirsin."
    )


def send_email_via_resend(email, subject, body):
    api_key = os.environ.get("RESEND_API_KEY")
    mail_from = os.environ.get("RESEND_FROM") or os.environ.get("MAIL_FROM")
    if not api_key or not mail_from:
        return False

    response = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"from": mail_from, "to": [email], "subject": subject, "text": body},
        timeout=15,
    )
    response.raise_for_status()
    return True


def send_email_via_brevo(email, subject, body):
    api_key = os.environ.get("BREVO_API_KEY")
    mail_from = os.environ.get("BREVO_FROM") or os.environ.get("MAIL_FROM")
    if not api_key or not mail_from:
        return False

    response = requests.post(
        "https://api.brevo.com/v3/smtp/email",
        headers={"api-key": api_key, "Content-Type": "application/json", "Accept": "application/json"},
        json={
            "sender": {"email": mail_from},
            "to": [{"email": email}],
            "subject": subject,
            "textContent": body,
        },
        timeout=15,
    )
    response.raise_for_status()
    return True


def create_email_verification(purpose, email, email_normalized, username=None, password_hash=None):
    EmailVerification.query.filter_by(purpose=purpose, username=username, email_normalized=email_normalized).delete(synchronize_session=False)
    code = verification_code()
    verification = EmailVerification(
        id=uuid4().hex,
        purpose=purpose,
        username=username,
        email=email,
        email_normalized=email_normalized,
        password_hash=password_hash,
        code_hash=generate_password_hash(code),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    db.session.add(verification)
    db.session.commit()
    sent = send_email_code(email, code, purpose)
    return verification, code, sent


def latest_email_verification(purpose, username, email_normalized):
    return EmailVerification.query.filter_by(
        purpose=purpose,
        username=username,
        email_normalized=email_normalized,
    ).order_by(EmailVerification.created_at.desc()).first()


def verification_resend_wait_seconds(row):
    if not row:
        return 0
    created_at = row.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    elapsed = (datetime.now(timezone.utc) - created_at).total_seconds()
    return max(0, int(TWO_FACTOR_RESEND_SECONDS - elapsed))


def expose_verification_codes():
    configured = os.environ.get("EXPOSE_VERIFICATION_CODES")
    if configured is None:
        return not bool(os.environ.get("RENDER"))
    return configured.strip().lower() in {"1", "true", "yes", "on"}


def send_email_code(email, code, purpose):
    subject = email_subject(purpose)
    body = email_body(code)

    if os.environ.get("RESEND_API_KEY"):
        try:
            if send_email_via_resend(email, subject, body):
                return True
        except Exception:
            app.logger.exception("Dogrulama maili Resend ile gonderilemedi")

    if os.environ.get("BREVO_API_KEY"):
        try:
            if send_email_via_brevo(email, subject, body):
                return True
        except Exception:
            app.logger.exception("Dogrulama maili Brevo ile gonderilemedi")

    smtp_host = os.environ.get("SMTP_HOST", DEFAULT_SMTP_HOST).strip()
    smtp_port = int(os.environ.get("SMTP_PORT", str(DEFAULT_SMTP_PORT)))
    smtp_username = os.environ.get("SMTP_USERNAME", DEFAULT_SMTP_USERNAME).strip().lower()
    smtp_password = "".join(os.environ.get("SMTP_PASSWORD", "").split())
    mail_from = os.environ.get("MAIL_FROM") or smtp_username

    if not smtp_host or not smtp_username or not smtp_password or not mail_from:
        app.logger.warning("SMTP ayarları eksik; %s adresine doğrulama maili gönderilemedi.", email)
        return False

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = mail_from
    message["To"] = email
    message.set_content(body)

    try:
        with IPv4SMTP(smtp_host, smtp_port, timeout=15) as smtp:
            smtp.starttls()
            smtp.login(smtp_username, smtp_password)
            smtp.send_message(message)
        return True
    except Exception:
        app.logger.exception("Doğrulama maili gönderilemedi")
        return False


def verification_response(message, code=None, sent=True):
    response = {"ok": True, "requiresVerification": True, "message": message, "mailSent": bool(sent)}
    if not sent:
        if expose_verification_codes():
            response["message"] += " Mail servisi hazır olmadığı için kod geliştirme modunda gösteriliyor."
            response["devCode"] = code
        else:
            response["message"] += " Doğrulama e-postası gönderilemedi; lütfen yeniden dene."
    return jsonify(response)


def login_success_payload(user, device_id=None, message="Giriş başarılı."):
    device = upsert_device_session(user.username, device_id)
    maybe_award_daily_login(user)
    db.session.commit()
    payload = {"ok": True, "message": message, "user": private_user(user.username)}
    if device:
        payload["device"] = device_session_to_dict(device)
    return payload


def cleanup_qr_login_sessions():
    now = datetime.now(timezone.utc)
    with qr_login_lock:
        expired = [
            session_id
            for session_id, row in qr_login_sessions.items()
            if row["expires_at"] <= now or row.get("consumed")
        ]
        for session_id in expired:
            qr_login_sessions.pop(session_id, None)


def parse_qr_login_token(value):
    value = (value or "").strip()
    if "nexaQrLogin=" in value:
        value = value.split("nexaQrLogin=", 1)[1].split("&", 1)[0]
    if "." not in value:
        return "", ""
    session_id, secret = value.split(".", 1)
    return session_id.strip(), secret.strip()


def qr_session_error(session_id, secret):
    cleanup_qr_login_sessions()
    with qr_login_lock:
        row = qr_login_sessions.get(session_id)
        if not row or row.get("secret") != secret:
            return None, (jsonify({"ok": False, "message": "QR oturumu bulunamadı."}), 404)
        if row["expires_at"] <= datetime.now(timezone.utc):
            qr_login_sessions.pop(session_id, None)
            return None, (jsonify({"ok": False, "message": "QR kodun süresi doldu."}), 410)
        return row, None


def rtc_servers():
    servers = [{"urls": "stun:stun.l.google.com:19302"}]
    extra_urls = [url.strip() for url in os.environ.get("RTC_ICE_URLS", "").split(",") if url.strip()]
    if extra_urls:
        servers = [{"urls": extra_urls}]

    turn_url = os.environ.get("TURN_URL")
    turn_username = os.environ.get("TURN_USERNAME")
    turn_credential = os.environ.get("TURN_CREDENTIAL")
    if turn_url and turn_username and turn_credential:
        servers.append({"urls": turn_url, "username": turn_username, "credential": turn_credential})

    return servers


AI_SYSTEM_PROMPT = """Sen NexaLine içinde çalışan kişiselleştirilebilir Nexa AI asistansın.
Kullanıcı sana hangi ismi verdiyse o isimle davran; yeri geldiğinde sıcak bir arkadaş, yeri geldiğinde net bir asistan ol.
Türkçe, doğal, kısa ve güvenli cevap ver. Sohbetleri özetleyebilir, cevap taslağı hazırlayabilir, uygulama ayarlarını
açıklayabilir ve izinli uygulama eylemleri önerebilirsin. Mesaj gönderme, sohbet silme, arama başlatma, kilitleme,
tema/gizlilik/AI ayarı değiştirme ve zamanlama gibi işlemler uygulama tarafından kullanıcının onay ayarına göre çalıştırılır.
Bilmediğin veya internette doğrulanması gereken konuda eminmiş gibi davranma."""

AI_SYSTEM_PROMPT += """
Uygulama bağlamındaki memory, clientHistory ve relevantChats alanlarını Nexa AI'nin ortak hafızası gibi kullan.
Sağlayıcı değişse bile üslubunu, kullanıcının sana verdiği ismi ve önceki konuşma bilgisini bu hafızadan koru.
Kullanıcı internetten araştırma isterse web araştırma notlarını kullan, kaynakları kısa ve okunur şekilde belirt; sonuç yoksa bunu açık söyle.
Kullanıcı önceki konuşmasına gönderme yapıyorsa yalnızca geçmişi listeleme; geçmişteki ilgili bilgiyi mevcut soruyla birleştirip doğrudan cevap ver.
Bir görsel veya dosya verildiyse gerçekten görebildiğin içeriği açıkla. Görsel verisi sağlayıcıya ulaşmadıysa gördüğünü iddia etme.
"""

def ai_get_system_tools():
    """Nexa AI'nin uygulama aksiyonu gerektiğinde JSON olarak seçeceği araçlar."""
    base_schema = {"type": "object", "properties": {}}
    return [
        {
            "name": "update_user_profile",
            "description": "Kullanıcının profil bilgilerini günceller.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "about": {"type": "string"},
                    "status": {"type": "string"},
                },
            },
        },
        {
            "name": "focus_chat",
            "description": "Belirli bir sohbeti açar veya ona odaklanır.",
            "parameters": {
                "type": "object",
                "properties": {
                    "chatId": {"type": "string"},
                    "chatName": {"type": "string"},
                },
            },
        },
        {
            "name": "set_privacy_setting",
            "description": "Gizlilik ayarlarını değiştirir.",
            "parameters": {
                "type": "object",
                "properties": {
                    "setting": {"type": "string"},
                    "value": {"type": "boolean"},
                },
            },
        },
        {"name": "set_theme", "description": "Temayı dark/light olarak değiştirir.", "parameters": base_schema},
        {"name": "set_censor_filter", "description": "AI sansür filtresini açar veya kapatır.", "parameters": base_schema},
        {"name": "set_ai_enabled", "description": "Nexa AI'yi açar veya kapatır.", "parameters": base_schema},
        {"name": "set_ai_auto_approve", "description": "Nexa AI tam yetki/onay modunu değiştirir.", "parameters": base_schema},
        {"name": "set_ai_name", "description": "Nexa AI özel adını değiştirir.", "parameters": base_schema},
        {"name": "open_settings", "description": "Ayarlar ekranını veya bir ayar sekmesini açar.", "parameters": base_schema},
        {"name": "set_chat_preference", "description": "Sohbet sabitleme, sessize alma veya kilit ayarını değiştirir.", "parameters": base_schema},
        {"name": "delete_chat", "description": "Sohbet silme/arşivleme aksiyonu hazırlar.", "parameters": base_schema},
        {"name": "start_call", "description": "Sesli veya görüntülü arama başlatır.", "parameters": base_schema},
        {"name": "schedule_call", "description": "Planlı arama aksiyonu hazırlar.", "parameters": base_schema},
        {"name": "end_call", "description": "Aktif aramayı kapatır.", "parameters": base_schema},
        {"name": "send_message", "description": "Hedef sohbete mesaj gönderme aksiyonu hazırlar.", "parameters": base_schema},
        {"name": "schedule_message", "description": "Zamanlı mesaj gönderme aksiyonu hazırlar.", "parameters": base_schema},
        {"name": "draft_message", "description": "Hedef sohbet için mesaj taslağı hazırlar.", "parameters": base_schema},
        {"name": "reply_message", "description": "Bir mesaja yanıt aksiyonu hazırlar.", "parameters": base_schema},
        {"name": "react_message", "description": "Bir mesaja emoji tepkisi bırakır.", "parameters": base_schema},
        {"name": "create_group", "description": "Yeni grup oluşturur.", "parameters": base_schema},
        {"name": "update_group", "description": "Grup adını veya üyelerini günceller.", "parameters": base_schema},
        {"name": "create_story", "description": "Yeni durum/güncelleme paylaşır.", "parameters": base_schema},
        {"name": "delete_story", "description": "Son durum/güncellemeyi siler.", "parameters": base_schema},
        {"name": "contact_request", "description": "Arkadaş veya mesajlaşma isteği gönderir.", "parameters": base_schema},
        {"name": "open_notifications", "description": "Bildirim merkezini açar.", "parameters": base_schema},
    ]


AI_SYSTEM_PROMPT += f"""
Uygulama içinde bir işlem gerekiyorsa regex/kelime eşleşmesi gibi davranma; kullanıcının niyetini bağlama göre anla.
İşlem gerekmiyorsa normal doğal cevap ver. İşlem gerekiyorsa SADECE geçerli JSON döndür.
Geçerli format:
{{"reply":"Kullanıcıya kısa açıklama","actions":[{{"action":"tool_name","parameters":{{}}}}]}}
Tek işlem için {{"action":"tool_name","parameters":{{}}}} formatı da geçerlidir.
JSON içinde markdown, kod bloğu veya fazladan metin kullanma.
Geçerli araçlar:
{json.dumps(ai_get_system_tools(), ensure_ascii=False, indent=2)}
"""


def ai_parse_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    lowered = fold_tr_ascii(str(value or "")).strip()
    if lowered in {"1", "true", "evet", "on", "acik", "ac", "aktif", "enable", "enabled"}:
        return True
    if lowered in {"0", "false", "hayir", "off", "kapali", "kapat", "pasif", "disable", "disabled"}:
        return False
    return default


def ai_extract_intent_json(text_value):
    raw = (text_value or "").strip()
    if not raw:
        return None
    candidates = [raw]
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw, re.IGNORECASE)
    if fence:
        candidates.insert(0, fence.group(1).strip())
    if "{" in raw and "}" in raw:
        candidates.append(raw[raw.find("{"): raw.rfind("}") + 1])
    if "[" in raw and "]" in raw:
        candidates.append(raw[raw.find("["): raw.rfind("]") + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, (dict, list)):
            return parsed
    return None


def ai_action_params(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            value = {}
    return value if isinstance(value, dict) else {}


def ai_context_chats(context):
    chats = []
    active = (context or {}).get("activeChat")
    if isinstance(active, dict):
        chats.append(active)
    for key in ("chats", "relevantChats"):
        for chat in (context or {}).get(key) or []:
            if isinstance(chat, dict) and chat not in chats:
                chats.append(chat)
    return chats


def ai_resolve_chat_id(context, params):
    direct = str(params.get("chatId") or params.get("chat_id") or "").strip()
    if direct:
        return direct
    target = fold_tr_ascii(
        params.get("chatName")
        or params.get("chat_name")
        or params.get("target")
        or params.get("name")
        or params.get("username")
        or ""
    )
    for chat in ai_context_chats(context):
        chat_id = str(chat.get("id") or "").strip()
        haystack = fold_tr_ascii(
            " ".join(
                str(item or "")
                for item in [chat.get("title"), chat.get("name"), chat.get("username"), chat.get("displayName"), chat_id]
            )
        )
        if chat_id and target and target in haystack:
            return chat_id
    active = (context or {}).get("activeChat") or {}
    return str(active.get("id") or "").strip() or None


def ai_normalize_tool_action(tool_call, context=None):
    if not isinstance(tool_call, dict):
        return None
    raw_name = (
        tool_call.get("action")
        or tool_call.get("name")
        or tool_call.get("tool")
        or tool_call.get("function")
        or tool_call.get("type")
        or ""
    )
    name = fold_tr_ascii(str(raw_name)).replace("-", "_").replace(" ", "_")
    params = ai_action_params(
        tool_call.get("parameters")
        or tool_call.get("params")
        or tool_call.get("arguments")
        or tool_call.get("input")
    )
    label = str(tool_call.get("label") or params.get("label") or "").strip()
    chat_id = ai_resolve_chat_id(context, params)

    if name in {"update_user_profile", "update_profile", "profile_update"}:
        action = {"type": "update_profile", "label": label or "Profil bilgilerini güncelle"}
        display_name = params.get("name") or params.get("displayName") or params.get("display_name")
        about = params.get("about") or params.get("status") or params.get("bio")
        if display_name:
            action["displayName"] = str(display_name)[:80]
        if about:
            action["about"] = str(about)[:180]
        return action if len(action) > 2 else None

    if name in {"focus_chat", "open_chat"} and chat_id:
        return {"type": "open_chat", "chatId": chat_id, "label": label or "Sohbeti aç"}

    if name in {"set_privacy_setting", "set_privacy"}:
        setting = fold_tr_ascii(params.get("setting") or params.get("key") or "")
        value = ai_parse_bool(params.get("value", params.get("enabled", True)), True)
        privacy_fields = {
            "last_seen": "lastSeenHidden",
            "son_gorulme": "lastSeenHidden",
            "lastseen": "lastSeenHidden",
            "online": "onlineHidden",
            "cevrim_ici": "onlineHidden",
            "read_receipts": "readReceiptsOff",
            "okundu": "readReceiptsOff",
            "mavi_tik": "readReceiptsOff",
            "email": "emailHidden",
            "gmail": "emailHidden",
        }
        for key, field in privacy_fields.items():
            if key in setting:
                return {"type": "set_privacy", "privacy": {field: value}, "label": label or "Gizlilik ayarını güncelle"}
        privacy = params.get("privacy") if isinstance(params.get("privacy"), dict) else {}
        return {"type": "set_privacy", "privacy": privacy, "label": label or "Gizlilik ayarlarını güncelle"} if privacy else None

    if name == "set_theme":
        theme = str(params.get("theme") or params.get("mode") or "dark").strip().lower()
        return {"type": "set_theme", "theme": theme, "label": label or "Temayı güncelle"}

    if name in {"set_censor_filter", "set_censor"}:
        return {"type": "set_censor", "enabled": ai_parse_bool(params.get("enabled", params.get("value", True)), True), "label": label or "AI sansür filtresini güncelle"}

    if name in {"set_ai_enabled", "set_ai_auto_approve"}:
        return {"type": name, "enabled": ai_parse_bool(params.get("enabled", params.get("value", True)), True), "label": label or "Nexa AI ayarını güncelle"}

    if name == "set_ai_name":
        ai_name = str(params.get("name") or params.get("assistantName") or "").strip()[:40]
        return {"type": "set_ai_name", "name": ai_name, "label": label or f"Nexa AI adını {ai_name} yap"} if ai_name else None

    if name == "open_settings":
        return {"type": "open_settings", "section": str(params.get("section") or "menu"), "label": label or "Ayarları aç"}

    if name in {"set_chat_preference", "set_chat_pref"} and chat_id:
        action = {"type": "set_chat_pref", "chatId": chat_id, "label": label or "Sohbet ayarını güncelle"}
        for key in ("pinned", "muted", "locked"):
            if key in params:
                action[key] = ai_parse_bool(params.get(key))
        return action if len(action) > 3 else None

    if name == "delete_chat" and chat_id:
        return {"type": "delete_chat", "chatId": chat_id, "mode": params.get("mode") or "archive", "label": label or "Sohbeti sil"}

    if name in {"start_call", "schedule_call"} and chat_id:
        action = {
            "type": name,
            "chatId": chat_id,
            "audioOnly": ai_parse_bool(params.get("audioOnly", params.get("audio_only", True)), True),
            "label": label or ("Planlı arama hazırla" if name == "schedule_call" else "Arama başlat"),
        }
        if name == "schedule_call" and params.get("callAt"):
            action["callAt"] = params.get("callAt")
        return action

    if name == "end_call":
        return {"type": "end_call", "label": label or "Aktif aramayı kapat"}

    if name in {"send_message", "schedule_message", "draft_message"} and chat_id:
        body = str(params.get("body") or params.get("message") or params.get("text") or "").strip()
        action = {"type": name, "chatId": chat_id, "body": body, "label": label or "Mesaj aksiyonunu hazırla"}
        if name == "schedule_message" and params.get("sendAt"):
            action["sendAt"] = params.get("sendAt")
        return action if body or name == "draft_message" else None

    if name in {"reply_message", "react_message"} and chat_id:
        action = {"type": name, "chatId": chat_id, "label": label or "Mesaj aksiyonunu hazırla"}
        if params.get("messageId"):
            action["messageId"] = params.get("messageId")
        if name == "reply_message":
            action["body"] = str(params.get("body") or params.get("message") or "").strip()
        else:
            action["emoji"] = str(params.get("emoji") or "👍")
        return action

    if name in {"create_group", "update_group"}:
        action = {"type": name, "label": label or ("Grubu güncelle" if name == "update_group" else "Grup oluştur")}
        if chat_id:
            action["chatId"] = chat_id
        if params.get("title"):
            action["title"] = str(params.get("title"))[:80]
        if isinstance(params.get("members"), list):
            action["members"] = [str(item).strip() for item in params["members"] if str(item).strip()]
        return action

    if name == "create_story":
        body = str(params.get("body") or params.get("text") or "").strip()
        return {"type": "create_story", "body": body, "label": label or "Yeni güncelleme paylaş"} if body else None

    if name == "delete_story":
        return {"type": "delete_story", "label": label or "Son güncellemeyi sil"}

    if name == "contact_request":
        target = str(params.get("username") or params.get("target") or "").strip().lower()
        return {"type": "contact_request", "username": target, "label": label or "İstek gönder"} if target else None

    if name == "open_notifications":
        return {"type": "open_notifications", "label": label or "Bildirimleri aç"}

    return None


def process_ai_intent(ai_response_text, context=None):
    """Model JSON aksiyon döndürdüyse frontend'in mevcut aksiyon şemasına çevirir."""
    parsed = ai_extract_intent_json(ai_response_text)
    if parsed is None:
        return {"is_intent": False, "reply": ai_response_text, "actions": []}

    reply = ""
    calls = []
    if isinstance(parsed, list):
        calls = parsed
    elif isinstance(parsed, dict):
        reply = str(parsed.get("reply") or parsed.get("message") or parsed.get("text") or "").strip()
        if isinstance(parsed.get("actions"), list):
            calls = parsed["actions"]
        elif isinstance(parsed.get("function_call"), dict):
            calls = [parsed["function_call"]]
        elif any(key in parsed for key in ("action", "name", "tool", "function", "type")):
            calls = [parsed]

    actions = []
    for call in calls:
        action = ai_normalize_tool_action(call, context)
        if action:
            actions.append(action)

    if not reply and actions:
        reply = "İşlemi hazırladım. Onaylarsan uygulayacağım."
    return {"is_intent": bool(actions or reply), "reply": reply or ai_response_text, "actions": actions}


ADULT_TERMS = {"+18", "porno", "porn", "cinsel", "nude", "nudes", "seks", "sex", "erotik", "onlyfans"}
ABUSE_TERMS = {"salak", "aptal", "gerizekali", "gerizekalı", "mal", "orospu", "siktir", "amk", "aq"}


AI_NATURAL_ERROR = "Anlayamadım, tekrar denemek ister misin?"


def ai_error_boundary(handler):
    @wraps(handler)
    def wrapped(*args, **kwargs):
        try:
            return handler(*args, **kwargs)
        except Exception as error:
            db.session.rollback()
            app.logger.exception("AI request failed in %s: %s", handler.__name__, error)
            return jsonify({"ok": False, "message": AI_NATURAL_ERROR, "errorType": type(error).__name__}), 503

    return wrapped


def ai_provider_status():
    provider = (os.environ.get("AI_PROVIDER") or "auto").strip().lower()
    gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("VITE_GEMINI_API_KEY")
    providers = {
        "gemini": {"provider": "gemini", "model": os.environ.get("GEMINI_MODEL", "gemini-1.5-flash"), "ready": bool(gemini_key), "task": "chat/vision/web"},
        "groq": {"provider": "groq", "model": os.environ.get("GROQ_CHAT_MODEL", "llama-3.3-70b-versatile"), "ready": bool(os.environ.get("GROQ_API_KEY")), "task": "fast chat/stt"},
        "deepinfra": {"provider": "deepinfra", "model": os.environ.get("DEEPINFRA_MODEL", "meta-llama/Llama-3.3-70B-Instruct-Turbo"), "ready": bool(os.environ.get("DEEPINFRA_API_KEY")), "task": "chat fallback"},
        "openrouter": {"provider": "openrouter", "model": os.environ.get("OPENROUTER_MODEL", "openrouter/free"), "ready": bool(os.environ.get("OPENROUTER_API_KEY")), "task": "free chat fallback"},
        "openai": {"provider": "openai", "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"), "ready": bool(os.environ.get("OPENAI_API_KEY")), "task": "chat fallback"},
        "openai_tts": {"provider": "openai_tts", "model": os.environ.get("OPENAI_TTS_MODEL", "gpt-4o-mini-tts"), "ready": bool(os.environ.get("OPENAI_API_KEY")), "task": "tts"},
        "ollama": {"provider": "ollama", "model": os.environ.get("OLLAMA_MODEL", os.environ.get("AI_MODEL", "llama3.2")), "ready": bool(os.environ.get("OLLAMA_BASE_URL")), "task": "local model"},
        "huggingface": {"provider": "huggingface", "model": os.environ.get("HF_IMAGE_MODEL", "black-forest-labs/FLUX.1-schnell"), "ready": bool(os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_API_KEY")), "task": "image fallback"},
        "elevenlabs": {"provider": "elevenlabs", "model": os.environ.get("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2"), "ready": bool(os.environ.get("ELEVENLABS_API_KEY")), "task": "tts"},
        "assemblyai": {"provider": "assemblyai", "model": os.environ.get("ASSEMBLYAI_SPEECH_MODEL", "best"), "ready": bool(os.environ.get("ASSEMBLYAI_API_KEY")), "task": "stt fallback"},
    }
    aliases = {"google": "gemini", "openai-compatible": "openai", "hf": "huggingface"}
    provider = aliases.get(provider, provider)
    if provider != "auto" and provider in providers:
        return {**providers[provider], "providers": list(providers.values())}
    for key in ("gemini", "groq", "deepinfra", "openrouter", "openai", "ollama"):
        if providers[key]["ready"]:
            return {**providers[key], "providers": list(providers.values())}
    return {"provider": "local", "model": "nexaline-free-ai", "ready": True, "free": True, "providers": list(providers.values())}


def ai_provider_attempts(preferred=None, needs_vision=False):
    preferred = (preferred or os.environ.get("AI_PROVIDER") or "auto").strip().lower()
    preferred = {"google": "gemini", "openai-compatible": "openai"}.get(preferred, preferred)
    order = ["gemini", "groq", "deepinfra", "openrouter", "openai", "ollama"]
    if needs_vision:
        order = ["gemini", "openai", "openrouter", "huggingface_vision"]
    if preferred != "auto" and preferred in order:
        order = [preferred] + [item for item in order if item != preferred]
    return order


def compact_ai_message(message):
    attachment = message.get("attachment") if isinstance(message, dict) else None
    attachment_label = None
    if isinstance(attachment, dict):
        attachment_label = attachment.get("transcript") or attachment.get("name") or attachment.get("type")
    return {
        "sender": message.get("sender"),
        "body": (message.get("body") or "")[:900],
        "attachment": attachment_label,
        "createdAt": message.get("createdAt"),
    }


def ai_prompt_tokens(prompt):
    lowered = fold_tr_ascii(prompt or "")
    tokens = re.findall(r"[\wçğıöşüÇĞİÖŞÜ]+", lowered)
    stop_words = {
        "bir", "bu", "şu", "su", "ben", "bana", "sen", "sana", "onu", "bunu", "şunu", "sohbet",
        "sohbeti", "önceki", "onceki", "geçmiş", "gecmis", "mesaj", "mesajı", "mesaji", "ne",
        "nedir", "nasıl", "nasil", "ara", "araştır", "arastir", "özetle", "ozetle", "hatırla",
        "hatirla", "hakkında", "hakkinda", "ile", "ve", "ya", "de", "da", "ki", "mi", "mı",
    }
    return [token for token in tokens if len(token) >= 3 and token not in stop_words][:24]


def fold_tr_ascii(value):
    mapping = str.maketrans({
        "ç": "c", "Ç": "c",
        "ğ": "g", "Ğ": "g",
        "ı": "i", "I": "i",
        "İ": "i", "i": "i",
        "ö": "o", "Ö": "o",
        "ş": "s", "Ş": "s",
        "ü": "u", "Ü": "u",
    })
    folded = (value or "").casefold().translate(mapping)
    return unicodedata.normalize("NFKD", folded).encode("ascii", "ignore").decode("ascii")


def compact_ai_memory(row):
    return {
        "role": row.role,
        "content": (row.content or "")[:1200],
        "chatId": row.chat_id,
        "provider": row.provider,
        "createdAt": to_iso(row.created_at),
    }


def sanitize_client_ai_history(items):
    if not isinstance(items, list):
        return []
    cleaned = []
    for item in items[-30:]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        text_value = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
        if role not in {"user", "assistant"} or not text_value:
            continue
        cleaned.append({
            "role": role,
            "content": text_value[:1000],
            "createdAt": str(item.get("createdAt") or "")[:40],
            "source": "client-local-history",
        })
    return cleaned


def ai_memory_for_user(username, chat_id=None, prompt="", client_history=None):
    rows = (
        AiMemory.query.filter_by(username=username)
        .order_by(AiMemory.created_at.desc())
        .limit(AI_MEMORY_MAX_ITEMS)
        .all()
    )
    tokens = set(ai_prompt_tokens(prompt))
    recent = list(reversed(rows[:24]))
    relevant = []
    for row in rows:
        haystack = fold_tr_ascii(f"{row.content or ''} {row.chat_id or ''}")
        if (chat_id and row.chat_id == chat_id) or (tokens and any(token in haystack for token in tokens)):
            relevant.append(row)
        if len(relevant) >= 24:
            break
    merged = []
    seen = set()
    for item in [*reversed(relevant), *recent]:
        if item.id in seen:
            continue
        seen.add(item.id)
        merged.append(compact_ai_memory(item))
    return {
        "serverMemory": merged[-60:],
        "clientHistory": sanitize_client_ai_history(client_history),
        "memoryRule": "Tum AI saglayicilari cevap vermeden once bu Nexa hafizasini dikkate alir. Sifreler ve gizli anahtarlar hafizaya eklenmez.",
    }


def relevant_chat_context_for_ai(username, prompt, active_chat_id=None):
    chats = visible_chats(username)
    tokens = set(ai_prompt_tokens(prompt))
    prompt_folded = fold_tr_ascii(prompt or "")
    scored = []
    for index, chat in enumerate(chats):
        score = 0
        if active_chat_id and chat["id"] == active_chat_id:
            score += 100
        haystack_parts = [chat.get("title") or "", chat.get("id") or ""]
        for member in chat.get("members", []):
            haystack_parts.extend([member.get("username") or "", member.get("displayName") or ""])
        for message in (chat.get("messages") or [])[-AI_RELEVANT_CHAT_MESSAGES:]:
            haystack_parts.append(message.get("body") or "")
            attachment = message.get("attachment")
            if isinstance(attachment, dict):
                haystack_parts.append(json.dumps({key: attachment.get(key) for key in ("name", "type", "transcript")}, ensure_ascii=False))
        haystack = fold_tr_ascii(" ".join(haystack_parts))
        chat_title = fold_tr_ascii(chat.get("title") or "")
        if chat_title and chat_title in prompt_folded:
            score += 60
        if tokens:
            score += sum(6 for token in tokens if token in haystack)
        if any(word in prompt_folded for word in ["onceki", "gecmis", "son konus", "ne konus", "sohbet", "hatirla"]):
            score += max(0, 12 - index)
        if any(word in prompt_folded for word in ["önceki", "onceki", "geçmiş", "gecmis", "son konuş", "son konus", "ne konuş", "ne konus"]):
            score += max(0, 12 - index)
        if score > 0:
            scored.append((score, index, chat))
    if not scored:
        scored = [(max(0, 8 - index), index, chat) for index, chat in enumerate(chats[:3])]
    selected = [chat for _score, _index, chat in sorted(scored, key=lambda item: (-item[0], item[1]))[:AI_RELEVANT_CHAT_LIMIT]]
    return [
        {
            "id": chat["id"],
            "title": chat["title"],
            "type": chat["type"],
            "members": [member.get("displayName") or member.get("username") for member in chat.get("members", [])],
            "lastMessages": [compact_ai_message(message) for message in (chat.get("messages") or [])[-AI_RELEVANT_CHAT_MESSAGES:]],
        }
        for chat in selected
    ]


def store_ai_memory(username, chat_id, role, content, provider=None, meta=None):
    text_value = re.sub(r"\s+", " ", (content or "").strip())
    if not text_value:
        return
    db.session.add(AiMemory(
        id=uuid4().hex,
        username=username,
        chat_id=chat_id,
        role=role,
        content=text_value[:4000],
        provider=provider,
        meta=meta or {},
    ))
    old_rows = (
        AiMemory.query.filter_by(username=username)
        .order_by(AiMemory.created_at.desc())
        .offset(AI_MEMORY_MAX_ITEMS * 4)
        .limit(80)
        .all()
    )
    for row in old_rows:
        db.session.delete(row)


def ai_context_for_user(username, chat_id=None, prompt="", client_history=None):
    chats = visible_chats(username)
    active = next((chat for chat in chats if chat["id"] == chat_id), None) if chat_id else None
    if not active and chats:
        active = chats[0]
    stories = active_stories(username)
    own_stories = [story for story in stories if story.get("username") == username]
    return {
        "user": {"username": username, "displayName": private_user(username).get("displayName")},
        "activeChat": None if not active else {
            "id": active["id"],
            "title": active["title"],
            "type": active["type"],
            "members": [member["displayName"] for member in active.get("members", [])],
            "messages": [compact_ai_message(message) for message in active.get("messages", [])[-AI_MAX_CONTEXT_MESSAGES:]],
        },
        "chats": [
            {
                "id": chat["id"],
                "title": chat["title"],
                "type": chat["type"],
                "members": [member["displayName"] for member in chat.get("members", [])],
                "lastMessage": compact_ai_message(chat["lastMessage"]) if chat.get("lastMessage") else None,
            }
            for chat in chats[:AI_MAX_CHATS]
        ],
        "stories": {
            "ownActiveCount": len(own_stories),
            "ownActive": [
                {
                    "id": story.get("id"),
                    "body": story.get("body"),
                    "createdAt": story.get("createdAt"),
                    "expiresAt": story.get("expiresAt"),
                }
                for story in own_stories[:5]
            ],
        },
        "notifications": "Kullanıcının eski bildirimleri tarayıcı içinde tutulur; AI onları uygulamada Bildirimler panelini açarak gösterebilir.",
        "memory": ai_memory_for_user(username, chat_id, prompt, client_history),
        "relevantChats": relevant_chat_context_for_ai(username, prompt, chat_id),
        "capabilities": [
            "sohbet ozeti",
            "cevap taslagi",
            "onayli mesaj gonderme",
            "profil adı ve hakkımda güncelleme",
            "tema değiştirme",
            "sohbet sabitleme/sessize alma/gizleme",
            "AI sansur filtresini acma/kapatma",
            "Nexa AI acma/kapatma, isim ve onay yetkisi ayarlama",
            "gizlilik ayarlarını onaylı değiştirme",
            "sohbet açma, sohbet silme ve arama başlatma",
            "mesaj zamanlama",
            "gelen mesaji +18/kufur/spam icin uyarmali gizleme",
        ],
        "privacy": "Şifreler ve şifre hashleri AI bağlamına eklenmez.",
    }


def text_has_any(value, terms):
    lowered = (value or "").lower()
    return any(term in lowered for term in terms)


def ai_moderation_labels(text_value):
    labels = []
    if text_has_any(text_value, ADULT_TERMS):
        labels.append("adult")
    if text_has_any(text_value, ABUSE_TERMS):
        labels.append("abuse")
    if re.search(r"https?://\S+", text_value or "", re.IGNORECASE) and re.search(r"bedava|kazand[ıi]n|t[ıi]kla|bonus", text_value or "", re.IGNORECASE):
        labels.append("spam")
    return labels


def find_chat_for_ai(username, prompt, active_chat_id=None):
    chats = visible_chats(username)
    if active_chat_id:
        active = next((chat for chat in chats if chat["id"] == active_chat_id), None)
        if active:
            return active
    prompt_folded = (prompt or "").casefold()
    for chat in chats:
        title = (chat.get("title") or "").casefold()
        if title and title in prompt_folded:
            return chat
        for member in chat.get("members", []):
            name = (member.get("displayName") or member.get("username") or "").casefold()
            if name and name in prompt_folded:
                return chat
    return None


def extract_quoted_text(prompt):
    match = re.search(r"[\"“']([^\"”']{1,1000})[\"”']", prompt or "")
    if match:
        return match.group(1).strip()
    match = re.search(r"(?:mesaj|de ki|şunu|sunu|bunu)\s*[:\-]\s*(.+)", prompt or "", re.IGNORECASE)
    if match:
        return match.group(1).strip()[:1000]
    match = re.search(r"(?:mesaj at|mesaj gönder|mesaj gonder|de ki)\s*[:\-]?\s+(.+)", prompt or "", re.IGNORECASE)
    if match:
        return match.group(1).strip()[:1000]
    return ""


def extract_value_after_phrases(prompt, phrases, max_length=120):
    text_value = re.sub(r"\s+", " ", prompt or "").strip()
    for phrase in phrases:
        pattern = rf"{phrase}\s*(?:[:\-]|olarak|olsun|yap|yapabilir misin|değiştir|degistir)?\s+(.+)"
        match = re.search(pattern, text_value, re.IGNORECASE)
        if not match:
            continue
        value = match.group(1).strip(" .!?\"'“”")
        value = re.sub(r"\b(yap|olsun|değiştir|degistir|lütfen|lutfen)$", "", value, flags=re.IGNORECASE).strip()
        value = re.sub(r"\b(olarak|diye)$", "", value, flags=re.IGNORECASE).strip()
        if value:
            return value[:max_length]
    return ""


def ai_mentioned_usernames(prompt, current_username):
    folded = (prompt or "").casefold()
    result = []
    for user in User.query.order_by(User.display_name).all():
        if user.username == current_username:
            continue
        names = {user.username.casefold(), (user.display_name or "").casefold()}
        first_name = (user.display_name or "").split(" ")[0].casefold()
        if first_name:
            names.add(first_name)
        if any(name and name in folded for name in names):
            result.append(user.username)
    return result[:20]


def ai_last_target_message(chat, username):
    for message in reversed(chat.get("messages") or []):
        if message.get("sender") != username and not message.get("deletedAt"):
            return message
    for message in reversed(chat.get("messages") or []):
        if not message.get("deletedAt"):
            return message
    return None


def parse_ai_schedule(prompt, timezone_offset_minutes=0):
    relative_match = re.search(r"\b(\d{1,3})\s*(dakika|dk|saat)\s*(?:sonra|içinde|icinde)\b", prompt or "", re.IGNORECASE)
    if relative_match:
        amount = int(relative_match.group(1))
        unit = relative_match.group(2).casefold()
        delta = timedelta(hours=amount) if "saat" in unit else timedelta(minutes=amount)
        return (datetime.now(timezone.utc) + delta).isoformat()
    hour_match = re.search(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\b", prompt or "")
    if not hour_match:
        return None
    now_utc = datetime.now(timezone.utc)
    local_tz = timezone(timedelta(minutes=-int(timezone_offset_minutes or 0)))
    local_now = now_utc.astimezone(local_tz)
    date_value = local_now.date()
    lowered = (prompt or "").lower()
    if "yarın" in lowered or "yarin" in lowered:
        date_value = date_value + timedelta(days=1)
    else:
        date_match = re.search(r"\b(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?\b", prompt or "")
        if date_match:
            day = int(date_match.group(1))
            month = int(date_match.group(2))
            year = int(date_match.group(3) or local_now.year)
            if year < 100:
                year += 2000
            try:
                date_value = datetime(year, month, day).date()
            except ValueError:
                return None
    scheduled = datetime(date_value.year, date_value.month, date_value.day, int(hour_match.group(1)), int(hour_match.group(2)), tzinfo=local_tz)
    if scheduled <= local_now:
        scheduled = scheduled + timedelta(days=1)
    return scheduled.isoformat()



LOCAL_AI_STOPWORDS = {
    "acaba", "ama", "bana", "beni", "benim", "bir", "bunu", "bunun", "böyle", "çok",
    "daha", "diye", "gibi", "için", "ile", "mi", "mı", "mu", "mü", "nasıl", "neden",
    "nedir", "nerede", "ne", "olur", "olan", "olarak", "şey", "şu", "ve", "veya", "ya",
}


def ai_prompt_keywords(prompt):
    words = re.findall(r"[\wçğıöşüÇĞİÖŞÜ]{3,}", (prompt or "").casefold())
    return [word for word in words if word not in LOCAL_AI_STOPWORDS][:8]


def local_chat_summary(messages):
    if not messages:
        return "Bu sohbet için özet çıkaracak kadar mesaj yok."
    recent = messages[-12:]
    speakers = []
    highlights = []
    for item in recent:
        sender = item.get("sender") or "kişi"
        if sender not in speakers:
            speakers.append(sender)
        text = (item.get("body") or item.get("attachment") or "medya").strip()
        if text:
            highlights.append(f"{sender}: {text[:140]}")
    return (
        f"Kısa özet: Son konuşmada {', '.join(speakers[:4])} yer alıyor. "
        + " Ana akış: "
        + " | ".join(highlights[-8:])[:1200]
    )


def local_reply_draft(context):
    active = context.get("activeChat") or {}
    username = (context.get("user") or {}).get("username")
    messages = active.get("messages") or []
    last = next((item for item in reversed(messages) if item.get("sender") != username and not item.get("deletedAt")), None)
    if not last:
        return "Tabii. Kısa ve doğal bir cevap taslağı: “Tamamdır, sana birazdan net dönüş yapayım.”"
    body = (last.get("body") or last.get("attachment") or "").strip()
    if "?" in body:
        return "Cevap taslağı: “Gördüm, kontrol edip sana net cevap vereyim. Birazdan yazıyorum.”"
    if any(word in body.casefold() for word in ["tamam", "olur", "ok", "peki"]):
        return "Cevap taslağı: “Süper, anlaştık.”"
    return "Cevap taslağı: “Mesajını gördüm. Bana çok kısa zaman ver, düzgünce cevaplayayım.”"


def local_app_help(prompt):
    lowered = (prompt or "").casefold()
    help_items = [
        (["şifre", "sifre"], "Şifre için Ayarlar > Hesap bölümünden değiştirme, giriş ekranından da “Şifremi unuttum” akışı var."),
        (["gizlilik", "son görülme", "online", "gmail"], "Gizlilik ayarlarında son görülme, çevrim içi durum, okundu bilgisi ve Gmail görünürlüğünü yönetebilirsin."),
        (["grup"], "Grup oluşturabilir, davet gönderebilir, yönetici olarak üye çıkarabilir ve grup bilgisini düzenleyebilirsin."),
        (["durum", "story", "hikaye"], "Akış bölümünden 24 saatlik durum paylaşabilir, gelen durumlara cevap verebilirsin."),
        (["arama", "sesli", "görüntülü"], "Sohbet içindeki telefon ve kamera düğmeleriyle sesli ya da görüntülü arama başlatabilirsin."),
        (["arşiv", "arsiv", "sil"], "Sohbet silerken arşive alma veya kalıcı gizleme seçenekleri var; arşivler 3 gün içinde temizlenir."),
    ]
    for words, answer in help_items:
        if any(word in lowered for word in words):
            return answer
    return ""


def local_research_answer(prompt, research):
    snippets = [item for item in (research or []) if item.get("snippet")]
    if not snippets:
        return ""
    bullets = []
    for item in snippets[:3]:
        title = item.get("title") or "Kaynak"
        snippet = re.sub(r"\s+", " ", item.get("snippet") or "").strip()
        bullets.append(f"- {title}: {snippet[:280]}")
    return (
        "Ücretsiz web aramasıyla bulabildiğim kısa cevap:\n"
        + "\n".join(bullets)
        + "\n\nBu cevap canlı web özetidir; kritik bir karar için kaynağı açıp kontrol etmeni öneririm."
    )


def local_memory_answer(prompt, context):
    ascii_prompt = fold_tr_ascii((prompt or "").casefold())
    save_requested = any(
        phrase in ascii_prompt
        for phrase in [
            "hafizanda tut",
            "hafizana al",
            "hafizana kaydet",
            "hafizamda kalsin",
            "bunu hatirla",
            "bunu unutma",
        ]
    )
    save_requested = save_requested or (
        any(word in ascii_prompt for word in ["tut", "kaydet", "hatirla"])
        and any(word in ascii_prompt for word in ["hafiza", "favori"])
    )
    if save_requested:
        return "Tamam, bunu Nexa AI hafızama aldım. Bundan sonraki sohbetlerde bu bilgiyi dikkate alacağım."

    memory_requested = any(
        phrase in ascii_prompt
        for phrase in [
            "onceki",
            "gecmis",
            "hatirla",
            "ne konustuk",
            "son sohbet",
            "eski sohbet",
            "hafiza",
            "favori",
            "az once",
            "en son",
        ]
    )
    if not memory_requested:
        return ""

    current_prompt = re.sub(r"\s+", " ", prompt or "").strip().casefold()
    raw_memory = (
        ((context.get("memory") or {}).get("serverMemory") or [])
        + ((context.get("memory") or {}).get("clientHistory") or [])
    )
    memory = []
    seen = set()
    for item in raw_memory:
        content = re.sub(r"\s+", " ", str(item.get("content") or item.get("text") or "")).strip()
        role = item.get("role")
        key = (role, content.casefold())
        if role not in {"user", "assistant"} or not content or key in seen:
            continue
        if role == "user" and content.casefold() == current_prompt:
            continue
        seen.add(key)
        memory.append({
            "role": role,
            "content": content,
            "createdAt": str(item.get("createdAt") or ""),
        })
    memory.sort(key=lambda item: item["createdAt"])

    recent_requested = any(
        phrase in ascii_prompt
        for phrase in ["az once", "en son", "son olarak", "bir onceki", "ne sordum", "ne yazdim"]
    )
    if recent_requested:
        latest_user_index = next(
            (index for index in range(len(memory) - 1, -1, -1) if memory[index]["role"] == "user"),
            None,
        )
        if latest_user_index is not None:
            latest_user = memory[latest_user_index]["content"][:420]
            latest_assistant = next(
                (
                    item["content"][:420]
                    for item in memory[latest_user_index + 1:]
                    if item["role"] == "assistant"
                ),
                "",
            )
            answer = f"Az önce bana şunu yazdın: “{latest_user}”"
            if latest_assistant:
                answer += f"\nBen de kısaca şöyle yanıtladım: “{latest_assistant}”"
            return answer

    lines = [
        f"{'Sen' if item['role'] == 'user' else 'Nexa AI'}: {item['content'][:220]}"
        for item in memory[-8:]
    ]
    for chat in (context.get("relevantChats") or [])[:3]:
        messages = chat.get("lastMessages") or []
        if messages:
            lines.append(f"{chat.get('title')} sohbetinden son notlar:")
            for message in messages[-3:]:
                body = message.get("body") or message.get("attachment") or ""
                if body:
                    lines.append(f"- {message.get('sender')}: {str(body)[:180]}")
    if not lines:
        return "Hafızamda bu konuda yeterli kayıt bulamadım. Bundan sonraki Nexa AI konuşmalarını server hafızasına yazacağım."
    return "Hafızamdan bulduğum yakın geçmiş:\n" + "\n".join(lines[-12:])


def local_should_research(prompt):
    quick_lowered = (prompt or "").casefold()
    quick_research_words = [
        "internetten", "internet", "webde", "web", "google", "arastir", "araştır",
        "son bilgi", "son durum", "en son", "kaynak", "bul", "bak", "guncel", "güncel",
        "haber", "fiyat", "bugun", "bugün", "su an", "şu an", "anlik", "anlık",
    ]
    quick_app_words = ["mesaj at", "mesaj gonder", "mesaj gönder", "sohbeti sil", "arama baslat", "arama başlat"]
    if len(quick_lowered) >= 8 and any(word in quick_lowered for word in quick_research_words) and not any(word in quick_lowered for word in quick_app_words):
        return True
    lowered = (prompt or "").casefold()
    if len(lowered) < 12:
        return False
    research_words = [
        "bugün", "bugun", "güncel", "guncel", "haber", "son dakika", "fiyat", "kaç tl",
        "kimdir", "nedir", "ne demek", "ne zaman", "nerede", "hangi", "kaç", "kac",
        "neden", "nasıl", "nasil", "en iyi", "karşılaştır", "karsilastir", "öner",
        "oner", "son durum", "2026", "yeni çıkan", "yeni cikan",
    ]
    app_words = [
        "sohbeti sil", "mesaj at", "mesaj gönder", "mesaj gonder", "tema değiştir",
        "tema degistir", "gizlilik ayar", "şifre", "sifre", "grup aç", "grup ac",
        "arama başlat", "arama baslat", "durum paylaş", "durum paylas",
    ]
    return any(word in lowered for word in research_words) and not any(word in lowered for word in app_words)


def wikipedia_research(query):
    title = re.sub(r"\s+", " ", query or "").strip()
    title = re.sub(r"\b(kimdir|nedir|ne demek|araştır|arastir|internette|webde|google|güncel|guncel)\b", " ", title, flags=re.IGNORECASE).strip()
    if len(title) < 3:
        return []
    try:
        search = requests.get(
            "https://tr.wikipedia.org/w/rest.php/v1/search/page",
            params={"q": title[:120], "limit": 1},
            headers={"User-Agent": "NexaLine/1.0 (https://nexalineapp.xyz)"},
            timeout=6,
        )
        search.raise_for_status()
        pages = search.json().get("pages") or []
        if not pages:
            return []
        key = pages[0].get("key") or pages[0].get("title")
        if not key:
            return []
        summary = requests.get(
            f"https://tr.wikipedia.org/api/rest_v1/page/summary/{key}",
            headers={"User-Agent": "NexaLine/1.0 (https://nexalineapp.xyz)"},
            timeout=6,
        )
        summary.raise_for_status()
        data = summary.json()
        extract = data.get("extract")
        if not extract:
            return []
        return [{
            "title": data.get("title") or title,
            "snippet": extract,
            "url": (data.get("content_urls") or {}).get("desktop", {}).get("page"),
        }]
    except Exception:
        return []


def strip_html_text(value):
    value = re.sub(r"<script[\s\S]*?</script>", " ", value or "", flags=re.IGNORECASE)
    value = re.sub(r"<style[\s\S]*?</style>", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]", "", value)
    return re.sub(r"\s+", " ", value).strip()


def normalize_search_url(value):
    url = html.unescape(value or "").strip()
    if url.startswith("//"):
        url = "https:" + url
    if "duckduckgo.com/l/?" in url or "duckduckgo.com/l/?" in urllib.parse.unquote(url):
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)
        target = (query.get("uddg") or [""])[0]
        if target:
            return urllib.parse.unquote(target)
    return url


def duckduckgo_html_research(query):
    try:
        response = requests.get(
            "https://duckduckgo.com/html/",
            params={"q": query[:180]},
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; NexaLineBot/1.0; +https://nexalineapp.xyz)",
                "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.6",
            },
            timeout=8,
        )
        response.raise_for_status()
        body = response.text
    except Exception:
        return []
    results = []
    blocks = re.findall(r'<div class="result[\s\S]*?</div>\s*</div>', body, flags=re.IGNORECASE)
    if not blocks:
        blocks = re.findall(r'<a rel="nofollow" class="result__a"[\s\S]*?(?=<a rel="nofollow" class="result__a"|$)', body, flags=re.IGNORECASE)
    for block in blocks[:8]:
        title_match = re.search(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>([\s\S]*?)</a>', block, flags=re.IGNORECASE)
        if not title_match:
            continue
        url = normalize_search_url(title_match.group(1))
        title = strip_html_text(title_match.group(2))
        snippet_match = re.search(r'<a[^>]+class="result__snippet"[^>]*>([\s\S]*?)</a>|<div[^>]+class="result__snippet"[^>]*>([\s\S]*?)</div>', block, flags=re.IGNORECASE)
        snippet = strip_html_text((snippet_match.group(1) or snippet_match.group(2)) if snippet_match else "")
        if title and url and not any(item.get("url") == url for item in results):
            results.append({"title": title[:180], "snippet": snippet[:700], "url": url})
    return results[:5]


def rss_search_research(url, query, source, extra_params=None):
    params = {"q": query[:180], **(extra_params or {})}
    try:
        response = requests.get(
            url,
            params=params,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; NexaLineBot/1.0; +https://nexalineapp.xyz)",
                "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.6",
            },
            timeout=8,
        )
        response.raise_for_status()
        root = ET.fromstring(response.content)
    except Exception as error:
        app.logger.info("%s search unavailable: %s", source, error)
        return []

    results = []
    for item in root.findall(".//item")[:8]:
        title = strip_html_text(item.findtext("title") or "")
        description = strip_html_text(item.findtext("description") or "")
        link = normalize_search_url(item.findtext("link") or "")
        if title and link:
            results.append(
                {
                    "title": title[:180],
                    "snippet": description[:700] or title[:300],
                    "url": link,
                    "source": source,
                }
            )
    return results


def bing_rss_research(query):
    return rss_search_research(
        "https://www.bing.com/search",
        query,
        "bing",
        {"format": "rss", "setlang": "tr"},
    )


def google_news_research(query):
    return rss_search_research(
        "https://news.google.com/rss/search",
        query,
        "google-news",
        {"hl": "tr", "gl": "TR", "ceid": "TR:tr"},
    )


def dedupe_research_results(items, limit=5):
    results = []
    seen = set()
    for item in items:
        url = normalize_search_url(item.get("url") or "")
        title = re.sub(r"\s+", " ", item.get("title") or "").strip()
        key = url.casefold() or title.casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        results.append({**item, "title": title, "url": url})
        if len(results) >= limit:
            break
    return results


def live_info_for_prompt(prompt, timezone_offset_minutes=0):
    lowered = (prompt or "").casefold()
    local_tz = timezone(timedelta(minutes=-int(timezone_offset_minutes or 0)))
    now = datetime.now(timezone.utc).astimezone(local_tz)
    info = {
        "now": now.isoformat(),
        "date": now.strftime("%d.%m.%Y"),
        "time": now.strftime("%H:%M"),
    }
    if not any(word in lowered for word in ["hava", "weather", "sıcaklık", "sicaklik", "yağmur", "yagmur"]):
        return info

    location = "İstanbul"
    location_match = re.search(
        r"\b([\wçğıöşüÇĞİÖŞÜ-]{2,40})\s+(?:hava\s+durumu|havası|havasi|weather)\b",
        prompt or "",
        re.IGNORECASE,
    )
    if not location_match:
        location_match = re.search(
            r"(?:hava|weather|sıcaklık|sicaklik)\s+(?:durumu|nasıl|nasil)?\s*(?:için|icin|de|da)?\s*([\wçğıöşüÇĞİÖŞÜ-]{2,40})",
            prompt or "",
            re.IGNORECASE,
        )
    if location_match:
        candidate = re.sub(
            r"\b(nasıl|nasil|kaç|kac|derece|bugün|bugun|nedir|ne)\b",
            " ",
            location_match.group(1),
            flags=re.IGNORECASE,
        ).strip(" ?.,")
        if candidate:
            location = candidate
    try:
        geo = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": location, "count": 1, "language": "tr", "format": "json"},
            timeout=5,
        )
        geo.raise_for_status()
        first = (geo.json().get("results") or [])[0]
        weather = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": first["latitude"],
                "longitude": first["longitude"],
                "current": "temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m,weather_code",
                "timezone": "auto",
            },
            timeout=5,
        )
        weather.raise_for_status()
        current = weather.json().get("current") or {}
        info["weather"] = {
            "location": f"{first.get('name')}, {first.get('country')}",
            "temperatureC": current.get("temperature_2m"),
            "humidityPercent": current.get("relative_humidity_2m"),
            "precipitationMm": current.get("precipitation"),
            "windKmh": current.get("wind_speed_10m"),
        }
    except Exception as error:
        info["weatherError"] = str(error)
    return info


def local_ai_reply(prompt, context, actions, research=None):
    active = context.get("activeChat") or {}
    messages = active.get("messages") or []
    assistant_name = (context.get("assistant") or {}).get("name") or "Nexa AI"
    lowered = (prompt or "").casefold()
    tokens = set(re.findall(r"[\wçğıöşüÇĞİÖŞÜ]+", lowered))
    memory_answer = local_memory_answer(prompt, context)
    if memory_answer:
        return memory_answer
    live_info = context.get("liveInfo") or {}
    input_attachment = context.get("inputAttachment") or {}
    if input_attachment:
        kind = input_attachment.get("kind") or input_attachment.get("type") or "dosya"
        name = input_attachment.get("name") or "ek"
        if input_attachment.get("location"):
            location = input_attachment["location"]
            return f"Konumu aldım: {location.get('lat')}, {location.get('lng')}. İstersen bu konuma göre yol tarifi, yakın yer araması veya konum paylaşımı için komut çalıştırabilirim."
        if str(kind).startswith("image") or kind == "image":
            return f"{name} görselini aldım. Gemini görsel okuma anahtarı açıksa içeriğini doğrudan analiz ederim; ücretsiz yerel modda dosya adı, tür ve komutuna göre arama/özet/düzenleme yardımı yapabilirim."
        return f"{name} dosyasını aldım. İçeriği metin olarak okunabiliyorsa özetleme, düzeltme veya arama komutuyla işleyebilirim."
    if any(word in lowered for word in ["saat kaç", "saat kac", "saat ne", "bugün tarih", "bugun tarih"]):
        return f"Şu an saat {live_info.get('time')}, tarih {live_info.get('date')}."
    if any(word in lowered for word in ["hava", "weather", "sıcaklık", "sicaklik"]):
        weather = live_info.get("weather")
        if weather:
            return f"{weather.get('location')} için hava: {weather.get('temperatureC')}°C, nem %{weather.get('humidityPercent')}, rüzgar {weather.get('windKmh')} km/sa."
        return "Hava durumu bilgisini şu an alamadım; bağlantı veya konum servisi yanıt vermemiş olabilir."
    research_answer = local_research_answer(prompt, research)
    if research_answer:
        return research_answer
    if "sana verdiği isim" in lowered or "sana verdigi isim" in lowered:
        return f"Buradayım, ben {assistant_name}. Ne yapalım?"
    if (tokens.intersection({"merhaba", "selam", "hello", "slm"}) and len(tokens) <= 3) or lowered.strip() in {"sa", "s.a", "s.a."}:
        return f"Merhaba, ben {assistant_name}. Buradayım; sohbeti özetleyebilir, cevap taslağı yazabilir, uygulama ayarlarını yönetebilir veya sadece normal şekilde sohbet edebilirim."
    if "özet" in lowered or "ozet" in lowered:
        return local_chat_summary(messages)
    if "cevap hazırla" in lowered or "cevap hazirla" in lowered or "ne yazayım" in lowered or "ne yazayim" in lowered:
        return local_reply_draft(context)
    if "ne dedi" in lowered or "anlat" in lowered:
        last = next((item for item in reversed(messages) if item.get("sender") != context.get("user", {}).get("username")), None)
        if last:
            return f"Son mesajın kısa anlamı: {last.get('body') or last.get('attachment') or 'medya gönderilmiş'}"
    app_help = local_app_help(prompt)
    if app_help:
        return app_help
    labels = ai_moderation_labels(prompt)
    if labels:
        return "Bu metinde AI filtresine takılabilecek içerik var: " + ", ".join(labels) + ". İstersen gizleme/uyarı modunu açabilirim."
    if actions:
        return "Bunu uygulayabilirim. Güvenlik için aşağıdaki eylemi onaylaman yeterli."
    keywords = ai_prompt_keywords(prompt)
    if keywords:
        return (
            f"Anladım. {', '.join(keywords[:4])} tarafında yardımcı olabilirim. "
            "İstersen bunu hemen taslak, özet, plan ya da uygulama içi komut olarak çalıştırabilirim."
        )
    return f"{assistant_name} ücretsiz yerel modda hazır. Uygulama içi komutlar, sohbet özeti, cevap taslağı, basit web özeti ve güvenlik filtresi çalışıyor."


def web_research_if_requested(prompt, force=False):
    if not force and not local_should_research(prompt):
        return []

    query = re.sub(
        r"\b(arastir|araştır|internetten|internette|internet|webde|web|google|haber|guncel|güncel|son durum|son bilgi|kaynak|bul|bak)\b",
        " ",
        prompt or "",
        flags=re.IGNORECASE,
    )
    query = re.sub(r"\s+", " ", query).strip(" .,:;-")
    if len(query) < 3:
        return []

    results = []
    try:
        response = requests.get(
            "https://api.duckduckgo.com/",
            params={"q": query[:160], "format": "json", "no_html": "1", "skip_disambig": "1"},
            headers={"User-Agent": "NexaLine/1.0 (https://nexalineapp.xyz)"},
            timeout=6,
        )
        response.raise_for_status()
        data = response.json()
    except Exception:
        data = {}

    if data.get("AbstractText"):
        results.append(
            {
                "title": data.get("Heading") or query,
                "snippet": data.get("AbstractText"),
                "url": data.get("AbstractURL"),
                "source": "duckduckgo-instant",
            }
        )
    for item in (data.get("RelatedTopics") or [])[:6]:
        if isinstance(item, dict) and item.get("Text"):
            results.append(
                {
                    "title": item.get("Text", "").split(" - ", 1)[0] or "Kaynak",
                    "snippet": item.get("Text"),
                    "url": item.get("FirstURL"),
                    "source": "duckduckgo-instant",
                }
            )

    current_query = any(word in fold_tr_ascii(prompt) for word in ["haber", "guncel", "bugun", "son durum", "son dakika"])
    if current_query:
        results.extend(google_news_research(query))
    results.extend(bing_rss_research(query))
    if len(results) < 3:
        results.extend(duckduckgo_html_research(query))
    if len(results) < 2:
        results.extend(wikipedia_research(query))
    return dedupe_research_results(results, limit=5)


def attachment_context_for_ai(attachment):
    if not isinstance(attachment, dict):
        return None
    data_url = str(attachment.get("dataUrl") or "")
    text_content = text_content_from_attachment(attachment)
    image_metadata = image_metadata_from_attachment(attachment)
    return {
        "name": str(attachment.get("name") or "")[:160],
        "type": str(attachment.get("type") or "")[:100],
        "kind": "image" if str(attachment.get("type") or "").startswith("image/") else attachment.get("type") or "file",
        "location": {
            "lat": attachment.get("lat"),
            "lng": attachment.get("lng"),
            "url": attachment.get("url"),
        } if attachment.get("type") == "location" else None,
        "hasInlineData": bool(data_url),
        "dataUrlPreview": data_url[:120] if data_url else "",
        "textContent": text_content,
        "imageMetadata": image_metadata,
    }


def gemini_inline_part_from_attachment(attachment):
    if not isinstance(attachment, dict):
        return None
    mime_type = str(attachment.get("type") or "")
    data_url = str(attachment.get("dataUrl") or "")
    if not mime_type.startswith("image/") or "," not in data_url:
        return None
    try:
        header, payload = data_url.split(",", 1)
    except ValueError:
        return None
    if "base64" not in header or len(payload) > MAX_ATTACHMENT_DATA_URL_CHARS:
        return None
    return {"inline_data": {"mime_type": mime_type, "data": payload}}


def attachment_data_bytes(attachment):
    if not isinstance(attachment, dict):
        return b"", ""
    data_url = str(attachment.get("dataUrl") or "")
    if "," not in data_url:
        return b"", ""
    header, payload = data_url.split(",", 1)
    if "base64" not in header or len(payload) > MAX_ATTACHMENT_DATA_URL_CHARS:
        return b"", ""
    try:
        return base64.b64decode(payload, validate=True), str(attachment.get("type") or "")
    except (ValueError, TypeError):
        return b"", ""


def text_content_from_attachment(attachment):
    raw, mime_type = attachment_data_bytes(attachment)
    allowed = (
        mime_type.startswith("text/")
        or mime_type in {"application/json", "application/xml", "text/csv", "application/csv"}
    )
    if not raw or not allowed:
        return ""
    for encoding in ("utf-8", "utf-8-sig", "cp1254", "latin-1"):
        try:
            return raw.decode(encoding)[:12000]
        except UnicodeDecodeError:
            continue
    return ""


def image_metadata_from_attachment(attachment):
    raw, mime_type = attachment_data_bytes(attachment)
    if not raw or not mime_type.startswith("image/"):
        return {}
    width = height = None
    if raw.startswith(b"\x89PNG\r\n\x1a\n") and len(raw) >= 24:
        width, height = struct.unpack(">II", raw[16:24])
    elif raw.startswith(b"\xff\xd8"):
        index = 2
        while index + 9 < len(raw):
            if raw[index] != 0xFF:
                index += 1
                continue
            marker = raw[index + 1]
            index += 2
            if marker in {0xD8, 0xD9}:
                continue
            if index + 2 > len(raw):
                break
            segment_length = struct.unpack(">H", raw[index:index + 2])[0]
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF} and index + 7 < len(raw):
                height, width = struct.unpack(">HH", raw[index + 3:index + 7])
                break
            index += max(2, segment_length)
    return {
        "mimeType": mime_type,
        "sizeBytes": len(raw),
        "width": width,
        "height": height,
    }


def openai_user_content(prompt, context_text, research, attachment=None):
    text_value = (
        f"Uygulama bağlamı:\n{context_text}\n\n"
        f"Web araştırma notları:\n{json.dumps(research, ensure_ascii=False)}\n\n"
        f"Kullanıcı:\n{prompt}"
    )
    data_url = str((attachment or {}).get("dataUrl") or "")
    mime_type = str((attachment or {}).get("type") or "")
    if mime_type.startswith("image/") and data_url.startswith("data:image/"):
        return [
            {"type": "text", "text": text_value},
            {"type": "image_url", "image_url": {"url": data_url, "detail": "auto"}},
        ]
    attachment_text = text_content_from_attachment(attachment)
    if attachment_text:
        text_value += f"\n\nEk dosyanın metin içeriği:\n{attachment_text}"
    return text_value


def call_gemini_ai(prompt, context_text, research, attachment=None):
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("VITE_GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY missing")
    model = os.environ.get("GEMINI_MODEL", "gemini-1.5-flash")
    parts = [{"text": f"Uygulama bağlamı:\n{context_text}\n\nWeb araştırma notları:\n{json.dumps(research, ensure_ascii=False)}\n\nKullanıcı:\n{prompt}"}]
    inline_part = gemini_inline_part_from_attachment(attachment)
    if inline_part:
        parts.append(inline_part)
    attachment_text = text_content_from_attachment(attachment)
    if attachment_text:
        parts.append({"text": f"Ek dosyanın metin içeriği:\n{attachment_text}"})
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        params={"key": key},
        json={
            "systemInstruction": {"parts": [{"text": AI_SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": 0.45, "maxOutputTokens": 900},
        },
        timeout=AI_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    parts = response.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
    return "\n".join(part.get("text", "") for part in parts).strip()


def call_openai_ai(prompt, context_text, research, attachment=None):
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY missing")
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    response = requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "temperature": 0.45,
            "max_tokens": 900,
            "messages": [
                {"role": "system", "content": AI_SYSTEM_PROMPT},
                {"role": "user", "content": openai_user_content(prompt, context_text, research, attachment)},
            ],
        },
        timeout=AI_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"].strip()


def call_groq_ai(prompt, context_text, research):
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise RuntimeError("GROQ_API_KEY missing")
    model = os.environ.get("GROQ_CHAT_MODEL", "llama-3.3-70b-versatile")
    response = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "temperature": 0.38,
            "max_tokens": 900,
            "messages": [
                {"role": "system", "content": AI_SYSTEM_PROMPT},
                {"role": "user", "content": f"Uygulama bağlamı:\n{context_text}\n\nWeb araştırma notları:\n{json.dumps(research, ensure_ascii=False)}\n\nKullanıcı:\n{prompt}"},
            ],
        },
        timeout=AI_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"].strip()


def call_openrouter_ai(prompt, context_text, research, attachment=None):
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY missing")
    has_image = bool(gemini_inline_part_from_attachment(attachment))
    model = (
        os.environ.get("OPENROUTER_VISION_MODEL", "openrouter/free")
        if has_image
        else os.environ.get("OPENROUTER_MODEL", "openrouter/free")
    )
    response = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": os.environ.get("APP_PUBLIC_URL", "https://nexalineapp.xyz"),
            "X-Title": "NexaLine",
        },
        json={
            "model": model,
            "temperature": 0.45,
            "max_tokens": 900,
            "messages": [
                {"role": "system", "content": AI_SYSTEM_PROMPT},
                {"role": "user", "content": openai_user_content(prompt, context_text, research, attachment)},
            ],
        },
        timeout=AI_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"].strip()


def call_deepinfra_ai(prompt, context_text, research):
    key = os.environ.get("DEEPINFRA_API_KEY")
    if not key:
        raise RuntimeError("DEEPINFRA_API_KEY missing")
    model = os.environ.get("DEEPINFRA_MODEL", "meta-llama/Llama-3.3-70B-Instruct-Turbo")
    response = requests.post(
        "https://api.deepinfra.com/v1/openai/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "temperature": 0.42,
            "max_tokens": 900,
            "messages": [
                {"role": "system", "content": AI_SYSTEM_PROMPT},
                {"role": "user", "content": f"Uygulama baglami:\n{context_text}\n\nWeb arastirma notlari:\n{json.dumps(research, ensure_ascii=False)}\n\nKullanici:\n{prompt}"},
            ],
        },
        timeout=AI_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"].strip()


def call_ollama_ai(prompt, context_text, research):
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    model = os.environ.get("OLLAMA_MODEL", os.environ.get("AI_MODEL", "llama3.2"))
    response = requests.post(
        f"{base_url}/api/chat",
        json={
            "model": model,
            "stream": False,
            "messages": [
                {"role": "system", "content": AI_SYSTEM_PROMPT},
                {"role": "user", "content": f"Uygulama bağlamı:\n{context_text}\n\nWeb araştırma notları:\n{json.dumps(research, ensure_ascii=False)}\n\nKullanıcı:\n{prompt}"},
            ],
        },
        timeout=AI_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json().get("message", {}).get("content", "").strip()


def call_huggingface_vision(prompt, attachment):
    key = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_API_KEY")
    raw, _mime_type = attachment_data_bytes(attachment)
    if not key or not raw:
        raise RuntimeError("Hugging Face vision key or image missing")
    model = os.environ.get("HF_VISION_MODEL", "Salesforce/blip-image-captioning-large")
    response = requests.post(
        f"https://router.huggingface.co/hf-inference/models/{model}",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/octet-stream"},
        data=raw,
        timeout=max(AI_TIMEOUT_SECONDS, 25),
    )
    response.raise_for_status()
    payload = response.json()
    generated = ""
    if isinstance(payload, list) and payload:
        generated = str(payload[0].get("generated_text") or payload[0].get("caption") or "").strip()
    elif isinstance(payload, dict):
        generated = str(payload.get("generated_text") or payload.get("caption") or "").strip()
    if not generated:
        raise RuntimeError("Hugging Face vision returned no caption")
    return (
        f"Görsel analizi: {generated}\n\n"
        f"Kullanıcının isteği: {prompt}\n"
        "Bu açıklama otomatik görsel tanıma sonucudur; küçük yazılar veya ince ayrıntılar için daha güçlü bir görsel model gerekebilir."
    )


def local_attachment_reply(prompt, attachment):
    text_content = text_content_from_attachment(attachment)
    if text_content:
        excerpt = re.sub(r"\s+", " ", text_content).strip()[:1600]
        return (
            f"{attachment.get('name') or 'Dosya'} içeriğini okuyabildim. İlk bölüm:\n"
            f"{excerpt}\n\nİstersen bunu özetleyebilir, düzeltebilir veya belirli bilgileri ayıklayabilirim."
        )
    metadata = image_metadata_from_attachment(attachment)
    if metadata:
        dimensions = (
            f"{metadata['width']}×{metadata['height']} piksel"
            if metadata.get("width") and metadata.get("height")
            else "ölçüsü çözümlenemedi"
        )
        return (
            f"{attachment.get('name') or 'Görsel'} dosyasını aldım: {dimensions}, "
            f"{round(metadata.get('sizeBytes', 0) / 1024, 1)} KB. "
            "Bu sunucuda semantik görsel sağlayıcısı şu an yanıt vermediği için logonun veya nesnelerin içeriğini görmüş gibi davranmayacağım. "
            "Gemini, OpenAI, OpenRouter ya da Hugging Face görsel sağlayıcısı hazır olduğunda aynı dosyayı doğrudan analiz ederim."
        )
    return ""


def append_research_sources(reply, research):
    usable = [item for item in (research or []) if item.get("url")]
    if not usable:
        return reply
    source_lines = []
    for item in usable[:4]:
        title = re.sub(r"\s+", " ", item.get("title") or item.get("source") or "Kaynak").strip()
        source_lines.append(f"- [{title}]({item['url']})")
    if not source_lines or "Kaynaklar:" in (reply or ""):
        return reply
    return f"{(reply or '').strip()}\n\nKaynaklar:\n" + "\n".join(source_lines)


def generate_ai_reply(prompt, context, actions, attachment=None):
    provider = ai_provider_status()
    context_text = json.dumps(context, ensure_ascii=False, indent=2)
    research = web_research_if_requested(prompt)
    prompt_ascii = fold_tr_ascii(prompt)
    has_live_weather = bool(
        (context.get("liveInfo") or {}).get("weather")
        and any(word in prompt_ascii for word in ["hava", "sicaklik", "weather"])
    )
    if has_live_weather:
        research = []
    if provider["provider"] == "local" and not research and not has_live_weather and local_should_research(prompt):
        research = web_research_if_requested(prompt, force=True)
    needs_vision = bool(gemini_inline_part_from_attachment(attachment))
    reply = ""
    for provider_name in ai_provider_attempts(needs_vision=needs_vision):
        try:
            if provider_name == "gemini" and (os.environ.get("GEMINI_API_KEY") or os.environ.get("VITE_GEMINI_API_KEY")):
                reply = call_gemini_ai(prompt, context_text, research, attachment)
                provider = {"provider": "gemini", "model": os.environ.get("GEMINI_MODEL", "gemini-1.5-flash"), "ready": True}
            elif provider_name == "groq" and os.environ.get("GROQ_API_KEY") and not needs_vision:
                reply = call_groq_ai(prompt, context_text, research)
                provider = {"provider": "groq", "model": os.environ.get("GROQ_CHAT_MODEL", "llama-3.3-70b-versatile"), "ready": True}
            elif provider_name == "deepinfra" and os.environ.get("DEEPINFRA_API_KEY") and not needs_vision:
                reply = call_deepinfra_ai(prompt, context_text, research)
                provider = {"provider": "deepinfra", "model": os.environ.get("DEEPINFRA_MODEL", "meta-llama/Llama-3.3-70B-Instruct-Turbo"), "ready": True}
            elif provider_name == "openrouter" and os.environ.get("OPENROUTER_API_KEY") and not needs_vision:
                reply = call_openrouter_ai(prompt, context_text, research, attachment)
                provider = {"provider": "openrouter", "model": os.environ.get("OPENROUTER_MODEL", "openrouter/free"), "ready": True}
            elif provider_name == "openai" and os.environ.get("OPENAI_API_KEY"):
                reply = call_openai_ai(prompt, context_text, research, attachment)
                provider = {"provider": "openai", "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"), "ready": True}
            elif provider_name == "openrouter" and os.environ.get("OPENROUTER_API_KEY"):
                reply = call_openrouter_ai(prompt, context_text, research, attachment)
                provider = {
                    "provider": "openrouter",
                    "model": os.environ.get("OPENROUTER_VISION_MODEL", "openrouter/free"),
                    "ready": True,
                }
            elif provider_name == "huggingface_vision" and (os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_API_KEY")):
                reply = call_huggingface_vision(prompt, attachment)
                provider = {
                    "provider": "huggingface",
                    "model": os.environ.get("HF_VISION_MODEL", "Salesforce/blip-image-captioning-large"),
                    "ready": True,
                }
            elif provider_name == "ollama" and os.environ.get("OLLAMA_BASE_URL"):
                reply = call_ollama_ai(prompt, context_text, research)
                provider = {"provider": "ollama", "model": os.environ.get("OLLAMA_MODEL", os.environ.get("AI_MODEL", "llama3.2")), "ready": True}
            if reply:
                break
        except Exception as error:
            app.logger.warning("AI provider %s failed: %s", provider_name, error)
    if not reply:
        provider = {"provider": "local", "model": "nexaline-free-ai", "ready": True, "free": True}
        reply = local_memory_answer(prompt, context)
        if not reply and attachment:
            reply = local_attachment_reply(prompt, attachment)
        if not reply:
            reply = local_ai_reply(prompt, context, actions, research)
    return append_research_sources(reply, research), provider, research


AI_ANALYSIS_STOP_WORDS = {
    "acaba", "ama", "artık", "bana", "bazı", "ben", "beni", "benim", "bile",
    "bir", "biri", "biz", "bize", "bunu", "bu", "çok", "daha", "de", "da",
    "değil", "diye", "en", "gibi", "hem", "her", "için", "ile", "ise", "ki",
    "kim", "mı", "mi", "mu", "mü", "nasıl", "ne", "neden", "olan", "olarak",
    "oldu", "olsun", "onu", "orada", "öyle", "şey", "şimdi", "şu", "ve",
    "veya", "ya", "yani", "yok", "zaten",
}

AI_ANALYSIS_LEXICONS = {
    "positive": {
        "güzel", "harika", "iyi", "mükemmel", "sevindim", "teşekkür", "mutlu",
        "süper", "başarılı", "tamam", "olur", "sevdim", "beğendim", "neşeli",
    },
    "negative": {
        "üzgün", "kötü", "mutsuz", "olmadı", "olmuyor", "sorun", "hata",
        "yoruldum", "kırıldım", "üzüldüm", "yalnız", "endişeli", "korkuyorum",
    },
    "anger": {
        "sinir", "sinirli", "kızgın", "öfke", "öfkeli", "yeter", "saçma",
        "berbat", "nefret", "bıktım", "lanet", "aptal",
    },
    "fun": {
        "haha", "hahaha", "komik", "eğlence", "eğlenceli", "şaka", "güldüm",
        "kahkaha", "oyun", "lol", "mizah",
    },
    "flirt": {
        "aşk", "aşkım", "canım", "tatlım", "sevgilim", "özledim", "öpücük",
        "yakışıklı", "güzelim", "bebeğim", "kalbim",
    },
}


def ai_analysis_tokens(text_value):
    normalized = unicodedata.normalize("NFKC", str(text_value or "")).lower()
    return re.findall(r"[a-zçğıöşü0-9]{2,}", normalized)


def ai_analysis_attachment_kind(attachment):
    if not isinstance(attachment, dict):
        return None
    type_value = str(attachment.get("type") or attachment.get("kind") or "").lower()
    if type_value == "bundle":
        return "bundle"
    if type_value.startswith("image/") or type_value in {"image", "photo"}:
        return "image"
    if type_value.startswith("video/") or type_value == "video":
        return "video"
    if type_value.startswith("audio/") or type_value in {"audio", "voice"}:
        return "audio"
    if type_value == "location":
        return "location"
    if type_value == "poll":
        return "poll"
    return "file" if type_value else None


def ai_profile_analysis_for(username):
    user = db.session.get(User, username)
    if not user:
        return None

    memberships = ChatMember.query.filter_by(username=username).all()
    chat_ids = [row.chat_id for row in memberships]
    messages = (
        Message.query.filter(Message.chat_id.in_(chat_ids), Message.deleted_at.is_(None))
        .order_by(Message.created_at.desc())
        .limit(1600)
        .all()
        if chat_ids else []
    )
    sent_messages = [row for row in messages if row.sender == username]
    received_messages = [row for row in messages if row.sender != username]
    updates = UpdatePost.query.filter_by(username=username).order_by(UpdatePost.created_at.desc()).limit(240).all()
    stories = Story.query.filter_by(username=username).order_by(Story.created_at.desc()).limit(240).all()
    memories = AiMemory.query.filter_by(username=username).order_by(AiMemory.created_at.desc()).limit(500).all()

    text_parts = [row.body for row in sent_messages if row.body]
    text_parts.extend(row.body for row in updates if row.body)
    text_parts.extend(row.body for row in stories if row.body)
    text_parts.extend(row.content for row in memories if row.role == "user" and row.content)
    combined_text = "\n".join(text_parts)
    tokens = ai_analysis_tokens(combined_text)
    token_counts = Counter(tokens)
    total_tokens = max(1, len(tokens))
    lexicon_counts = {
        name: sum(token_counts[word] for word in words)
        for name, words in AI_ANALYSIS_LEXICONS.items()
    }
    emoji_count = len(re.findall(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", combined_text))
    question_count = combined_text.count("?")
    exclamation_count = combined_text.count("!")

    attachment_counts = Counter()
    for row in sent_messages:
        kind = ai_analysis_attachment_kind(row.attachment)
        if kind:
            attachment_counts[kind] += 1
    for row in updates:
        for media_item in row.media or []:
            kind = ai_analysis_attachment_kind(media_item)
            if kind:
                attachment_counts[kind] += 1
    for row in stories:
        kind = ai_analysis_attachment_kind(row.attachment)
        if kind:
            attachment_counts[kind] += 1

    active_chat_ids = {row.chat_id for row in sent_messages}
    contact_names = {
        row.username
        for row in ChatMember.query.filter(ChatMember.chat_id.in_(list(active_chat_ids))).all()
        if row.username != username
    } if active_chat_ids else set()
    activity_days = {
        row.created_at.date().isoformat()
        for row in sent_messages + updates + stories
        if row.created_at
    }
    sent_count = len(sent_messages)
    interaction_total = sent_count + len(received_messages)
    positive = lexicon_counts["positive"]
    negative = lexicon_counts["negative"]
    anger = lexicon_counts["anger"]
    fun = lexicon_counts["fun"]
    flirt = lexicon_counts["flirt"]

    def percent(value):
        return int(max(0, min(100, round(value))))

    mood_denominator = max(3, positive + negative + anger + fun)
    happy_score = percent(35 + (positive + fun * 0.7 - negative * 0.45 - anger * 0.7) * 65 / mood_denominator)
    unhappy_score = percent(18 + (negative + anger * 0.35) * 72 / mood_denominator)
    angry_score = percent((anger * 120 + exclamation_count * 2) / max(8, total_tokens / 9))
    fun_score = percent(18 + (fun * 105 + emoji_count * 2.5) / max(8, total_tokens / 10))
    flirt_score = percent((flirt * 125) / max(6, total_tokens / 12))
    social_score = percent(
        12
        + min(35, len(contact_names) * 5)
        + min(24, len(activity_days) * 1.5)
        + min(18, interaction_total / 18)
        + min(11, (len(updates) + len(stories)) * 1.7)
    )
    introverted_score = percent(100 - social_score)

    ignored_words = AI_ANALYSIS_STOP_WORDS | set().union(*AI_ANALYSIS_LEXICONS.values())
    interests = [
        word for word, count in token_counts.most_common(80)
        if word not in ignored_words and not word.isdigit() and count >= 2
    ][:8]
    profession_markers = {
        "Yazılım / Teknoloji": {"kod", "yazılım", "api", "frontend", "backend", "site", "uygulama", "github"},
        "Eğitim": {"öğretmen", "öğrenci", "ders", "okul", "sınav", "eğitim"},
        "Tasarım": {"tasarım", "grafik", "logo", "arayüz", "ui", "ux"},
        "Sağlık": {"doktor", "hemşire", "hastane", "sağlık", "klinik"},
        "Ticaret": {"satış", "müşteri", "mağaza", "ürün", "ticaret"},
    }
    profession_scores = {
        label: sum(token_counts[word] for word in markers)
        for label, markers in profession_markers.items()
    }
    profession, profession_evidence = max(profession_scores.items(), key=lambda item: item[1])
    if profession_evidence < 3:
        profession = "Yeterli açık veri yok"

    ai_prompt_count = len([row for row in memories if row.role == "user"])
    sample_size = sent_count + len(updates) + len(stories) + ai_prompt_count
    confidence = percent(min(92, 20 + sample_size * 1.5 + len(activity_days) * 2))
    average_length = round(sum(len(row.body or "") for row in sent_messages) / max(1, sent_count), 1)
    metrics = [
        {"id": "fun", "label": "Eğlenceli ifade", "value": fun_score, "tone": "violet"},
        {"id": "angry", "label": "Öfkeli ifade", "value": angry_score, "tone": "red"},
        {"id": "happy", "label": "Olumlu ifade", "value": happy_score, "tone": "green"},
        {"id": "unhappy", "label": "Olumsuz ifade", "value": unhappy_score, "tone": "blue"},
        {"id": "flirt", "label": "Flörtöz ifade", "value": flirt_score, "tone": "pink"},
        {"id": "social", "label": "Sosyal etkileşim", "value": social_score, "tone": "cyan"},
        {"id": "introverted", "label": "İçe dönük kullanım", "value": introverted_score, "tone": "amber"},
    ]
    dominant = sorted(metrics[:6], key=lambda item: item["value"], reverse=True)[:2]
    dominant_text = " ve ".join(item["label"].lower() for item in dominant)
    summary = (
        f"NexaLine içindeki {sample_size} kişisel etkinlik kaydı incelendi. "
        f"Ölçümlerde en belirgin iki alan {dominant_text}. "
        "Bu sonuçlar yalnızca uygulamadaki kelime, emoji, medya ve etkileşim "
        "sıklıklarından hesaplanır; kişilik testi veya psikolojik değerlendirme değildir."
    )
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "metrics": metrics,
        "interests": interests,
        "profession": profession,
        "confidence": confidence,
        "evidence": {
            "sentMessages": sent_count,
            "receivedMessages": len(received_messages),
            "chatContacts": len(contact_names),
            "activeDays": len(activity_days),
            "updates": len(updates),
            "stories": len(stories),
            "aiPrompts": ai_prompt_count,
            "questions": question_count,
            "emojis": emoji_count,
            "averageMessageLength": average_length,
            "media": dict(attachment_counts),
        },
    }


@app.route("/ai/profile-analysis", methods=["POST"])
@ai_error_boundary
def ai_profile_analysis():
    data = request.get_json() or {}
    username = (data.get("username") or "").strip().lower()
    if not username:
        return jsonify({"ok": False, "message": "Analiz için giriş yapmalısın."}), 401
    analysis = ai_profile_analysis_for(username)
    if not analysis:
        return jsonify({"ok": False, "message": "Profil analiz edilemedi; kullanıcı bulunamadı."}), 404
    return jsonify({"ok": True, "analysis": analysis})


@app.route("/")
def index():
    response = send_from_directory("static", "client.html")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.route("/client.html")
def client():
    response = send_from_directory("static", "client.html")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.route("/chat/<path:chat_id>")
@app.route("/call/<path:chat_id>")
def client_deeplink(chat_id):
    response = send_from_directory("static", "client.html")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.route("/robots.txt")
def robots_txt():
    body = "\n".join(
        [
            "User-agent: *",
            "Allow: /",
            f"Sitemap: {PUBLIC_SITE_URL}/sitemap.xml",
            "",
        ]
    )
    response = app.response_class(body, mimetype="text/plain")
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


@app.route("/sitemap.xml")
def sitemap_xml():
    today = datetime.now(timezone.utc).date().isoformat()
    url_entries = [
        ("/", "1.0", "daily"),
        ("/client.html", "0.8", "weekly"),
        ("/manifest.webmanifest", "0.4", "monthly"),
    ]
    urls = []
    for path, priority, changefreq in url_entries:
        loc = html.escape(f"{PUBLIC_SITE_URL}{path}", quote=True)
        urls.append(
            f"  <url><loc>{loc}</loc><lastmod>{today}</lastmod>"
            f"<changefreq>{changefreq}</changefreq><priority>{priority}</priority></url>"
        )
    body = "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
    body += "<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">\n"
    body += "\n".join(urls)
    body += "\n</urlset>\n"
    response = app.response_class(body, mimetype="application/xml")
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


@app.route("/reset-client")
def reset_client():
    html = """<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#050911">
  <title>NexaLine yenileniyor</title>
  <style>
    body{margin:0;min-height:100vh;display:grid;place-items:center;background:#050911;color:#fff;font-family:Arial,sans-serif}
    .box{width:min(340px,calc(100vw - 36px));padding:28px;border:1px solid rgba(255,255,255,.12);border-radius:24px;background:rgba(17,24,39,.88);box-shadow:0 24px 80px rgba(0,0,0,.45);text-align:center}
    .mark{width:72px;height:72px;margin:0 auto 14px;border-radius:22px;background:linear-gradient(135deg,#704CFF,#FF00B8);box-shadow:0 0 36px rgba(151,71,255,.55)}
    h1{font-size:22px;margin:0 0 8px} p{color:#B0B7C3;margin:0}
  </style>
</head>
<body>
  <main class="box">
    <div class="mark"></div>
    <h1>NexaLine yenileniyor</h1>
    <p>Eski giriş önbelleği temizleniyor. Birazdan yeniden açılacak.</p>
  </main>
  <script>
    (async () => {
      try {
        if ("serviceWorker" in navigator) {
          const registrations = await navigator.serviceWorker.getRegistrations();
          await Promise.all(registrations.map(registration => registration.unregister()));
        }
        if ("caches" in window) {
          const keys = await caches.keys();
          await Promise.all(keys.map(key => caches.delete(key)));
        }
        localStorage.removeItem("nexalineUser");
        localStorage.removeItem("nexalineClientBuild");
        sessionStorage.clear();
      } catch (error) {
        console.warn("NexaLine reset tamamlanamadı", error);
      }
      location.replace("/?fresh=" + Date.now());
    })();
  </script>
</body>
</html>"""
    response = app.response_class(html, mimetype="text/html")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.route("/manifest.webmanifest")
def manifest():
    return send_from_directory("static", "manifest.webmanifest", mimetype="application/manifest+json")


@app.route("/sw.js")
def service_worker():
    response = send_from_directory("static", "sw.js", mimetype="application/javascript")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.route("/downloads/<path:filename>")
def downloads(filename):
    return send_from_directory(os.path.join(app.root_path, "static", "downloads"), filename, as_attachment=True)


@app.route("/admin")
def admin_page():
    response = send_from_directory("static", "admin.html")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.route("/design")
def public_design():
    return jsonify({"ok": True, "design": design_settings()})


@app.route("/admin/design", methods=["GET", "POST"])
def admin_design():
    admin_error = require_admin()
    if admin_error:
        return admin_error

    if request.method == "GET":
        return jsonify({"ok": True, "design": design_settings()})

    data = request.get_json() or {}
    settings = sanitize_design_settings(data.get("design") or data)
    row = db.session.get(AppSetting, "design")
    if not row:
        row = AppSetting(key="design", value=settings)
        db.session.add(row)
    else:
        row.value = settings
        row.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    socketio.emit("design:update", {"design": settings}, namespace="/")
    return jsonify({"ok": True, "design": settings, "message": "Tasarım ayarları kaydedildi."})


@app.route("/health")
def health():
    try:
        ensure_lobby()
        return jsonify({"ok": True, "users": User.query.count(), "chats": Chat.query.count()})
    except Exception as error:
        app.logger.exception("Health check failed")
        return jsonify({"ok": False, "message": str(error)}), 500


@app.route("/rtc-config")
def rtc_config():
    return jsonify({"ok": True, "iceServers": rtc_servers(), "secureContext": request.is_secure})


@app.route("/push/public-key")
def push_public_key():
    if not VAPID_PUBLIC_KEY:
        return jsonify({"ok": False, "message": "Web Push VAPID anahtarı henüz yapılandırılmadı.", "publicKey": ""})
    return jsonify({"ok": True, "publicKey": VAPID_PUBLIC_KEY})


@app.route("/push/subscribe", methods=["POST"])
def push_subscribe():
    data = request.get_json() or {}
    username = (data.get("username") or "").strip().lower()
    subscription = data.get("subscription") or {}
    endpoint = (subscription.get("endpoint") or "").strip()

    if not username or not db.session.get(User, username):
        return jsonify({"ok": False, "message": "Bildirim aboneliği için geçerli kullanıcı bulunamadı."}), 400
    if not endpoint:
        return jsonify({"ok": False, "message": "Bildirim aboneliği endpoint bilgisi eksik."}), 400

    row = PushSubscription.query.filter_by(endpoint=endpoint).first()
    if not row:
        row = PushSubscription(id=uuid4().hex, username=username, endpoint=endpoint, subscription=subscription)
        db.session.add(row)
    else:
        row.username = username
        row.subscription = subscription
        row.updated_at = datetime.now(timezone.utc)
    row.user_agent = request.headers.get("User-Agent", "")[:600]
    db.session.commit()
    return jsonify({"ok": True, "message": "Bildirim aboneliği kaydedildi."})


@app.route("/ai/status")
@ai_error_boundary
def ai_status():
    status = ai_provider_status()
    return jsonify({"ok": True, "ai": status, "moderation": True, "actions": True, "memory": True, "webResearch": True})


@app.route("/ai/chat", methods=["POST"])
@ai_error_boundary
def ai_chat():
    data = request.get_json() or {}
    username = (data.get("username") or "").strip().lower()
    prompt = (data.get("prompt") or "").strip()
    chat_id = data.get("chatId")
    assistant_name = re.sub(r"\s+", " ", (data.get("assistantName") or "Nexa AI").strip())[:40] or "Nexa AI"
    attachment = data.get("attachment") if isinstance(data.get("attachment"), dict) else None
    client_history = data.get("clientHistory") if isinstance(data.get("clientHistory"), list) else []

    if not username or not db.session.get(User, username):
        return jsonify({"ok": False, "message": "Önce giriş yapmalısın."}), 401
    if not prompt and not attachment:
        return jsonify({"ok": False, "message": "AI için bir şey yaz."}), 400
    if len(prompt) > 2500:
        return jsonify({"ok": False, "message": "AI isteği çok uzun."}), 400
    if attachment and len(str(attachment.get("dataUrl") or "")) > MAX_ATTACHMENT_DATA_URL_CHARS:
        return jsonify({"ok": False, "message": "AI eki çok büyük."}), 400

    context = ai_context_for_user(username, chat_id, prompt, client_history)
    context["assistant"] = {"name": assistant_name}
    context["preferences"] = {
        "responseLength": str(data.get("responseLength") or "medium")[:20],
        "persona": str(data.get("persona") or "")[:240],
    }
    context["liveInfo"] = live_info_for_prompt(prompt, data.get("timezoneOffsetMinutes", 0))
    if attachment:
        context["inputAttachment"] = attachment_context_for_ai(attachment)
    actions = []
    reply, provider, research = generate_ai_reply(prompt or "Bu eki incele ve yardımcı ol.", context, actions, attachment)
    intent = process_ai_intent(reply, context)
    if intent.get("is_intent"):
        reply = intent.get("reply") or "İşlemi hazırladım. Onaylarsan uygulayacağım."
        actions = intent.get("actions") or []
        provider = {**(provider or {}), "intent": bool(actions)}
    add_points_once(username, POINT_RULES["ai_chat"], "ai_chat", datetime.now(timezone.utc).strftime("%Y-%m-%d"), {"prompt": prompt[:120]})
    attachment_meta = attachment_context_for_ai(attachment) if attachment else None
    if attachment_meta:
        attachment_meta.pop("textContent", None)
        attachment_meta.pop("dataUrlPreview", None)
    store_ai_memory(
        username,
        chat_id,
        "user",
        prompt or "Bu eki incele.",
        meta={"hasAttachment": bool(attachment), "attachment": attachment_meta},
    )
    store_ai_memory(username, chat_id, "assistant", reply, provider=(provider or {}).get("provider"), meta={"researchCount": len(research or []), "actions": actions})
    db.session.commit()
    return jsonify(
        {
            "ok": True,
            "reply": reply,
            "actions": actions,
            "provider": provider,
            "research": research,
        }
    )


def local_generated_image_data_url(prompt, variant=0):
    seed = hashlib.sha256(f"{prompt}:{variant}".encode("utf-8")).hexdigest()
    hue_a = int(seed[:2], 16) % 360
    hue_b = (hue_a + 120 + int(seed[2:4], 16) % 80) % 360
    hue_c = (hue_a + 250) % 360
    safe_prompt = html.escape(re.sub(r"\s+", " ", prompt or "NexaLine görseli").strip()[:120])
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="768" viewBox="0 0 1024 768">
<defs>
<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
<stop stop-color="hsl({hue_a},86%,38%)"/><stop offset=".52" stop-color="hsl({hue_b},84%,32%)"/><stop offset="1" stop-color="hsl({hue_c},88%,44%)"/>
</linearGradient>
<radialGradient id="glow" cx=".36" cy=".28" r=".74"><stop stop-color="rgba(255,255,255,.72)"/><stop offset=".42" stop-color="rgba(255,255,255,.08)"/><stop offset="1" stop-color="rgba(255,255,255,0)"/></radialGradient>
<filter id="blur"><feGaussianBlur stdDeviation="34"/></filter>
</defs>
<rect width="1024" height="768" fill="#06101f"/>
<rect width="1024" height="768" fill="url(#bg)" opacity=".86"/>
<circle cx="210" cy="170" r="220" fill="#2f80ff" opacity=".28" filter="url(#blur)"/>
<circle cx="820" cy="560" r="260" fill="#ff00b8" opacity=".24" filter="url(#blur)"/>
<path d="M145 595 C270 370 424 330 548 398 C693 478 768 322 889 178" fill="none" stroke="rgba(255,255,255,.28)" stroke-width="16" stroke-linecap="round"/>
<rect x="86" y="86" width="852" height="596" rx="48" fill="rgba(4,9,20,.38)" stroke="rgba(255,255,255,.28)"/>
<text x="112" y="160" fill="#fff" font-size="34" font-family="Inter,Arial,sans-serif" font-weight="800">Nexa AI görsel taslağı</text>
<foreignObject x="112" y="202" width="800" height="210">
<div xmlns="http://www.w3.org/1999/xhtml" style="color:#edf6ff;font-family:Inter,Arial,sans-serif;font-size:44px;font-weight:900;line-height:1.08;text-shadow:0 8px 34px rgba(0,0,0,.38);">{safe_prompt}</div>
</foreignObject>
<text x="112" y="642" fill="rgba(255,255,255,.72)" font-size="24" font-family="Inter,Arial,sans-serif">Ücretsiz yerel mod • Gemini varsa gerçek image model denenir</text>
</svg>"""
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")


def data_url_to_bytes(data_url, max_chars=MAX_ATTACHMENT_DATA_URL_CHARS):
    data_url = str(data_url or "")
    if len(data_url) > max_chars:
        raise ValueError("data-url-too-large")
    if "," not in data_url:
        raise ValueError("invalid-data-url")
    header, payload = data_url.split(",", 1)
    mime_match = re.match(r"data:([^;]+)", header)
    mime_type = mime_match.group(1) if mime_match else "application/octet-stream"
    if "base64" in header:
        return base64.b64decode(payload), mime_type
    return payload.encode("utf-8"), mime_type


def bytes_to_data_url(payload, mime_type):
    return f"data:{mime_type};base64,{base64.b64encode(payload).decode('ascii')}"


def call_huggingface_image(prompt):
    key = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_API_KEY")
    if not key:
        raise RuntimeError("HF_TOKEN missing")
    model = os.environ.get("HF_IMAGE_MODEL", "black-forest-labs/FLUX.1-schnell")
    timeout_seconds = max(AI_TIMEOUT_SECONDS, 25)
    endpoints = [
        f"https://router.huggingface.co/hf-inference/models/{model}",
        f"https://api-inference.huggingface.co/models/{model}",
    ]
    last_error = None
    for endpoint in endpoints:
        try:
            response = requests.post(
                endpoint,
                headers={"Authorization": f"Bearer {key}", "Accept": "image/png"},
                json={"inputs": prompt, "parameters": {"num_inference_steps": 4}},
                timeout=timeout_seconds,
            )
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "image/png").split(";")[0]
            if content_type.startswith("image/") and response.content:
                return bytes_to_data_url(response.content, content_type), "Hugging Face görsel modeliyle oluşturuldu."
            data = response.json()
            if isinstance(data, dict) and data.get("error"):
                raise RuntimeError(data["error"])
        except Exception as error:
            last_error = error
    raise RuntimeError(f"Hugging Face image failed: {last_error}")


def call_gemini_image(prompt):
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("VITE_GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY missing")
    model = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-2.0-flash-preview-image-generation")
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        params={"key": key},
        json={
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
        },
        timeout=max(AI_TIMEOUT_SECONDS, 18),
    )
    response.raise_for_status()
    parts = response.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
    note = ""
    for part in parts:
        if part.get("text"):
            note = part.get("text")
        inline = part.get("inlineData") or part.get("inline_data")
        if inline and inline.get("data"):
            mime_type = inline.get("mimeType") or inline.get("mime_type") or "image/png"
            return f"data:{mime_type};base64,{inline['data']}", note
    raise RuntimeError("Gemini image response has no image")


def call_groq_stt(audio_bytes, mime_type):
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise RuntimeError("GROQ_API_KEY missing")
    extension = mimetypes.guess_extension(mime_type or "") or ".webm"
    if extension == ".oga":
        extension = ".ogg"
    filename = f"nexa-voice{extension}"
    response = requests.post(
        "https://api.groq.com/openai/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {key}"},
        data={
            "model": os.environ.get("GROQ_STT_MODEL", "whisper-large-v3-turbo"),
            "language": os.environ.get("GROQ_STT_LANGUAGE", "tr"),
            "response_format": "json",
        },
        files={"file": (filename, audio_bytes, mime_type or "audio/webm")},
        timeout=max(AI_TIMEOUT_SECONDS, 20),
    )
    response.raise_for_status()
    return (response.json().get("text") or "").strip()


def call_gemini_stt(audio_bytes, mime_type):
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("VITE_GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY missing")
    model = os.environ.get("GEMINI_STT_MODEL", os.environ.get("GEMINI_MODEL", "gemini-1.5-flash"))
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        params={"key": key},
        json={
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": "Bu Türkçe ses kaydını eksiksiz biçimde yazıya çevir. Yalnızca konuşma metnini döndür."},
                        {
                            "inline_data": {
                                "mime_type": mime_type or "audio/webm",
                                "data": base64.b64encode(audio_bytes).decode("ascii"),
                            }
                        },
                    ],
                }
            ],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 700},
        },
        timeout=max(AI_TIMEOUT_SECONDS, 25),
    )
    response.raise_for_status()
    parts = response.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
    return "\n".join(part.get("text", "") for part in parts).strip()


def call_openai_stt(audio_bytes, mime_type):
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY missing")
    extension = mimetypes.guess_extension(mime_type or "") or ".webm"
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    response = requests.post(
        f"{base_url}/audio/transcriptions",
        headers={"Authorization": f"Bearer {key}"},
        data={
            "model": os.environ.get("OPENAI_STT_MODEL", "gpt-4o-mini-transcribe"),
            "language": os.environ.get("OPENAI_STT_LANGUAGE", "tr"),
            "response_format": "json",
        },
        files={"file": (f"nexa-voice{extension}", audio_bytes, mime_type or "audio/webm")},
        timeout=max(AI_TIMEOUT_SECONDS, 25),
    )
    response.raise_for_status()
    return (response.json().get("text") or "").strip()


def call_assemblyai_stt(audio_bytes, mime_type):
    key = os.environ.get("ASSEMBLYAI_API_KEY")
    if not key:
        raise RuntimeError("ASSEMBLYAI_API_KEY missing")
    headers = {"Authorization": key}
    upload = requests.post(
        "https://api.assemblyai.com/v2/upload",
        headers=headers,
        data=audio_bytes,
        timeout=max(AI_TIMEOUT_SECONDS, 25),
    )
    upload.raise_for_status()
    audio_url = upload.json().get("upload_url")
    if not audio_url:
        raise RuntimeError("AssemblyAI upload_url missing")
    transcript_response = requests.post(
        "https://api.assemblyai.com/v2/transcript",
        headers={**headers, "Content-Type": "application/json"},
        json={
            "audio_url": audio_url,
            "language_code": os.environ.get("ASSEMBLYAI_LANGUAGE_CODE", "tr"),
            "speech_model": os.environ.get("ASSEMBLYAI_SPEECH_MODEL", "best"),
        },
        timeout=AI_TIMEOUT_SECONDS,
    )
    transcript_response.raise_for_status()
    transcript_id = transcript_response.json().get("id")
    if not transcript_id:
        raise RuntimeError("AssemblyAI transcript id missing")
    deadline = time.time() + int(os.environ.get("ASSEMBLYAI_STT_TIMEOUT", "45"))
    while time.time() < deadline:
        poll = requests.get(f"https://api.assemblyai.com/v2/transcript/{transcript_id}", headers=headers, timeout=AI_TIMEOUT_SECONDS)
        poll.raise_for_status()
        payload = poll.json()
        status = payload.get("status")
        if status == "completed":
            return (payload.get("text") or "").strip()
        if status == "error":
            raise RuntimeError(payload.get("error") or "AssemblyAI transcript failed")
        time.sleep(1.5)
    raise RuntimeError("AssemblyAI transcript timeout")


def elevenlabs_voice_id(voice_key):
    explicit = os.environ.get(f"ELEVENLABS_VOICE_ID_{(voice_key or '').upper()}") or os.environ.get("ELEVENLABS_VOICE_ID")
    if explicit:
        return explicit
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY missing")
    response = requests.get("https://api.elevenlabs.io/v1/voices", headers={"xi-api-key": key}, timeout=AI_TIMEOUT_SECONDS)
    response.raise_for_status()
    voices = response.json().get("voices") or []
    if not voices:
        raise RuntimeError("ElevenLabs voice list empty")
    target = (voice_key or "").casefold()
    if target:
        for voice in voices:
            labels = json.dumps(voice.get("labels") or {}, ensure_ascii=False).casefold()
            name = (voice.get("name") or "").casefold()
            if target in name or target in labels:
                return voice["voice_id"]
    return voices[0]["voice_id"]


def openai_tts_voice(voice_key):
    voice = (voice_key or "warm").strip().casefold()
    mapping = {
        "warm": os.environ.get("OPENAI_TTS_VOICE_WARM", "alloy"),
        "alloy": "alloy",
        "fable": "fable",
        "female": os.environ.get("OPENAI_TTS_VOICE_FEMALE", "alloy"),
        "kadin": os.environ.get("OPENAI_TTS_VOICE_FEMALE", "alloy"),
        "kadın": os.environ.get("OPENAI_TTS_VOICE_FEMALE", "nova"),
        "male": os.environ.get("OPENAI_TTS_VOICE_MALE", "fable"),
        "erkek": os.environ.get("OPENAI_TTS_VOICE_MALE", "fable"),
        "calm": os.environ.get("OPENAI_TTS_VOICE_CALM", "alloy"),
        "energetic": os.environ.get("OPENAI_TTS_VOICE_ENERGETIC", "fable"),
    }
    return mapping.get(voice, os.environ.get("OPENAI_TTS_VOICE", "alloy"))


def call_openai_tts(text_value, voice_key="warm"):
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY missing")
    model = os.environ.get("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    payload = {
        "model": model,
        "voice": openai_tts_voice(voice_key),
        "input": text_value[:1200],
        "response_format": "mp3",
    }
    if model.startswith("gpt-4o"):
        payload["instructions"] = (
            "Duygulu, dogal, sicak ve konusma diline yakin Turkce ses kullan. "
            "Cumleleri acele etmeden, arkadasca ve net oku."
        )
    response = requests.post(
        f"{base_url}/audio/speech",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=payload,
        timeout=max(AI_TIMEOUT_SECONDS, 30),
    )
    response.raise_for_status()
    return bytes_to_data_url(response.content, response.headers.get("Content-Type", "audio/mpeg").split(";")[0])


def call_elevenlabs_tts(text_value, voice_key="warm"):
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY missing")
    voice_id = elevenlabs_voice_id(voice_key)
    response = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        headers={"xi-api-key": key, "Content-Type": "application/json"},
        json={
            "text": text_value[:1200],
            "model_id": os.environ.get("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2"),
            "voice_settings": {
                "stability": float(os.environ.get("ELEVENLABS_STABILITY", "0.48")),
                "similarity_boost": float(os.environ.get("ELEVENLABS_SIMILARITY", "0.78")),
                "style": float(os.environ.get("ELEVENLABS_STYLE", "0.45")),
                "use_speaker_boost": os.environ.get("ELEVENLABS_SPEAKER_BOOST", "1") != "0",
            },
        },
        timeout=max(AI_TIMEOUT_SECONDS, 20),
    )
    response.raise_for_status()
    return bytes_to_data_url(response.content, response.headers.get("Content-Type", "audio/mpeg").split(";")[0])


def call_human_tts(text_value, voice_key="warm"):
    errors = []
    if os.environ.get("OPENAI_API_KEY"):
        try:
            return call_openai_tts(text_value, voice_key), {
                "provider": "openai",
                "model": os.environ.get("OPENAI_TTS_MODEL", "gpt-4o-mini-tts"),
                "voice": openai_tts_voice(voice_key),
            }
        except Exception as error:
            errors.append(f"OpenAI TTS: {error}")
            app.logger.warning("OpenAI TTS failed, trying fallback: %s", error)
    if os.environ.get("ELEVENLABS_API_KEY"):
        try:
            return call_elevenlabs_tts(text_value, voice_key), {
                "provider": "elevenlabs",
                "model": os.environ.get("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2"),
                "voice": voice_key or "warm",
            }
        except Exception as error:
            errors.append(f"ElevenLabs TTS: {error}")
            app.logger.warning("ElevenLabs TTS failed: %s", error)
    raise RuntimeError("; ".join(errors) or "TTS provider missing")


class ProcessAI:
    LANGUAGE_CODES = {
        "otomatik": "",
        "auto": "",
        "turkce": "tr",
        "türkçe": "tr",
        "ingilizce": "en",
        "almanca": "de",
        "fransizca": "fr",
        "fransızca": "fr",
        "ispanyolca": "es",
        "italyanca": "it",
        "arapca": "ar",
        "arapça": "ar",
        "rusca": "ru",
    }

    def __init__(self):
        self.openai_base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")

    def _openai_key(self):
        key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not key:
            raise RuntimeError("OPENAI_API_KEY missing")
        return key

    def _openai_headers(self):
        return {"Authorization": f"Bearer {self._openai_key()}", "Content-Type": "application/json"}

    def _openai_text(self, system_prompt, text_value):
        model = os.environ.get("OPENAI_PROCESS_MODEL", os.environ.get("OPENAI_MODEL", "gpt-4o-mini"))
        response = requests.post(
            f"{self.openai_base_url}/chat/completions",
            headers=self._openai_headers(),
            json={
                "model": model,
                "temperature": 0.2,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text_value[:6000]},
                ],
            },
            timeout=max(AI_TIMEOUT_SECONDS, 25),
        )
        response.raise_for_status()
        result = response.json()["choices"][0]["message"]["content"].strip()
        if not result:
            raise RuntimeError("OpenAI returned empty text")
        return result, {"provider": "openai", "model": model}

    def correct_text(self, text_value):
        if not os.environ.get("OPENAI_API_KEY") and not any(
            os.environ.get(name)
            for name in ("GEMINI_API_KEY", "VITE_GEMINI_API_KEY", "GROQ_API_KEY", "DEEPINFRA_API_KEY", "OPENROUTER_API_KEY", "OLLAMA_BASE_URL")
        ):
            cleaned = re.sub(r"[ \t]+", " ", text_value or "").strip()
            replacements = {
                r"\bmeraba\b": "merhaba",
                r"\bherkez\b": "herkes",
                r"\byanlız\b": "yalnız",
                r"\bbişey\b": "bir şey",
                r"\bbirşey\b": "bir şey",
                r"\bdeyil\b": "değil",
                r"\bgelicem\b": "geleceğim",
                r"\byapıcam\b": "yapacağım",
                r"\byazıcam\b": "yazacağım",
                r"\bsorucam\b": "soracağım",
            }
            for pattern, replacement in replacements.items():
                cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"\s+([,.!?;:])", r"\1", cleaned)
            cleaned = re.sub(r"([,.!?;:])([^\s])", r"\1 \2", cleaned)
            cleaned = re.sub(
                r"(^|[.!?]\s+)([a-zçğıöşü])",
                lambda match: match.group(1) + match.group(2).upper(),
                cleaned,
            )
            if cleaned and cleaned[-1] not in ".!?":
                cleaned += "."
            return cleaned, {"provider": "local-correction", "model": "nexaline-turkish-rules"}
        return self._openai_text(
            "Turkce yazim editorusun. Metnin anlamini degistirmeden imla, noktalama ve anlatim bozukluklarini duzelt. Yalnizca duzeltilmis metni dondur.",
            text_value,
        )

    def translate_text(self, text_value, source_lang="otomatik", target_lang="tr"):
        key = (
            os.environ.get("GOOGLE_TRANSLATE_API_KEY")
            or os.environ.get("GOOGLE_CLOUD_TRANSLATE_API_KEY")
            or ""
        ).strip()
        source = self.LANGUAGE_CODES.get(str(source_lang).strip().casefold(), str(source_lang).strip().lower())
        target = self.LANGUAGE_CODES.get(str(target_lang).strip().casefold(), str(target_lang).strip().lower()) or "tr"
        if key:
            payload = {"q": text_value[:6000], "target": target, "format": "text"}
            if source:
                payload["source"] = source
            response = requests.post(
                "https://translation.googleapis.com/language/translate/v2",
                params={"key": key},
                json=payload,
                timeout=max(AI_TIMEOUT_SECONDS, 20),
            )
            response.raise_for_status()
            translated = response.json().get("data", {}).get("translations", [{}])[0].get("translatedText", "")
            provider = {"provider": "google-translate", "model": "translation-v2"}
        else:
            response = requests.get(
                "https://translate.googleapis.com/translate_a/single",
                params={
                    "client": "gtx",
                    "sl": source or "auto",
                    "tl": target,
                    "dt": "t",
                    "q": text_value[:4500],
                },
                timeout=max(AI_TIMEOUT_SECONDS, 20),
            )
            response.raise_for_status()
            translated = "".join(part[0] for part in (response.json()[0] or []) if part and part[0])
            provider = {"provider": "google-translate-free", "model": "gtx"}
        translated = html.unescape(translated).strip()
        if not translated:
            raise RuntimeError("Google Translate returned empty text")
        return translated, provider

    def generate_image(self, prompt):
        model = os.environ.get("OPENAI_IMAGE_MODEL", "dall-e-3")
        payload = {"model": model, "prompt": prompt[:1000], "n": 1, "size": "1024x1024"}
        if model.startswith("dall-e"):
            payload.update({"quality": os.environ.get("OPENAI_IMAGE_QUALITY", "standard"), "response_format": "b64_json"})
        else:
            payload.update({"quality": os.environ.get("OPENAI_IMAGE_QUALITY", "medium"), "output_format": "png"})
        response = requests.post(
            f"{self.openai_base_url}/images/generations",
            headers=self._openai_headers(),
            json=payload,
            timeout=max(AI_TIMEOUT_SECONDS, 60),
        )
        response.raise_for_status()
        image = (response.json().get("data") or [{}])[0]
        encoded = image.get("b64_json")
        if encoded:
            return f"data:image/png;base64,{encoded}", "OpenAI ile görsel oluşturuldu.", {"provider": "openai-image", "model": model}
        image_url = image.get("url")
        if image_url:
            image_response = requests.get(image_url, timeout=max(AI_TIMEOUT_SECONDS, 30))
            image_response.raise_for_status()
            mime_type = image_response.headers.get("Content-Type", "image/png").split(";")[0]
            return bytes_to_data_url(image_response.content, mime_type), "OpenAI ile görsel oluşturuldu.", {"provider": "openai-image", "model": model}
        raise RuntimeError("OpenAI image response has no image")


process_ai = ProcessAI()


TURKISH_IMAGE_PROMPT_WORDS = {
    "acik", "adam", "aksam", "altinda", "araba", "arkaplan", "ay", "beyaz", "bir",
    "ciz", "cizim", "cocuk", "dag", "deniz", "detayli", "dogal", "ev", "evde", "fotograf",
    "gece", "gercekci", "gibi", "gokyuzu", "gol", "gorsel", "gun", "gunes", "icinde", "ile",
    "insan", "kadin", "karanlik", "kedi", "kirmizi", "kiz", "kopek", "kosan", "kus",
    "manzara", "mavi", "mor", "olan", "olsun", "onunde", "orman", "olustur",
    "pembe", "portre", "resim", "sahilde", "sahne", "sari", "sehir", "siyah", "sokakta",
    "tarzinda", "turuncu", "ustunde", "uzay", "uzayda", "ve", "yagmur", "yaninda", "yap",
    "yesil",
}


def detect_image_prompt_language(prompt):
    value = str(prompt or "").strip()
    if re.search(r"[çğıöşüÇĞİÖŞÜ]", value):
        return "tr"
    tokens = set(re.findall(r"[a-z0-9]+", fold_tr_ascii(value)))
    matches = tokens.intersection(TURKISH_IMAGE_PROMPT_WORDS)
    if len(matches) >= 2 or matches.intersection({"gorsel", "olustur", "resim", "ciz", "yap"}):
        return "tr"
    return "en"


def remote_image_provider_ready():
    return any(
        os.environ.get(name)
        for name in (
            "OPENAI_API_KEY",
            "GEMINI_API_KEY",
            "VITE_GEMINI_API_KEY",
            "HF_TOKEN",
            "HUGGINGFACE_API_KEY",
        )
    )


def prepare_image_generation_prompt(prompt, translate_prompt=True):
    original_prompt = re.sub(r"\s+", " ", str(prompt or "")).strip()
    language = detect_image_prompt_language(original_prompt)
    metadata = {
        "promptLanguage": language,
        "promptTranslated": False,
    }
    if language != "tr":
        return original_prompt[:1000], metadata
    if not translate_prompt:
        metadata["translationSkipped"] = "no-remote-image-provider"
        return original_prompt[:1000], metadata

    try:
        translated, translation_provider = process_ai.translate_text(original_prompt, "tr", "en")
        translated = re.sub(r"\s+", " ", translated).strip()
        if translated:
            metadata["promptTranslated"] = True
            metadata["translationProvider"] = (translation_provider or {}).get("provider")
            return translated[:1000], metadata
    except Exception as error:
        app.logger.info("Turkish image prompt translation fallback: %s", error)

    bilingual_prompt = (
        "Create an image that follows this Turkish request exactly. "
        "Preserve every named subject, color, number, style, and composition detail. "
        f"Turkish request: {original_prompt}"
    )
    metadata["translationFallback"] = "bilingual"
    return bilingual_prompt[:1000], metadata


def call_openai_image(prompt):
    data_url, note, _provider = process_ai.generate_image(prompt)
    return data_url, note


@app.route("/ai/stt", methods=["POST"])
@ai_error_boundary
def ai_stt():
    data = request.get_json() or {}
    username = (data.get("username") or "").strip().lower()
    if not username or not db.session.get(User, username):
        return jsonify({"ok": False, "message": "Önce giriş yapmalısın."}), 401
    try:
        audio_bytes, mime_type = data_url_to_bytes(data.get("audioDataUrl"), MAX_AI_AUDIO_DATA_URL_CHARS)
        if not audio_bytes:
            return jsonify({"ok": False, "message": "Ses kaydı boş."}), 400
        transcript = ""
        provider = {"provider": "local", "model": "none"}
        if os.environ.get("GROQ_API_KEY"):
            try:
                transcript = call_groq_stt(audio_bytes, mime_type)
                provider = {"provider": "groq", "model": os.environ.get("GROQ_STT_MODEL", "whisper-large-v3-turbo")}
            except Exception as error:
                app.logger.warning("Groq STT failed: %s", error)
        if not transcript and (os.environ.get("GEMINI_API_KEY") or os.environ.get("VITE_GEMINI_API_KEY")):
            try:
                transcript = call_gemini_stt(audio_bytes, mime_type)
                provider = {"provider": "gemini", "model": os.environ.get("GEMINI_STT_MODEL", os.environ.get("GEMINI_MODEL", "gemini-1.5-flash"))}
            except Exception as error:
                app.logger.warning("Gemini STT failed: %s", error)
        if not transcript and os.environ.get("OPENAI_API_KEY"):
            try:
                transcript = call_openai_stt(audio_bytes, mime_type)
                provider = {"provider": "openai", "model": os.environ.get("OPENAI_STT_MODEL", "gpt-4o-mini-transcribe")}
            except Exception as error:
                app.logger.warning("OpenAI STT failed: %s", error)
        if not transcript and os.environ.get("ASSEMBLYAI_API_KEY"):
            try:
                transcript = call_assemblyai_stt(audio_bytes, mime_type)
                provider = {"provider": "assemblyai", "model": os.environ.get("ASSEMBLYAI_SPEECH_MODEL", "best")}
            except Exception as error:
                app.logger.warning("AssemblyAI STT failed: %s", error)
        if not transcript:
            return jsonify({"ok": False, "message": "Ses metne çevrilemedi."}), 400
        return jsonify({"ok": True, "text": transcript, "provider": provider})
    except Exception as error:
        app.logger.warning("AI STT failed: %s", error)
        return jsonify({"ok": False, "message": "Ses tanıma servisleri şu an yanıt vermiyor. Groq, Gemini, OpenAI veya AssemblyAI anahtarını kontrol et."}), 503


@app.route("/ai/tts", methods=["POST"])
@ai_error_boundary
def ai_tts():
    data = request.get_json() or {}
    username = (data.get("username") or "").strip().lower()
    text_value = re.sub(r"\s+", " ", (data.get("text") or "").strip())
    if not username or not db.session.get(User, username):
        return jsonify({"ok": False, "message": "Önce giriş yapmalısın."}), 401
    if len(text_value) < 2:
        return jsonify({"ok": False, "message": "Seslendirilecek metin yok."}), 400
    try:
        audio_data_url, provider = call_human_tts(text_value, data.get("voice") or "warm")
        return jsonify({"ok": True, "audioDataUrl": audio_data_url, "provider": provider})
    except Exception as error:
        app.logger.warning("AI TTS failed: %s", error)
        return jsonify({"ok": False, "message": "Ses uretimi icin OPENAI_API_KEY veya ELEVENLABS_API_KEY gerekli; servis su an yanit vermiyor."}), 503


def local_text_summary(text_value, limit=5):
    sentences = [
        re.sub(r"\s+", " ", item).strip(" -\t")
        for item in re.split(r"(?<=[.!?])\s+|\n+", text_value or "")
        if re.sub(r"\s+", " ", item).strip(" -\t")
    ]
    selected = []
    for sentence in sentences:
        if sentence.casefold() not in {item.casefold() for item in selected}:
            selected.append(sentence)
        if len(selected) >= limit:
            break
    if not selected:
        return "Özetlenecek anlamlı bir içerik bulunamadı."
    return "Kısa özet:\n" + "\n".join(f"- {sentence}" for sentence in selected)


def local_chat_analysis(text_value):
    sentences = [
        re.sub(r"\s+", " ", item).strip(" -\t")
        for item in re.split(r"(?<=[.!?])\s+|\n+", text_value or "")
        if re.sub(r"\s+", " ", item).strip(" -\t")
    ]
    lowered = [(sentence, sentence.casefold()) for sentence in sentences]
    task_terms = ("yapacak", "edecek", "gönderecek", "hazırlayacak", "tamamlayacak", "görüşecek", "gerekiyor", "unutma", "bekliyor")
    decision_terms = ("karar", "onaylandı", "kabul edildi", "kararlaştırıldı", "anlaşıldı")
    date_pattern = re.compile(r"\b(?:pazartesi|salı|çarşamba|perşembe|cuma|cumartesi|pazar|bugün|yarın|\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?|\d{1,2}:\d{2})\b", re.IGNORECASE)
    tasks = [sentence for sentence, folded in lowered if any(term in folded for term in task_terms)][:5]
    decisions = [sentence for sentence, folded in lowered if any(term in folded for term in decision_terms)][:5]
    dated = [sentence for sentence, _folded in lowered if date_pattern.search(sentence)][:5]
    stop_words = {
        "acaba", "ama", "ancak", "ben", "bir", "biz", "bu", "da", "daha", "de", "diye", "en", "gibi",
        "için", "ile", "mi", "mı", "mu", "mü", "ne", "o", "olarak", "olan", "sen", "şu", "ve", "veya"
    }
    counts = {}
    for word in re.findall(r"[A-Za-zÇĞİÖŞÜçğıöşü]{4,}", text_value or ""):
        folded = word.casefold()
        if folded in stop_words:
            continue
        counts[folded] = counts.get(folded, 0) + 1
    topics = [word for word, _count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:6]]
    sections = []
    sections.append("Ana konular:\n" + ("\n".join(f"- {topic}" for topic in topics) if topics else "- Belirgin ana konu bulunamadı."))
    sections.append("Alınan kararlar:\n" + ("\n".join(f"- {item}" for item in decisions) if decisions else "- Açık bir karar cümlesi bulunamadı."))
    sections.append("Bekleyen işler:\n" + ("\n".join(f"- {item}" for item in tasks) if tasks else "- Açık bir görev veya bekleyen iş bulunamadı."))
    sections.append("Tarih ve zamanlar:\n" + ("\n".join(f"- {item}" for item in dated) if dated else "- Belirgin tarih veya saat bulunamadı."))
    sections.append(local_text_summary(text_value, limit=3))
    return "\n\n".join(sections)


@app.route("/ai/text-tool", methods=["POST"])
@ai_error_boundary
def ai_text_tool():
    data = request.get_json() or {}
    username = (data.get("username") or "").strip().lower()
    tool = (data.get("tool") or "summarize").strip().lower()
    text_value = (data.get("text") or "").strip()
    source_lang = (data.get("sourceLang") or "otomatik").strip()
    target_lang = (data.get("targetLang") or "Türkçe").strip()
    if not username or not db.session.get(User, username):
        return jsonify({"ok": False, "message": "Önce giriş yapmalısın."}), 401
    if len(text_value) < 2:
        return jsonify({"ok": False, "message": "İşlenecek metin gerekli."}), 400
    if len(text_value) > 6000:
        text_value = text_value[:6000]
    if tool == "translate":
        prompt = f"{source_lang} dilinden {target_lang} diline doğal ve anlamı koruyan çeviri yap. Sadece çeviriyi yaz:\n{text_value}"
    elif tool == "fix":
        prompt = f"Bu metni imla, noktalama ve anlatım açısından düzelt. Anlamı değiştirme, sadece düzeltilmiş metni yaz:\n{text_value}"
    else:
        prompt = f"Bu metni kısa, işe yarar ve maddeli şekilde özetle:\n{text_value}"
    if tool == "analyze":
        prompt = (
            "Bu sohbet dokumunu analiz et. Turkce olarak ana konulari, alinan kararlari, "
            "bekleyen isleri, onemli tarih veya kisileri ve en sonda kisa bir ozeti maddeler halinde yaz:\n"
            f"{text_value}"
        )
    context = ai_context_for_user(username, data.get("chatId"), prompt)
    context["assistant"] = {"name": data.get("assistantName") or "Nexa AI"}
    research = []
    try:
        if tool == "translate":
            reply, provider = process_ai.translate_text(text_value, source_lang, target_lang)
        elif tool == "fix":
            reply, provider = process_ai.correct_text(text_value)
        else:
            reply, provider, research = generate_ai_reply(prompt, context, [])
    except Exception as error:
        app.logger.warning("ProcessAI text module fallback (%s): %s", tool, error)
        reply, provider, research = generate_ai_reply(prompt, context, [])
    if (provider or {}).get("provider") == "local":
        if tool == "analyze":
            reply = local_chat_analysis(text_value)
            provider = {"provider": "local-analysis", "model": "nexaline-structured-analyzer"}
            research = []
        elif tool == "summarize":
            reply = local_text_summary(text_value)
            provider = {"provider": "local-summary", "model": "nexaline-extractive-summary"}
            research = []
    store_ai_memory(username, data.get("chatId"), "user", prompt, provider="text-tool", meta={"tool": tool})
    store_ai_memory(username, data.get("chatId"), "assistant", reply, provider=(provider or {}).get("provider"), meta={"tool": tool})
    db.session.commit()
    return jsonify({"ok": True, "result": reply, "provider": provider, "research": research})


@app.route("/ai/image", methods=["POST"])
@ai_error_boundary
def ai_image():
    data = request.get_json() or {}
    username = (data.get("username") or "").strip().lower()
    prompt = re.sub(r"\s+", " ", (data.get("prompt") or "").strip())
    variant = int(data.get("variant") or 0)
    if not username or not db.session.get(User, username):
        return jsonify({"ok": False, "message": "Önce giriş yapmalısın."}), 401
    if len(prompt) < 3:
        return jsonify({"ok": False, "message": "Görsel için ne istediğini yaz."}), 400
    if len(prompt) > 800:
        prompt = prompt[:800]
    provider_prompt, prompt_metadata = prepare_image_generation_prompt(
        prompt,
        translate_prompt=remote_image_provider_ready(),
    )
    provider = {"provider": "local", "model": "nexaline-free-image", "ready": True, "free": True}
    note = "Ücretsiz yerel görsel üretildi."
    try:
        data_url, note = call_openai_image(provider_prompt)
        provider = {"provider": "openai-image", "model": os.environ.get("OPENAI_IMAGE_MODEL", "dall-e-3"), "ready": True}
    except Exception as error:
        app.logger.info("AI image OpenAI fallback: %s", error)
        try:
            data_url, note = call_gemini_image(provider_prompt)
            provider = {"provider": "gemini", "model": os.environ.get("GEMINI_IMAGE_MODEL", "gemini-2.0-flash-preview-image-generation"), "ready": True}
        except Exception as error:
            app.logger.info("AI image Gemini fallback: %s", error)
            try:
                data_url, note = call_huggingface_image(provider_prompt)
                provider = {"provider": "huggingface", "model": os.environ.get("HF_IMAGE_MODEL", "black-forest-labs/FLUX.1-schnell"), "ready": True}
            except Exception as error:
                app.logger.info("AI image local fallback: %s", error)
                data_url = local_generated_image_data_url(prompt, variant)
    provider.update(prompt_metadata)
    mime_match = re.match(r"data:([^;]+);", data_url)
    mime_type = mime_match.group(1) if mime_match else "image/svg+xml"
    extension = "png" if mime_type == "image/png" else "jpg" if mime_type == "image/jpeg" else "svg"
    store_ai_memory(username, None, "user", f"Gorsel olustur: {prompt}", provider="image", meta={"variant": variant})
    store_ai_memory(username, None, "assistant", note, provider=(provider or {}).get("provider"), meta={"type": "image", "mimeType": mime_type})
    db.session.commit()
    return jsonify({"ok": True, "image": {"dataUrl": data_url, "name": f"nexa-ai-gorsel.{extension}", "type": mime_type}, "note": note, "provider": provider})


@app.route("/ai/moderate", methods=["POST"])
@ai_error_boundary
def ai_moderate():
    data = request.get_json() or {}
    username = (data.get("username") or "").strip().lower()
    if not username or not db.session.get(User, username):
        return jsonify({"ok": False, "message": "Önce giriş yapmalısın."}), 401
    text_value = (data.get("text") or "")[:4000]
    labels = ai_moderation_labels(text_value)
    return jsonify({"ok": True, "labels": labels, "blocked": bool(labels), "reason": ", ".join(labels)})


@app.route("/ai/chat-summary", methods=["POST"])
@ai_error_boundary
def ai_chat_summary_route():
    data = request.get_json() or {}
    username = (data.get("username") or "").strip().lower()
    chat = db.session.get(Chat, data.get("chatId"))
    if not username or not chat or not user_can_see_chat(chat, username):
        return jsonify({"ok": False, "message": "Sohbet bulunamadi."}), 404
    messages = [message_to_dict(item) for item in recent_visible_messages(chat.id, username, min(80, RECENT_MESSAGE_SCAN_LIMIT))]
    prompt = f"{chat.title} sohbetini kisa, islevsel ve maddeli ozetle. Onemli karar, tarih, dosya ve bekleyen aksiyonlari ayir."
    context = ai_context_for_user(username, chat.id, prompt)
    context["activeChat"] = {"title": chat.title, "messages": messages}
    context["assistant"] = {"name": "Nexa AI"}
    reply, provider, research = generate_ai_reply(prompt, context, [])
    return jsonify({"ok": True, "summary": reply or local_chat_summary(messages), "provider": provider, "research": research})


@app.route("/ai/search", methods=["POST"])
@ai_error_boundary
def ai_search_route():
    data = request.get_json() or {}
    username = (data.get("username") or "").strip().lower()
    query = (data.get("query") or "").strip()
    chat_id = data.get("chatId")
    if not username or not db.session.get(User, username):
        return jsonify({"ok": False, "message": "Önce giriş yapmalısın."}), 401
    if len(query) < 2:
        return jsonify({"ok": False, "message": "Arama metni çok kısa."}), 400
    chats = [db.session.get(Chat, chat_id)] if chat_id else [
        Chat.query.get(member.chat_id) for member in ChatMember.query.filter_by(username=username).limit(AI_MAX_CHATS * 3).all()
    ]
    tokens = {token.casefold() for token in re.findall(r"[\wÃ§ÄŸÄ±Ã¶ÅŸÃ¼Ã‡ÄÄ°Ã–ÅÃœ]+", query)}
    matches = []
    for chat in [row for row in chats if row and user_can_see_chat(row, username)]:
        for message in recent_visible_messages(chat.id, username, RECENT_MESSAGE_SCAN_LIMIT):
            haystack = f"{message.body or ''} {json.dumps(message.attachment or {}, ensure_ascii=False)}".casefold()
            score = sum(1 for token in tokens if token and token in haystack)
            if score:
                matches.append({
                    "chatId": chat.id,
                    "chatTitle": chat.title,
                    "message": message_to_dict(message),
                    "score": score,
                })
    matches.sort(key=lambda item: (item["score"], item["message"]["createdAt"]), reverse=True)
    summary_prompt = f"Arama sorgusu: {query}\nSonuçları kullanıcıya kısa açıkla ve en yakın 5 sonucu seç."
    context = ai_context_for_user(username, chat_id, summary_prompt)
    context["matches"] = matches[:12]
    context["assistant"] = {"name": "Nexa AI"}
    reply, provider, research = generate_ai_reply(summary_prompt, context, [])
    return jsonify({"ok": True, "answer": reply, "results": matches[:20], "provider": provider, "research": research})


AI_COMMANDS = [
    {"id": "daily_plan", "title": "Günlük plan oluştur", "prompt": "Bugün için kısa ve uygulanabilir bir günlük plan oluştur."},
    {"id": "summarize", "title": "Metin özetle", "prompt": "Aşağıdaki metni kısa maddelerle özetle:"},
    {"id": "translate", "title": "Çeviri yap", "prompt": "Aşağıdaki metni istediğim dile doğal şekilde çevir:"},
    {"id": "fix_text", "title": "Yazı düzelt", "prompt": "Bu yazıyı imla ve anlatım açısından düzelt:"},
    {"id": "image", "title": "Görsel oluştur", "prompt": "Şu tarife uygun görsel oluştur:"},
    {"id": "ai_settings", "title": "AI ayarları", "prompt": "Nexa AI ayarlarını aç."},
    {"id": "chat_summary", "title": "Sohbeti özetle", "prompt": "Aktif sohbeti özetle."},
    {"id": "call_person", "title": "Kişiyi ara", "prompt": "Bu kişiyi ara:"},
    {"id": "draft_message", "title": "Mesaj taslağı oluştur", "prompt": "Bu kişiye kısa ve doğal bir mesaj taslağı oluştur:"},
    {"id": "send_message", "title": "Mesaj gönder", "prompt": "Bu kişiye mesaj gönder: "},
    {"id": "schedule_message", "title": "Zamanlı mesaj", "prompt": "5 dakika sonra bu kişiye mesaj gönder: "},
    {"id": "start_voice_call", "title": "Sesli arama başlat", "prompt": "Bu kişiyi sesli ara: "},
    {"id": "start_video_call", "title": "Görüntülü arama başlat", "prompt": "Bu kişiyi görüntülü ara: "},
    {"id": "open_chat", "title": "Sohbet kutusu aç", "prompt": "Bu kişiyle sohbeti aç: "},
    {"id": "delete_chat", "title": "Sohbeti sil", "prompt": "Bu sohbeti arşive alarak sil."},
    {"id": "react_message", "title": "Mesaja tepki bırak", "prompt": "Son mesaja kalp ifadesi bırak."},
    {"id": "reply_message", "title": "Yanıtlayarak cevap ver", "prompt": "Son mesaja yanıtla: "},
    {"id": "create_group", "title": "Grup oluştur", "prompt": "Yeni grup oluştur. Grup adı: "},
    {"id": "create_story", "title": "Güncelleme paylaş", "prompt": "Durum güncellemesi paylaş: "},
    {"id": "delete_story", "title": "Son güncellemeyi sil", "prompt": "Son paylaştığım güncellemeyi sil."},
    {"id": "privacy_last_seen", "title": "Son görülmeyi yönet", "prompt": "Son görülmemi arkadaşlarıma aç/kapat."},
    {"id": "open_notifications", "title": "Bildirimleri aç", "prompt": "Eski bildirimlerimi göster."},
    {"id": "internet_search", "title": "İnternette araştır", "prompt": "İnternette araştır: "},
    {"id": "weather", "title": "Hava durumunu sor", "prompt": "İstanbul hava durumu nasıl?"},
    {"id": "time", "title": "Saat ve tarih", "prompt": "Saat kaç ve bugün tarih ne?"},
]


@app.route("/ai/commands")
@ai_error_boundary
def ai_commands():
    return jsonify({"ok": True, "commands": AI_COMMANDS})


@app.route("/ai/sync-all", methods=["GET"])
@ai_error_boundary
def ai_sync_all():
    try:
        username = (request.args.get("username") or "").strip().lower()
        if not username or not db.session.get(User, username):
            return jsonify({"ok": False, "message": "AI senkronizasyonu için geçerli kullanıcı gerekli."}), 401
        state_payload = ai_full_state_for(username)
        return jsonify({"ok": True, **state_payload})
    except Exception as error:
        db.session.rollback()
        app.logger.exception("AI sync failed: %s", error)
        return jsonify({"ok": False, "message": "Nexa AI hafıza birimi erişilemiyor."}), 500


@app.route("/ai/settings/<username>", methods=["POST"])
@ai_error_boundary
def ai_settings_update(username):
    try:
        username = username.strip().lower()
        user = db.session.get(User, username)
        if not user:
            return jsonify({"ok": False, "message": "Kullanıcı bulunamadı."}), 404
        data = request.get_json() or {}
        settings = ai_settings_for_user(user)
        allowed = {"enabled", "name", "image", "autoApprove", "voice", "responseLength", "persona", "saveHistory", "notifications", "censorEnabled"}
        for key in allowed:
            if key in data:
                settings[key] = data[key]
        settings["name"] = re.sub(r"\s+", " ", str(settings.get("name") or "Nexa AI")).strip()[:40] or "Nexa AI"
        user.ai_settings = {key: settings.get(key) for key in allowed}
        db.session.commit()
        return jsonify({"ok": True, "message": "Nexa AI ayarları sunucuda güncellendi.", "settings": ai_settings_for_user(user)})
    except Exception as error:
        db.session.rollback()
        app.logger.exception("AI settings update failed: %s", error)
        return jsonify({"ok": False, "message": "Nexa AI ayar birimi güncellenemedi."}), 500


AI_RISKY_ACTIONS = {
    "update_profile",
    "set_privacy",
    "set_theme",
    "set_censor",
    "set_ai_enabled",
    "set_ai_auto_approve",
    "set_ai_name",
    "set_chat_pref",
    "delete_chat",
    "start_call",
    "schedule_call",
    "end_call",
    "send_message",
    "schedule_message",
    "reply_message",
    "react_message",
    "create_group",
    "update_group",
    "create_story",
    "delete_story",
    "contact_request",
}


def ai_user_has_full_access(user):
    settings = ai_settings_for_user(user)
    return bool(settings.get("autoApprove"))


def ai_action_confirm_message(action):
    label = action.get("label") or action.get("type") or "AI işlemi"
    return f"{label} işlemini yapmak için onay gerekiyor."


def ai_action_chat_for_user(action, username):
    chat_id = action.get("chatId") or action.get("chat_id")
    chat = db.session.get(Chat, chat_id) if chat_id else None
    if not chat or not user_can_see_chat(chat, username):
        return None
    return chat


def execute_ai_server_action(user, action):
    action_type = action.get("type")
    username = user.username

    if action_type == "update_profile":
        if action.get("displayName"):
            user.display_name = re.sub(r"\s+", " ", str(action.get("displayName")).strip())[:120] or user.display_name
        if action.get("about"):
            user.about = re.sub(r"\s+", " ", str(action.get("about")).strip())[:255] or user.about
        db.session.commit()
        return {"message": "Profil bilgileri güncellendi.", "state": ai_full_state_for(username)}

    if action_type == "set_privacy":
        privacy = action.get("privacy") if isinstance(action.get("privacy"), dict) else {}
        if "lastSeenHidden" in privacy:
            user.hide_last_seen = bool(privacy["lastSeenHidden"])
        if "onlineHidden" in privacy:
            user.hide_online = bool(privacy["onlineHidden"])
        if "readReceiptsOff" in privacy:
            user.disable_read_receipts = bool(privacy["readReceiptsOff"])
        if "emailHidden" in privacy:
            user.hide_email = bool(privacy["emailHidden"])
        user.privacy_settings = {**(user.privacy_settings or {}), **privacy}
        db.session.commit()
        return {"message": "Gizlilik ayarları güncellendi.", "state": ai_full_state_for(username)}

    if action_type == "set_theme":
        theme = str(action.get("theme") or "dark").strip().lower()
        user.theme_preference = "light" if theme == "light" else "dark"
        db.session.commit()
        return {"message": "Tema ayarı güncellendi.", "state": ai_full_state_for(username), "clientAction": action}

    if action_type in {"set_censor", "set_ai_enabled", "set_ai_auto_approve", "set_ai_name"}:
        settings = ai_settings_for_user(user)
        if action_type == "set_censor":
            settings["censorEnabled"] = action.get("enabled") is not False
        elif action_type == "set_ai_enabled":
            settings["enabled"] = action.get("enabled") is not False
        elif action_type == "set_ai_auto_approve":
            settings["autoApprove"] = action.get("enabled") is not False
        elif action_type == "set_ai_name":
            settings["name"] = re.sub(r"\s+", " ", str(action.get("name") or "Nexa AI").strip())[:40] or "Nexa AI"
        user.ai_settings = {key: settings.get(key) for key in default_ai_settings().keys()}
        db.session.commit()
        return {"message": "Nexa AI ayarı güncellendi.", "state": ai_full_state_for(username)}

    if action_type == "delete_chat":
        chat = ai_action_chat_for_user(action, username)
        if not chat:
            return {"error": "Sohbet bulunamadı veya yetkin yok.", "status": 404}
        if (action.get("mode") or "archive") == "archive":
            archive_chat_for_user(chat, username, "deleted")
            message = "Sohbet arşive alındı."
        else:
            hide_chat_messages_for_user(chat, username)
            message = "Sohbet kalıcı olarak gizlendi."
        ScheduledMessage.query.filter_by(sender=username, chat_id=chat.id).delete(synchronize_session=False)
        db.session.commit()
        for sid in connected_sids_for(username):
            socketio.emit("chat:remove", {"chatId": chat.id}, room=sid)
            socketio.emit("archive:update", visible_archives(username), room=sid)
        return {"message": message, "state": ai_full_state_for(username)}

    if action_type == "send_message":
        chat = ai_action_chat_for_user(action, username)
        body = re.sub(r"\s+", " ", str(action.get("body") or "").strip())
        if not chat or not body:
            return {"error": "Mesaj gönderilecek sohbet veya metin bulunamadı.", "status": 400}
        send_error = chat_send_error(username, chat)
        if send_error:
            return {"error": send_error, "status": 403}
        message = create_chat_message(chat, username, body, action.get("attachment"), action.get("replyTo"), action.get("expiresInSeconds"))
        db.session.commit()
        socketio.emit("message:new", message_to_dict(message), room=chat.id)
        return {"message": "Mesaj gönderildi.", "state": ai_full_state_for(username)}

    if action_type == "schedule_message":
        chat = ai_action_chat_for_user(action, username)
        body = re.sub(r"\s+", " ", str(action.get("body") or "").strip())
        if not chat or not body:
            return {"error": "Zamanlı mesaj için sohbet veya metin eksik.", "status": 400}
        try:
            send_at = datetime.fromisoformat(str(action.get("sendAt")).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return {"error": "Zamanlı mesaj tarihi okunamadı.", "status": 400}
        if send_at.tzinfo is None:
            send_at = send_at.replace(tzinfo=timezone.utc)
        row = ScheduledMessage(
            id=uuid4().hex,
            chat_id=chat.id,
            sender=username,
            body=body,
            attachment=action.get("attachment"),
            reply_to=action.get("replyTo") if isinstance(action.get("replyTo"), dict) else None,
            expires_in_seconds=parse_expiry_seconds(action.get("expiresInSeconds")),
            send_at=send_at,
        )
        db.session.add(row)
        db.session.commit()
        emit_scheduled_update(username)
        return {"message": "Zamanlı mesaj oluşturuldu.", "state": ai_full_state_for(username)}

    if action_type in {"open_chat", "open_settings", "start_call", "schedule_call", "end_call", "draft_message", "reply_message", "react_message", "create_group", "update_group", "create_story", "delete_story", "contact_request", "set_chat_pref"}:
        return {"message": "Sunucu yetki verdi.", "clientAction": action, "state": ai_full_state_for(username)}

    return {"error": "Bu AI aksiyonu tanınmıyor.", "status": 400}


@app.route("/ai/action-execute", methods=["POST"])
@ai_error_boundary
def ai_action_execute():
    try:
        data = request.get_json() or {}
        username = (data.get("username") or "").strip().lower()
        user = db.session.get(User, username)
        action = data.get("action") if isinstance(data.get("action"), dict) else {}
        confirmed = bool(data.get("confirmed"))
        if not user:
            return jsonify({"ok": False, "message": "AI aksiyonu için kullanıcı bulunamadı."}), 401
        if not action.get("type"):
            return jsonify({"ok": False, "message": "AI aksiyon paketi eksik."}), 400

        risky = action.get("type") in AI_RISKY_ACTIONS
        if risky and not confirmed and not ai_user_has_full_access(user):
            return jsonify({
                "ok": False,
                "requiresConfirmation": True,
                "message": ai_action_confirm_message(action),
                "action": action,
            })

        result = execute_ai_server_action(user, action)
        if result.get("error"):
            return jsonify({"ok": False, "message": result["error"]}), result.get("status", 400)
        return jsonify({"ok": True, **result})
    except Exception as error:
        db.session.rollback()
        app.logger.exception("AI action execute failed: %s", error)
        return jsonify({"ok": False, "message": "Nexa AI aksiyon birimi işlemi tamamlayamadı."}), 500


@app.route("/ai/tasks/<username>", methods=["GET", "POST"])
@ai_error_boundary
def ai_tasks(username):
    username = username.strip().lower()
    user = db.session.get(User, username)
    if not user:
        return jsonify({"ok": False, "message": "Kullanıcı bulunamadı."}), 404
    if request.method == "GET":
        return jsonify({"ok": True, "tasks": ai_tasks_for(username)})
    data = request.get_json() or {}
    title = re.sub(r"\s+", " ", (data.get("title") or "").strip())[:180]
    if len(title) < 2:
        return jsonify({"ok": False, "message": "Görev başlığı gerekli."}), 400
    repeat = (data.get("repeat") or "none").strip().lower()
    if repeat not in {"none", "daily", "weekly", "monthly"}:
        repeat = "none"
    remind_at = None
    if data.get("remindAt"):
        try:
            remind_at = datetime.fromisoformat(str(data.get("remindAt")).replace("Z", "+00:00"))
        except ValueError:
            remind_at = None
    row = AiTask(
        id=uuid4().hex,
        username=username,
        title=title,
        description=(data.get("description") or "").strip()[:1000],
        repeat=repeat,
        remind_at=remind_at,
    )
    db.session.add(row)
    db.session.commit()
    return jsonify({"ok": True, "message": "AI görevi eklendi.", "tasks": ai_tasks_for(username)})


@app.route("/ai/tasks/<username>/<task_id>", methods=["PATCH", "DELETE"])
@ai_error_boundary
def ai_task_update(username, task_id):
    username = username.strip().lower()
    row = db.session.get(AiTask, task_id)
    if not row or row.username != username:
        return jsonify({"ok": False, "message": "Görev bulunamadı."}), 404
    if request.method == "DELETE":
        db.session.delete(row)
        db.session.commit()
        return jsonify({"ok": True, "message": "Görev silindi.", "tasks": ai_tasks_for(username)})
    data = request.get_json() or {}
    if "title" in data:
        title = re.sub(r"\s+", " ", (data.get("title") or "").strip())[:180]
        if title:
            row.title = title
    if "description" in data:
        row.description = (data.get("description") or "").strip()[:1000]
    if "repeat" in data and data.get("repeat") in {"none", "daily", "weekly", "monthly"}:
        row.repeat = data.get("repeat")
    if "completed" in data:
        row.completed_at = datetime.now(timezone.utc) if data.get("completed") else None
    row.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify({"ok": True, "message": "Görev güncellendi.", "tasks": ai_tasks_for(username)})


@app.route("/qr-login/start", methods=["POST"])
def qr_login_start():
    cleanup_qr_login_sessions()
    session_id = secrets.token_urlsafe(12)
    secret = secrets.token_urlsafe(24)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=QR_LOGIN_TTL_SECONDS)
    with qr_login_lock:
        qr_login_sessions[session_id] = {
            "secret": secret,
            "expires_at": expires_at,
            "username": None,
            "confirmed_at": None,
            "consumed": False,
        }
    token = f"{session_id}.{secret}"
    return jsonify(
        {
            "ok": True,
            "sessionId": session_id,
            "secret": secret,
            "token": token,
            "payload": f"{request.host_url.rstrip('/')}/?nexaQrLogin={token}",
            "expiresAt": to_iso(expires_at),
            "ttl": QR_LOGIN_TTL_SECONDS,
        }
    )


@app.route("/qr-login/status/<session_id>", methods=["GET", "POST"])
def qr_login_status(session_id):
    data = request.get_json(silent=True) or {}
    secret = data.get("secret") or request.args.get("secret") or ""
    row, error = qr_session_error(session_id, secret)
    if error:
        return error
    username = row.get("username")
    if not username:
        return jsonify({"ok": True, "status": "pending", "expiresAt": to_iso(row["expires_at"])})
    user = db.session.get(User, username)
    if not user:
        return jsonify({"ok": False, "message": "Bağlanan kullanıcı bulunamadı."}), 404
    device_id = data.get("deviceId") or request.args.get("deviceId") or request.headers.get("X-Nexa-Device")
    payload = login_success_payload(user, device_id, "QR giriş başarılı.")
    with qr_login_lock:
        row["consumed"] = True
    return jsonify({"status": "confirmed", **payload})


@app.route("/qr-login/confirm", methods=["POST"])
def qr_login_confirm():
    data = request.get_json() or {}
    session_id = data.get("sessionId") or ""
    secret = data.get("secret") or ""
    if data.get("token") or data.get("payload"):
        session_id, secret = parse_qr_login_token(data.get("token") or data.get("payload"))
    username = (data.get("username") or "").strip().lower()
    if not username or not db.session.get(User, username):
        return jsonify({"ok": False, "message": "Telefonda açık bir NexaLine hesabı bulunamadı."}), 401
    row, error = qr_session_error(session_id, secret)
    if error:
        return error
    with qr_login_lock:
        row["username"] = username
        row["confirmed_at"] = datetime.now(timezone.utc)
    socketio.emit("qr:confirmed", {"ok": True, "sessionId": session_id, "user": private_user(username)}, room=f"qr:{session_id}", namespace="/")
    return jsonify({"ok": True, "message": "Web oturumu bağlandı."})


@app.route("/register", methods=["POST"])
def register():
    try:
        data = request.get_json() or {}
        username = (data.get("username") or "").strip().lower()
        contact = (data.get("contact") or data.get("email") or data.get("phone") or "").strip()
        password = data.get("password") or ""

        username_problem = username_error(username)
        if username_problem:
            return jsonify({"ok": False, "message": username_problem}), 400

        if db.session.get(User, username):
            return jsonify({"ok": False, "message": "Bu kullanıcı adı zaten kayıtlı. Farklı bir kullanıcı adı dene."}), 409

        password_hash = None
        if password:
            password_problem = password_error(username, password)
            if password_problem:
                return jsonify({"ok": False, "message": password_problem}), 400
            password_hash = generate_password_hash(password)

        phone, phone_normalized = normalize_phone(contact)
        if phone_normalized:
            if phone_exists(phone_normalized):
                return jsonify({"ok": False, "message": "Bu telefon numarası zaten bir hesapta kullanılıyor."}), 409
            _, code, sent, retry_after = create_phone_verification(
                purpose="register",
                username=username,
                phone=phone,
                phone_normalized=phone_normalized,
                password_hash=password_hash,
            )
            return phone_verification_response(
                f"{mask_phone(phone_normalized)} numarasına doğrulama kodu gönderdik.",
                code,
                sent,
                retry_after,
            )

        email_problem = email_error(contact)
        if email_problem:
            return jsonify({"ok": False, "message": "Geçerli bir Gmail adresi veya Türkiye cep telefonu numarası yazmalısın."}), 400
        email, email_normalized = normalize_email(contact)
        if email_exists(email_normalized):
            return jsonify({"ok": False, "message": "Bu Gmail zaten bir hesapta kullanılıyor."}), 409
        _, code, sent = create_email_verification(
            purpose="register",
            username=username,
            email=email,
            email_normalized=email_normalized,
            password_hash=password_hash,
        )
        return verification_response("Gmail adresine doğrulama kodu gönderdik.", code, sent)
    except Exception:
        db.session.rollback()
        app.logger.exception("Register failed")
        return jsonify({"ok": False, "message": "Sunucuda kayıt hatası oluştu. Render kayıtlarını kontrol et."}), 500

@app.route("/register/check-username", methods=["POST"])
def register_check_username():
    data = request.get_json() or {}
    username = (data.get("username") or "").strip().lower()

    username_problem = username_error(username)
    if username_problem:
        return jsonify({"ok": False, "message": username_problem}), 400

    if db.session.get(User, username):
        return jsonify({"ok": False, "message": "Bu kullanıcı adı zaten kayıtlı. Farklı bir kullanıcı adı dene."}), 409

    return jsonify({"ok": True, "message": "Kullanıcı adı uygun."})


@app.route("/users/search")
def users_search():
    query = (request.args.get("q") or "").strip().lower()
    if len(query) < 2:
        return jsonify({"ok": True, "users": []})
    rows = User.query.filter(
        db.or_(
            db.func.lower(User.username).contains(query),
            db.func.lower(User.display_name).contains(query),
        )
    ).order_by(User.created_at.desc()).limit(20).all()
    return jsonify({"ok": True, "users": [public_user(row.username) for row in rows]})


@app.route("/register/verify", methods=["POST"])
def register_verify():
    try:
        data = request.get_json() or {}
        username = (data.get("username") or "").strip().lower()
        contact = (data.get("contact") or data.get("email") or data.get("phone") or "").strip()
        code = (data.get("code") or "").strip()
        password = data.get("password") or ""
        confirm_password = data.get("confirmPassword")
        device_id = data.get("deviceId") or request.headers.get("X-Nexa-Device")
        display_name, display_name_error = normalize_display_name(data)
        if display_name_error:
            return jsonify({"ok": False, "message": display_name_error}), 400

        phone, phone_normalized = normalize_phone(contact)
        if phone_normalized:
            verification = PhoneVerification.query.filter_by(
                purpose="register",
                username=username,
                phone_normalized=phone_normalized,
            ).order_by(PhoneVerification.created_at.desc()).first()
            contact_kind = "phone"
        else:
            email_problem = email_error(contact)
            if email_problem:
                return jsonify({"ok": False, "message": "Geçerli bir Gmail adresi veya Türkiye cep telefonu numarası yazmalısın."}), 400
            email, email_normalized = normalize_email(contact)
            verification = EmailVerification.query.filter_by(
                purpose="register",
                username=username,
                email_normalized=email_normalized,
            ).order_by(EmailVerification.created_at.desc()).first()
            contact_kind = "email"

        if not verification:
            return jsonify({"ok": False, "message": "Doğrulama kaydı bulunamadı. Kayıt işlemini yeniden başlat."}), 404

        if is_past(verification.expires_at):
            return jsonify({"ok": False, "message": "Doğrulama kodunun süresi doldu."}), 400

        if verification.attempts >= 5:
            return jsonify({"ok": False, "message": "Çok fazla yanlış deneme yaptın. Yeni kod iste."}), 429

        if contact_kind == "phone" and verification.provider == "twilio":
            try:
                code_is_valid = check_twilio_verification(phone_normalized, code)
            except Exception:
                app.logger.exception("Twilio doğrulama kodu kontrol edilemedi")
                return jsonify({"ok": False, "message": "SMS doğrulama servisine ulaşılamadı. Lütfen yeniden dene."}), 502
            if code_is_valid:
                verification.code_hash = generate_password_hash(code)
                verification.provider = "twilio_verified"
                db.session.commit()
        else:
            code_is_valid = check_password_hash(verification.code_hash, code)

        if not code_is_valid:
            verification.attempts += 1
            db.session.commit()
            return jsonify({"ok": False, "message": "Doğrulama kodu hatalı."}), 400

        if db.session.get(User, username):
            return jsonify({"ok": False, "message": "Bu kullanıcı adı zaten kayıtlı."}), 409

        if contact_kind == "phone" and phone_exists(phone_normalized):
            return jsonify({"ok": False, "message": "Bu telefon numarası zaten bir hesapta kullanılıyor."}), 409
        if contact_kind == "email" and email_exists(email_normalized):
            return jsonify({"ok": False, "message": "Bu Gmail zaten bir hesapta kullanılıyor."}), 409

        if not password and not verification.password_hash:
            return jsonify({"ok": True, "message": "Kod doğrulandı. Şimdi güçlü şifreni oluştur.", "requiresPassword": True})

        if password:
            if confirm_password is not None and password != confirm_password:
                return jsonify({"ok": False, "message": "Yazdığın iki şifre aynı değil."}), 400
            password_problem = password_error(username, password)
            if password_problem:
                return jsonify({"ok": False, "message": password_problem}), 400
            password_hash = generate_password_hash(password)
        else:
            password_hash = verification.password_hash

        if not password_hash:
            return jsonify({"ok": False, "message": "Şifre oluşturulmadan kayıt tamamlanamaz."}), 400

        user = User(
            username=username,
            password_hash=password_hash,
            display_name=display_name[:120] or username,
            email=verification.email if contact_kind == "email" else None,
            email_normalized=verification.email_normalized if contact_kind == "email" else None,
            email_verified=contact_kind == "email",
            phone=verification.phone if contact_kind == "phone" else None,
            phone_normalized=verification.phone_normalized if contact_kind == "phone" else None,
            phone_verified=contact_kind == "phone",
            avatar=tr_upper((display_name or username)[:2]),
            about="NexaLine kullanıyorum.",
        )
        db.session.add(user)
        db.session.delete(verification)
        ensure_lobby()
        db.session.flush()
        return jsonify(login_success_payload(user, device_id, "Kayıt başarılı."))
    except Exception:
        db.session.rollback()
        app.logger.exception("Register verify failed")
        return jsonify({"ok": False, "message": "Doğrulama tamamlanamadı."}), 500


@app.route("/login", methods=["POST"])
def login():
    data = request.get_json() or {}
    identifier = (data.get("username") or data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    device_id = data.get("deviceId") or request.headers.get("X-Nexa-Device")
    user = user_by_login_identifier(identifier)
    if not user and password:
        user = user_by_username_typo(identifier, password)

    if not user or not check_password_hash(user.password_hash, password):
        return jsonify({"ok": False, "message": "Kullanıcı adı veya şifre hatalı."}), 401

    if user.two_factor_enabled:
        if not user.email_normalized:
            return jsonify({"ok": False, "message": "2FA için Gmail gerekli. Yöneticiyle iletişime geç."}), 400
        _, code, sent = create_email_verification("login_2fa", user.email, user.email_normalized, username=user.username)
        response = {
            "ok": True,
            "requiresTwoFactor": True,
            "message": "Gmail adresine giriş doğrulama kodu gönderdik.",
            "username": user.username,
            "maskedEmail": mask_email(user.email_normalized or user.email),
            "resendAfter": TWO_FACTOR_RESEND_SECONDS,
        }
        if not sent:
            if expose_verification_codes():
                response["message"] += " Mail ayarları eksik olduğu için kod geliştirme modunda gösteriliyor."
                response["devCode"] = code
            else:
                response["message"] += " Doğrulama e-postası gönderilemedi; lütfen yeniden dene."
        return jsonify(response)

    return jsonify(login_success_payload(user, device_id))


@app.route("/login/2fa/resend", methods=["POST"])
def login_two_factor_resend():
    data = request.get_json() or {}
    username = (data.get("username") or "").strip().lower()
    user = db.session.get(User, username)
    if not user or not user.two_factor_enabled or not user.email_normalized:
        return jsonify({"ok": False, "message": "Doğrulama oturumu bulunamadı."}), 404
    latest = latest_email_verification("login_2fa", user.username, user.email_normalized)
    wait_seconds = verification_resend_wait_seconds(latest)
    if wait_seconds:
        return jsonify({"ok": False, "message": f"Yeni kod için {wait_seconds} saniye bekle.", "retryAfter": wait_seconds}), 429
    _, code, sent = create_email_verification("login_2fa", user.email, user.email_normalized, username=user.username)
    response = {
        "ok": True,
        "message": "Yeni doğrulama kodu Gmail adresine gönderildi.",
        "maskedEmail": mask_email(user.email_normalized or user.email),
        "resendAfter": TWO_FACTOR_RESEND_SECONDS,
    }
    if not sent:
        if expose_verification_codes():
            response["message"] += " Mail ayarları eksik olduğu için kod geliştirme modunda gösteriliyor."
            response["devCode"] = code
        else:
            response["message"] += " Doğrulama e-postası gönderilemedi; lütfen yeniden dene."
    return jsonify(response)


@app.route("/login/2fa", methods=["POST"])
def login_two_factor():
    data = request.get_json() or {}
    username = (data.get("username") or "").strip().lower()
    code = (data.get("code") or "").strip()
    device_id = data.get("deviceId") or request.headers.get("X-Nexa-Device")
    user = db.session.get(User, username)
    if not user or not user.two_factor_enabled:
        return jsonify({"ok": False, "message": "Doğrulama oturumu bulunamadı."}), 404
    verification = EmailVerification.query.filter_by(
        purpose="login_2fa",
        username=username,
        email_normalized=user.email_normalized,
    ).order_by(EmailVerification.created_at.desc()).first()
    if not verification or is_past(verification.expires_at):
        return jsonify({"ok": False, "message": "Kod bulunamadı veya süresi doldu."}), 400
    if verification.attempts >= 5:
        return jsonify({"ok": False, "message": "Çok fazla yanlış deneme. Yeniden giriş yap."}), 429
    if not check_password_hash(verification.code_hash, code):
        verification.attempts += 1
        db.session.commit()
        return jsonify({"ok": False, "message": "Doğrulama kodu hatalı."}), 400
    db.session.delete(verification)
    return jsonify(login_success_payload(user, device_id))


@app.route("/password/forgot/start", methods=["POST"])
def forgot_password_start():
    data = request.get_json() or {}
    identifier = (data.get("identifier") or data.get("username") or data.get("email") or "").strip()
    user = user_by_login_identifier(identifier)
    if not user:
        return jsonify({"ok": False, "message": "Bu kullanıcı adı veya Gmail ile kayıtlı hesap bulunamadı."}), 404
    if not user.email_normalized:
        return jsonify({"ok": False, "message": "Bu hesapta doğrulanmış Gmail yok. Destek talebi oluştur."}), 400

    _, code, sent = create_email_verification("forgot", user.email, user.email_normalized, username=user.username)
    response = verification_response("Gmail adresine şifre sıfırlama kodu gönderdik.", code, sent)
    return response


@app.route("/password/forgot/verify", methods=["POST"])
def forgot_password_verify():
    data = request.get_json() or {}
    identifier = (data.get("identifier") or data.get("username") or data.get("email") or "").strip()
    code = (data.get("code") or "").strip()
    new_password = data.get("password") or ""
    user = user_by_login_identifier(identifier)
    if not user:
        return jsonify({"ok": False, "message": "Kullanıcı bulunamadı."}), 404
    email_normalized = user.email_normalized

    password_problem = password_error(user.username, new_password)
    if password_problem:
        return jsonify({"ok": False, "message": password_problem}), 400

    verification = EmailVerification.query.filter_by(
        purpose="forgot",
        username=user.username,
        email_normalized=email_normalized,
    ).order_by(EmailVerification.created_at.desc()).first()

    if not verification or is_past(verification.expires_at):
        return jsonify({"ok": False, "message": "Kod bulunamadı veya süresi doldu."}), 400

    if verification.attempts >= 5:
        return jsonify({"ok": False, "message": "Çok fazla yanlış deneme yaptın. Yeni kod iste."}), 429

    if not check_password_hash(verification.code_hash, code):
        verification.attempts += 1
        db.session.commit()
        return jsonify({"ok": False, "message": "Doğrulama kodu hatalı."}), 400

    user.password_hash = generate_password_hash(new_password)
    db.session.delete(verification)
    db.session.commit()
    return jsonify({"ok": True, "message": "Şifre değiştirildi."})


@app.route("/account/<username>/password", methods=["POST"])
def change_password(username):
    data = request.get_json() or {}
    username = username.strip().lower()
    current_password = data.get("currentPassword") or ""
    new_password = data.get("newPassword") or ""
    user = db.session.get(User, username)

    if not user or not check_password_hash(user.password_hash, current_password):
        return jsonify({"ok": False, "message": "Mevcut şifre hatalı."}), 401

    password_problem = password_error(username, new_password)
    if password_problem:
        return jsonify({"ok": False, "message": password_problem}), 400

    user.password_hash = generate_password_hash(new_password)
    db.session.commit()
    return jsonify({"ok": True, "message": "Şifre değiştirildi."})


@app.route("/account/<username>/email/start", methods=["POST"])
def change_email_start(username):
    data = request.get_json() or {}
    username = username.strip().lower()
    password = data.get("password") or ""
    email = (data.get("email") or "").strip()
    user = db.session.get(User, username)

    if not user or not check_password_hash(user.password_hash, password):
        return jsonify({"ok": False, "message": "Şifre hatalı."}), 401

    email_problem = email_error(email)
    if email_problem:
        return jsonify({"ok": False, "message": email_problem}), 400

    email, email_normalized = normalize_email(email)
    if email_exists(email_normalized, except_username=username):
        return jsonify({"ok": False, "message": "Bu Gmail zaten başka bir hesapta kullanılıyor."}), 409

    _, code, sent = create_email_verification("email_change", email, email_normalized, username=username)
    return verification_response("Yeni Gmail adresine doğrulama kodu gönderdik.", code, sent)


@app.route("/account/<username>/email/verify", methods=["POST"])
def change_email_verify(username):
    data = request.get_json() or {}
    username = username.strip().lower()
    email = (data.get("email") or "").strip()
    code = (data.get("code") or "").strip()
    email, email_normalized = normalize_email(email)
    user = db.session.get(User, username)
    if not user:
        return jsonify({"ok": False, "message": "Kullanıcı bulunamadı."}), 404

    if email_exists(email_normalized, except_username=username):
        return jsonify({"ok": False, "message": "Bu Gmail zaten başka bir hesapta kullanılıyor."}), 409

    verification = EmailVerification.query.filter_by(
        purpose="email_change",
        username=username,
        email_normalized=email_normalized,
    ).order_by(EmailVerification.created_at.desc()).first()

    if not verification or is_past(verification.expires_at):
        return jsonify({"ok": False, "message": "Kod bulunamadı veya süresi doldu."}), 400

    if verification.attempts >= 5:
        return jsonify({"ok": False, "message": "Çok fazla yanlış deneme yaptın. Yeni kod iste."}), 429

    if not check_password_hash(verification.code_hash, code):
        verification.attempts += 1
        db.session.commit()
        return jsonify({"ok": False, "message": "Doğrulama kodu hatalı."}), 400

    user.email = verification.email
    user.email_normalized = verification.email_normalized
    user.email_verified = True
    db.session.delete(verification)
    db.session.commit()
    return jsonify({"ok": True, "message": "Gmail değiştirildi.", "user": private_user(username)})


@app.route("/support/access-request", methods=["POST"])
def support_access_request():
    data = request.get_json() or {}
    username = (data.get("username") or "").strip().lower()
    remembered_email = (data.get("rememberedEmail") or data.get("email") or "").strip()
    description = re.sub(r"\s+", " ", (data.get("description") or "").strip())

    username_problem = username_error(username)
    if username_problem:
        return jsonify({"ok": False, "message": username_problem}), 400
    if len(description) < 12:
        return jsonify({"ok": False, "message": "Sorunu en az 12 karakterle açıkla."}), 400
    remembered_email_value = None
    remembered_email_normalized = None
    if remembered_email:
        remembered_email_value, remembered_email_normalized = normalize_email(remembered_email)
        if not remembered_email_value:
            return jsonify({"ok": False, "message": "Hatırladığın Gmail geçerli görünmüyor."}), 400

    request_row = SupportRequest(
        id=uuid4().hex,
        username=username,
        remembered_email=remembered_email_value,
        remembered_email_normalized=remembered_email_normalized,
        description=description[:2000],
    )
    db.session.add(request_row)
    db.session.commit()
    return jsonify({"ok": True, "message": "Destek talebin kaydedildi. Hesap bilgilerin incelenebilir durumda."})


@app.route("/account/<username>/profile", methods=["POST"])
def update_profile(username):
    data = request.get_json() or {}
    username = username.strip().lower()
    user = db.session.get(User, username)
    if not user:
        return jsonify({"ok": False, "message": "Kullanıcı bulunamadı."}), 404

    display_name, display_name_error = normalize_display_name({**data, "displayName": data.get("displayName") or user.display_name})
    if display_name_error:
        return jsonify({"ok": False, "message": display_name_error}), 400
    about = (data.get("about") or user.about or "").strip()
    profile_image = data.get("profileImage")
    avatar = re.sub(r"\s+", "", str(data.get("avatar") or "")).strip()
    avatar_gradient = str(data.get("avatarGradient") or "").strip()

    if len(display_name) < 2 or len(display_name) > 80:
        return jsonify({"ok": False, "message": "Görünen ad 2-40 karakter olmalı."}), 400

    if len(about) > 180:
        return jsonify({"ok": False, "message": "Hakkımda yazısı en fazla 180 karakter olmalı."}), 400

    user.display_name = display_name
    if avatar:
        user.avatar = tr_upper(avatar[:5])
    else:
        user.avatar = tr_upper(display_name[:2])
    user.avatar_gradient = avatar_gradient if avatar_gradient in AVATAR_GRADIENTS else None
    user.about = about or "NexaLine kullanıyorum."
    if isinstance(profile_image, str):
        if profile_image and not profile_image.startswith("data:image/"):
            return jsonify({"ok": False, "message": "Profil fotoğrafı sadece resim olabilir."}), 400
        if len(profile_image) > 1_500_000:
            return jsonify({"ok": False, "message": "Profil fotoğrafı 1.5 MB altında olmalı."}), 400
        user.profile_image = profile_image or None

    maybe_award_profile_completion(user)
    db.session.commit()
    broadcast_presence()

    for chat in Chat.query.all():
        if user_can_see_chat(chat, username):
            for member in chat_member_names(chat):
                for sid in connected_sids_for(member):
                    socketio.emit("chat:upsert", chat_for_user(chat, member), room=sid)

    return jsonify({"ok": True, "message": "Profil güncellendi.", "user": private_user(username)})


def replace_username_in_json(value, old_username, new_username):
    if isinstance(value, str):
        return new_username if value == old_username else value
    if isinstance(value, list):
        return [replace_username_in_json(item, old_username, new_username) for item in value]
    if isinstance(value, dict):
        return {
            (new_username if key == old_username else key): replace_username_in_json(item, old_username, new_username)
            for key, item in value.items()
        }
    return value


@app.route("/account/<username>/username", methods=["POST"])
def change_username(username):
    data = request.get_json() or {}
    old_username = username.strip().lower()
    new_username = (data.get("newUsername") or "").strip().lower()
    user = db.session.get(User, old_username)
    if not user:
        return jsonify({"ok": False, "message": "Kullanıcı bulunamadı."}), 404

    problem = username_error(new_username)
    if problem:
        return jsonify({"ok": False, "message": problem}), 400
    if new_username == old_username:
        return jsonify({"ok": True, "message": "Kullanıcı adı zaten güncel.", "user": private_user(old_username)})
    if db.session.get(User, new_username):
        return jsonify({"ok": False, "message": "Bu kullanıcı adı zaten kullanılıyor."}), 409

    try:
        user_values = {
            column.name: getattr(user, column.name)
            for column in User.__table__.columns
            if column.name != "username"
        }
        db.session.add(User(username=new_username, **user_values))
        db.session.flush()

        username_columns = [
            (DeviceSession, DeviceSession.username),
            (PushSubscription, PushSubscription.username),
            (ChatMember, ChatMember.username),
            (Message, Message.sender),
            (Message, Message.deleted_by),
            (ScheduledMessage, ScheduledMessage.sender),
            (Story, Story.username),
            (StoryView, StoryView.viewer_username),
            (UpdatePost, UpdatePost.username),
            (CallLog, CallLog.caller),
            (EmailVerification, EmailVerification.username),
            (SupportRequest, SupportRequest.username),
            (BlockedUser, BlockedUser.blocker),
            (BlockedUser, BlockedUser.blocked),
            (ContactRequest, ContactRequest.from_username),
            (ContactRequest, ContactRequest.to_username),
            (GroupInvite, GroupInvite.inviter),
            (GroupInvite, GroupInvite.invitee),
            (HiddenChat, HiddenChat.username),
            (ChatArchive, ChatArchive.username),
            (PointLedger, PointLedger.username),
            (AiTask, AiTask.username),
            (AiMemory, AiMemory.username),
            (Community, Community.owner),
            (CommunityMember, CommunityMember.username),
            (CommunityAnnouncement, CommunityAnnouncement.author),
            (VaultItem, VaultItem.username),
        ]
        for model, column in username_columns:
            model.query.filter(column == old_username).update({column: new_username}, synchronize_session=False)

        json_columns = [
            (Message, ("attachment", "reply_to", "read_by", "reactions", "versions", "deleted_for")),
            (ScheduledMessage, ("attachment", "reply_to")),
            (Story, ("attachment",)),
            (UpdatePost, ("media", "liked_by")),
            (ChatArchive, ("messages",)),
            (PointLedger, ("meta",)),
            (AiMemory, ("meta",)),
            (VaultItem, ("payload",)),
        ]
        for model, fields in json_columns:
            for row in model.query.all():
                changed = False
                for field in fields:
                    current = getattr(row, field)
                    updated = replace_username_in_json(current, old_username, new_username)
                    if updated != current:
                        setattr(row, field, updated)
                        changed = True
                if changed:
                    db.session.add(row)

        Chat.query.filter(Chat.title == old_username).update({Chat.title: new_username}, synchronize_session=False)
        db.session.delete(user)
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.exception("Kullanıcı adı değiştirilemedi")
        return jsonify({"ok": False, "message": "Kullanıcı adı güncellenirken bağlı kayıtlar taşınamadı."}), 500

    for sid, connected_username in list(connections.items()):
        if connected_username == old_username:
            connections[sid] = new_username
    for users in typing_users.values():
        if old_username in users:
            users.discard(old_username)
            users.add(new_username)

    db.session.expire_all()
    broadcast_presence()
    return jsonify({
        "ok": True,
        "message": "Kullanıcı adı güncellendi.",
        "user": private_user(new_username),
    })


@app.route("/account/<username>/privacy", methods=["POST"])
def update_privacy(username):
    data = request.get_json() or {}
    username = username.strip().lower()
    user = db.session.get(User, username)
    if not user:
        return jsonify({"ok": False, "message": "Kullanıcı bulunamadı."}), 404

    privacy = data.get("privacy") or {}
    incoming_scopes = data.get("privacyScopes") or privacy.get("scopes") or {}
    current_scopes = privacy_scopes_for(user)
    if isinstance(incoming_scopes, dict):
        for key in DEFAULT_PRIVACY_SCOPES:
            value = str(incoming_scopes.get(key) or current_scopes.get(key) or DEFAULT_PRIVACY_SCOPES[key]).strip()
            current_scopes[key] = value if value in PRIVACY_SCOPE_VALUES else DEFAULT_PRIVACY_SCOPES[key]

    if not isinstance(incoming_scopes, dict) or not incoming_scopes:
        current_scopes["lastSeen"] = "nobody" if privacy.get("lastSeenHidden") else "everyone"
        current_scopes["online"] = "nobody" if privacy.get("onlineHidden") else "everyone"
        current_scopes["email"] = "nobody" if privacy.get("emailHidden", True) else "friends"

    user.privacy_settings = current_scopes
    user.hide_last_seen = current_scopes.get("lastSeen") == "nobody"
    user.hide_online = current_scopes.get("online") == "nobody"
    user.disable_read_receipts = bool(privacy.get("readReceiptsOff"))
    user.hide_email = current_scopes.get("email") == "nobody"
    db.session.commit()
    broadcast_presence()
    return jsonify({"ok": True, "message": "Gizlilik ayarları güncellendi.", "user": private_user(username)})


@app.route("/account/<username>/preferences", methods=["POST"])
def update_preferences(username):
    data = request.get_json() or {}
    username = username.strip().lower()
    user = db.session.get(User, username)
    if not user:
        return jsonify({"ok": False, "message": "Kullanıcı bulunamadı."}), 404

    theme = (data.get("theme") or user.theme_preference or "dark").strip().lower()
    font_size = (data.get("fontSize") or user.font_size_preference or "medium").strip().lower()
    sound = (data.get("notificationSound") or user.notification_sound or "classic").strip().lower()
    if theme not in {"dark", "light", "system"}:
        theme = "dark"
    if font_size not in {"small", "medium", "large"}:
        font_size = "medium"
    if sound not in {"classic", "notify", "glass", "ripple", "neon", "soft", "bright", "deep", "calm", "pulse", "silent", "nexaline", "crystal", "alert", "ding", "pop", "arcade"}:
        sound = "classic"
    user.theme_preference = theme
    user.font_size_preference = font_size
    user.notification_sound = sound
    db.session.commit()
    return jsonify({"ok": True, "message": "Tercihler kaydedildi.", "user": private_user(username)})


BADGE_DEFINITIONS = [
    {"id": "first_step", "title": "İlk Adım", "description": "İlk puanını kazan.", "reward": "+25", "target": 1, "reason": "any"},
    {"id": "chat_master", "title": "Sohbet Ustası", "description": "100 mesaj puanı kazan.", "reward": "Sohbet rozeti", "target": 100, "reason": "message"},
    {"id": "early_bird", "title": "Erken Kuş", "description": "5 günlük giriş puanı kazan.", "reward": "+50", "target": 5, "reason": "daily_login"},
    {"id": "helpful", "title": "Yardımsever", "description": "5 arkadaşlık kabul puanı kazan.", "reward": "Profil rozeti", "target": 5, "reason": "friend_accept"},
    {"id": "loyal", "title": "Sadık Üye", "description": "10 günlük giriş serisi oluştur.", "reward": "Sadakat rozeti", "target": 10, "reason": "daily_streak"},
    {"id": "explorer", "title": "Keşifçi", "description": "3 farklı özelliği kullan.", "reward": "Keşif teması", "target": 3, "reason": "feature_mix"},
    {"id": "voice_master", "title": "Sesli Oda Ustası", "description": "3 sesli odaya katıl.", "reward": "Sesli oda rozeti", "target": 3, "reason": "voice_room_join"},
    {"id": "community_leader", "title": "Topluluk Lideri", "description": "Bir topluluk oluştur veya 3 topluluğa katıl.", "reward": "Lider rozeti", "target": 3, "reason": "community_join"},
    {"id": "ai_friend", "title": "AI Dostu", "description": "Nexa AI ile 5 gün konuş.", "reward": "+100", "target": 5, "reason": "ai_chat"},
    *[
        {
            "id": item["id"],
            "title": item["title"],
            "description": f"{item['threshold']:,} puana ulaş.",
            "reward": item["reward"],
            "target": item["threshold"],
            "reason": "points",
        }
        for item in POINT_MILESTONES
    ],
]


QUEST_DEFINITIONS = [
    {"id": "daily_messages", "type": "daily", "title": "3 kişiye mesaj gönder", "description": "Bugün en az 3 mesaj gönder.", "reward": POINT_RULES["quest_daily"], "reason": "message", "target": 3},
    {"id": "daily_ai", "type": "daily", "title": "AI ile sohbet et", "description": "Bugün Nexa AI'ya bir komut ver.", "reward": POINT_RULES["quest_daily"], "reason": "ai_chat", "target": 1},
    {"id": "weekly_voice", "type": "weekly", "title": "Bir sesli odaya katıl", "description": "Bu hafta canlı bir odaya gir.", "reward": POINT_RULES["quest_weekly"], "reason": "voice_room_join", "target": 1},
    {"id": "weekly_community", "type": "weekly", "title": "Bir topluluğa katıl", "description": "Bu hafta bir topluluğa katıl veya oluştur.", "reward": POINT_RULES["quest_weekly"], "reason": "community_join", "target": 1},
    {"id": "special_story", "type": "special", "title": "Durum paylaş", "description": "Bir güncelleme paylaş.", "reward": POINT_RULES["quest_special"], "reason": "story", "target": 1},
]


def ledger_count(username, reason=None, since=None):
    query = PointLedger.query.filter_by(username=username)
    if reason and reason != "any":
        query = query.filter_by(reason=reason)
    if since:
        query = query.filter(PointLedger.created_at >= since)
    return query.count()


def daily_streak(username):
    rows = PointLedger.query.filter_by(username=username, reason="daily_login").order_by(PointLedger.created_at.desc()).all()
    days = {row.created_at.date() for row in rows}
    today = datetime.now(timezone.utc).date()
    streak = 0
    while today - timedelta(days=streak) in days:
        streak += 1
    return streak


def badge_progress(username, points):
    feature_reasons = {row.reason for row in PointLedger.query.filter_by(username=username).all()}
    result = []
    streak = daily_streak(username)
    for badge in BADGE_DEFINITIONS:
        reason = badge["reason"]
        if reason == "points":
            value = points
        elif reason == "daily_streak":
            value = streak
        elif reason == "feature_mix":
            value = len(feature_reasons.intersection({"message", "story", "voice_room_join", "community_join", "ai_chat"}))
        elif reason == "any":
            value = PointLedger.query.filter_by(username=username).count()
        else:
            value = ledger_count(username, reason)
        target = max(1, int(badge["target"]))
        result.append({
            **badge,
            "current": value,
            "progress": min(100, int((value / target) * 100)),
            "unlocked": value >= target,
        })
    return result


def quest_window(quest_type):
    now = datetime.now(timezone.utc)
    if quest_type == "daily":
        return datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    if quest_type == "weekly":
        start = now - timedelta(days=now.weekday())
        return datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    return None


def quest_progress(username):
    rows = []
    for quest in QUEST_DEFINITIONS:
        since = quest_window(quest["type"])
        count = ledger_count(username, quest["reason"], since)
        target = int(quest["target"])
        claimed_key = f"{quest['id']}:{since.date().isoformat() if since else 'all'}"
        claimed = PointLedger.query.filter_by(username=username, reason="quest").filter(PointLedger.meta["uniqueKey"].as_string() == claimed_key).first() is not None
        rows.append({
            **quest,
            "current": count,
            "progress": min(100, int((count / max(1, target)) * 100)),
            "completed": count >= target,
            "claimed": claimed,
            "claimKey": claimed_key,
        })
    return rows


def leaderboard_for(username):
    rows = User.query.order_by(User.points.desc(), User.created_at.asc()).limit(50).all()
    global_rows = [
        {"rank": index + 1, "user": public_user(row.username, username), "points": user_points(row.username)}
        for index, row in enumerate(rows)
    ]
    friend_names = {username}
    for request_row in ContactRequest.query.filter(
        ContactRequest.status == "accepted",
        db.or_(ContactRequest.from_username == username, ContactRequest.to_username == username),
    ):
        friend_names.add(request_row.from_username)
        friend_names.add(request_row.to_username)
    friend_rows = [item for item in global_rows if item["user"]["username"] in friend_names]
    return {"global": global_rows, "country": global_rows, "friends": friend_rows}


@app.route("/games/<username>/state", methods=["GET", "POST"])
def nexa_play_state_route(username):
    username = username.strip().lower()
    if not db.session.get(User, username):
        return jsonify({"ok": False, "message": "Kullanıcı bulunamadı."}), 404
    if request.method == "GET":
        return jsonify({"ok": True, "state": nexa_play_state(username)})
    try:
        state_data = save_nexa_play_state(username, request.get_json() or {})
    except (TypeError, ValueError) as error:
        db.session.rollback()
        return jsonify({"ok": False, "message": str(error)}), 400
    return jsonify({"ok": True, "state": state_data})


@app.route("/games/<username>/hint", methods=["POST"])
def nexa_play_hint_route(username):
    username = username.strip().lower()
    user = db.session.get(User, username)
    if not user:
        return jsonify({"ok": False, "message": "Kullanıcı bulunamadı."}), 404
    data = request.get_json() or {}
    game_id = str(data.get("game") or "").strip().lower()
    if game_id not in NEXA_PLAY_GAMES:
        return jsonify({"ok": False, "message": "Oyun bulunamadı."}), 404
    fallbacks = {
        "chess": "Taşlarını geliştir, şahını güvende tut ve rakibin savunmasız taşlarını kontrol et.",
        "solitaire": "Önce kapalı kart açan hamleleri, sonra boş sütun oluşturan hamleleri tercih et.",
        "2048": "En büyük taşı bir köşede tut; mümkün olduğunca iki ana yönü kullan.",
        "block-blast": "Büyük parçalar için alan bırak ve aynı anda satır ile sütun temizleyen yerleşimleri ara.",
    }
    hint_keywords = {
        "chess": {"taş", "şah", "piyon", "satranç", "hamle", "rakip"},
        "solitaire": {"kart", "sütun", "temel", "kapalı", "solitaire"},
        "2048": {"sayı", "köşe", "birleştir", "yön", "2048"},
        "block-blast": {"block", "parça", "satır", "sütun", "alan", "tahta"},
    }
    state_summary = json.dumps(data.get("state") or {}, ensure_ascii=False)[:2400]
    prompt = (
        f"Nexa Play içindeki {game_id} oyunu için tek cümlelik, uygulanabilir Türkçe bir hamle ipucu ver. "
        f"Oyun durumu: {state_summary}"
    )
    context = ai_context_for_user(username, chat_id=f"game:{game_id}", prompt=prompt)
    try:
        reply, provider, _research = generate_ai_reply(prompt, context, [])
        hint = re.sub(r"\s+", " ", (reply or "").strip())[:360]
        normalized_hint = hint.casefold()
        if not hint or not any(keyword in normalized_hint for keyword in hint_keywords[game_id]):
            raise ValueError("Boş AI yanıtı")
    except Exception:
        app.logger.exception("Nexa Play hint provider failed")
        hint = fallbacks[game_id]
        provider = "local"
    return jsonify({"ok": True, "hint": hint, "provider": provider})


@app.route("/points/<username>")
def points_state(username):
    username = username.strip().lower()
    user = db.session.get(User, username)
    if not user:
        return jsonify({"ok": False, "message": "Kullanıcı bulunamadı."}), 404
    points = user_points(username)
    return jsonify({
        "ok": True,
        "points": points,
        "level": point_level(points),
        "rules": POINT_RULES,
        "ledger": point_ledger_for(username, 80),
        "badges": badge_progress(username, points),
        "quests": quest_progress(username),
        "leaderboard": leaderboard_for(username),
        "dailyStreak": daily_streak(username),
    })


@app.route("/points/<username>/quests/<quest_id>/claim", methods=["POST"])
def claim_point_quest(username, quest_id):
    username = username.strip().lower()
    if not db.session.get(User, username):
        return jsonify({"ok": False, "message": "Kullanıcı bulunamadı."}), 404
    quests = {quest["id"]: quest for quest in quest_progress(username)}
    quest = quests.get(quest_id)
    if not quest:
        return jsonify({"ok": False, "message": "Görev bulunamadı."}), 404
    if not quest["completed"]:
        return jsonify({"ok": False, "message": "Görev henüz tamamlanmadı."}), 400
    if quest["claimed"]:
        return jsonify({"ok": False, "message": "Bu görev ödülü zaten alındı."}), 400
    add_points_once(username, int(quest["reward"]), "quest", quest["claimKey"], {"questId": quest_id})
    db.session.commit()
    return points_state(username)


@app.route("/nearby/<username>", methods=["GET", "POST"])
def nearby_state(username):
    username = username.strip().lower()
    user = db.session.get(User, username)
    if not user:
        return jsonify({"ok": False, "message": "Kullanıcı bulunamadı."}), 404
    if request.method == "POST":
        data = request.get_json() or {}
        user.nearby_enabled = bool(data.get("enabled"))
        if user.nearby_enabled:
            try:
                user.last_lat = float(data.get("lat"))
                user.last_lng = float(data.get("lng"))
            except (TypeError, ValueError):
                return jsonify({"ok": False, "message": "Konum okunamadı."}), 400
        else:
            user.last_lat = None
            user.last_lng = None
        db.session.commit()
    return jsonify({"ok": True, "enabled": bool(user.nearby_enabled), "users": nearby_users_for(username)})


@app.route("/communities", methods=["GET", "POST"])
def communities_route():
    if request.method == "GET":
        username = (request.args.get("username") or "").strip().lower()
        return jsonify({"ok": True, "communities": communities_for(username or None)})
    data = request.get_json() or {}
    username = (data.get("username") or "").strip().lower()
    user = db.session.get(User, username)
    if not user:
        return jsonify({"ok": False, "message": "Önce giriş yapmalısın."}), 401
    title = re.sub(r"\s+", " ", (data.get("title") or "").strip())[:160]
    if len(title) < 2:
        return jsonify({"ok": False, "message": "Topluluk adı gerekli."}), 400
    community = Community(
        id=uuid4().hex,
        title=title,
        description=(data.get("description") or "").strip()[:800],
        category=(data.get("category") or "Genel").strip()[:80],
        privacy=(data.get("privacy") or "public").strip().lower()[:30],
        owner=username,
    )
    db.session.add(community)
    db.session.flush()
    db.session.add(CommunityMember(community_id=community.id, username=username, role="owner"))
    add_points_once(username, POINT_RULES["community_join"], "community_join", f"community:{community.id}", {"communityId": community.id, "role": "owner"})
    db.session.commit()
    return jsonify({"ok": True, "message": "Topluluk oluşturuldu.", "community": community_to_dict(community, username), "communities": communities_for(username)})


@app.route("/communities/<community_id>/join", methods=["POST"])
def community_join(community_id):
    data = request.get_json() or {}
    username = (data.get("username") or "").strip().lower()
    community = db.session.get(Community, community_id)
    if not db.session.get(User, username) or not community:
        return jsonify({"ok": False, "message": "Topluluk veya kullanıcı bulunamadı."}), 404
    row = CommunityMember.query.filter_by(community_id=community_id, username=username).first()
    if not row:
        db.session.add(CommunityMember(community_id=community_id, username=username, role="member"))
        add_points_once(username, POINT_RULES["community_join"], "community_join", f"community:{community_id}", {"communityId": community_id})
        db.session.commit()
    return jsonify({"ok": True, "message": "Topluluğa katıldın.", "community": community_to_dict(community, username), "communities": communities_for(username)})


@app.route("/communities/<community_id>/announcements", methods=["POST"])
def community_announcement_create(community_id):
    data = request.get_json() or {}
    username = (data.get("username") or "").strip().lower()
    body = re.sub(r"\s+", " ", (data.get("body") or "").strip())[:1200]
    community = db.session.get(Community, community_id)
    member = CommunityMember.query.filter_by(community_id=community_id, username=username).first()
    if not community or not member:
        return jsonify({"ok": False, "message": "Topluluk üyeliği gerekli."}), 403
    if member.role not in {"owner", "admin", "moderator"}:
        return jsonify({"ok": False, "message": "Duyuru için yönetici yetkisi gerekli."}), 403
    if len(body) < 2:
        return jsonify({"ok": False, "message": "Duyuru metni gerekli."}), 400
    db.session.add(CommunityAnnouncement(id=uuid4().hex, community_id=community_id, author=username, body=body))
    db.session.commit()
    return jsonify({"ok": True, "message": "Duyuru yayınlandı.", "community": community_to_dict(community, username), "communities": communities_for(username)})


def vault_items_for(username):
    rows = VaultItem.query.filter_by(username=username).order_by(VaultItem.created_at.desc()).limit(120).all()
    return [
        {"id": row.id, "kind": row.kind, "title": row.title, "payload": row.payload or {}, "createdAt": to_iso(row.created_at)}
        for row in rows
    ]


def vault_message_kind(attachment, body):
    if not isinstance(attachment, dict):
        return "message" if str(body or "").strip() else None
    attachment_type = str(attachment.get("type") or "").lower()
    if attachment_type == "bundle":
        items = attachment.get("items") or []
        if items and all(vault_message_kind(item, "") in {"image", "video", "audio", "media"} for item in items):
            return "media"
        return None
    if attachment_type.startswith("image/") or attachment_type == "gif":
        return "image"
    if attachment_type.startswith("video/"):
        return "video"
    if attachment_type.startswith("audio/"):
        return "audio"
    return None


def vault_message_title(message, kind):
    sender_name = message.sender_user.display_name if message.sender_user else message.sender
    labels = {
        "message": "Mesaj",
        "image": "Görsel",
        "video": "Video",
        "audio": "Sesli mesaj",
        "media": "Medya paketi",
    }
    return f"{sender_name} - {labels.get(kind, 'Gönderi')}"[:140]


def verify_vault_pin(user, pin):
    now = datetime.now(timezone.utc)
    locked_until = user.vault_locked_until
    if locked_until and locked_until.tzinfo is None:
        locked_until = locked_until.replace(tzinfo=timezone.utc)
    if locked_until and locked_until > now:
        return False, f"Kasa kilitli. {int((locked_until - now).total_seconds())} sn sonra tekrar dene."
    if not user.vault_pin_hash:
        return False, "Önce kasa PIN'i oluştur."
    if check_password_hash(user.vault_pin_hash, str(pin or "")):
        user.vault_failed_attempts = 0
        user.vault_locked_until = None
        return True, ""
    user.vault_failed_attempts = int(user.vault_failed_attempts or 0) + 1
    if user.vault_failed_attempts >= 5:
        user.vault_locked_until = now + timedelta(minutes=1)
        user.vault_failed_attempts = 0
    return False, "PIN hatalı."


@app.route("/vault/<username>/setup", methods=["POST"])
def vault_setup(username):
    data = request.get_json() or {}
    username = username.strip().lower()
    user = db.session.get(User, username)
    pin = str(data.get("pin") or "")
    if not user:
        return jsonify({"ok": False, "message": "Kullanıcı bulunamadı."}), 404
    if not re.fullmatch(r"\d{4}", pin):
        return jsonify({"ok": False, "message": "PIN 4 rakam olmalı."}), 400
    user.vault_pin_hash = generate_password_hash(pin)
    user.vault_failed_attempts = 0
    user.vault_locked_until = None
    db.session.commit()
    return jsonify({"ok": True, "message": "Gizli kasa PIN'i oluşturuldu.", "user": private_user(username)})


@app.route("/vault/<username>/unlock", methods=["POST"])
def vault_unlock(username):
    data = request.get_json() or {}
    username = username.strip().lower()
    user = db.session.get(User, username)
    if not user:
        return jsonify({"ok": False, "message": "Kullanıcı bulunamadı."}), 404
    ok, message = verify_vault_pin(user, data.get("pin"))
    db.session.commit()
    if not ok:
        return jsonify({"ok": False, "message": message, "user": private_user(username)}), 403
    return jsonify({"ok": True, "items": vault_items_for(username), "user": private_user(username)})


@app.route("/vault/<username>/reset", methods=["DELETE"])
def vault_reset(username):
    username = username.strip().lower()
    user = db.session.get(User, username)
    if not user:
        return jsonify({"ok": False, "message": "Kullanıcı bulunamadı."}), 404

    device_id = request.headers.get("X-Nexa-Device") or ""
    device = db.session.get(DeviceSession, normalize_device_id(device_id)) if device_id else None
    if not device or device.username != username or device.revoked_at is not None:
        return jsonify({"ok": False, "message": "Bu işlem için aktif cihaz oturumu gerekli."}), 403

    deleted_items = VaultItem.query.filter_by(username=username).delete(synchronize_session=False)
    user.vault_pin_hash = None
    user.vault_failed_attempts = 0
    user.vault_locked_until = None
    db.session.commit()
    return jsonify({
        "ok": True,
        "message": "Gizli kasa sıfırlandı. PIN ve kasa içeriği silindi.",
        "deletedItems": deleted_items,
        "items": [],
        "user": private_user(username),
    })


@app.route("/vault/<username>/items", methods=["POST"])
def vault_item_create(username):
    data = request.get_json() or {}
    username = username.strip().lower()
    user = db.session.get(User, username)
    if not user:
        return jsonify({"ok": False, "message": "Kullanıcı bulunamadı."}), 404
    ok, message = verify_vault_pin(user, data.get("pin"))
    if not ok:
        db.session.commit()
        return jsonify({"ok": False, "message": message, "user": private_user(username)}), 403
    title = re.sub(r"\s+", " ", (data.get("title") or "Kasa notu").strip())[:140]
    kind = (data.get("kind") or "note").strip().lower()[:30]
    payload = data.get("payload") if isinstance(data.get("payload"), dict) else {"text": str(data.get("text") or "")[:4000]}
    db.session.add(VaultItem(id=uuid4().hex, username=username, kind=kind or "note", title=title or "Kasa notu", payload=payload))
    db.session.commit()
    return jsonify({"ok": True, "items": vault_items_for(username), "user": private_user(username)})


@app.route("/vault/<username>/messages/<message_id>", methods=["POST"])
def vault_message_create(username, message_id):
    data = request.get_json() or {}
    username = username.strip().lower()
    user = db.session.get(User, username)
    message = Message.query.options(joinedload(Message.sender_user)).filter_by(id=message_id).first()
    if not user or not message:
        return jsonify({"ok": False, "message": "Mesaj bulunamadı."}), 404

    ok, pin_message = verify_vault_pin(user, data.get("pin"))
    if not ok:
        db.session.commit()
        return jsonify({"ok": False, "message": pin_message, "user": private_user(username)}), 403

    chat = db.session.get(Chat, message.chat_id)
    if not chat or not user_can_see_chat(chat, username) or username in (message.deleted_for or []):
        return jsonify({"ok": False, "message": "Bu mesaja erişimin yok."}), 403
    if message.sender == username:
        return jsonify({"ok": False, "message": "Yalnızca gelen mesajlar kasaya eklenebilir."}), 400
    if message.deleted_at:
        return jsonify({"ok": False, "message": "Silinmiş mesaj kasaya eklenemez."}), 400
    if is_view_once_attachment(message.attachment):
        return jsonify({"ok": False, "message": "Tek görüntülemelik içerik kasaya eklenemez."}), 400

    kind = vault_message_kind(message.attachment, message.body)
    if not kind:
        return jsonify({"ok": False, "message": "Bu içerik türü kasaya eklenemez."}), 400

    existing = next(
        (
            row for row in VaultItem.query.filter_by(username=username).order_by(VaultItem.created_at.desc()).limit(240).all()
            if (row.payload or {}).get("source") == "chat" and (row.payload or {}).get("messageId") == message.id
        ),
        None,
    )
    if existing:
        return jsonify({"ok": True, "message": "Bu gönderi zaten gizli kasada.", "items": vault_items_for(username), "user": private_user(username)})

    attachment_copy = json.loads(json.dumps(message.attachment, ensure_ascii=False)) if message.attachment else None
    sender_name = message.sender_user.display_name if message.sender_user else message.sender
    payload = {
        "source": "chat",
        "messageId": message.id,
        "chatId": message.chat_id,
        "sender": message.sender,
        "senderName": sender_name,
        "body": message.body or "",
        "attachment": attachment_copy,
        "messageCreatedAt": to_iso(message.created_at),
        "savedAt": now_iso(),
    }
    db.session.add(VaultItem(
        id=uuid4().hex,
        username=username,
        kind=kind,
        title=vault_message_title(message, kind),
        payload=payload,
    ))
    db.session.commit()
    return jsonify({"ok": True, "message": "Gönderi gizli kasaya eklendi.", "items": vault_items_for(username), "user": private_user(username)})


@app.route("/vault/<username>/items/<item_id>", methods=["DELETE"])
def vault_item_delete(username, item_id):
    data = request.get_json(silent=True) or {}
    username = username.strip().lower()
    user = db.session.get(User, username)
    item = db.session.get(VaultItem, item_id)
    if not user or not item or item.username != username:
        return jsonify({"ok": False, "message": "Kasa öğesi bulunamadı."}), 404
    ok, message = verify_vault_pin(user, data.get("pin"))
    if not ok:
        db.session.commit()
        return jsonify({"ok": False, "message": message, "user": private_user(username)}), 403
    db.session.delete(item)
    db.session.commit()
    return jsonify({"ok": True, "items": vault_items_for(username), "user": private_user(username)})


@app.route("/account/<username>/security/two-factor", methods=["POST"])
def update_two_factor(username):
    data = request.get_json() or {}
    username = username.strip().lower()
    user = db.session.get(User, username)
    password = data.get("password") or ""
    if not user:
        return jsonify({"ok": False, "message": "Kullanıcı bulunamadı."}), 404
    if password and not check_password_hash(user.password_hash, password):
        return jsonify({"ok": False, "message": "Şifre hatalı."}), 401
    user.two_factor_enabled = bool(data.get("enabled"))
    db.session.commit()
    return jsonify({"ok": True, "message": "İki adımlı doğrulama güncellendi.", "user": private_user(username)})


@app.route("/account/<username>/devices", methods=["GET"])
def list_devices(username):
    username = username.strip().lower()
    if not db.session.get(User, username):
        return jsonify({"ok": False, "message": "Kullanıcı bulunamadı."}), 404
    device_id = request.args.get("deviceId") or request.headers.get("X-Nexa-Device")
    if device_id:
        upsert_device_session(username, device_id)
        db.session.commit()
    current_device_id = normalize_device_id(device_id) if device_id else ""
    devices = active_device_sessions(username)
    for device in devices:
        device["isCurrent"] = bool(current_device_id and device["id"] == current_device_id)
    return jsonify({"ok": True, "devices": devices})


@app.route("/account/<username>/devices/<device_id>", methods=["DELETE"])
def revoke_device(username, device_id):
    username = username.strip().lower()
    row = db.session.get(DeviceSession, normalize_device_id(device_id))
    if not row or row.username != username:
        return jsonify({"ok": False, "message": "Cihaz bulunamadı."}), 404
    row.revoked_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify({"ok": True, "message": "Cihaz oturumu kapatıldı.", "devices": active_device_sessions(username)})


@app.route("/account/<username>/devices", methods=["DELETE"])
def revoke_other_devices(username):
    username = username.strip().lower()
    if not db.session.get(User, username):
        return jsonify({"ok": False, "message": "Kullanıcı bulunamadı."}), 404
    current_device_id = normalize_device_id(request.headers.get("X-Nexa-Device") or request.args.get("deviceId") or "")
    now = datetime.now(timezone.utc)
    query = DeviceSession.query.filter_by(username=username, revoked_at=None)
    revoked = 0
    for row in query.all():
        if current_device_id and row.id == current_device_id:
            continue
        row.revoked_at = now
        revoked += 1
    db.session.commit()
    devices = active_device_sessions(username)
    for device in devices:
        device["isCurrent"] = bool(current_device_id and device["id"] == current_device_id)
    return jsonify({"ok": True, "message": f"{revoked} cihaz oturumu kapatıldı.", "devices": devices})


@app.route("/account/<username>/blocked", methods=["GET"])
def list_blocked(username):
    username = username.strip().lower()
    if not db.session.get(User, username):
        return jsonify({"ok": False, "message": "Kullanıcı bulunamadı."}), 404
    return jsonify({"ok": True, "blocked": [public_user(item, username) for item in blocked_users_for(username)]})


@app.route("/account/<username>/blocked/<target>", methods=["DELETE"])
def unblock_user(username, target):
    username = username.strip().lower()
    target = target.strip().lower()
    row = BlockedUser.query.filter_by(blocker=username, blocked=target).first()
    if row:
        db.session.delete(row)
        db.session.commit()
        emit_social_updates(username, target)
    return jsonify({"ok": True, "message": "Engel kaldırıldı.", "blocked": [public_user(item, username) for item in blocked_users_for(username)]})


@app.route("/account/<username>/archives/<archive_id>", methods=["POST"])
def read_archive(username, archive_id):
    username = username.strip().lower()

    purge_expired_archives()
    archive = db.session.get(ChatArchive, archive_id)
    if not archive or archive.username != username:
        return jsonify({"ok": False, "message": "Arşiv bulunamadı."}), 404

    return jsonify(
        {
            "ok": True,
            "archive": {
                **archive_to_summary(archive),
                "messages": archive.messages or [],
            },
        }
    )


@app.route("/account/<username>/archives/unlock", methods=["POST"])
def unlock_archives(username):
    username = username.strip().lower()

    return jsonify({"ok": True, "archives": archives_with_messages(username)})


@app.route("/account/<username>/archives/<archive_id>/restore", methods=["POST"])
def restore_archive(username, archive_id):
    username = username.strip().lower()

    purge_expired_archives()
    archive = db.session.get(ChatArchive, archive_id)
    if not archive or archive.username != username:
        return jsonify({"ok": False, "message": "Arşiv bulunamadı."}), 404

    chat = restore_archive_for_user(archive)
    if not chat:
        return jsonify({"ok": False, "message": "Sohbet artik geri yuklenemiyor."}), 404

    db.session.commit()
    return jsonify(
        {
            "ok": True,
            "message": "Sohbet arşivden çıkarıldı.",
            "chat": chat_for_user(chat, username),
            "archives": visible_archives(username),
            "scheduledMessages": visible_scheduled_messages(username),
        }
    )


@app.route("/account/<username>/archives/<archive_id>", methods=["DELETE"])
def delete_archive(username, archive_id):
    username = username.strip().lower()

    archive = db.session.get(ChatArchive, archive_id)
    if not archive or archive.username != username:
        return jsonify({"ok": False, "message": "Arşiv bulunamadı."}), 404

    db.session.delete(archive)
    db.session.commit()
    return jsonify({"ok": True, "archives": visible_archives(username)})


@app.route("/account/<username>", methods=["DELETE"])
def delete_account(username):
    try:
        username = username.strip().lower()
        data = request.get_json() or {}
        password = data.get("password") or ""
        user = db.session.get(User, username)

        if not user or not check_password_hash(user.password_hash, password):
            return jsonify({"ok": False, "message": "Şifre hatalı."}), 401

        user_email_normalized = user.email_normalized
        member_rows = ChatMember.query.filter_by(username=username).all()
        direct_chat_ids = [row.chat_id for row in member_rows if row.chat and row.chat.type == "direct"]
        group_chat_ids = [row.chat_id for row in member_rows if row.chat and row.chat.type == "group"]
        owned_community_ids = [
            row[0]
            for row in db.session.query(Community.id).filter_by(owner=username).all()
        ]

        owned_story_ids = [
            row[0]
            for row in db.session.query(Story.id).filter_by(username=username).all()
        ]
        StoryView.query.filter_by(viewer_username=username).delete(synchronize_session=False)
        if owned_story_ids:
            StoryView.query.filter(StoryView.story_id.in_(owned_story_ids)).delete(synchronize_session=False)
        Story.query.filter_by(username=username).delete(synchronize_session=False)
        UpdatePost.query.filter_by(username=username).delete(synchronize_session=False)
        CallLog.query.filter_by(caller=username).delete(synchronize_session=False)
        Message.query.filter_by(sender=username).delete(synchronize_session=False)
        BlockedUser.query.filter(db.or_(BlockedUser.blocker == username, BlockedUser.blocked == username)).delete(synchronize_session=False)
        ContactRequest.query.filter(db.or_(ContactRequest.from_username == username, ContactRequest.to_username == username)).delete(synchronize_session=False)
        GroupInvite.query.filter(db.or_(GroupInvite.inviter == username, GroupInvite.invitee == username)).delete(synchronize_session=False)
        ScheduledMessage.query.filter_by(sender=username).delete(synchronize_session=False)
        HiddenChat.query.filter_by(username=username).delete(synchronize_session=False)
        ChatArchive.query.filter_by(username=username).delete(synchronize_session=False)
        PointLedger.query.filter_by(username=username).delete(synchronize_session=False)
        AiTask.query.filter_by(username=username).delete(synchronize_session=False)
        AiMemory.query.filter_by(username=username).delete(synchronize_session=False)
        VaultItem.query.filter_by(username=username).delete(synchronize_session=False)
        PushSubscription.query.filter_by(username=username).delete(synchronize_session=False)
        DeviceSession.query.filter_by(username=username).delete(synchronize_session=False)
        EmailVerification.query.filter(
            db.or_(
                EmailVerification.username == username,
                EmailVerification.email_normalized == user_email_normalized,
            )
        ).delete(synchronize_session=False)
        SupportRequest.query.filter_by(username=username).delete(synchronize_session=False)
        CommunityAnnouncement.query.filter_by(author=username).delete(synchronize_session=False)
        CommunityMember.query.filter_by(username=username).delete(synchronize_session=False)
        if owned_community_ids:
            CommunityAnnouncement.query.filter(
                CommunityAnnouncement.community_id.in_(owned_community_ids)
            ).delete(synchronize_session=False)
            CommunityMember.query.filter(
                CommunityMember.community_id.in_(owned_community_ids)
            ).delete(synchronize_session=False)
            Community.query.filter(
                Community.id.in_(owned_community_ids)
            ).delete(synchronize_session=False)
        if direct_chat_ids:
            CallLog.query.filter(CallLog.chat_id.in_(direct_chat_ids)).delete(synchronize_session=False)
            HiddenChat.query.filter(HiddenChat.chat_id.in_(direct_chat_ids)).delete(synchronize_session=False)

        for member in member_rows:
            if member.chat and member.chat.type == "group":
                db.session.delete(member)

        for chat_id in direct_chat_ids:
            chat = db.session.get(Chat, chat_id)
            if chat:
                db.session.delete(chat)

        db.session.delete(user)
        db.session.flush()
        for chat_id in group_chat_ids:
            promote_fallback_group_admin(db.session.get(Chat, chat_id))
        db.session.commit()

        for sid, connected_user in list(connections.items()):
            if connected_user == username:
                connections.pop(sid, None)
                socketio.emit("account:deleted", {"username": username}, room=sid)
        with voice_room_lock:
            for room in voice_rooms.values():
                room.get("participants", {}).pop(username, None)
                room.get("requests", {}).pop(username, None)
                room["comments"] = [
                    item
                    for item in room.get("comments", [])
                    if item.get("username") != username
                ]

        broadcast_presence()
        emit_general_group_updates()
        return jsonify({"ok": True, "message": "Hesap silindi."})
    except Exception:
        db.session.rollback()
        app.logger.exception("Delete account failed")
        return jsonify({"ok": False, "message": "Hesap silinemedi. Render kayıtlarını kontrol et."}), 500


@app.route("/admin/users", methods=["DELETE"])
def delete_all_users():
    admin_error = require_admin()
    if admin_error:
        return admin_error

    try:
        reset_all_user_data()
        socketio.emit("admin:reset", {"message": "Tüm kullanıcılar silindi."}, namespace="/")
        broadcast_presence()
        emit_general_group_updates()
        broadcast_stories()
        return jsonify({"ok": True, "message": "Tüm kullanıcılar silindi."})
    except Exception:
        db.session.rollback()
        app.logger.exception("Delete all users failed")
        return jsonify({"ok": False, "message": "Kullanıcılar silinemedi."}), 500


@app.route("/admin/state")
def admin_state():
    admin_error = require_admin()
    if admin_error:
        return admin_error

    users = [
        public_user(user.username) | {
            "createdAt": to_iso(user.created_at),
            "email": user.email,
            "emailVerified": user.email_verified,
        }
        for user in User.query.order_by(User.created_at.desc()).all()
    ]
    chats = []
    for chat in Chat.query.order_by(Chat.created_at.desc()).all():
        visible_messages = recent_visible_messages(chat.id, None, ADMIN_MESSAGE_LIMIT_PER_CHAT)
        chats.append(
            {
                "id": chat.id,
                "type": chat.type,
                "title": chat.title,
                "createdAt": to_iso(chat.created_at),
                "members": [{**public_user(member.username), "isAdmin": member.is_admin} for member in chat.members],
                "messages": [admin_message_to_dict(message) for message in visible_messages],
                "messageCount": visible_message_count(chat.id),
            }
        )

    archives = [
        {
            **archive_to_summary(archive),
            "username": archive.username,
            "messages": compact_archive_messages_for_admin(archive.messages),
        }
        for archive in ChatArchive.query.order_by(ChatArchive.created_at.desc()).all()
    ]
    blocks = [
        {
            "id": row.id,
            "blocker": public_user(row.blocker),
            "blocked": public_user(row.blocked),
            "createdAt": to_iso(row.created_at),
        }
        for row in BlockedUser.query.order_by(BlockedUser.created_at.desc()).all()
    ]
    contact_requests = [
        contact_request_to_dict(row)
        for row in ContactRequest.query.order_by(ContactRequest.created_at.desc()).all()
    ]
    group_invites = [
        group_invite_to_dict(row)
        for row in GroupInvite.query.order_by(GroupInvite.created_at.desc()).all()
    ]

    return jsonify(
        {
            "ok": True,
            "users": users,
            "chats": chats,
            "stories": [admin_story_to_dict(story) for story in Story.query.filter(Story.expires_at > datetime.now(timezone.utc)).order_by(Story.created_at.desc()).all()],
            "calls": [call_log_to_dict(log, log.caller) for log in CallLog.query.order_by(CallLog.started_at.desc()).all()],
            "archives": archives,
            "blocks": blocks,
            "contactRequests": contact_requests,
            "groupInvites": group_invites,
            "scheduledMessages": [admin_scheduled_message_to_dict(row) for row in ScheduledMessage.query.order_by(ScheduledMessage.send_at.asc()).all()],
            "updates": [update_post_to_dict(row) for row in UpdatePost.query.order_by(UpdatePost.created_at.desc()).limit(240).all()],
            "activities": admin_activity_feed(),
            "pointLedger": [point_ledger_to_dict(row) for row in PointLedger.query.order_by(PointLedger.created_at.desc()).limit(300).all()],
            "aiMemories": [admin_ai_memory_to_dict(row) for row in AiMemory.query.order_by(AiMemory.created_at.desc()).limit(300).all()],
            "aiTasks": [ai_task_to_dict(row) for row in AiTask.query.order_by(AiTask.created_at.desc()).limit(240).all()],
            "voiceRooms": voice_rooms_state(),
            "communities": [community_to_dict(row) for row in Community.query.order_by(Community.created_at.desc()).limit(160).all()],
            "nexaPlay": admin_nexa_play_states(),
            "devices": [device_session_to_dict(row) for row in DeviceSession.query.order_by(DeviceSession.last_seen.desc()).limit(240).all()],
            "ai": ai_provider_status(),
            "design": design_settings(),
            "serverIp": request.host,
            "yourIp": request_ip(),
            "localAdmin": is_local_admin_request(),
        }
    )


@app.route("/admin/user/<username>/points", methods=["POST"])
def admin_adjust_points(username):
    admin_error = require_admin()
    if admin_error:
        return admin_error
    user = db.session.get(User, username)
    if not user:
        return jsonify({"ok": False, "message": "Kullanıcı bulunamadı."}), 404
    data = request.get_json() or {}
    try:
        amount = int(data.get("amount") or 0)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "message": "Puan miktarı sayı olmalı."}), 400
    if amount == 0 or abs(amount) > 100_000:
        return jsonify({"ok": False, "message": "Puan değişimi -100000 ile 100000 arasında ve sıfırdan farklı olmalı."}), 400
    user.points = max(0, int(user.points or 0) + amount)
    db.session.add(
        PointLedger(
            id=uuid4().hex,
            username=username,
            amount=amount,
            reason="admin_adjustment",
            meta={"note": str(data.get("note") or "Admin paneli")[:180]},
        )
    )
    db.session.commit()
    return jsonify({"ok": True, "points": user.points})


@app.route("/admin/user/<username>/ai-memory", methods=["DELETE"])
def admin_clear_ai_memory(username):
    admin_error = require_admin()
    if admin_error:
        return admin_error
    if not db.session.get(User, username):
        return jsonify({"ok": False, "message": "Kullanıcı bulunamadı."}), 404
    deleted = AiMemory.query.filter_by(username=username).delete(synchronize_session=False)
    AiTask.query.filter_by(username=username).delete(synchronize_session=False)
    db.session.commit()
    return jsonify({"ok": True, "deleted": deleted})


@app.route("/admin/user/<username>/games", methods=["DELETE"])
def admin_reset_games(username):
    admin_error = require_admin()
    if admin_error:
        return admin_error
    row = db.session.get(AppSetting, nexa_play_setting_key(username))
    if row:
        db.session.delete(row)
        db.session.commit()
    return jsonify({"ok": True})


@app.route("/admin/voice-room/<room_id>", methods=["DELETE"])
def admin_close_voice_room(room_id):
    admin_error = require_admin()
    if admin_error:
        return admin_error
    with voice_room_lock:
        room = voice_rooms.get(room_id)
        if not room:
            return jsonify({"ok": False, "message": "Sesli oda bulunamadı."}), 404
        if room_id == "lounge":
            room["participants"] = {}
            room["comments"] = []
            room["requests"] = {}
        else:
            voice_rooms.pop(room_id, None)
    emit_voice_rooms()
    return jsonify({"ok": True})


@app.route("/admin/update/<post_id>", methods=["DELETE"])
def admin_delete_update(post_id):
    admin_error = require_admin()
    if admin_error:
        return admin_error
    row = db.session.get(UpdatePost, post_id)
    if not row:
        return jsonify({"ok": False, "message": "Güncelleme bulunamadı."}), 404
    db.session.delete(row)
    db.session.commit()
    socketio.emit("updates:changed", {"postId": post_id, "deleted": True})
    return jsonify({"ok": True})


@app.route("/admin/user", methods=["POST"])
def admin_create_user():
    admin_error = require_admin()
    if admin_error:
        return admin_error

    data = request.get_json() or {}
    display_name, display_name_error = normalize_display_name(data)
    email, email_normalized = normalize_email(data.get("email") or "")
    username = (data.get("username") or "").strip().lower()
    password = data.get("password") or ""

    if display_name_error:
        return jsonify({"ok": False, "message": display_name_error}), 400

    if display_name_exists(display_name):
        return jsonify({"ok": False, "message": "Bu isim zaten kayıtlı."}), 400

    if not email or not email_normalized:
        return jsonify({"ok": False, "message": "Giriş için geçerli bir e-posta formatı yaz."}), 400

    if email_exists(email_normalized):
        return jsonify({"ok": False, "message": "Bu e-posta zaten kayıtlı."}), 400

    if not username:
        base = email_normalized.split("@", 1)[0]
        username = re.sub(r"[^a-z0-9_]", "_", base).strip("_")[:18] or "adminuser"
        candidate = username
        counter = 1
        while db.session.get(User, candidate):
            counter += 1
            candidate = f"{username[:18]}_{counter}"
        username = candidate

    username_problem = username_error(username)
    if username_problem:
        return jsonify({"ok": False, "message": username_problem}), 400

    if db.session.get(User, username):
        return jsonify({"ok": False, "message": "Bu kullanıcı adı zaten var."}), 400

    password_problem = password_error(username, password)
    if password_problem:
        return jsonify({"ok": False, "message": password_problem}), 400

    user = User(
        username=username,
        password_hash=generate_password_hash(password),
        display_name=display_name,
        email=email,
        email_normalized=email_normalized,
        email_verified=True,
        avatar=tr_upper(display_name[:2]),
    )
    db.session.add(user)
    db.session.commit()
    broadcast_presence()
    return jsonify({"ok": True, "user": private_user(username), "message": "Kullanıcı oluşturuldu."})


@app.route("/admin/user/<username>", methods=["DELETE"])
def admin_delete_user(username):
    admin_error = require_admin()
    if admin_error:
        return admin_error

    user = db.session.get(User, username.strip().lower())
    if not user:
        return jsonify({"ok": False, "message": "Kullanıcı bulunamadı."}), 404

    owned_story_ids = [
        row[0]
        for row in db.session.query(Story.id).filter_by(username=user.username).all()
    ]
    StoryView.query.filter_by(viewer_username=user.username).delete(synchronize_session=False)
    if owned_story_ids:
        StoryView.query.filter(StoryView.story_id.in_(owned_story_ids)).delete(synchronize_session=False)
    Story.query.filter_by(username=user.username).delete(synchronize_session=False)
    CallLog.query.filter_by(caller=user.username).delete(synchronize_session=False)
    Message.query.filter_by(sender=user.username).delete(synchronize_session=False)
    BlockedUser.query.filter(db.or_(BlockedUser.blocker == user.username, BlockedUser.blocked == user.username)).delete(synchronize_session=False)
    ContactRequest.query.filter(db.or_(ContactRequest.from_username == user.username, ContactRequest.to_username == user.username)).delete(synchronize_session=False)
    GroupInvite.query.filter(db.or_(GroupInvite.inviter == user.username, GroupInvite.invitee == user.username)).delete(synchronize_session=False)
    ScheduledMessage.query.filter_by(sender=user.username).delete(synchronize_session=False)
    HiddenChat.query.filter_by(username=user.username).delete(synchronize_session=False)
    ChatArchive.query.filter_by(username=user.username).delete(synchronize_session=False)
    DeviceSession.query.filter_by(username=user.username).delete(synchronize_session=False)
    member_rows = ChatMember.query.filter_by(username=user.username).all()
    direct_chat_ids = [row.chat_id for row in member_rows if row.chat and row.chat.type == "direct"]
    group_chat_ids = [row.chat_id for row in member_rows if row.chat and row.chat.type == "group"]
    if direct_chat_ids:
        CallLog.query.filter(CallLog.chat_id.in_(direct_chat_ids)).delete(synchronize_session=False)
        HiddenChat.query.filter(HiddenChat.chat_id.in_(direct_chat_ids)).delete(synchronize_session=False)
        ScheduledMessage.query.filter(ScheduledMessage.chat_id.in_(direct_chat_ids)).delete(synchronize_session=False)
    for member in member_rows:
        db.session.delete(member)
    for chat_id in direct_chat_ids:
        chat = db.session.get(Chat, chat_id)
        if chat:
            db.session.delete(chat)
    db.session.delete(user)
    db.session.flush()
    for chat_id in group_chat_ids:
        promote_fallback_group_admin(db.session.get(Chat, chat_id))
    db.session.commit()
    broadcast_presence()
    emit_general_group_updates()
    broadcast_stories()
    return jsonify({"ok": True, "message": "Kullanıcı silindi."})


@app.route("/admin/user/<username>/password", methods=["POST"])
def admin_reset_password(username):
    admin_error = require_admin()
    if admin_error:
        return admin_error

    data = request.get_json() or {}
    new_password = data.get("password") or ""
    user = db.session.get(User, username.strip().lower())
    if not user:
        return jsonify({"ok": False, "message": "Kullanıcı bulunamadı."}), 404

    password_problem = password_error(user.username, new_password)
    if password_problem:
        return jsonify({"ok": False, "message": password_problem}), 400

    user.password_hash = generate_password_hash(new_password)
    db.session.commit()
    return jsonify({"ok": True, "message": "Şifre sıfırlandı."})


@app.route("/admin/user/<username>/privacy", methods=["POST"])
def admin_update_user_privacy(username):
    admin_error = require_admin()
    if admin_error:
        return admin_error

    user = db.session.get(User, username.strip().lower())
    if not user:
        return jsonify({"ok": False, "message": "Kullanıcı bulunamadı."}), 404

    data = request.get_json() or {}
    user.hide_last_seen = bool(data.get("lastSeenHidden"))
    user.hide_online = bool(data.get("onlineHidden"))
    user.disable_read_receipts = bool(data.get("readReceiptsOff"))
    user.hide_email = bool(data.get("emailHidden", True))
    db.session.commit()
    broadcast_presence()
    return jsonify({"ok": True, "message": "Gizlilik güncellendi.", "user": private_user(user.username)})


@app.route("/admin/archive/<archive_id>", methods=["DELETE"])
def admin_delete_archive(archive_id):
    admin_error = require_admin()
    if admin_error:
        return admin_error

    archive = db.session.get(ChatArchive, archive_id)
    if not archive:
        return jsonify({"ok": False, "message": "Arşiv bulunamadı."}), 404

    db.session.delete(archive)
    db.session.commit()
    return jsonify({"ok": True, "message": "Arşiv silindi."})


@app.route("/admin/archive/<archive_id>/restore", methods=["POST"])
def admin_restore_archive(archive_id):
    admin_error = require_admin()
    if admin_error:
        return admin_error

    archive = db.session.get(ChatArchive, archive_id)
    if not archive:
        return jsonify({"ok": False, "message": "Arşiv bulunamadı."}), 404

    username = archive.username
    chat = restore_archive_for_user(archive)
    if not chat:
        return jsonify({"ok": False, "message": "Sohbet geri yuklenemiyor."}), 404

    db.session.commit()
    for sid in connected_sids_for(username):
        socketio.emit("chat:upsert", chat_for_user(chat, username), room=sid)
        socketio.emit("archive:update", visible_archives(username), room=sid)
    return jsonify({"ok": True, "message": "Arşivden çıkarıldı."})


@app.route("/admin/message/<message_id>", methods=["DELETE"])
def admin_delete_message(message_id):
    admin_error = require_admin()
    if admin_error:
        return admin_error

    message = db.session.get(Message, message_id)
    if not message:
        return jsonify({"ok": False, "message": "Mesaj bulunamadı."}), 404

    db.session.delete(message)
    db.session.commit()
    return jsonify({"ok": True, "message": "Mesaj silindi."})


@app.route("/admin/story/<story_id>", methods=["DELETE"])
def admin_delete_story(story_id):
    admin_error = require_admin()
    if admin_error:
        return admin_error

    story = db.session.get(Story, story_id)
    if not story:
        return jsonify({"ok": False, "message": "Durum bulunamadı."}), 404

    db.session.delete(story)
    db.session.commit()
    broadcast_stories()
    return jsonify({"ok": True, "message": "Durum silindi."})


@app.route("/admin/chat/<chat_id>", methods=["DELETE"])
def admin_delete_chat(chat_id):
    admin_error = require_admin()
    if admin_error:
        return admin_error

    if chat_id == "lobby":
        return jsonify({"ok": False, "message": "Genel Grup silinemez."}), 400

    chat = db.session.get(Chat, chat_id)
    if not chat:
        return jsonify({"ok": False, "message": "Sohbet bulunamadı."}), 404

    affected_members = chat_member_names(chat)
    GroupInvite.query.filter_by(chat_id=chat_id).delete(synchronize_session=False)
    HiddenChat.query.filter_by(chat_id=chat_id).delete(synchronize_session=False)
    ScheduledMessage.query.filter_by(chat_id=chat_id).delete(synchronize_session=False)
    db.session.delete(chat)
    db.session.commit()

    for member in affected_members:
        for sid in connected_sids_for(member):
            socketio.emit("chat:remove", {"chatId": chat_id}, room=sid)

    return jsonify({"ok": True, "message": "Sohbet silindi."})


@app.route("/bootstrap/<username>")
def bootstrap(username):
    expire_due_messages(notify=False)
    deliver_due_scheduled_messages()
    username = username.strip().lower()
    if not db.session.get(User, username):
        return jsonify({"ok": False, "message": "Kullanıcı bulunamadı."}), 404

    device_id = request.args.get("deviceId") or request.headers.get("X-Nexa-Device")
    if device_revoked(username, device_id):
        return jsonify({"ok": False, "message": "Bu cihaz oturumu uzaktan kapatildi."}), 401
    if device_id:
        upsert_device_session(username, device_id)
        db.session.commit()

    return jsonify({"ok": True, **app_state_for_user(username)})


@app.route("/chat/<chat_id>/messages")
def chat_messages(chat_id):
    username = (request.args.get("username") or "").strip().lower()
    chat = db.session.get(Chat, chat_id)
    if not username or not chat or not user_can_see_chat(chat, username):
        return jsonify({"ok": False, "message": "Sohbet bulunamadı."}), 404

    query = Message.query.filter_by(chat_id=chat.id)
    before = request.args.get("before")
    if before:
        try:
            before_date = datetime.fromisoformat(before.replace("Z", "+00:00"))
            if before_date.tzinfo is None:
                before_date = before_date.replace(tzinfo=timezone.utc)
            query = query.filter(Message.created_at < before_date)
        except ValueError:
            pass

    rows = query.order_by(Message.created_at.desc()).limit(RECENT_MESSAGE_SCAN_LIMIT).all()
    visible = [
        message
        for message in rows
        if username not in (message.deleted_for or [])
    ][:MAX_BOOTSTRAP_MESSAGES]

    return jsonify(
        {
            "ok": True,
            "messages": [message_to_dict(message) for message in reversed(visible)],
            "messageCount": visible_message_count(chat.id, username),
        }
    )


@socketio.on("connect")
def handle_connect():
    ensure_lobby()


@socketio.on("qr:watch")
def handle_qr_watch(data):
    data = data or {}
    session_id = (data.get("sessionId") or "").strip()
    secret = (data.get("secret") or "").strip()
    row, error = qr_session_error(session_id, secret)
    if error:
        emit("qr:error", {"message": "QR oturumu bulunamadi veya suresi doldu."})
        return
    join_room(f"qr:{session_id}")
    emit("qr:ready", {"sessionId": session_id, "expiresAt": to_iso(row["expires_at"])})


@socketio.on("user:join")
def handle_user_join(data):
    username = (data or {}).get("username", "").strip().lower()
    include_state = (data or {}).get("includeState", True) is not False
    device_id = (data or {}).get("deviceId")
    expire_due_messages(notify=True)
    deliver_due_scheduled_messages()

    if not db.session.get(User, username):
        emit("auth:error", {"message": "Önce giriş yapmalısın."})
        return {"ok": False, "message": "Önce giriş yapmalısın."}

    if device_revoked(username, device_id):
        emit("auth:error", {"message": "Bu cihaz oturumu uzaktan kapatildi."})
        return {"ok": False, "message": "Bu cihaz oturumu uzaktan kapatildi."}

    connections[request.sid] = username
    user = db.session.get(User, username)
    if user:
        user.last_seen = datetime.now(timezone.utc)
        upsert_device_session(username, device_id)
        db.session.commit()

    chat_ids = [
        row.chat_id
        for row in ChatMember.query.filter_by(username=username).with_entities(ChatMember.chat_id).all()
    ]
    for chat_id in chat_ids:
        join_room(chat_id)

    if include_state:
        emit("app:state", app_state_for_user(username))
    broadcast_presence()
    emit_general_group_updates()
    return {"ok": True, "username": username}


@socketio.on("contact:respond")
def handle_contact_respond(data):
    username = connections.get(request.sid)
    data = data or {}
    request_row = db.session.get(ContactRequest, data.get("requestId"))
    accept = bool(data.get("accept"))

    if not username or not request_row or request_row.to_username != username or request_row.status != "pending":
        emit("notice", {"message": "Mesajlaşma isteği bulunamadı."})
        return

    request_row.status = "accepted" if accept else "declined"
    request_row.responded_at = datetime.now(timezone.utc)
    if accept:
        add_points([request_row.from_username, request_row.to_username], POINT_RULES["friend_accept"], "friend_accept")
    db.session.commit()

    if accept and not is_blocked_between(request_row.from_username, request_row.to_username):
        chat = ensure_direct_chat(request_row.from_username, request_row.to_username)
        for member in chat_member_names(chat):
            for sid in connected_sids_for(member):
                join_room(chat.id, sid=sid)
                emit("chat:upsert", chat_for_user(chat, member), room=sid)

    emit_social_updates(request_row.from_username, request_row.to_username)
    emit("notice", {"message": "İstek kabul edildi." if accept else "İstek reddedildi."})


@socketio.on("contact:cancel")
def handle_contact_cancel(data):
    username = connections.get(request.sid)
    request_row = db.session.get(ContactRequest, (data or {}).get("requestId"))

    if (
        not username
        or not request_row
        or request_row.from_username != username
        or request_row.status != "pending"
    ):
        emit("notice", {"message": "Geri çekilecek bekleyen istek bulunamadı."})
        return

    target = request_row.to_username
    request_row.status = "cancelled"
    request_row.responded_at = datetime.now(timezone.utc)
    db.session.commit()
    emit_social_updates(username, target)
    emit("notice", {"message": "Arkadaşlık isteği geri çekildi."})


@socketio.on("contact:remove")
def handle_contact_remove(data):
    username = connections.get(request.sid)
    data = data or {}
    target = (data.get("username") or "").strip().lower()
    mode = data.get("mode") or "keep"

    if not username or not db.session.get(User, target) or username == target:
        return

    request_row = contact_request_between(username, target)
    if not request_row or request_row.status != "accepted":
        emit("notice", {"message": "Bu kişi zaten arkadaş listende değil."})
        return

    if mode not in {"keep", "archive", "permanent"}:
        mode = "keep"

    request_row.status = "declined"
    request_row.responded_at = datetime.now(timezone.utc)
    chat = find_direct_chat(username, target)
    if chat and mode == "archive":
        archive_chat_for_user(chat, username, "deleted")
        emit("chat:remove", {"chatId": chat.id}, room=request.sid)
        emit("archive:update", visible_archives(username), room=request.sid)
    elif chat and mode == "permanent":
        hide_chat_messages_for_user(chat, username)
        emit("chat:remove", {"chatId": chat.id}, room=request.sid)

    db.session.commit()
    emit_social_updates(username, target)
    emit("notice", {"message": "Arkadaşlıktan çıkarıldı."})


@socketio.on("user:block")
def handle_user_block(data):
    username = connections.get(request.sid)
    target = (data or {}).get("username", "").strip().lower()
    blocked = bool((data or {}).get("blocked", True))
    archive_mode = (data or {}).get("archiveMode") or "keep"

    if not username or target == username or not db.session.get(User, target):
        emit("notice", {"message": "Kullanıcı bulunamadı."})
        return

    row = BlockedUser.query.filter_by(blocker=username, blocked=target).first()
    if blocked and not row:
        chat = find_direct_chat(username, target)
        if chat and archive_mode in {"archive", "permanent"}:
            if archive_mode == "archive":
                archive_chat_for_user(chat, username, "blocked")
            else:
                hide_chat_messages_for_user(chat, username)
        db.session.add(BlockedUser(blocker=username, blocked=target))
    elif not blocked and row:
        db.session.delete(row)

    db.session.commit()
    emit_social_updates(username, target)
    emit("notice", {"message": "Kullanıcı engellendi." if blocked else "Engel kaldırıldı."})


@socketio.on("chat:create")
def handle_chat_create(data):
    username = connections.get(request.sid)
    target = (data or {}).get("target", "").strip().lower()

    if not username or not db.session.get(User, target) or target == username:
        emit("notice", {"message": "Kullanıcı bulunamadı."})
        return

    if is_blocked_between(username, target):
        emit("notice", {"message": "Bu kişiyle mesajlaşma veya arama engellenmiş."})
        return

    existing_request = contact_request_between(username, target)
    if not accepted_contact(username, target):
        if existing_request and existing_request.status == "pending":
            emit("notice", {"message": "Mesajlaşma isteği zaten bekliyor."})
        else:
            if existing_request:
                request_row = existing_request
                request_row.from_username = username
                request_row.to_username = target
                request_row.status = "pending"
                request_row.created_at = datetime.now(timezone.utc)
                request_row.responded_at = None
            else:
                request_row = ContactRequest(
                    id=uuid4().hex,
                    from_username=username,
                    to_username=target,
                    status="pending",
                )
                db.session.add(request_row)
                add_points(username, POINT_RULES["friend_invite"], "friend_invite", {"target": target})
            db.session.commit()
            emit("notice", {"message": "Mesajlaşma isteği gönderildi. Karşı taraf kabul edince sohbet açılacak."})
            emit_social_updates(username, target)
        return

    chat = ensure_direct_chat(username, target)
    HiddenChat.query.filter_by(username=username, chat_id=chat.id).delete(synchronize_session=False)
    db.session.commit()
    join_room(chat.id)
    emit("chat:upsert", chat_for_user(chat, username), room=request.sid)

    for sid in connected_sids_for(target):
        join_room(chat.id, sid=sid)
        emit("chat:upsert", chat_for_user(chat, target), room=sid)


@socketio.on("chat:group:create")
def handle_group_create(data):
    username = connections.get(request.sid)
    data = data or {}
    title = (data.get("title") or "").strip()
    image = data.get("image") if isinstance(data.get("image"), str) else None
    requested_members = {(member or "").strip().lower() for member in data.get("members", [])}
    requested_members.discard("")
    requested_members.discard(username)

    if not username or len(title) < 2:
        emit("notice", {"message": "Grup adı en az 2 karakter olmalı."})
        return

    chat = Chat(id="group:" + uuid4().hex, type="group", title=title, image=image)
    db.session.add(chat)
    db.session.flush()

    db.session.add(ChatMember(chat_id=chat.id, username=username, is_admin=True))
    add_points(username, POINT_RULES["group_join"], "group_join", {"chatId": chat.id, "role": "creator"})
    invitees = []
    for member in sorted(requested_members):
        if db.session.get(User, member) and not is_blocked_between(username, member):
            invitees.append(member)
            db.session.add(GroupInvite(id=uuid4().hex, chat_id=chat.id, inviter=username, invitee=member))

    db.session.commit()

    join_room(chat.id)
    emit("chat:upsert", chat_for_user(chat, username), room=request.sid)
    emit_social_updates(username, *invitees)


@socketio.on("chat:group:add")
def handle_group_add(data):
    username = connections.get(request.sid)
    data = data or {}
    chat = db.session.get(Chat, data.get("chatId"))
    target = (data.get("username") or "").strip().lower()

    if not username or not chat or chat.type != "group" or not is_group_admin(chat, username):
        emit("notice", {"message": "Sadece grup yöneticisi kişi ekleyebilir."})
        return

    if not db.session.get(User, target):
        emit("notice", {"message": "Kullanıcı bulunamadı."})
        return

    if is_blocked_between(username, target):
        emit("notice", {"message": "Bu kişi davet edilemiyor."})
        return

    if ChatMember.query.filter_by(chat_id=chat.id, username=target).first():
        emit("notice", {"message": "Bu kişi zaten grupta."})
        return

    invite = GroupInvite.query.filter_by(chat_id=chat.id, invitee=target, status="pending").first()
    if not invite:
        invite = GroupInvite(id=uuid4().hex, chat_id=chat.id, inviter=username, invitee=target)
        db.session.add(invite)
        db.session.commit()

    for member in chat_member_names(chat):
        for sid in connected_sids_for(member):
            emit("chat:upsert", chat_for_user(chat, member), room=sid)

    emit_social_updates(username, target)
    emit("notice", {"message": "Grup daveti gönderildi. Kişi kabul edince eklenecek."})


@socketio.on("chat:group:remove")
def handle_group_remove(data):
    username = connections.get(request.sid)
    data = data or {}
    chat = db.session.get(Chat, data.get("chatId"))
    target = (data.get("username") or "").strip().lower()

    if not username or not chat or chat.type != "group" or not is_group_admin(chat, username):
        emit("notice", {"message": "Sadece grup yöneticisi kişi çıkarabilir."})
        return

    if chat.id == "lobby":
        emit("notice", {"message": "Genel Grup üyelerini yönetmek için katıl/çık kullanılır."})
        return

    if target == username:
        emit("notice", {"message": "Kendini çıkarmak için gruptan çık düğmesini kullan."})
        return

    member = ChatMember.query.filter_by(chat_id=chat.id, username=target).first()
    if not member:
        emit("notice", {"message": "Bu kişi grupta değil."})
        return

    db.session.delete(member)
    db.session.commit()
    promote_fallback_group_admin(chat)
    db.session.commit()

    for sid in connected_sids_for(target):
        leave_room(chat.id, sid=sid)
        emit("chat:remove", {"chatId": chat.id}, room=sid)

    for member_name in chat_member_names(chat):
        for sid in connected_sids_for(member_name):
            emit("chat:upsert", chat_for_user(chat, member_name), room=sid)


@socketio.on("group:invite:respond")
def handle_group_invite_respond(data):
    username = connections.get(request.sid)
    invite = db.session.get(GroupInvite, (data or {}).get("inviteId"))
    accept = bool((data or {}).get("accept"))

    if not username or not invite or invite.invitee != username or invite.status != "pending":
        emit("notice", {"message": "Grup daveti bulunamadı."})
        return

    chat = db.session.get(Chat, invite.chat_id)
    invite.status = "accepted" if accept else "declined"
    invite.responded_at = datetime.now(timezone.utc)

    if accept and chat and not ChatMember.query.filter_by(chat_id=chat.id, username=username).first():
        db.session.add(ChatMember(chat_id=chat.id, username=username, is_admin=False))
        add_points(username, POINT_RULES["group_join"], "group_join", {"chatId": chat.id})

    db.session.commit()

    if accept and chat:
        join_room(chat.id)
        for member in chat_member_names(chat):
            for sid in connected_sids_for(member):
                emit("chat:upsert", chat_for_user(chat, member), room=sid)

    emit_social_updates(invite.inviter, invite.invitee)
    emit("notice", {"message": "Gruba katıldın." if accept else "Grup daveti reddedildi."})


@socketio.on("chat:group:update")
def handle_group_update(data):
    username = connections.get(request.sid)
    data = data or {}
    chat = db.session.get(Chat, data.get("chatId"))

    if not username or not chat or chat.type != "group" or chat.id == "lobby" or not is_group_admin(chat, username):
        emit("notice", {"message": "Sadece grup yöneticisi grup bilgisini değiştirebilir."})
        return

    title = (data.get("title") or chat.title).strip()
    image = data.get("image")
    transfer_to = (data.get("transferTo") or "").strip().lower()

    if len(title) < 2 or len(title) > 60:
        emit("notice", {"message": "Grup adı 2-60 karakter olmalı."})
        return

    chat.title = title
    if isinstance(image, str):
        if image and not image.startswith("data:image/"):
            emit("notice", {"message": "Grup fotoğrafı sadece resim olabilir."})
            return
        if len(image) > 1_500_000:
            emit("notice", {"message": "Grup fotoğrafı 1.5 MB altında olmalı."})
            return
        chat.image = image or None

    if transfer_to:
        target_member = ChatMember.query.filter_by(chat_id=chat.id, username=transfer_to).first()
        current_member = ChatMember.query.filter_by(chat_id=chat.id, username=username).first()
        if not target_member:
            emit("notice", {"message": "Yönetici devredilecek kişi grupta değil."})
            return
        target_member.is_admin = True
        if current_member and transfer_to != username:
            current_member.is_admin = False

    db.session.commit()
    for member in chat_member_names(chat):
        for sid in connected_sids_for(member):
            emit("chat:upsert", chat_for_user(chat, member), room=sid)
    emit("notice", {"message": "Grup güncellendi."})


@socketio.on("chat:lobby:join")
def handle_lobby_join():
    username = connections.get(request.sid)
    lobby = db.session.get(Chat, "lobby")

    if not username or not lobby:
        return

    already_member = ChatMember.query.filter_by(chat_id=lobby.id, username=username).first()
    add_chat_member(lobby.id, username)
    if not already_member:
        add_points(username, POINT_RULES["group_join"], "group_join", {"chatId": lobby.id})
    db.session.commit()
    join_room(lobby.id)
    emit("chat:upsert", chat_for_user(lobby, username), room=request.sid)
    emit_general_group_updates()


@socketio.on("chat:leave")
def handle_chat_leave(data):
    username = connections.get(request.sid)
    chat_id = (data or {}).get("chatId")
    chat = db.session.get(Chat, chat_id)

    if not username or not chat or chat.type != "group":
        return

    member = ChatMember.query.filter_by(chat_id=chat.id, username=username).first()
    if member:
        db.session.delete(member)
        db.session.commit()
        promote_fallback_group_admin(chat)
        db.session.commit()

    leave_room(chat.id)
    emit("chat:remove", {"chatId": chat.id}, room=request.sid)

    for sid, connected_user in connections.items():
        if user_can_see_chat(chat, connected_user):
            emit("chat:upsert", chat_for_user(chat, connected_user), room=sid)

    if chat.id == "lobby":
        emit_general_group_updates()


@socketio.on("chat:delete")
def handle_chat_delete(data):
    username = connections.get(request.sid)
    data = data or {}
    chat = db.session.get(Chat, data.get("chatId"))
    mode = data.get("mode") or "archive"

    if not username or not chat or not user_can_see_chat(chat, username):
        return

    if mode not in {"archive", "permanent"}:
        mode = "archive"

    if mode == "archive":
        archive_chat_for_user(chat, username, "deleted")
        message = "Sohbet 3 gunluk arsive alindi."
    else:
        hide_chat_messages_for_user(chat, username)
        message = "Sohbet kalici olarak silindi."

    ScheduledMessage.query.filter_by(sender=username, chat_id=chat.id).delete(synchronize_session=False)
    db.session.commit()
    emit("chat:remove", {"chatId": chat.id}, room=request.sid)
    emit("archive:update", visible_archives(username), room=request.sid)
    emit("notice", {"message": message})


@socketio.on("message:send")
def handle_message_send(data):
    username = connections.get(request.sid)
    data = data or {}
    chat = db.session.get(Chat, data.get("chatId"))
    body = (data.get("body") or "").strip()
    attachment = data.get("attachment")
    reply_to = data.get("replyTo")

    if not username or not chat:
        return

    send_error = chat_send_error(username, chat)
    if send_error:
        emit("notice", {"message": send_error})
        return

    if chat.type == "direct":
        others = [member for member in chat_member_names(chat) if member != username]
        if any(is_blocked_between(username, other) for other in others) or any(not accepted_contact(username, other) for other in others):
            emit("notice", {"message": "Mesaj göndermek için önce istek kabul edilmeli ve engel olmamalı."})
            return

    if not body and not attachment:
        return

    error = attachment_error(attachment)
    if error:
        emit("notice", {"message": error})
        return

    message = create_chat_message(chat, username, body, attachment, reply_to, data.get("expiresInSeconds"))
    db.session.commit()
    emit("message:new", message_to_dict(message), room=chat.id)
    sender_name = public_user(username)["displayName"]
    notification_body = body or (attachment or {}).get("name") or "Yeni mesaj"
    send_push_notification(
        chat.id,
        chat.title if chat.type == "group" else sender_name,
        notification_body,
        notification_type="message",
        sender=username,
        url=f"/chat/{chat.id}",
        emit_socket=False,
    )


@socketio.on("message:schedule")
def handle_message_schedule(data):
    username = connections.get(request.sid)
    data = data or {}
    chat = db.session.get(Chat, data.get("chatId"))
    body = (data.get("body") or "").strip()
    attachment = data.get("attachment")
    reply_to = data.get("replyTo")

    if not username or not chat:
        return

    send_error = chat_send_error(username, chat)
    if send_error:
        emit("notice", {"message": send_error})
        return

    if not body and not attachment:
        return

    error = attachment_error(attachment)
    if error:
        emit("notice", {"message": error})
        return

    try:
        send_at = datetime.fromisoformat(str(data.get("sendAt")).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        emit("notice", {"message": "Zamanlanacak saat okunamadÄ±."})
        return

    if send_at.tzinfo is None:
        send_at = send_at.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    if send_at <= now + timedelta(seconds=10):
        message = create_chat_message(chat, username, body, attachment, reply_to, data.get("expiresInSeconds"))
        db.session.commit()
        emit("message:new", message_to_dict(message), room=chat.id)
        sender_name = public_user(username)["displayName"]
        notification_body = body or (attachment or {}).get("name") or "Yeni mesaj"
        send_push_notification(
            chat.id,
            chat.title if chat.type == "group" else sender_name,
            notification_body,
            notification_type="message",
            sender=username,
            url=f"/chat/{chat.id}",
            emit_socket=False,
        )
        return

    if send_at > now + timedelta(days=MAX_SCHEDULE_DAYS):
        emit("notice", {"message": f"Mesaj en fazla {MAX_SCHEDULE_DAYS} gÃ¼n sonrasÄ±na zamanlanabilir."})
        return

    row = ScheduledMessage(
        id=uuid4().hex,
        chat_id=chat.id,
        sender=username,
        body=body,
        attachment=attachment,
        reply_to=reply_to if isinstance(reply_to, dict) else None,
        expires_in_seconds=parse_expiry_seconds(data.get("expiresInSeconds")),
        send_at=send_at,
    )
    db.session.add(row)
    db.session.commit()
    emit_scheduled_update(username)
    emit("notice", {"message": "Mesaj zamanlandÄ±."})


@socketio.on("message:scheduled:delete")
def handle_scheduled_delete(data):
    username = connections.get(request.sid)
    row = db.session.get(ScheduledMessage, (data or {}).get("scheduledId"))
    if not username or not row or row.sender != username:
        return

    db.session.delete(row)
    db.session.commit()
    emit_scheduled_update(username)
    emit("notice", {"message": "ZamanlÄ± mesaj iptal edildi."})


@socketio.on("typing")
def handle_typing(data):
    username = connections.get(request.sid)
    data = data or {}
    chat_id = data.get("chatId")
    chat = db.session.get(Chat, chat_id)

    if not username or not chat or not user_can_see_chat(chat, username):
        return

    typing_users.setdefault(chat_id, set())
    if data.get("typing"):
        typing_users[chat_id].add(username)
    else:
        typing_users[chat_id].discard(username)

    emit(
        "typing:update",
        {"chatId": chat_id, "users": sorted(typing_users.get(chat_id, set()))},
        room=chat_id,
        include_self=False,
    )


@socketio.on("message:read")
def handle_message_read(data):
    username = connections.get(request.sid)
    chat_id = (data or {}).get("chatId")
    chat = db.session.get(Chat, chat_id)

    if not username or not chat or not user_can_see_chat(chat, username):
        return

    user = db.session.get(User, username)
    private_receipt = bool(user and user.disable_read_receipts)

    message_ids = [
        str(message_id)
        for message_id in ((data or {}).get("messageIds") or [])
        if message_id
    ][:200]
    if message_ids:
        rows = Message.query.filter(Message.chat_id == chat.id, Message.id.in_(message_ids)).all()
    else:
        rows = recent_visible_messages(chat.id, username, MAX_BOOTSTRAP_MESSAGES)

    updated_ids = []
    for message in rows:
        read_by = list(message.read_by or [])
        if username not in read_by:
            read_by.append(username)
            message.read_by = read_by
            updated_ids.append(message.id)

    if updated_ids:
        db.session.commit()
        payload = {
            "chatId": chat_id,
            "reader": username,
            "messageIds": updated_ids,
            "receiptVisible": not private_receipt,
        }
        if private_receipt:
            for sid in connected_sids_for(username):
                socketio.emit("message:read", payload, room=sid)
        else:
            emit("message:read", payload, room=chat_id)


@socketio.on("message:view_once_opened")
def handle_view_once_opened(data):
    username = connections.get(request.sid)
    data = data or {}
    message = db.session.get(Message, data.get("messageId"))

    if not username or not message or message.deleted_at:
        return

    chat = db.session.get(Chat, message.chat_id)
    if not chat or not user_can_see_chat(chat, username):
        return

    attachment = dict(message.attachment or {})
    if not is_view_once_attachment(attachment):
        return

    opened_by = list(attachment.get("openedBy") or [])
    if username not in opened_by:
        opened_by.append(username)
        attachment["openedBy"] = opened_by
        message.attachment = attachment

    if username != message.sender:
        message.body = ""
        message.attachment = None
        message.reply_to = {"systemType": "view_once_opened"}
        message.deleted_at = datetime.now(timezone.utc)
        message.deleted_by = username

    db.session.commit()
    emit("message:deleted", message_to_dict(message), room=chat.id)


@socketio.on("message:poll_vote")
def handle_message_poll_vote(data):
    username = connections.get(request.sid)
    data = data or {}
    message = db.session.get(Message, data.get("messageId"))
    try:
        option_index = int(data.get("optionIndex"))
    except (TypeError, ValueError):
        option_index = -1

    if not username or not message or message.deleted_at:
        return

    chat = db.session.get(Chat, message.chat_id)
    if not chat or not user_can_see_chat(chat, username):
        return

    attachment = dict(message.attachment or {})
    poll = dict(attachment.get("poll") or {})
    options = list(poll.get("options") or [])
    votes = dict(poll.get("votes") or {})
    if attachment.get("type") != "poll" or option_index < 0 or option_index >= len(options):
        emit("notice", {"message": "Anket seçeneği okunamadı."})
        return
    if username in votes:
        emit("notice", {"message": "Bu anketteki seçimin kilitlendi; değiştirilemez."})
        return

    votes[username] = option_index
    poll["votes"] = votes
    attachment["poll"] = poll
    message.attachment = attachment
    db.session.commit()
    emit("message:edited", message_to_dict(message), room=chat.id)


@socketio.on("message:delete")
def handle_message_delete(data):
    username = connections.get(request.sid)
    data = data or {}
    message = db.session.get(Message, data.get("messageId"))
    scope = data.get("scope") or "me"

    if not username or not message:
        return

    chat = db.session.get(Chat, message.chat_id)
    if not chat or not user_can_see_chat(chat, username):
        return

    if scope == "all":
        if message.sender != username and not is_group_admin(chat, username):
            emit("notice", {"message": "Bu mesajı herkesten sadece gönderen veya grup yöneticisi silebilir."})
            return
        message.body = ""
        message.attachment = None
        message.reply_to = None
        message.deleted_at = datetime.now(timezone.utc)
        message.deleted_by = username
        db.session.commit()
        emit("message:deleted", message_to_dict(message), room=chat.id)
        return

    deleted_for = list(message.deleted_for or [])
    if username not in deleted_for:
        deleted_for.append(username)
        message.deleted_for = deleted_for
        db.session.commit()
    emit("message:remove-local", {"chatId": chat.id, "messageId": message.id}, room=request.sid)


@socketio.on("message:edit")
def handle_message_edit(data):
    username = connections.get(request.sid)
    data = data or {}
    message = db.session.get(Message, data.get("messageId"))
    body = (data.get("body") or "").strip()

    if not username or not message or not body:
        return

    chat = db.session.get(Chat, message.chat_id)
    if not chat or not user_can_see_chat(chat, username) or message.sender != username or message.deleted_at:
        return

    now = datetime.now(timezone.utc)
    versions = list(message.versions or [])
    if message.body and message.body != body[:4000]:
        versions.append({
            "body": message.body,
            "editedAt": to_iso(now),
            "editor": username,
        })
        message.versions = versions[-20:]
    message.body = body[:4000]
    message.edited_at = now
    db.session.commit()
    emit("message:edited", message_to_dict(message), room=chat.id)


@socketio.on("message:react")
def handle_message_react(data):
    username = connections.get(request.sid)
    data = data or {}
    message = db.session.get(Message, data.get("messageId"))
    emoji = (data.get("emoji") or "").strip()[:8]

    if not username or not message or not emoji:
        return

    chat = db.session.get(Chat, message.chat_id)
    if not chat or not user_can_see_chat(chat, username):
        return

    reactions = dict(message.reactions or {})
    reactions[username] = emoji
    message.reactions = reactions
    db.session.commit()
    emit("message:reaction", {"chatId": chat.id, "messageId": message.id, "reactions": reactions}, room=chat.id)


@socketio.on("story:create")
def handle_story_create(data):
    username = connections.get(request.sid)
    data = data or {}
    body = (data.get("body") or "").strip()
    attachment = data.get("attachment")

    if not username or (not body and not attachment):
        return

    error = attachment_error(attachment)
    if error:
        emit("notice", {"message": error})
        return

    story = Story(
        id=uuid4().hex,
        username=username,
        body=body,
        attachment=attachment,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    db.session.add(story)
    add_points(username, POINT_RULES["story"], "story")
    db.session.commit()
    broadcast_stories()


@socketio.on("story:delete")
def handle_story_delete(data):
    username = connections.get(request.sid)
    story = db.session.get(Story, (data or {}).get("storyId"))

    if not username or not story or story.username != username:
        return

    db.session.delete(story)
    db.session.commit()
    broadcast_stories()


@socketio.on("story:view")
def handle_story_view(data):
    username = connections.get(request.sid)
    story = db.session.get(Story, (data or {}).get("storyId"))

    if (
        not username
        or not story
        or story.username == username
        or is_past(story.expires_at)
    ):
        return

    existing = StoryView.query.filter_by(
        story_id=story.id,
        viewer_username=username,
    ).first()
    if existing:
        emit("story:consumed", {"storyId": story.id})
        return

    db.session.add(
        StoryView(
            id=uuid4().hex,
            story_id=story.id,
            viewer_username=username,
        )
    )
    db.session.commit()
    emit("story:consumed", {"storyId": story.id})
    broadcast_stories()


@socketio.on("story:reply")
def handle_story_reply(data):
    username = connections.get(request.sid)
    data = data or {}
    story = db.session.get(Story, data.get("storyId"))
    body = (data.get("body") or "").strip()

    if not username or not story or story.username == username or not body:
        if username:
            emit("story:reply:result", {"ok": False, "message": "Durum yanıtı gönderilemedi."})
        return

    if is_blocked_between(username, story.username):
        emit("story:reply:result", {"ok": False, "message": "Bu kişiye yanıt gönderilemiyor."})
        return

    if not accepted_contact(username, story.username):
        emit("story:reply:result", {"ok": False, "message": "Duruma yanıt vermek için önce mesajlaşma isteği kabul edilmeli."})
        return

    chat = ensure_direct_chat(username, story.username)
    reply_to = {
        "storyId": story.id,
        "senderName": public_user(story.username, username)["displayName"],
        "body": story.body or "Durum",
        "attachmentName": "Silinen durum",
        "expiresAt": to_iso(story.expires_at),
    }
    message = Message(
        id=uuid4().hex,
        chat_id=chat.id,
        sender=username,
        body=body,
        reply_to=reply_to,
        read_by=[username],
    )
    db.session.add(message)
    add_points(chat_member_names(chat), POINT_RULES["message"], "message", {"chatId": chat.id, "source": "story_reply"})
    db.session.commit()

    for member in chat_member_names(chat):
        for sid in connected_sids_for(member):
            join_room(chat.id, sid=sid)
            socketio.emit("chat:upsert", chat_for_user(chat, member), room=sid)
    emit("story:reply:result", {
        "ok": True,
        "message": "Durum yanıtı gönderildi.",
        "storyId": story.id,
        "chat": chat_for_user(chat, username),
    })


@socketio.on("updates:create")
def handle_update_post_create(data):
    username = connections.get(request.sid)
    data = data or {}
    body = (data.get("body") or "").strip()[:2000]
    media = data.get("media") if isinstance(data.get("media"), list) else []
    media = [item for item in media[:4] if isinstance(item, dict)]
    if not username or (not body and not media):
        emit("notice", {"message": "Paylaşmak için yazı, fotoğraf veya ses ekle."})
        return
    audio_count = 0
    for attachment in media:
        error = attachment_error(attachment)
        if error:
            emit("notice", {"message": error})
            return
        attachment_type = str(attachment.get("type") or "")
        if attachment_type.startswith("audio/"):
            audio_count += 1
        elif not attachment_type.startswith("image/"):
            emit("notice", {"message": "Güncelleme akışına yalnızca fotoğraf veya ses kaydı eklenebilir."})
            return
    if audio_count > 1:
        emit("notice", {"message": "Bir güncellemeye yalnızca bir ses kaydı ekleyebilirsin."})
        return
    post = UpdatePost(
        id=uuid4().hex,
        username=username,
        body=body,
        media=media,
        liked_by=[],
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    db.session.add(post)
    add_points(username, POINT_RULES["story"], "update_post")
    db.session.commit()
    broadcast_update_posts()


@socketio.on("updates:edit")
def handle_update_post_edit(data):
    username = connections.get(request.sid)
    data = data or {}
    post = db.session.get(UpdatePost, data.get("postId"))
    body = (data.get("body") or "").strip()[:2000]
    if not username or not post or post.username != username:
        emit("notice", {"message": "Bu paylaşımı düzenleme yetkin yok."})
        return
    if not body and not post.media:
        emit("notice", {"message": "Paylaşım boş bırakılamaz."})
        return
    post.body = body
    post.edited_at = datetime.now(timezone.utc)
    db.session.commit()
    broadcast_update_posts()


@socketio.on("updates:delete")
def handle_update_post_delete(data):
    username = connections.get(request.sid)
    post = db.session.get(UpdatePost, (data or {}).get("postId"))
    if not username or not post or post.username != username:
        emit("notice", {"message": "Bu paylaşımı silme yetkin yok."})
        return
    db.session.delete(post)
    db.session.commit()
    broadcast_update_posts()


@socketio.on("updates:like")
def handle_update_post_like(data):
    username = connections.get(request.sid)
    post = db.session.get(UpdatePost, (data or {}).get("postId"))
    if not username or not post:
        return
    liked_by = list(post.liked_by or [])
    if username in liked_by:
        liked_by.remove(username)
    else:
        liked_by.append(username)
    post.liked_by = liked_by
    db.session.commit()
    broadcast_update_posts()


def forward_call_event(event_name, data):
    username = connections.get(request.sid)
    data = data or {}
    chat = db.session.get(Chat, data.get("chatId"))

    if not username or not chat or not user_can_see_chat(chat, username):
        return

    if chat.type == "direct":
        others = [member for member in chat_member_names(chat) if member != username]
        if any(is_blocked_between(username, other) for other in others) or any(not accepted_contact(username, other) for other in others):
            emit("notice", {"message": "Arama için önce istek kabul edilmeli ve engel olmamalı."})
            return

    payload = dict(data)
    payload["from"] = username
    payload["fromName"] = public_user(username)["displayName"]
    target = (data.get("to") or "").strip().lower()
    if target:
        if not user_can_see_chat(chat, target):
            return
        target_sids = connected_sids_for(target)
        for sid in target_sids:
            emit(event_name, payload, room=sid)
        if event_name == "call:offer" and target_sids:
            emit("call:ringing", {"chatId": chat.id, "to": target})
        if event_name == "call:offer" and not data.get("retry"):
            call_kind = "audio" if data.get("audioOnly", True) else "video"
            send_push_notification(
                chat.id,
                "NexaLine arama",
                f"{payload['fromName']} arıyor",
                notification_type=f"call.{call_kind}",
                sender=username,
                target_usernames=[target],
                url=f"/call/{chat.id}",
                call_kind=call_kind,
                emit_socket=False,
            )
        return

    emit(event_name, payload, room=chat.id, include_self=False)
    if event_name == "call:offer" and not data.get("retry"):
        call_kind = "audio" if data.get("audioOnly", True) else "video"
        send_push_notification(
            chat.id,
            "NexaLine arama",
            f"{payload['fromName']} arıyor",
            notification_type=f"call.{call_kind}",
            sender=username,
            url=f"/call/{chat.id}",
            call_kind=call_kind,
            emit_socket=False,
        )


def emit_call_logs_for_chat(chat):
    if not chat:
        return

    for member in chat_member_names(chat):
        for sid in connected_sids_for(member):
            socketio.emit("calls:update", visible_call_logs(member), room=sid)


@socketio.on("calls:seen")
def handle_calls_seen(data=None):
    username = connections.get(request.sid)
    if not username:
        return {"ok": False, "message": "Oturum bulunamadı."}

    requested_ids = {
        str(item)
        for item in ((data or {}).get("callIds") or [])
        if str(item).strip()
    }
    logs = (
        CallLog.query.join(Chat, CallLog.chat_id == Chat.id)
        .join(ChatMember, ChatMember.chat_id == Chat.id)
        .filter(
            ChatMember.username == username,
            CallLog.status == "missed",
            CallLog.caller != username,
        )
        .all()
    )
    changed = 0
    for log in logs:
        if requested_ids and log.id not in requested_ids:
            continue
        seen_by = list(log.seen_by or [])
        if username in seen_by:
            continue
        log.seen_by = [*seen_by, username]
        changed += 1
    if changed:
        db.session.commit()
    emit("calls:update", visible_call_logs(username))
    return {"ok": True, "seenCount": changed}


def emit_scheduled_update(username):
    for sid in connected_sids_for(username):
        socketio.emit("scheduled:update", visible_scheduled_messages(username), room=sid)


def create_chat_message(chat, username, body, attachment=None, reply_to=None, expires_in_seconds=None):
    expires_at = None
    seconds = parse_expiry_seconds(expires_in_seconds)
    if seconds:
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=seconds)

    message = Message(
        id=uuid4().hex,
        chat_id=chat.id,
        sender=username,
        body=body,
        attachment=attachment,
        reply_to=reply_to if isinstance(reply_to, dict) else None,
        read_by=[username],
        expires_at=expires_at,
    )
    db.session.add(message)
    HiddenChat.query.filter_by(username=username, chat_id=chat.id).delete(synchronize_session=False)
    add_points(chat_member_names(chat), POINT_RULES["message"], "message", {"chatId": chat.id})
    return message


def deliver_due_scheduled_messages():
    if not scheduled_delivery_lock.acquire(blocking=False):
        return

    try:
        now = datetime.now(timezone.utc)
        due_rows = ScheduledMessage.query.filter(ScheduledMessage.send_at <= now).order_by(ScheduledMessage.send_at.asc()).limit(60).all()
        for row in due_rows:
            chat = row.chat or db.session.get(Chat, row.chat_id)
            if chat and not chat_send_error(row.sender, chat):
                message = create_chat_message(
                    chat,
                    row.sender,
                    row.body,
                    row.attachment,
                    row.reply_to,
                    row.expires_in_seconds,
                )
                db.session.delete(row)
                db.session.commit()
                socketio.emit("message:new", message_to_dict(message), room=chat.id)
                sender_name = public_user(row.sender)["displayName"]
                notification_body = row.body or (row.attachment or {}).get("name") or "Yeni mesaj"
                send_push_notification(
                    chat.id,
                    chat.title if chat.type == "group" else sender_name,
                    notification_body,
                    notification_type="message",
                    sender=row.sender,
                    url=f"/chat/{chat.id}",
                    emit_socket=False,
                )
                emit_scheduled_update(row.sender)
            else:
                db.session.delete(row)
                db.session.commit()
                emit_scheduled_update(row.sender)
    finally:
        scheduled_delivery_lock.release()


@socketio.on("background-sync")
def handle_background_sync(data=None):
    username = connections.get(request.sid)
    data = data or {}
    if not username:
        emit("background-sync:error", {"ok": False, "message": "Oturum bulunamadi."})
        return
    try:
        mode = str(data.get("mode") or "register").strip().lower()
        if mode == "register":
            emit("background-sync:ready", {"ok": True, "username": username, "serverTime": now_iso()})
            return
        chat_id = str(data.get("chatId") or "").strip()
        chat = db.session.get(Chat, chat_id)
        if not chat or not user_can_see_chat(chat, username):
            emit("background-sync:error", {"ok": False, "message": "Sohbet bulunamadi."})
            return
        title = re.sub(r"\s+", " ", str(data.get("title") or chat.title or "NexaLine"))[:120]
        message = re.sub(r"\s+", " ", str(data.get("message") or "Yeni bildirim"))[:500]
        notification_type = str(data.get("type") or "message")[:40]
        result = send_push_notification(
            chat.id,
            title,
            message,
            notification_type=notification_type,
            sender=username,
            url=data.get("url") or f"/chat/{chat.id}",
            call_kind=data.get("callKind"),
            emit_socket=True,
        )
        emit("background-sync:complete", result)
    except Exception as error:
        app.logger.exception("Background sync failed: %s", error)
        emit("background-sync:error", {"ok": False, "message": AI_NATURAL_ERROR})


@socketio.on("call:log")
def handle_call_log(data):
    username = connections.get(request.sid)
    data = data or {}
    chat = db.session.get(Chat, data.get("chatId"))

    if not username or not chat or not user_can_see_chat(chat, username):
        return

    if chat.type == "direct":
        others = [member for member in chat_member_names(chat) if member != username]
        if any(is_blocked_between(username, other) for other in others) or any(not accepted_contact(username, other) for other in others):
            return

    kind = "video" if data.get("kind") == "video" else "audio"
    status = "active" if data.get("status") == "active" else "ended"
    duration = max(0, int(data.get("durationSeconds") or 0))
    started_at = datetime.now(timezone.utc)
    ended_at = None if status == "active" else datetime.now(timezone.utc)
    label = "Görüntülü arama" if kind == "video" else "Sesli arama"
    body = f"{label} aktif" if status == "active" else f"{label} bitti • {format_duration(duration)}"

    log = CallLog(
        id=uuid4().hex,
        chat_id=chat.id,
        caller=username,
        kind=kind,
        status=status,
        duration_seconds=duration,
        started_at=started_at,
        ended_at=ended_at,
    )
    message = Message(
        id=uuid4().hex,
        chat_id=chat.id,
        sender=username,
        body=body,
        attachment={"kind": kind, "status": status, "durationSeconds": duration, "type": "call"},
        read_by=[username],
    )
    db.session.add(log)
    db.session.add(message)
    add_points(chat_member_names(chat), POINT_RULES["message"], "message", {"chatId": chat.id, "source": "call"})
    db.session.commit()
    emit("message:new", message_to_dict(message), room=chat.id)
    emit_call_logs_for_chat(chat)


def format_duration(seconds):
    minutes = seconds // 60
    remaining = seconds % 60
    if minutes:
        return f"{minutes} dk {remaining} sn"
    return f"{remaining} sn"


@socketio.on("call:offer")
def handle_call_offer(data):
    forward_call_event("call:offer", data)


@socketio.on("call:answer")
def handle_call_answer(data):
    forward_call_event("call:answer", data)


@socketio.on("call:ice")
def handle_call_ice(data):
    forward_call_event("call:ice", data)


@socketio.on("call:end")
def handle_call_end(data):
    forward_call_event("call:end", data)


def emit_voice_rooms():
    for sid, username in connections.items():
        socketio.emit("voice:rooms", voice_rooms_state(username), room=sid)


@socketio.on("voice:join")
def handle_voice_join(data):
    username = connections.get(request.sid)
    room_id = ((data or {}).get("roomId") or "general").strip()
    if not username or room_id not in voice_rooms:
        return {"ok": False, "message": "Sesli oda bulunamadı."}
    previous_rooms = []
    with voice_room_lock:
        room = normalize_voice_room(voice_rooms[room_id])
        if username in (room.get("bans") or []):
            emit("notice", {"message": "Bu odadan engellendin."})
            return {"ok": False, "message": "Bu odadan engellendin."}
        if len(room["participants"]) >= int(room.get("limit") or 50) and username not in room["participants"]:
            emit("notice", {"message": "Oda katılımcı limiti dolu."})
            return {"ok": False, "message": "Oda katılımcı limiti dolu."}
        for existing_room_id, existing_room in voice_rooms.items():
            if existing_room_id != room_id and username in existing_room["participants"]:
                existing_room["participants"].pop(username, None)
                previous_rooms.append(existing_room_id)
        room = normalize_voice_room(voice_rooms[room_id])
        peers = [name for name in room["participants"] if name != username]
        role = "founder" if room.get("owner") == username else ("speaker" if room.get("talkMode") == "everyone" else "listener")
        room["participants"][username] = {
            "muted": False,
            "speaking": False,
            "role": role,
            "handRaised": False,
            "joinedAt": now_iso(),
        }
    for previous_room_id in previous_rooms:
        leave_room(f"voice:{previous_room_id}")
        socketio.emit(
            "voice:peer-left",
            {"roomId": previous_room_id, "username": username},
            room=f"voice:{previous_room_id}",
        )
    join_room(f"voice:{room_id}")
    add_points_once(username, POINT_RULES["voice_room_join"], "voice_room_join", f"{datetime.now(timezone.utc).date()}:{room_id}", {"roomId": room_id})
    db.session.commit()
    socketio.emit(
        "voice:peer-joined",
        {"roomId": room_id, "username": username},
        room=f"voice:{room_id}",
        skip_sid=request.sid,
    )
    emit_voice_rooms()
    return {"ok": True, "roomId": room_id, "peers": peers}


@socketio.on("voice:leave")
def handle_voice_leave(data=None):
    username = connections.get(request.sid)
    if not username:
        return {"ok": False, "message": "Oturum bulunamadı."}
    left_rooms = []
    with voice_room_lock:
        for room_id, room in voice_rooms.items():
            if username in room["participants"]:
                participant = room["participants"].pop(username, None) or {}
                joined_at = participant.get("joinedAt")
                if joined_at:
                    try:
                        joined_dt = datetime.fromisoformat(str(joined_at).replace("Z", "+00:00"))
                        if (datetime.now(timezone.utc) - joined_dt).total_seconds() >= 600:
                            add_points(username, POINT_RULES["voice_room_10min"], "voice_room_10min", {"roomId": room_id})
                    except ValueError:
                        pass
                leave_room(f"voice:{room_id}")
                left_rooms.append(room_id)
    db.session.commit()
    for room_id in left_rooms:
        socketio.emit(
            "voice:peer-left",
            {"roomId": room_id, "username": username},
            room=f"voice:{room_id}",
        )
    emit_voice_rooms()
    return {"ok": True, "rooms": left_rooms}


@socketio.on("voice:mute")
def handle_voice_mute(data):
    username = connections.get(request.sid)
    room_id = ((data or {}).get("roomId") or "").strip()
    if not username or room_id not in voice_rooms:
        return {"ok": False, "message": "Sesli oda bulunamadı."}
    muted = bool((data or {}).get("muted"))
    with voice_room_lock:
        participant = voice_rooms[room_id]["participants"].get(username)
        if participant is not None:
            participant["muted"] = muted
    emit_voice_rooms()
    return {"ok": True, "muted": muted}


@socketio.on("voice:speaking")
def handle_voice_speaking(data):
    username = connections.get(request.sid)
    room_id = ((data or {}).get("roomId") or "").strip()
    if not username or room_id not in voice_rooms:
        return
    with voice_room_lock:
        participant = voice_rooms[room_id]["participants"].get(username)
        if participant is not None:
            participant["speaking"] = bool((data or {}).get("speaking"))
    emit_voice_rooms()


@socketio.on("voice:create")
def handle_voice_create(data):
    username = connections.get(request.sid)
    data = data or {}
    if not username:
        return {"ok": False, "message": "Oturum bulunamadı."}
    title = re.sub(r"\s+", " ", (data.get("title") or "").strip())[:120]
    if len(title) < 2:
        emit("notice", {"message": "Oda adı gerekli."})
        return {"ok": False, "message": "Oda adı gerekli."}
    room_id = uuid4().hex[:12]
    with voice_room_lock:
        voice_rooms[room_id] = normalize_voice_room({
            "id": room_id,
            "title": title,
            "topic": (data.get("topic") or data.get("category") or "Canlı sohbet").strip()[:180],
            "category": (data.get("category") or "Genel").strip()[:60],
            "privacy": (data.get("privacy") or "public").strip().lower(),
            "limit": clamp_voice_room_limit(data.get("limit")),
            "joinMode": (data.get("joinMode") or "open").strip().lower(),
            "talkMode": (data.get("talkMode") or "request").strip().lower(),
            "commentsEnabled": data.get("commentsEnabled") is not False,
            "aiModeration": data.get("aiModeration") is not False,
            "recording": bool(data.get("recording")),
            "owner": username,
            "participants": {},
        })
    result = handle_voice_join({"roomId": room_id}) or {"ok": True, "roomId": room_id, "peers": []}
    return {**result, "created": True}


@socketio.on("voice:signal")
def handle_voice_signal(data):
    username = connections.get(request.sid)
    payload = data or {}
    room_id = (payload.get("roomId") or "").strip()
    target = (payload.get("to") or "").strip().lower()
    signal = payload.get("signal")
    if not username or room_id not in voice_rooms or not target or not isinstance(signal, dict):
        return {"ok": False, "message": "Ses bağlantısı bilgisi eksik."}
    with voice_room_lock:
        room = normalize_voice_room(voice_rooms[room_id])
        if username not in room["participants"] or target not in room["participants"]:
            return {"ok": False, "message": "Kullanıcı sesli odada değil."}
    for sid in connected_sids_for(target):
        socketio.emit(
            "voice:signal",
            {"roomId": room_id, "from": username, "signal": signal},
            room=sid,
        )
    return {"ok": True}


@socketio.on("voice:request_speak")
def handle_voice_request_speak(data):
    username = connections.get(request.sid)
    room_id = ((data or {}).get("roomId") or "").strip()
    if not username or room_id not in voice_rooms:
        return
    with voice_room_lock:
        room = normalize_voice_room(voice_rooms[room_id])
        if username in room["participants"]:
            room["participants"][username]["handRaised"] = True
            room["requests"][username] = now_iso()
    emit_voice_rooms()


@socketio.on("voice:approve_speaker")
def handle_voice_approve_speaker(data):
    username = connections.get(request.sid)
    room_id = ((data or {}).get("roomId") or "").strip()
    target = ((data or {}).get("username") or "").strip().lower()
    accept = (data or {}).get("accept") is not False
    if not username or room_id not in voice_rooms:
        return
    with voice_room_lock:
        room = normalize_voice_room(voice_rooms[room_id])
        if not can_manage_voice_room(room, username):
            emit("notice", {"message": "Oda yönetimi için yetkin yok."})
            return
        room["requests"].pop(target, None)
        if target in room["participants"]:
            room["participants"][target]["handRaised"] = False
            if accept:
                room["participants"][target]["role"] = "speaker"
                room["participants"][target]["muted"] = False
    emit_voice_rooms()


@socketio.on("voice:comment")
def handle_voice_comment(data):
    username = connections.get(request.sid)
    room_id = ((data or {}).get("roomId") or "").strip()
    body = re.sub(r"\s+", " ", ((data or {}).get("body") or "").strip())[:500]
    if not username or room_id not in voice_rooms or not body:
        return
    with voice_room_lock:
        room = normalize_voice_room(voice_rooms[room_id])
        if not room.get("commentsEnabled", True):
            emit("notice", {"message": "Bu odada yorumlar kapalı."})
            return
        labels = ai_moderation_labels(body) if room.get("aiModeration", True) else []
        comment = {
            "id": uuid4().hex,
            "username": username,
            "displayName": db.session.get(User, username).display_name if db.session.get(User, username) else username,
            "body": body,
            "labels": labels,
            "hidden": bool(labels),
            "pinned": False,
            "createdAt": now_iso(),
        }
        room["comments"].append(comment)
        room["comments"] = room["comments"][-100:]
    emit_voice_rooms()


@socketio.on("voice:settings")
def handle_voice_settings(data):
    username = connections.get(request.sid)
    room_id = ((data or {}).get("roomId") or "").strip()
    if not username or room_id not in voice_rooms:
        return
    with voice_room_lock:
        room = normalize_voice_room(voice_rooms[room_id])
        if not can_manage_voice_room(room, username):
            emit("notice", {"message": "Oda ayarları için yetkin yok."})
            return
        for key in ["privacy", "joinMode", "talkMode", "commentsEnabled", "aiModeration", "recording"]:
            if key in data:
                room[key] = data[key]
        if "limit" in data:
            room["limit"] = clamp_voice_room_limit(data.get("limit"), room.get("limit") or 50)
    emit_voice_rooms()


@socketio.on("voice:ban")
def handle_voice_ban(data):
    username = connections.get(request.sid)
    room_id = ((data or {}).get("roomId") or "").strip()
    target = ((data or {}).get("username") or "").strip().lower()
    banned = (data or {}).get("banned") is not False
    if not username or room_id not in voice_rooms or not target:
        return
    with voice_room_lock:
        room = normalize_voice_room(voice_rooms[room_id])
        if not can_manage_voice_room(room, username):
            emit("notice", {"message": "Ban işlemi için yetkin yok."})
            return
        bans = set(room.get("bans") or [])
        if banned:
            bans.add(target)
            room["participants"].pop(target, None)
        else:
            bans.discard(target)
        room["bans"] = sorted(bans)
    emit_voice_rooms()


@socketio.on("disconnect")
def handle_disconnect():
    username = connections.pop(request.sid, None)

    if username:
        departed_voice_rooms = []
        with voice_room_lock:
            for room_id, room in voice_rooms.items():
                if room["participants"].pop(username, None) is not None:
                    departed_voice_rooms.append(room_id)
        for room_id in departed_voice_rooms:
            socketio.emit(
                "voice:peer-left",
                {"roomId": room_id, "username": username},
                room=f"voice:{room_id}",
            )
        user = db.session.get(User, username)
        if user:
            user.last_seen = datetime.now(timezone.utc)
            db.session.commit()

        for chat_id, users_typing in typing_users.items():
            if username in users_typing:
                users_typing.discard(username)
                emit("typing:update", {"chatId": chat_id, "users": sorted(users_typing)}, room=chat_id)

    broadcast_presence()
    emit_voice_rooms()


def background_scheduler():
    while True:
        with app.app_context():
            try:
                expire_due_messages(notify=True)
                deliver_due_scheduled_messages()
            except Exception:
                db.session.rollback()
                app.logger.exception("Background scheduler failed")
        socketio.sleep(SCHEDULE_POLL_SECONDS)


with app.app_context():
    db.create_all()
    inspector = inspect(db.engine)
    chat_member_columns = {column["name"] for column in inspector.get_columns("chat_member")} if inspector.has_table("chat_member") else set()
    if "is_admin" not in chat_member_columns:
        db.session.execute(text("ALTER TABLE chat_member ADD COLUMN is_admin BOOLEAN DEFAULT FALSE NOT NULL"))
        db.session.commit()
    message_columns = {column["name"] for column in inspector.get_columns("message")} if inspector.has_table("message") else set()
    if "reply_to" not in message_columns:
        db.session.execute(text("ALTER TABLE message ADD COLUMN reply_to JSON"))
        db.session.commit()
    message_migrations = {
        "reactions": "ALTER TABLE message ADD COLUMN reactions JSON",
        "versions": "ALTER TABLE message ADD COLUMN versions JSON",
        "deleted_for": "ALTER TABLE message ADD COLUMN deleted_for JSON",
        "deleted_at": "ALTER TABLE message ADD COLUMN deleted_at TIMESTAMP",
        "deleted_by": "ALTER TABLE message ADD COLUMN deleted_by VARCHAR(80)",
        "edited_at": "ALTER TABLE message ADD COLUMN edited_at TIMESTAMP",
        "expires_at": "ALTER TABLE message ADD COLUMN expires_at TIMESTAMP",
    }
    for column_name, statement in message_migrations.items():
        if column_name not in message_columns:
            db.session.execute(text(statement))
            db.session.commit()
    chat_columns = {column["name"] for column in inspector.get_columns("chat")} if inspector.has_table("chat") else set()
    if "image" not in chat_columns:
        db.session.execute(text("ALTER TABLE chat ADD COLUMN image TEXT"))
        db.session.commit()
    call_log_columns = {column["name"] for column in inspector.get_columns("call_log")} if inspector.has_table("call_log") else set()
    if "seen_by" not in call_log_columns:
        db.session.execute(text("ALTER TABLE call_log ADD COLUMN seen_by JSON"))
        db.session.commit()
    user_columns = {column["name"] for column in inspector.get_columns("user")} if inspector.has_table("user") else set()
    user_migrations = {
        "email": "ALTER TABLE \"user\" ADD COLUMN email VARCHAR(255)",
        "email_normalized": "ALTER TABLE \"user\" ADD COLUMN email_normalized VARCHAR(255)",
        "email_verified": "ALTER TABLE \"user\" ADD COLUMN email_verified BOOLEAN DEFAULT FALSE NOT NULL",
        "phone": "ALTER TABLE \"user\" ADD COLUMN phone VARCHAR(24)",
        "phone_normalized": "ALTER TABLE \"user\" ADD COLUMN phone_normalized VARCHAR(24)",
        "phone_verified": "ALTER TABLE \"user\" ADD COLUMN phone_verified BOOLEAN DEFAULT FALSE NOT NULL",
        "profile_image": "ALTER TABLE \"user\" ADD COLUMN profile_image TEXT",
        "avatar_gradient": "ALTER TABLE \"user\" ADD COLUMN avatar_gradient VARCHAR(160)",
        "last_seen": "ALTER TABLE \"user\" ADD COLUMN last_seen TIMESTAMP",
        "hide_last_seen": "ALTER TABLE \"user\" ADD COLUMN hide_last_seen BOOLEAN DEFAULT FALSE NOT NULL",
        "hide_online": "ALTER TABLE \"user\" ADD COLUMN hide_online BOOLEAN DEFAULT FALSE NOT NULL",
        "disable_read_receipts": "ALTER TABLE \"user\" ADD COLUMN disable_read_receipts BOOLEAN DEFAULT FALSE NOT NULL",
        "hide_email": "ALTER TABLE \"user\" ADD COLUMN hide_email BOOLEAN DEFAULT TRUE NOT NULL",
        "privacy_settings": "ALTER TABLE \"user\" ADD COLUMN privacy_settings JSON",
        "points": "ALTER TABLE \"user\" ADD COLUMN points INTEGER DEFAULT 0 NOT NULL",
        "two_factor_enabled": "ALTER TABLE \"user\" ADD COLUMN two_factor_enabled BOOLEAN DEFAULT FALSE NOT NULL",
        "theme_preference": "ALTER TABLE \"user\" ADD COLUMN theme_preference VARCHAR(20) DEFAULT 'dark' NOT NULL",
        "font_size_preference": "ALTER TABLE \"user\" ADD COLUMN font_size_preference VARCHAR(20) DEFAULT 'medium' NOT NULL",
        "notification_sound": "ALTER TABLE \"user\" ADD COLUMN notification_sound VARCHAR(40) DEFAULT 'classic' NOT NULL",
        "ai_settings": "ALTER TABLE \"user\" ADD COLUMN ai_settings JSON",
        "nearby_enabled": "ALTER TABLE \"user\" ADD COLUMN nearby_enabled BOOLEAN DEFAULT FALSE NOT NULL",
        "last_lat": "ALTER TABLE \"user\" ADD COLUMN last_lat FLOAT",
        "last_lng": "ALTER TABLE \"user\" ADD COLUMN last_lng FLOAT",
        "vault_pin_hash": "ALTER TABLE \"user\" ADD COLUMN vault_pin_hash VARCHAR(255)",
        "vault_failed_attempts": "ALTER TABLE \"user\" ADD COLUMN vault_failed_attempts INTEGER DEFAULT 0 NOT NULL",
        "vault_locked_until": "ALTER TABLE \"user\" ADD COLUMN vault_locked_until TIMESTAMP",
        "last_daily_login": "ALTER TABLE \"user\" ADD COLUMN last_daily_login TIMESTAMP",
        "profile_bonus_awarded": "ALTER TABLE \"user\" ADD COLUMN profile_bonus_awarded BOOLEAN DEFAULT FALSE NOT NULL",
    }
    for column_name, statement in user_migrations.items():
        if column_name not in user_columns:
            db.session.execute(text(statement))
            db.session.commit()
    phone_verification_columns = (
        {column["name"] for column in inspector.get_columns("phone_verification")}
        if inspector.has_table("phone_verification")
        else set()
    )
    if "provider" not in phone_verification_columns:
        db.session.execute(text("ALTER TABLE phone_verification ADD COLUMN provider VARCHAR(32) DEFAULT 'local' NOT NULL"))
        db.session.commit()
    db.session.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS ix_user_phone_normalized_unique ON "user" (phone_normalized)'))
    db.session.commit()
    reset_user_data_once()
    db.session.execute(text("UPDATE \"user\" SET last_seen = COALESCE(last_seen, created_at)"))
    db.session.commit()
    for existing_user in User.query.all():
        if not existing_user.points:
            existing_user.points = historical_points(existing_user.username)
    db.session.commit()
    ensure_lobby()
    for group_chat in Chat.query.filter_by(type="group").all():
        promote_fallback_group_admin(group_chat)
    db.session.commit()

if os.environ.get("WERKZEUG_RUN_MAIN") != "true" or os.environ.get("FLASK_DEBUG") != "1":
    socketio.start_background_task(background_scheduler)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
