import os
import ipaddress
import json
import math
import re
import socket
import secrets
import smtplib
import threading
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from uuid import uuid4

import requests
from flask import Flask, jsonify, request, send_from_directory
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, text
from sqlalchemy.orm import joinedload, selectinload
from werkzeug.security import check_password_hash, generate_password_hash

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

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading", max_http_buffer_size=10_000_000)

connections = {}
typing_users = {}
DEV_ADMIN_TOKEN = "NexaLineAdmin2026!"
MAX_BOOTSTRAP_MESSAGES = max(20, int(os.environ.get("MAX_BOOTSTRAP_MESSAGES", "35")))
ADMIN_MESSAGE_LIMIT_PER_CHAT = max(20, int(os.environ.get("ADMIN_MESSAGE_LIMIT_PER_CHAT", "40")))
ADMIN_ARCHIVE_MESSAGE_LIMIT = max(10, int(os.environ.get("ADMIN_ARCHIVE_MESSAGE_LIMIT", "30")))
ADMIN_ATTACHMENT_INLINE_LIMIT = max(20_000, int(os.environ.get("ADMIN_ATTACHMENT_INLINE_LIMIT", "160000")))
SCHEDULE_POLL_SECONDS = max(5, int(os.environ.get("SCHEDULE_POLL_SECONDS", "15")))
MAX_SCHEDULE_DAYS = max(1, int(os.environ.get("MAX_SCHEDULE_DAYS", "7")))
MAX_ATTACHMENT_DATA_URL_CHARS = max(250_000, int(os.environ.get("MAX_ATTACHMENT_DATA_URL_CHARS", "5_500_000")))
RECENT_MESSAGE_SCAN_LIMIT = max(MAX_BOOTSTRAP_MESSAGES * 2, int(os.environ.get("RECENT_MESSAGE_SCAN_LIMIT", "120")))
AI_TIMEOUT_SECONDS = max(4, int(os.environ.get("AI_TIMEOUT_SECONDS", "12")))
AI_MAX_CONTEXT_MESSAGES = max(8, int(os.environ.get("AI_MAX_CONTEXT_MESSAGES", "16")))
AI_MAX_CHATS = max(5, int(os.environ.get("AI_MAX_CHATS", "16")))
QR_LOGIN_TTL_SECONDS = max(60, int(os.environ.get("QR_LOGIN_TTL_SECONDS", "180")))
POINT_RULES = {
    "daily_login": 10,
    "message": 1,
    "friend_invite": 50,
    "friend_accept": 3,
    "group_join": 25,
    "profile_complete": 20,
    "story": 5,
}
scheduled_delivery_lock = threading.Lock()
qr_login_lock = threading.Lock()
qr_login_sessions = {}
voice_room_lock = threading.Lock()
voice_rooms = {
    "general": {"id": "general", "title": "Nexa Meydan", "topic": "Herkese acik sohbet odasi", "participants": {}},
    "study": {"id": "study", "title": "Odak Odasi", "topic": "Sessiz calisma ve kisa molalar", "participants": {}},
    "music": {"id": "music", "title": "Muzik Kosesi", "topic": "Sarki, sohbet ve kesif", "participants": {}},
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
    profile_image = db.Column(db.Text, nullable=True)
    avatar = db.Column(db.String(8), nullable=False)
    hide_last_seen = db.Column(db.Boolean, nullable=False, default=False)
    hide_online = db.Column(db.Boolean, nullable=False, default=False)
    disable_read_receipts = db.Column(db.Boolean, nullable=False, default=False)
    hide_email = db.Column(db.Boolean, nullable=False, default=True)
    points = db.Column(db.Integer, nullable=False, default=0)
    two_factor_enabled = db.Column(db.Boolean, nullable=False, default=False)
    theme_preference = db.Column(db.String(20), nullable=False, default="dark")
    font_size_preference = db.Column(db.String(20), nullable=False, default="medium")
    notification_sound = db.Column(db.String(40), nullable=False, default="classic")
    about = db.Column(db.String(255), nullable=False, default="NexaLine kullanıyorum.")
    temporary_status = db.Column(db.String(80), nullable=True)
    temporary_status_expires_at = db.Column(db.DateTime, nullable=True)
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


class CallLog(db.Model):
    id = db.Column(db.String(40), primary_key=True)
    chat_id = db.Column(db.String(140), db.ForeignKey("chat.id"), nullable=False)
    caller = db.Column(db.String(80), db.ForeignKey("user.username"), nullable=False)
    kind = db.Column(db.String(20), nullable=False, default="audio")
    status = db.Column(db.String(20), nullable=False, default="ended")
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


DEFAULT_DESIGN_SETTINGS = {
    "brandName": "NexaLine",
    "logoUrl": "/static/nexaline-mark.png",
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
        "stories": "Güncellemeler",
        "explore": "Kesfet",
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
        "navOrder": ["calls", "explore", "chats", "contacts", "settings"],
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
    story_points = Story.query.filter_by(username=username).count() * 5
    friend_points = ContactRequest.query.filter(
        ContactRequest.status == "accepted",
        db.or_(ContactRequest.from_username == username, ContactRequest.to_username == username),
    ).count() * 3
    return message_points + received_points + story_points + friend_points


def point_level(points):
    points = max(0, int(points or 0))
    level = points // 250 + 1
    current_floor = (level - 1) * 250
    next_floor = level * 250
    progress = 100 if next_floor == current_floor else int(((points - current_floor) / (next_floor - current_floor)) * 100)
    return {
        "level": level,
        "title": "Nexa Ustasi" if level >= 10 else "Nexa Elcisi" if level >= 5 else "Nexa Kesifcisi",
        "current": points,
        "next": next_floor,
        "progress": max(0, min(100, progress)),
    }


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
    if not user or user.profile_bonus_awarded:
        return False
    if user.display_name and user.email and user.about and user.profile_image:
        user.profile_bonus_awarded = True
        add_points(user.username, POINT_RULES["profile_complete"], "profile_complete")
        return True
    return False


def active_temp_status(user):
    if not user or not user.temporary_status:
        return None
    expires_at = user.temporary_status_expires_at
    now = datetime.now(timezone.utc)
    if expires_at:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= now:
            user.temporary_status = None
            user.temporary_status_expires_at = None
            return None
    return {"text": user.temporary_status, "expiresAt": to_iso(user.temporary_status_expires_at) if user.temporary_status_expires_at else None}


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


def voice_rooms_state(viewer=None):
    with voice_room_lock:
        rooms = []
        for room in voice_rooms.values():
            participants = []
            for username, data in room["participants"].items():
                participant = public_user(username, viewer)
                participant.update({
                    "muted": bool(data.get("muted")),
                    "speaking": bool(data.get("speaking")),
                    "joinedAt": data.get("joinedAt"),
                })
                participants.append(participant)
            rooms.append({
                "id": room["id"],
                "title": room["title"],
                "topic": room["topic"],
                "participants": participants,
                "count": len(participants),
            })
        return rooms


def public_user(username, viewer=None):
    user = db.session.get(User, username)
    is_self = viewer == username
    blocked = bool(viewer and viewer != username and is_blocked_between(viewer, username))
    online = any(name == username for name in connections.values())
    show_online = not blocked and (is_self or not bool(user.hide_online if user else False))
    show_last_seen = not blocked and (is_self or not bool(user.hide_last_seen if user else False))
    show_email = not blocked and (is_self or not bool(user.hide_email if user else True))
    temp_status = None if blocked else active_temp_status(user)
    points = 0 if blocked else user_points(username) if user else 0
    return {
        "username": username,
        "displayName": user.display_name if user else username,
        "avatar": user.avatar if user else username[:2].upper(),
        "profileImage": None if blocked else user.profile_image if user else None,
        "about": "" if blocked else user.about if user else "NexaLine kullanıyorum.",
        "createdAt": to_iso(user.created_at) if user else now_iso(),
        "points": points,
        "pointLevel": point_level(points),
        "temporaryStatus": temp_status,
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
    }


def private_user(username):
    user = db.session.get(User, username)
    data = public_user(username, username)
    if user:
        data["email"] = user.email
        data["emailVerified"] = user.email_verified
        data["privacy"] = {
            "lastSeenHidden": bool(user.hide_last_seen),
            "onlineHidden": bool(user.hide_online),
            "readReceiptsOff": bool(user.disable_read_receipts),
            "emailHidden": bool(user.hide_email),
        }
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
    return {
        "id": story.id,
        "username": story.username,
        "user": public_user(story.username, viewer),
        "body": story.body,
        "attachment": story.attachment,
        "createdAt": to_iso(story.created_at),
        "expiresAt": to_iso(story.expires_at),
    }


def active_stories(viewer=None):
    now = datetime.now(timezone.utc)
    Story.query.filter(Story.expires_at <= now).delete(synchronize_session=False)
    db.session.commit()
    stories = Story.query.filter(Story.expires_at > now).order_by(Story.created_at.desc()).all()
    return [story_to_dict(story, viewer) for story in stories]


def attachment_error(attachment):
    if not attachment:
        return None
    if not isinstance(attachment, dict):
        return "Dosya bilgisi okunamadı."

    data_url = attachment.get("dataUrl") or ""
    if data_url and len(str(data_url)) > MAX_ATTACHMENT_DATA_URL_CHARS:
        return "Dosya çok büyük. Daha küçük dosya seç veya resmi sıkıştır."

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
    return {
        "id": log.id,
        "chatId": log.chat_id,
        "chatTitle": chat_for_user(chat, username)["title"] if chat and user_can_see_chat(chat, username) else "Arama",
        "caller": log.caller,
        "callerName": log.caller_user.display_name if log.caller_user else log.caller,
        "kind": log.kind,
        "status": log.status,
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


def app_state_for_user(username):
    return {
        "user": private_user(username),
        "users": public_users_for(username),
        "chats": visible_chats(username),
        "generalGroup": general_group_state(username),
        "stories": active_stories(username),
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
        "nearbyUsers": nearby_users_for(username),
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
    expected_token = os.environ.get("ADMIN_TOKEN") or (None if os.environ.get("RENDER") else DEV_ADMIN_TOKEN)
    if is_local_admin_request():
        return None

    if not expected_token:
        return jsonify({"ok": False, "message": "ADMIN_TOKEN ayarlı değil."}), 503

    if admin_token_from_request() != expected_token:
        return jsonify({"ok": False, "message": "Yönetici token hatalı."}), 401

    return None


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

    if len(username) > 24:
        return "Kullanıcı adı en fazla 24 karakter olmalı."

    if not re.fullmatch(r"[a-z0-9_]+", username):
        return "Kullanıcı adı sadece küçük harf, sayı ve alt çizgi içerebilir."

    return None


def normalize_email(email):
    email = (email or "").strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return None, None

    local, domain = email.rsplit("@", 1)
    if domain in {"gmail.com", "googlemail.com"}:
        local = local.split("+", 1)[0].replace(".", "")
        domain = "gmail.com"

    return email, f"{local}@{domain}"


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


def email_subject(purpose):
    labels = {
        "register": "NexaLine kayıt doğrulama kodun",
        "forgot": "NexaLine şifre sıfırlama kodun",
        "email_change": "NexaLine Gmail değiştirme kodun",
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

    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_username = os.environ.get("SMTP_USERNAME")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    mail_from = os.environ.get("MAIL_FROM") or smtp_username

    if not smtp_host or not smtp_username or not smtp_password or not mail_from:
        app.logger.warning("SMTP ayarları eksik. %s doğrulama kodu: %s", email, code)
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
    if not sent and os.environ.get("RENDER"):
        return jsonify({"ok": False, "message": "Mail gonderilemedi. Render mail servisi ayarlarini kontrol et."}), 503

    response = {"ok": True, "requiresVerification": True, "message": message}
    if not sent:
        response["message"] += " Mail ayarları eksik olduğu için kod sunucu loglarına yazıldı."
        if not os.environ.get("RENDER"):
            response["devCode"] = code
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

ADULT_TERMS = {"+18", "porno", "porn", "cinsel", "nude", "nudes", "seks", "sex", "erotik", "onlyfans"}
ABUSE_TERMS = {"salak", "aptal", "gerizekali", "gerizekalı", "mal", "orospu", "siktir", "amk", "aq"}


def ai_provider_status():
    provider = (os.environ.get("AI_PROVIDER") or "auto").strip().lower()
    if provider in {"gemini", "google"} or (provider == "auto" and os.environ.get("GEMINI_API_KEY")):
        return {"provider": "gemini", "model": os.environ.get("GEMINI_MODEL", "gemini-1.5-flash"), "ready": bool(os.environ.get("GEMINI_API_KEY"))}
    if provider in {"openai", "openai-compatible"} or (provider == "auto" and os.environ.get("OPENAI_API_KEY")):
        return {"provider": "openai", "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"), "ready": bool(os.environ.get("OPENAI_API_KEY"))}
    if provider == "ollama" or os.environ.get("OLLAMA_BASE_URL"):
        return {"provider": "ollama", "model": os.environ.get("OLLAMA_MODEL", os.environ.get("AI_MODEL", "llama3.2")), "ready": True}
    return {"provider": "local", "model": "nexaline-free-ai", "ready": True, "free": True}


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


def ai_context_for_user(username, chat_id=None):
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


def ai_detect_actions(prompt, username, active_chat_id=None, timezone_offset_minutes=0):
    actions = []
    lowered = (prompt or "").lower()
    chat = find_chat_for_ai(username, prompt, active_chat_id)
    mentioned_users = ai_mentioned_usernames(prompt, username)
    if any(word in lowered for word in ["açık tema", "acik tema", "light theme", "beyaz tema"]):
        actions.append({"type": "set_theme", "theme": "light", "label": "Temayı açık yap"})
    if any(word in lowered for word in ["koyu tema", "dark theme", "gece modu", "siyah tema"]):
        actions.append({"type": "set_theme", "theme": "dark", "label": "Temayı koyu yap"})
    wants_censor_off = (
        any(word in lowered for word in ["sansürü kapat", "sansuru kapat", "sansür filtresini kapat", "sansur filtresini kapat", "filtreyi kapat", "sansür kapansın", "sansur kapansin"])
        or (("sansür" in lowered or "sansur" in lowered or "filtre" in lowered) and any(word in lowered for word in ["kapat", "kapalı", "kapali", "kapansın", "kapansin"]))
    )
    wants_censor_on = (
        any(word in lowered for word in ["sansürü aç", "sansuru ac", "sansür aç", "sansur ac", "+18 filtre", "filtreyi aç", "filtreyi ac"])
        or (("sansür" in lowered or "sansur" in lowered or "filtre" in lowered) and any(word in lowered for word in ["aç", "ac", "açık", "acik", "açılsın", "acilsin"]))
    )
    if wants_censor_off:
        actions.append({"type": "set_censor", "enabled": False, "label": "AI sansür filtresini kapat"})
    elif wants_censor_on or ("sansür" in lowered or "sansur" in lowered or "+18" in lowered or "filtre" in lowered):
        actions.append({"type": "set_censor", "enabled": True, "label": "AI sansür filtresini aç"})
    if any(word in lowered for word in ["next ai kapat", "nexa ai kapat", "nex ai kapat", "asistanı kapat", "asistani kapat", "yapay zekayı kapat", "yapay zekayi kapat"]):
        actions.append({"type": "set_ai_enabled", "enabled": False, "label": "Nexa AI'ı kapat"})
    if any(word in lowered for word in ["next ai aç", "next ai ac", "nexa ai aç", "nexa ai ac", "nex ai aç", "nex ai ac", "asistanı aç", "asistani ac", "yapay zekayı aç", "yapay zekayi ac"]):
        actions.append({"type": "set_ai_enabled", "enabled": True, "label": "Nexa AI'ı aç"})
    if any(word in lowered for word in ["tam yetki ver", "tam erişim ver", "tam erisim ver", "onaysız yap", "onaysiz yap", "izin almadan yap", "direkt yap"]):
        actions.append({"type": "set_ai_auto_approve", "enabled": True, "label": "Nexa AI tam yetkisini aç"})
    if any(word in lowered for word in ["tam yetki kapat", "tam erişimi kapat", "tam erisimi kapat", "onay al", "önce onay", "once onay"]):
        actions.append({"type": "set_ai_auto_approve", "enabled": False, "label": "Nexa AI her işlemde onay alsın"})
    ai_name = extract_value_after_phrases(
        prompt,
        [
            r"(?:next ai adını|next ai adini|nex ai adını|nex ai adini|asistan adını|asistan adini|senin adın|senin adin|adını|adini)",
        ],
        40,
    )
    if ai_name and any(word in lowered for word in ["değiştir", "degistir", "yap", "olsun"]):
        actions.append({"type": "set_ai_name", "name": ai_name, "label": f"Nexa AI adını “{ai_name}” yap"})
    settings_section = None
    if "next ai ayar" in lowered or "nexa ai ayar" in lowered or "ai ayar" in lowered or "asistan ayar" in lowered:
        settings_section = "ai"
    elif "gizlilik ayar" in lowered:
        settings_section = "privacy"
    elif "görünüm ayar" in lowered or "gorunum ayar" in lowered or "tema ayar" in lowered:
        settings_section = "appearance"
    elif "profil ayar" in lowered:
        settings_section = "profile"
    elif "hesap ayar" in lowered:
        settings_section = "account"
    elif "ayarları aç" in lowered or "ayarlari ac" in lowered or "ayarları göster" in lowered or "ayarlari goster" in lowered:
        settings_section = "menu"
    if settings_section:
        actions.append({"type": "open_settings", "section": settings_section, "label": "Ayarları aç"})
    privacy = {}
    if ("son görülme" in lowered or "son gorulme" in lowered) and any(word in lowered for word in ["gizle", "kapat", "kapalı", "kapali"]):
        privacy["lastSeenHidden"] = True
    if ("son görülme" in lowered or "son gorulme" in lowered) and any(word in lowered for word in ["göster", "goster", "aç", "ac", "açık", "acik"]):
        privacy["lastSeenHidden"] = False
    if ("çevrim içi" in lowered or "cevrim ici" in lowered or "online" in lowered) and any(word in lowered for word in ["gizle", "kapat", "kapalı", "kapali"]):
        privacy["onlineHidden"] = True
    if ("çevrim içi" in lowered or "cevrim ici" in lowered or "online" in lowered) and any(word in lowered for word in ["göster", "goster", "aç", "ac", "açık", "acik"]):
        privacy["onlineHidden"] = False
    if ("okundu" in lowered or "mavi tik" in lowered) and any(word in lowered for word in ["kapat", "kapalı", "kapali", "gizle"]):
        privacy["readReceiptsOff"] = True
    if ("okundu" in lowered or "mavi tik" in lowered) and any(word in lowered for word in ["aç", "ac", "açık", "acik", "göster", "goster"]):
        privacy["readReceiptsOff"] = False
    if "gmail" in lowered and any(word in lowered for word in ["gizle", "kapat", "kapalı", "kapali"]):
        privacy["emailHidden"] = True
    if "gmail" in lowered and any(word in lowered for word in ["göster", "goster", "aç", "ac", "açık", "acik"]):
        privacy["emailHidden"] = False
    if privacy:
        actions.append({"type": "set_privacy", "privacy": privacy, "label": "Gizlilik ayarlarını güncelle"})
    new_name = extract_value_after_phrases(
        prompt,
        [
            r"(?:ismimi|adımı|adimi|profil adımı|profil adimi)",
            r"(?:görünen adımı|gorunen adimi|kullanıcı adımı değil ismimi|kullanici adimi degil ismimi)",
        ],
        80,
    )
    if new_name and any(word in lowered for word in ["değiştir", "degistir", "yap", "olsun"]):
        actions.append({"type": "update_profile", "displayName": new_name, "label": f"Adımı “{new_name}” yap"})
    new_about = extract_value_after_phrases(prompt, [r"(?:hakkımda|hakkimda|bio|biyografi)"], 180)
    if new_about and any(word in lowered for word in ["değiştir", "degistir", "yap", "olsun", "yaz"]):
        actions.append({"type": "update_profile", "about": new_about, "label": "Hakkımda bilgisini güncelle"})
    if chat and any(word in lowered for word in ["sabitle", "pinle"]):
        actions.append({"type": "set_chat_pref", "chatId": chat["id"], "pinned": True, "label": f"{chat['title']} sohbetini sabitle"})
    if chat and any(word in lowered for word in ["sessize al", "sustur", "bildirim kapat"]):
        actions.append({"type": "set_chat_pref", "chatId": chat["id"], "muted": True, "label": f"{chat['title']} sohbetini sessize al"})
    if chat and any(word in lowered for word in ["gizli sohbet", "kilitle", "sakla"]):
        actions.append({"type": "set_chat_pref", "chatId": chat["id"], "locked": True, "label": f"{chat['title']} sohbetini kilitle"})
    if chat and any(word in lowered for word in ["sohbeti aç", "sohbeti ac", "sohbet aç", "sohbet ac", "mesaj kutusunu aç", "mesaj kutusunu ac", "chat aç", "chat ac"]):
        actions.append({"type": "open_chat", "chatId": chat["id"], "label": f"{chat['title']} sohbetini aç"})
    if chat and any(word in lowered for word in ["sohbeti sil", "sohbet sil", "konuşmayı sil", "konusmayi sil", "listemden kaldır", "listemden kaldir"]):
        delete_mode = "permanent" if any(word in lowered for word in ["kalıcı", "kalici", "tamamen"]) else "archive"
        actions.append({"type": "delete_chat", "chatId": chat["id"], "mode": delete_mode, "label": f"{chat['title']} sohbetini sil"})
    wants_call = chat and not any(word in lowered for word in ["araştır", "arastir", "webde ara", "internette ara"]) and any(
        word in lowered for word in ["arama yap", "sesli ara", "görüntülü ara", "goruntulu ara", "telefon et", "kameralı ara", "kamerali ara"]
    )
    if wants_call:
        video_call = any(word in lowered for word in ["görüntülü", "goruntulu", "kamera", "kameralı", "kamerali", "video"])
        call_at = parse_ai_schedule(prompt, timezone_offset_minutes)
        if call_at:
            actions.append({"type": "schedule_call", "chatId": chat["id"], "audioOnly": not video_call, "callAt": call_at, "label": f"{chat['title']} için planlı arama başlat"})
        else:
            actions.append({"type": "start_call", "chatId": chat["id"], "audioOnly": not video_call, "label": f"{chat['title']} için {'görüntülü' if video_call else 'sesli'} arama başlat"})
    if any(word in lowered for word in ["aramayı kapat", "aramayi kapat", "aramayı bitir", "aramayi bitir", "çağrıyı kapat", "cagriyi kapat"]):
        actions.append({"type": "end_call", "label": "Aktif aramayı kapat"})
    if chat and any(word in lowered for word in ["ifade bırak", "ifade birak", "tepki bırak", "tepki birak", "reaksiyon", "emoji bırak", "emoji birak"]):
        target = ai_last_target_message(chat, username)
        emoji = "❤️" if any(word in lowered for word in ["kalp", "beğen", "begen", "sevgi"]) else "👍"
        if target:
            actions.append({"type": "react_message", "chatId": chat["id"], "messageId": target["id"], "emoji": emoji, "label": f"{chat['title']} son mesaja tepki bırak"})
    if chat and any(word in lowered for word in ["yanıtla", "yanitla", "cevap ver", "reply"]):
        target = ai_last_target_message(chat, username)
        reply_body = extract_quoted_text(prompt) or "Tamam, gördüm. Birazdan net döneceğim."
        if target:
            actions.append({"type": "reply_message", "chatId": chat["id"], "messageId": target["id"], "body": reply_body, "label": f"{chat['title']} son mesaja yanıt ver"})
    if any(word in lowered for word in ["grup aç", "grup ac", "grup oluştur", "grup olustur", "yeni grup"]):
        title = extract_quoted_text(prompt) or extract_value_after_phrases(prompt, [r"(?:grup adı|grup adi|grubun adı|grubun adi)"], 80) or "Yeni grup"
        actions.append({"type": "create_group", "title": title, "members": mentioned_users, "label": f"“{title}” grubunu oluştur"})
    if chat and chat.get("type") == "group" and any(word in lowered for word in ["grup adını", "grup adini", "grubun adını", "grubun adini", "gruba ekle", "gruba kişi ekle", "gruba kisi ekle"]):
        title = extract_value_after_phrases(prompt, [r"(?:grup adını|grup adini|grubun adını|grubun adini)"], 80)
        action = {"type": "update_group", "chatId": chat["id"], "members": mentioned_users, "label": f"{chat['title']} grubunu yönet"}
        if title:
            action["title"] = title
        actions.append(action)
    if any(word in lowered for word in ["güncelleme paylaş", "guncelleme paylas", "durum paylaş", "durum paylas", "story paylaş", "story paylas"]):
        body_text = extract_quoted_text(prompt) or extract_value_after_phrases(prompt, [r"(?:güncelleme|guncelleme|durum|story)"], 220)
        if body_text:
            actions.append({"type": "create_story", "body": body_text, "label": "Yeni güncelleme paylaş"})
    if any(word in lowered for word in ["güncellemeyi sil", "guncellemeyi sil", "durumu sil", "story sil", "eski güncellemeyi sil", "eski guncellemeyi sil"]):
        actions.append({"type": "delete_story", "label": "Son güncellemeyi sil"})
    if mentioned_users and any(word in lowered for word in ["istek at", "istek gönder", "istek gonder", "mesajlaşma isteği", "mesajlasma istegi", "sohbet isteği", "sohbet istegi"]):
        actions.append({"type": "contact_request", "username": mentioned_users[0], "label": "Mesajlaşma isteği gönder"})
    if any(word in lowered for word in ["bildirimleri aç", "bildirimleri ac", "eski bildirim", "bildirim geçmişi", "bildirim gecmisi"]):
        actions.append({"type": "open_notifications", "label": "Bildirimleri aç"})
    body = extract_quoted_text(prompt)
    wants_send_message = any(word in lowered for word in ["mesaj at", "mesaj gönder", "mesaj gonder", "gönder", "gonder"])
    wants_draft_message = any(word in lowered for word in ["yaz", "cevap hazırla", "cevap hazirla", "taslak"])
    wants_message = wants_send_message or wants_draft_message
    if chat and wants_message:
        send_at = parse_ai_schedule(prompt, timezone_offset_minutes)
        if send_at:
            body = body or "Mesaj taslağını buraya yazabilirsin."
            actions.append({"type": "schedule_message", "chatId": chat["id"], "body": body, "sendAt": send_at, "label": f"{chat['title']} için zamanlı mesaj hazırla"})
        elif wants_send_message and body:
            actions.append({"type": "send_message", "chatId": chat["id"], "body": body, "label": f"{chat['title']} sohbetine mesajı gönder"})
        else:
            body = body or "Mesaj taslağını buraya yazabilirsin."
            actions.append({"type": "draft_message", "chatId": chat["id"], "body": body, "label": f"{chat['title']} için mesajı kutuya hazırla"})
    return actions


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
        (["durum", "story", "hikaye"], "Güncellemeler bölümünden 24 saatlik durum paylaşabilir, gelen durumlara cevap verebilirsin."),
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


def local_should_research(prompt):
    lowered = (prompt or "").casefold()
    if len(lowered) < 12:
        return False
    research_words = [
        "bugün", "bugun", "güncel", "guncel", "haber", "son dakika", "fiyat", "kaç tl",
        "kimdir", "nedir", "ne zaman", "nerede", "hangi", "kaç", "kac",
    ]
    app_words = ["sohbet", "mesaj", "tema", "gizlilik", "şifre", "sifre", "grup", "arama", "durum"]
    return any(word in lowered for word in research_words) and not any(word in lowered for word in app_words)


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
    location_match = re.search(r"(?:hava|weather|sıcaklık|sicaklik)\s+(?:durumu|nasıl|nasil)?\s*(?:için|icin|de|da)?\s*([\wçğıöşüÇĞİÖŞÜ\s]{3,40})", prompt or "", re.IGNORECASE)
    if location_match:
        candidate = re.sub(r"\b(nasıl|nasil|kaç|kac|derece|bugün|bugun)\b", " ", location_match.group(1), flags=re.IGNORECASE).strip()
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
    research_answer = local_research_answer(prompt, research)
    if research_answer:
        return research_answer
    live_info = context.get("liveInfo") or {}
    if any(word in lowered for word in ["saat kaç", "saat kac", "saat ne", "bugün tarih", "bugun tarih"]):
        return f"Şu an saat {live_info.get('time')}, tarih {live_info.get('date')}."
    if any(word in lowered for word in ["hava", "weather", "sıcaklık", "sicaklik"]):
        weather = live_info.get("weather")
        if weather:
            return f"{weather.get('location')} için hava: {weather.get('temperatureC')}°C, nem %{weather.get('humidityPercent')}, rüzgar {weather.get('windKmh')} km/sa."
        return "Hava durumu bilgisini şu an alamadım; bağlantı veya konum servisi yanıt vermemiş olabilir."
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
            "Bunu şöyle ele alırım: "
            + ", ".join(keywords[:4])
            + " başlıklarını netleştirip küçük adımlara bölelim. "
            "İstersen bana hedefini, mevcut durumu ve takıldığın noktayı yaz; buna göre daha iyi bir taslak veya çözüm planı çıkarırım."
        )
    return f"{assistant_name} ücretsiz yerel modda hazır. Uygulama içi komutlar, sohbet özeti, cevap taslağı, basit web özeti ve güvenlik filtresi çalışıyor."


def web_research_if_requested(prompt, force=False):
    if not force and not re.search(r"\b(araştır|arastir|internette|webde|google|haber|güncel|guncel)\b", prompt or "", re.IGNORECASE):
        return []
    query = re.sub(r"\b(araştır|arastir|internette|webde|google|haber|güncel|guncel)\b", " ", prompt or "", flags=re.IGNORECASE).strip()
    if len(query) < 3:
        return []
    try:
        response = requests.get("https://api.duckduckgo.com/", params={"q": query[:160], "format": "json", "no_html": "1", "skip_disambig": "1"}, timeout=6)
        response.raise_for_status()
        data = response.json()
    except Exception:
        return []
    results = []
    if data.get("AbstractText"):
        results.append({"title": data.get("Heading") or query, "snippet": data.get("AbstractText"), "url": data.get("AbstractURL")})
    for item in data.get("RelatedTopics", [])[:6]:
        if isinstance(item, dict) and item.get("Text"):
            results.append({"title": item.get("FirstURL") or "Kaynak", "snippet": item.get("Text"), "url": item.get("FirstURL")})
    return results[:5]


def call_gemini_ai(prompt, context_text, research):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY missing")
    model = os.environ.get("GEMINI_MODEL", "gemini-1.5-flash")
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        params={"key": key},
        json={
            "systemInstruction": {"parts": [{"text": AI_SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": f"Uygulama bağlamı:\n{context_text}\n\nWeb araştırma notları:\n{json.dumps(research, ensure_ascii=False)}\n\nKullanıcı:\n{prompt}"}]}],
            "generationConfig": {"temperature": 0.45, "maxOutputTokens": 900},
        },
        timeout=AI_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    parts = response.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
    return "\n".join(part.get("text", "") for part in parts).strip()


def call_openai_ai(prompt, context_text, research):
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
                {"role": "user", "content": f"Uygulama bağlamı:\n{context_text}\n\nWeb araştırma notları:\n{json.dumps(research, ensure_ascii=False)}\n\nKullanıcı:\n{prompt}"},
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


def generate_ai_reply(prompt, context, actions):
    provider = ai_provider_status()
    context_text = json.dumps(context, ensure_ascii=False, indent=2)
    research = web_research_if_requested(prompt)
    if provider["provider"] == "local" and not research and local_should_research(prompt):
        research = web_research_if_requested(prompt, force=True)
    try:
        if provider["provider"] == "gemini" and provider["ready"]:
            reply = call_gemini_ai(prompt, context_text, research)
        elif provider["provider"] == "openai" and provider["ready"]:
            reply = call_openai_ai(prompt, context_text, research)
        elif provider["provider"] == "ollama":
            reply = call_ollama_ai(prompt, context_text, research)
        else:
            reply = local_ai_reply(prompt, context, actions, research)
    except Exception as error:
        app.logger.warning("AI provider failed: %s", error)
        provider = {"provider": "local", "model": "nexaline-free-ai", "ready": True, "free": True}
        reply = local_ai_reply(prompt, context, actions, research)
    if research and "Kaynak" not in reply:
        sources = "\n".join(f"- {item.get('title')}: {item.get('url')}" for item in research if item.get("url"))
        if sources:
            reply = f"{reply}\n\nKaynaklar:\n{sources}"
    return reply, provider, research


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


@app.route("/manifest.webmanifest")
def manifest():
    return send_from_directory("static", "manifest.webmanifest", mimetype="application/manifest+json")


@app.route("/sw.js")
def service_worker():
    response = send_from_directory("static", "sw.js", mimetype="application/javascript")
    response.headers["Cache-Control"] = "no-cache"
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


@app.route("/ai/status")
def ai_status():
    status = ai_provider_status()
    return jsonify({"ok": True, "ai": status, "moderation": True, "actions": True})


@app.route("/ai/chat", methods=["POST"])
def ai_chat():
    data = request.get_json() or {}
    username = (data.get("username") or "").strip().lower()
    prompt = (data.get("prompt") or "").strip()
    chat_id = data.get("chatId")
    assistant_name = re.sub(r"\s+", " ", (data.get("assistantName") or "Nexa AI").strip())[:40] or "Nexa AI"

    if not username or not db.session.get(User, username):
        return jsonify({"ok": False, "message": "Önce giriş yapmalısın."}), 401
    if not prompt:
        return jsonify({"ok": False, "message": "AI için bir şey yaz."}), 400
    if len(prompt) > 2500:
        return jsonify({"ok": False, "message": "AI isteği çok uzun."}), 400

    context = ai_context_for_user(username, chat_id)
    context["assistant"] = {"name": assistant_name}
    context["liveInfo"] = live_info_for_prompt(prompt, data.get("timezoneOffsetMinutes", 0))
    actions = ai_detect_actions(prompt, username, chat_id, data.get("timezoneOffsetMinutes", 0))
    reply, provider, research = generate_ai_reply(prompt, context, actions)
    return jsonify(
        {
            "ok": True,
            "reply": reply,
            "actions": actions,
            "provider": provider,
            "research": research,
        }
    )


@app.route("/ai/moderate", methods=["POST"])
def ai_moderate():
    data = request.get_json() or {}
    username = (data.get("username") or "").strip().lower()
    if not username or not db.session.get(User, username):
        return jsonify({"ok": False, "message": "Önce giriş yapmalısın."}), 401
    text_value = (data.get("text") or "")[:4000]
    labels = ai_moderation_labels(text_value)
    return jsonify({"ok": True, "labels": labels, "blocked": bool(labels), "reason": ", ".join(labels)})


@app.route("/ai/chat-summary", methods=["POST"])
def ai_chat_summary_route():
    data = request.get_json() or {}
    username = (data.get("username") or "").strip().lower()
    chat = db.session.get(Chat, data.get("chatId"))
    if not username or not chat or not user_can_see_chat(chat, username):
        return jsonify({"ok": False, "message": "Sohbet bulunamadi."}), 404
    messages = [message_to_dict(item) for item in recent_visible_messages(chat.id, username, min(80, RECENT_MESSAGE_SCAN_LIMIT))]
    context = {"user": private_user(username), "activeChat": {"title": chat.title, "messages": messages}, "assistant": {"name": "Nexa AI"}}
    prompt = f"{chat.title} sohbetini kisa, islevsel ve maddeli ozetle. Onemli karar, tarih, dosya ve bekleyen aksiyonlari ayir."
    reply, provider, research = generate_ai_reply(prompt, context, [])
    return jsonify({"ok": True, "summary": reply or local_chat_summary(messages), "provider": provider, "research": research})


@app.route("/ai/search", methods=["POST"])
def ai_search_route():
    data = request.get_json() or {}
    username = (data.get("username") or "").strip().lower()
    query = (data.get("query") or "").strip()
    chat_id = data.get("chatId")
    if not username or not db.session.get(User, username):
        return jsonify({"ok": False, "message": "Once giris yapmalisin."}), 401
    if len(query) < 2:
        return jsonify({"ok": False, "message": "Arama metni cok kisa."}), 400
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
    summary_prompt = f"Arama sorgusu: {query}\nSonuclari kullaniciya kisa acikla ve en yakin 5 sonucu sec."
    context = {"user": private_user(username), "matches": matches[:12], "assistant": {"name": "Nexa AI"}}
    reply, provider, research = generate_ai_reply(summary_prompt, context, [])
    return jsonify({"ok": True, "answer": reply, "results": matches[:20], "provider": provider, "research": research})


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
        email = (data.get("email") or "").strip()
        password = data.get("password") or ""

        username_problem = username_error(username)
        if username_problem:
            return jsonify({"ok": False, "message": username_problem}), 400

        email_problem = email_error(email)
        if email_problem:
            return jsonify({"ok": False, "message": email_problem}), 400

        email, email_normalized = normalize_email(email)

        if db.session.get(User, username):
            return jsonify({"ok": False, "message": "Bu kullanıcı adı zaten kayıtlı. Farklı bir kullanıcı adı dene."}), 409

        if email_exists(email_normalized):
            return jsonify({"ok": False, "message": "Bu Gmail zaten bir hesapta kullanılıyor."}), 409

        password_hash = None
        if password:
            password_problem = password_error(username, password)
            if password_problem:
                return jsonify({"ok": False, "message": password_problem}), 400
            password_hash = generate_password_hash(password)

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


@app.route("/register/verify", methods=["POST"])
def register_verify():
    try:
        data = request.get_json() or {}
        username = (data.get("username") or "").strip().lower()
        email = (data.get("email") or "").strip()
        code = (data.get("code") or "").strip()
        password = data.get("password") or ""
        confirm_password = data.get("confirmPassword")
        display_name, display_name_error = normalize_display_name(data)
        if display_name_error:
            return jsonify({"ok": False, "message": display_name_error}), 400

        email_problem = email_error(email)
        if email_problem:
            return jsonify({"ok": False, "message": email_problem}), 400

        email, email_normalized = normalize_email(email)
        verification = EmailVerification.query.filter_by(
            purpose="register",
            username=username,
            email_normalized=email_normalized,
        ).order_by(EmailVerification.created_at.desc()).first()

        if not verification:
            return jsonify({"ok": False, "message": "Doğrulama kaydı bulunamadı. Kayıt işlemini yeniden başlat."}), 404

        if is_past(verification.expires_at):
            return jsonify({"ok": False, "message": "Doğrulama kodunun süresi doldu."}), 400

        if verification.attempts >= 5:
            return jsonify({"ok": False, "message": "Çok fazla yanlış deneme yaptın. Yeni kod iste."}), 429

        if not check_password_hash(verification.code_hash, code):
            verification.attempts += 1
            db.session.commit()
            return jsonify({"ok": False, "message": "Doğrulama kodu hatalı."}), 400

        if db.session.get(User, username):
            return jsonify({"ok": False, "message": "Bu kullanıcı adı zaten kayıtlı."}), 409

        if email_exists(email_normalized):
            return jsonify({"ok": False, "message": "Bu Gmail zaten bir hesapta kullanılıyor."}), 409

        if not password and not verification.password_hash:
            return jsonify({"ok": True, "message": "Kod dogrulandi. Simdi guclu sifreni olustur.", "requiresPassword": True})

        if password:
            if confirm_password is not None and password != confirm_password:
                return jsonify({"ok": False, "message": "Yazdigin iki sifre ayni degil."}), 400
            password_problem = password_error(username, password)
            if password_problem:
                return jsonify({"ok": False, "message": password_problem}), 400
            password_hash = generate_password_hash(password)
        else:
            password_hash = verification.password_hash

        if not password_hash:
            return jsonify({"ok": False, "message": "Şifre oluşturulmadan kayıt tamamlanamaz."}), 400

        db.session.add(
            User(
                username=username,
                password_hash=password_hash,
                display_name=display_name[:120] or username,
                email=verification.email,
                email_normalized=verification.email_normalized,
                email_verified=True,
                avatar=tr_upper((display_name or username)[:2]),
                about="NexaLine kullanıyorum.",
            )
        )
        db.session.delete(verification)
        ensure_lobby()
        db.session.commit()
        return jsonify({"ok": True, "message": "Kayıt başarılı.", "user": private_user(username)})
    except Exception:
        db.session.rollback()
        app.logger.exception("Register verify failed")
        return jsonify({"ok": False, "message": "Doğrulama tamamlanamadı."}), 500


@app.route("/login", methods=["POST"])
def login():
    data = request.get_json() or {}
    identifier = (data.get("email") or data.get("username") or "").strip().lower()
    password = data.get("password") or ""
    device_id = data.get("deviceId") or request.headers.get("X-Nexa-Device")
    user = None
    if "@" in identifier:
        user = User.query.filter(db.func.lower(User.email_normalized) == identifier.lower()).first()
    if not user:
        user = db.session.get(User, identifier)

    if not user or not check_password_hash(user.password_hash, password):
        return jsonify({"ok": False, "message": "Gmail veya şifre hatalı."}), 401

    if user.two_factor_enabled:
        if not user.email_normalized:
            return jsonify({"ok": False, "message": "2FA için Gmail gerekli. Yöneticiyle iletişime geç."}), 400
        _, code, sent = create_email_verification("login_2fa", user.email, user.email_normalized, username=user.username)
        response = {"ok": True, "requiresTwoFactor": True, "message": "Gmail adresine giriş doğrulama kodu gönderdik.", "username": user.username}
        if not sent:
            if os.environ.get("RENDER"):
                return jsonify({"ok": False, "message": "Mail gönderilemedi. Render mail ayarlarını kontrol et."}), 503
            response["message"] += " Mail ayarları eksik olduğu için kod geliştirme modunda gösteriliyor."
            response["devCode"] = code
        return jsonify(response)

    return jsonify(login_success_payload(user, device_id))


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
    email = (data.get("email") or "").strip()
    email_problem = email_error(email)
    if email_problem:
        return jsonify({"ok": False, "message": email_problem}), 400

    email, email_normalized = normalize_email(email)
    user = User.query.filter(db.func.lower(User.email_normalized) == email_normalized.lower()).first()
    if not user:
        return jsonify({"ok": False, "message": "Bu Gmail ile kayıtlı hesap bulunamadı."}), 404

    _, code, sent = create_email_verification("forgot", email, email_normalized, username=user.username)
    return verification_response("Gmail adresine şifre sıfırlama kodu gönderdik.", code, sent)


@app.route("/password/forgot/verify", methods=["POST"])
def forgot_password_verify():
    data = request.get_json() or {}
    email = (data.get("email") or "").strip()
    code = (data.get("code") or "").strip()
    new_password = data.get("password") or ""
    email, email_normalized = normalize_email(email)
    user = User.query.filter(db.func.lower(User.email_normalized) == email_normalized.lower()).first() if email_normalized else None
    if not user:
        return jsonify({"ok": False, "message": "Kullanıcı bulunamadı."}), 404

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

    if len(display_name) < 2 or len(display_name) > 80:
        return jsonify({"ok": False, "message": "Görünen ad 2-40 karakter olmalı."}), 400

    if len(about) > 180:
        return jsonify({"ok": False, "message": "Hakkımda yazısı en fazla 180 karakter olmalı."}), 400

    user.display_name = display_name
    user.avatar = tr_upper(display_name[:2])
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


@app.route("/account/<username>/privacy", methods=["POST"])
def update_privacy(username):
    data = request.get_json() or {}
    username = username.strip().lower()
    user = db.session.get(User, username)
    if not user:
        return jsonify({"ok": False, "message": "Kullanıcı bulunamadı."}), 404

    privacy = data.get("privacy") or {}
    user.hide_last_seen = bool(privacy.get("lastSeenHidden"))
    user.hide_online = bool(privacy.get("onlineHidden"))
    user.disable_read_receipts = bool(privacy.get("readReceiptsOff"))
    user.hide_email = bool(privacy.get("emailHidden", True))
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


@app.route("/account/<username>/status", methods=["POST"])
def update_temporary_status(username):
    data = request.get_json() or {}
    username = username.strip().lower()
    user = db.session.get(User, username)
    if not user:
        return jsonify({"ok": False, "message": "Kullanici bulunamadi."}), 404
    text_value = re.sub(r"\s+", " ", (data.get("text") or "").strip())[:80]
    minutes = max(0, min(24 * 60, int(data.get("minutes") or 0)))
    user.temporary_status = text_value or None
    user.temporary_status_expires_at = datetime.now(timezone.utc) + timedelta(minutes=minutes) if text_value and minutes else None
    db.session.commit()
    broadcast_presence()
    return jsonify({"ok": True, "message": "Gecici durum guncellendi.", "user": private_user(username)})


@app.route("/points/<username>")
def points_state(username):
    username = username.strip().lower()
    user = db.session.get(User, username)
    if not user:
        return jsonify({"ok": False, "message": "Kullanici bulunamadi."}), 404
    points = user_points(username)
    rewards = [
        {"id": "profile_boost", "title": "Profil vitrini", "cost": 300, "description": "Profilini kesfet alaninda daha gorunur yapar."},
        {"id": "theme_unlock", "title": "Ozel tema rozeti", "cost": 500, "description": "Nexa atmosfer temalarinda rozet acar."},
        {"id": "room_badge", "title": "Sesli oda rozeti", "cost": 750, "description": "Sesli odalarda adinin yaninda rozet gosterir."},
    ]
    return jsonify({
        "ok": True,
        "points": points,
        "level": point_level(points),
        "rules": POINT_RULES,
        "ledger": point_ledger_for(username, 80),
        "rewards": rewards,
    })


@app.route("/nearby/<username>", methods=["GET", "POST"])
def nearby_state(username):
    username = username.strip().lower()
    user = db.session.get(User, username)
    if not user:
        return jsonify({"ok": False, "message": "Kullanici bulunamadi."}), 404
    if request.method == "POST":
        data = request.get_json() or {}
        user.nearby_enabled = bool(data.get("enabled"))
        if user.nearby_enabled:
            try:
                user.last_lat = float(data.get("lat"))
                user.last_lng = float(data.get("lng"))
            except (TypeError, ValueError):
                return jsonify({"ok": False, "message": "Konum okunamadi."}), 400
        db.session.commit()
    return jsonify({"ok": True, "enabled": bool(user.nearby_enabled), "users": nearby_users_for(username)})


def vault_items_for(username):
    rows = VaultItem.query.filter_by(username=username).order_by(VaultItem.created_at.desc()).limit(120).all()
    return [
        {"id": row.id, "kind": row.kind, "title": row.title, "payload": row.payload or {}, "createdAt": to_iso(row.created_at)}
        for row in rows
    ]


def verify_vault_pin(user, pin):
    now = datetime.now(timezone.utc)
    locked_until = user.vault_locked_until
    if locked_until and locked_until.tzinfo is None:
        locked_until = locked_until.replace(tzinfo=timezone.utc)
    if locked_until and locked_until > now:
        return False, f"Kasa kilitli. {int((locked_until - now).total_seconds())} sn sonra tekrar dene."
    if not user.vault_pin_hash:
        return False, "Once kasa PIN'i olustur."
    if check_password_hash(user.vault_pin_hash, str(pin or "")):
        user.vault_failed_attempts = 0
        user.vault_locked_until = None
        return True, ""
    user.vault_failed_attempts = int(user.vault_failed_attempts or 0) + 1
    if user.vault_failed_attempts >= 5:
        user.vault_locked_until = now + timedelta(minutes=1)
        user.vault_failed_attempts = 0
    return False, "PIN hatali."


@app.route("/vault/<username>/setup", methods=["POST"])
def vault_setup(username):
    data = request.get_json() or {}
    username = username.strip().lower()
    user = db.session.get(User, username)
    pin = str(data.get("pin") or "")
    if not user:
        return jsonify({"ok": False, "message": "Kullanici bulunamadi."}), 404
    if not re.fullmatch(r"\d{4}", pin):
        return jsonify({"ok": False, "message": "PIN 4 rakam olmali."}), 400
    user.vault_pin_hash = generate_password_hash(pin)
    user.vault_failed_attempts = 0
    user.vault_locked_until = None
    db.session.commit()
    return jsonify({"ok": True, "message": "Gizli kasa PIN'i olusturuldu.", "user": private_user(username)})


@app.route("/vault/<username>/unlock", methods=["POST"])
def vault_unlock(username):
    data = request.get_json() or {}
    username = username.strip().lower()
    user = db.session.get(User, username)
    if not user:
        return jsonify({"ok": False, "message": "Kullanici bulunamadi."}), 404
    ok, message = verify_vault_pin(user, data.get("pin"))
    db.session.commit()
    if not ok:
        return jsonify({"ok": False, "message": message, "user": private_user(username)}), 403
    return jsonify({"ok": True, "items": vault_items_for(username), "user": private_user(username)})


@app.route("/vault/<username>/items", methods=["POST"])
def vault_item_create(username):
    data = request.get_json() or {}
    username = username.strip().lower()
    user = db.session.get(User, username)
    if not user:
        return jsonify({"ok": False, "message": "Kullanici bulunamadi."}), 404
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


@app.route("/vault/<username>/items/<item_id>", methods=["DELETE"])
def vault_item_delete(username, item_id):
    data = request.get_json(silent=True) or {}
    username = username.strip().lower()
    user = db.session.get(User, username)
    item = db.session.get(VaultItem, item_id)
    if not user or not item or item.username != username:
        return jsonify({"ok": False, "message": "Kasa ogesi bulunamadi."}), 404
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
    return jsonify({"ok": True, "devices": active_device_sessions(username)})


@app.route("/account/<username>/devices/<device_id>", methods=["DELETE"])
def revoke_device(username, device_id):
    username = username.strip().lower()
    row = db.session.get(DeviceSession, normalize_device_id(device_id))
    if not row or row.username != username:
        return jsonify({"ok": False, "message": "Cihaz bulunamadı."}), 404
    row.revoked_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify({"ok": True, "message": "Cihaz oturumu kapatıldı.", "devices": active_device_sessions(username)})


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

        member_rows = ChatMember.query.filter_by(username=username).all()
        direct_chat_ids = [row.chat_id for row in member_rows if row.chat and row.chat.type == "direct"]
        group_chat_ids = [row.chat_id for row in member_rows if row.chat and row.chat.type == "group"]

        Story.query.filter_by(username=username).delete(synchronize_session=False)
        CallLog.query.filter_by(caller=username).delete(synchronize_session=False)
        Message.query.filter_by(sender=username).delete(synchronize_session=False)
        BlockedUser.query.filter(db.or_(BlockedUser.blocker == username, BlockedUser.blocked == username)).delete(synchronize_session=False)
        ContactRequest.query.filter(db.or_(ContactRequest.from_username == username, ContactRequest.to_username == username)).delete(synchronize_session=False)
        GroupInvite.query.filter(db.or_(GroupInvite.inviter == username, GroupInvite.invitee == username)).delete(synchronize_session=False)
        ScheduledMessage.query.filter_by(sender=username).delete(synchronize_session=False)
        HiddenChat.query.filter_by(username=username).delete(synchronize_session=False)
        ChatArchive.query.filter_by(username=username).delete(synchronize_session=False)
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
        Story.query.delete(synchronize_session=False)
        CallLog.query.delete(synchronize_session=False)
        Message.query.delete(synchronize_session=False)
        BlockedUser.query.delete(synchronize_session=False)
        ContactRequest.query.delete(synchronize_session=False)
        GroupInvite.query.delete(synchronize_session=False)
        ScheduledMessage.query.delete(synchronize_session=False)
        HiddenChat.query.delete(synchronize_session=False)
        ChatArchive.query.delete(synchronize_session=False)
        ChatMember.query.delete(synchronize_session=False)

        for chat in Chat.query.filter(Chat.id != "lobby").all():
            db.session.delete(chat)

        User.query.delete(synchronize_session=False)

        lobby = db.session.get(Chat, "lobby")
        if lobby:
            lobby.title = "Genel Grup"

        db.session.commit()
        connections.clear()
        typing_users.clear()
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
            "ai": ai_provider_status(),
            "design": design_settings(),
            "serverIp": request.host,
            "yourIp": request_ip(),
            "localAdmin": is_local_admin_request(),
        }
    )


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
        return

    if device_revoked(username, device_id):
        emit("auth:error", {"message": "Bu cihaz oturumu uzaktan kapatildi."})
        return

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
    if user and user.disable_read_receipts:
        return

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
        emit("message:read", {"chatId": chat_id, "reader": username, "messageIds": updated_ids}, room=chat_id)


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


@socketio.on("story:reply")
def handle_story_reply(data):
    username = connections.get(request.sid)
    data = data or {}
    story = db.session.get(Story, data.get("storyId"))
    body = (data.get("body") or "").strip()

    if not username or not story or story.username == username or not body:
        return

    if is_blocked_between(username, story.username):
        emit("notice", {"message": "Bu kişiye yanıt gönderilemiyor."})
        return

    if not accepted_contact(username, story.username):
        emit("notice", {"message": "Duruma yanıt vermek için önce mesajlaşma isteği kabul edilmeli."})
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
        for sid in connected_sids_for(target):
            emit(event_name, payload, room=sid)
        return

    emit(event_name, payload, room=chat.id, include_self=False)


def emit_call_logs_for_chat(chat):
    if not chat:
        return

    for member in chat_member_names(chat):
        for sid in connected_sids_for(member):
            socketio.emit("calls:update", visible_call_logs(member), room=sid)


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
                emit_scheduled_update(row.sender)
            else:
                db.session.delete(row)
                db.session.commit()
                emit_scheduled_update(row.sender)
    finally:
        scheduled_delivery_lock.release()


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
        return
    with voice_room_lock:
        for room in voice_rooms.values():
            room["participants"].pop(username, None)
        voice_rooms[room_id]["participants"][username] = {
            "muted": False,
            "speaking": False,
            "joinedAt": now_iso(),
        }
    join_room(f"voice:{room_id}")
    emit_voice_rooms()


@socketio.on("voice:leave")
def handle_voice_leave(data=None):
    username = connections.get(request.sid)
    if not username:
        return
    with voice_room_lock:
        for room_id, room in voice_rooms.items():
            if username in room["participants"]:
                room["participants"].pop(username, None)
                leave_room(f"voice:{room_id}")
    emit_voice_rooms()


@socketio.on("voice:mute")
def handle_voice_mute(data):
    username = connections.get(request.sid)
    room_id = ((data or {}).get("roomId") or "").strip()
    if not username or room_id not in voice_rooms:
        return
    with voice_room_lock:
        participant = voice_rooms[room_id]["participants"].get(username)
        if participant is not None:
            participant["muted"] = bool((data or {}).get("muted"))
    emit_voice_rooms()


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


@socketio.on("disconnect")
def handle_disconnect():
    username = connections.pop(request.sid, None)

    if username:
        with voice_room_lock:
            for room in voice_rooms.values():
                room["participants"].pop(username, None)
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
    user_columns = {column["name"] for column in inspector.get_columns("user")} if inspector.has_table("user") else set()
    user_migrations = {
        "email": "ALTER TABLE \"user\" ADD COLUMN email VARCHAR(255)",
        "email_normalized": "ALTER TABLE \"user\" ADD COLUMN email_normalized VARCHAR(255)",
        "email_verified": "ALTER TABLE \"user\" ADD COLUMN email_verified BOOLEAN DEFAULT FALSE NOT NULL",
        "profile_image": "ALTER TABLE \"user\" ADD COLUMN profile_image TEXT",
        "last_seen": "ALTER TABLE \"user\" ADD COLUMN last_seen TIMESTAMP",
        "hide_last_seen": "ALTER TABLE \"user\" ADD COLUMN hide_last_seen BOOLEAN DEFAULT FALSE NOT NULL",
        "hide_online": "ALTER TABLE \"user\" ADD COLUMN hide_online BOOLEAN DEFAULT FALSE NOT NULL",
        "disable_read_receipts": "ALTER TABLE \"user\" ADD COLUMN disable_read_receipts BOOLEAN DEFAULT FALSE NOT NULL",
        "hide_email": "ALTER TABLE \"user\" ADD COLUMN hide_email BOOLEAN DEFAULT TRUE NOT NULL",
        "points": "ALTER TABLE \"user\" ADD COLUMN points INTEGER DEFAULT 0 NOT NULL",
        "two_factor_enabled": "ALTER TABLE \"user\" ADD COLUMN two_factor_enabled BOOLEAN DEFAULT FALSE NOT NULL",
        "theme_preference": "ALTER TABLE \"user\" ADD COLUMN theme_preference VARCHAR(20) DEFAULT 'dark' NOT NULL",
        "font_size_preference": "ALTER TABLE \"user\" ADD COLUMN font_size_preference VARCHAR(20) DEFAULT 'medium' NOT NULL",
        "notification_sound": "ALTER TABLE \"user\" ADD COLUMN notification_sound VARCHAR(40) DEFAULT 'classic' NOT NULL",
        "temporary_status": "ALTER TABLE \"user\" ADD COLUMN temporary_status VARCHAR(80)",
        "temporary_status_expires_at": "ALTER TABLE \"user\" ADD COLUMN temporary_status_expires_at TIMESTAMP",
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
