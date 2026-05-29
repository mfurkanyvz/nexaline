import os
import ipaddress
import re
import socket
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from uuid import uuid4

import requests
from flask import Flask, jsonify, request, send_from_directory
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, text
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
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading", max_http_buffer_size=8_000_000)

connections = {}
typing_users = {}
DEV_ADMIN_TOKEN = "NexaLineAdmin2026!"


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
    about = db.Column(db.String(255), nullable=False, default="NexaLine kullanıyorum.")
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    last_seen = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


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
    deleted_for = db.Column(db.JSON, nullable=False, default=list)
    deleted_at = db.Column(db.DateTime, nullable=True)
    deleted_by = db.Column(db.String(80), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

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


def public_user(username):
    user = db.session.get(User, username)
    online = any(name == username for name in connections.values())
    return {
        "username": username,
        "displayName": user.display_name if user else username,
        "avatar": user.avatar if user else username[:2].upper(),
        "profileImage": user.profile_image if user else None,
        "about": user.about if user else "NexaLine kullanıyorum.",
        "online": online,
        "lastSeen": now_iso() if online else to_iso(user.last_seen) if user else None,
    }


def private_user(username):
    user = db.session.get(User, username)
    data = public_user(username)
    if user:
        data["email"] = user.email
        data["emailVerified"] = user.email_verified
    return data


def story_to_dict(story):
    return {
        "id": story.id,
        "username": story.username,
        "user": public_user(story.username),
        "body": story.body,
        "attachment": story.attachment,
        "createdAt": to_iso(story.created_at),
        "expiresAt": to_iso(story.expires_at),
    }


def active_stories():
    now = datetime.now(timezone.utc)
    Story.query.filter(Story.expires_at <= now).delete(synchronize_session=False)
    db.session.commit()
    stories = Story.query.filter(Story.expires_at > now).order_by(Story.created_at.desc()).all()
    return [story_to_dict(story) for story in stories]


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
        "status": "sent",
        "readBy": message.read_by or [],
        "reactions": message.reactions or {},
        "deletedAt": deleted_at,
        "deletedBy": message.deleted_by,
        "deletedFor": message.deleted_for or [],
    }


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
    members = [public_user(member) for member in chat_member_names(lobby)]
    joined = any(member["username"] == username for member in members)

    return {
        "id": lobby.id,
        "title": lobby.title,
        "members": members,
        "joined": joined,
    }


def chat_for_user(chat, username):
    messages = [
        message_to_dict(message)
        for message in chat.messages
        if username not in (message.deleted_for or [])
    ]
    last_message = messages[-1] if messages else None
    member_names = chat_member_names(chat)
    title = chat.title

    if chat.type == "direct":
        other_users = [member for member in member_names if member != username]
        title = public_user(other_users[0])["displayName"] if other_users else "Kişisel sohbet"

    return {
        "id": chat.id,
        "type": chat.type,
        "title": title,
        "image": chat.image,
        "members": [
            {**public_user(member), "isAdmin": is_group_admin(chat, member)}
            for member in member_names
        ],
        "lastMessage": last_message,
        "messages": messages,
    }


def visible_chats(username):
    ensure_lobby()
    result = []

    for chat in Chat.query.order_by(Chat.created_at).all():
        if user_can_see_chat(chat, username):
            result.append(chat_for_user(chat, username))

    return sorted(result, key=lambda item: item["lastMessage"]["createdAt"] if item["lastMessage"] else "", reverse=True)


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

    legacy_chat = find_direct_chat(first_username, second_username)
    return bool(legacy_chat and legacy_chat.messages)


def contact_request_to_dict(request_row):
    return {
        "id": request_row.id,
        "from": public_user(request_row.from_username),
        "to": public_user(request_row.to_username),
        "status": request_row.status,
        "createdAt": to_iso(request_row.created_at),
        "respondedAt": to_iso(request_row.responded_at) if request_row.responded_at else None,
    }


def visible_contact_requests(username):
    rows = ContactRequest.query.filter(
        db.or_(ContactRequest.from_username == username, ContactRequest.to_username == username)
    ).order_by(ContactRequest.created_at.desc()).all()
    return [contact_request_to_dict(row) for row in rows]


def group_invite_to_dict(invite):
    return {
        "id": invite.id,
        "chatId": invite.chat_id,
        "chatTitle": invite.chat.title if invite.chat else "Grup",
        "chatImage": invite.chat.image if invite.chat else None,
        "inviter": public_user(invite.inviter),
        "invitee": public_user(invite.invitee),
        "status": invite.status,
        "createdAt": to_iso(invite.created_at),
        "respondedAt": to_iso(invite.responded_at) if invite.responded_at else None,
    }


def visible_group_invites(username):
    rows = GroupInvite.query.filter(
        db.or_(GroupInvite.inviter == username, GroupInvite.invitee == username)
    ).order_by(GroupInvite.created_at.desc()).all()
    return [group_invite_to_dict(row) for row in rows]


def blocked_users_for(username):
    return [row.blocked for row in BlockedUser.query.filter_by(blocker=username).all()]


def connected_sids_for(username):
    return [sid for sid, connected_user in connections.items() if connected_user == username]


def broadcast_presence():
    users = [public_user(user.username) for user in User.query.order_by(User.display_name).all()]
    socketio.emit("presence:update", users, namespace="/")


def broadcast_stories():
    socketio.emit("stories:update", active_stories(), namespace="/")


def emit_general_group_updates():
    for sid, username in connections.items():
        socketio.emit("general:update", general_group_state(username), room=sid)


def emit_social_updates(*usernames):
    for username in {name for name in usernames if name}:
        for sid in connected_sids_for(username):
            socketio.emit(
                "social:update",
                {
                    "contactRequests": visible_contact_requests(username),
                    "groupInvites": visible_group_invites(username),
                    "blockedUsers": blocked_users_for(username),
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
        "register": "NexaLine kayit dogrulama kodun",
        "forgot": "NexaLine sifre sifirlama kodun",
        "email_change": "NexaLine Gmail degistirme kodun",
    }
    return labels.get(purpose, "NexaLine dogrulama kodun")


def email_body(code):
    return (
        f"NexaLine dogrulama kodun: {code}\n\n"
        "Bu kod 10 dakika gecerlidir. Bu islemi sen yapmadiysan bu mesaji yok sayabilirsin."
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


@app.route("/")
def index():
    return send_from_directory("static", "client.html")


@app.route("/client.html")
def client():
    return send_from_directory("static", "client.html")


@app.route("/manifest.webmanifest")
def manifest():
    return send_from_directory("static", "manifest.webmanifest", mimetype="application/manifest+json")


@app.route("/sw.js")
def service_worker():
    response = send_from_directory("static", "sw.js", mimetype="application/javascript")
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.route("/admin")
def admin_page():
    return send_from_directory("static", "admin.html")


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
        return jsonify({"ok": False, "message": "Bu kullanici adi zaten kayitli. Farkli bir kullanici adi dene."}), 409

    return jsonify({"ok": True, "message": "Kullanici adi uygun."})


@app.route("/register/verify", methods=["POST"])
def register_verify():
    try:
        data = request.get_json() or {}
        username = (data.get("username") or "").strip().lower()
        email = (data.get("email") or "").strip()
        code = (data.get("code") or "").strip()
        password = data.get("password") or ""
        confirm_password = data.get("confirmPassword")

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
            return jsonify({"ok": False, "message": "Sifre olusturulmadan kayit tamamlanamaz."}), 400

        db.session.add(
            User(
                username=username,
                password_hash=password_hash,
                display_name=username,
                email=verification.email,
                email_normalized=verification.email_normalized,
                email_verified=True,
                avatar=username[:2].upper(),
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
    username = (data.get("username") or "").strip().lower()
    password = data.get("password") or ""
    user = db.session.get(User, username)

    if not user or not check_password_hash(user.password_hash, password):
        return jsonify({"ok": False, "message": "Kullanıcı adı veya şifre hatalı."}), 401

    return jsonify({"ok": True, "message": "Giriş başarılı.", "user": private_user(username)})


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

    display_name = (data.get("displayName") or user.display_name).strip()
    about = (data.get("about") or user.about or "").strip()
    profile_image = data.get("profileImage")

    if len(display_name) < 2 or len(display_name) > 40:
        return jsonify({"ok": False, "message": "Görünen ad 2-40 karakter olmalı."}), 400

    if len(about) > 180:
        return jsonify({"ok": False, "message": "Hakkımda yazısı en fazla 180 karakter olmalı."}), 400

    user.display_name = display_name
    user.avatar = display_name[:2].upper()
    user.about = about or "NexaLine kullanıyorum."
    if isinstance(profile_image, str):
        if profile_image and not profile_image.startswith("data:image/"):
            return jsonify({"ok": False, "message": "Profil fotoğrafı sadece resim olabilir."}), 400
        if len(profile_image) > 1_500_000:
            return jsonify({"ok": False, "message": "Profil fotoğrafı 1.5 MB altında olmalı."}), 400
        user.profile_image = profile_image or None

    db.session.commit()
    broadcast_presence()

    for chat in Chat.query.all():
        if user_can_see_chat(chat, username):
            for member in chat_member_names(chat):
                for sid in connected_sids_for(member):
                    socketio.emit("chat:upsert", chat_for_user(chat, member), room=sid)

    return jsonify({"ok": True, "message": "Profil güncellendi.", "user": private_user(username)})


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
        if direct_chat_ids:
            CallLog.query.filter(CallLog.chat_id.in_(direct_chat_ids)).delete(synchronize_session=False)

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
        chats.append(
            {
                "id": chat.id,
                "type": chat.type,
                "title": chat.title,
                "createdAt": to_iso(chat.created_at),
                "members": [{**public_user(member.username), "isAdmin": member.is_admin} for member in chat.members],
                "messages": [message_to_dict(message) for message in chat.messages],
            }
        )

    return jsonify(
        {
            "ok": True,
            "users": users,
            "chats": chats,
            "stories": active_stories(),
            "calls": [call_log_to_dict(log, log.caller) for log in CallLog.query.order_by(CallLog.started_at.desc()).all()],
            "serverIp": request.host,
            "yourIp": request_ip(),
            "localAdmin": is_local_admin_request(),
        }
    )


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
    member_rows = ChatMember.query.filter_by(username=user.username).all()
    direct_chat_ids = [row.chat_id for row in member_rows if row.chat and row.chat.type == "direct"]
    group_chat_ids = [row.chat_id for row in member_rows if row.chat and row.chat.type == "group"]
    if direct_chat_ids:
        CallLog.query.filter(CallLog.chat_id.in_(direct_chat_ids)).delete(synchronize_session=False)
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
    db.session.delete(chat)
    db.session.commit()

    for member in affected_members:
        for sid in connected_sids_for(member):
            socketio.emit("chat:remove", {"chatId": chat_id}, room=sid)

    return jsonify({"ok": True, "message": "Sohbet silindi."})


@app.route("/bootstrap/<username>")
def bootstrap(username):
    username = username.strip().lower()
    if not db.session.get(User, username):
        return jsonify({"ok": False, "message": "Kullanıcı bulunamadı."}), 404

    return jsonify(
        {
            "ok": True,
            "user": private_user(username),
            "users": [public_user(user.username) for user in User.query.order_by(User.display_name).all()],
            "chats": visible_chats(username),
            "generalGroup": general_group_state(username),
            "stories": active_stories(),
            "callLogs": visible_call_logs(username),
            "contactRequests": visible_contact_requests(username),
            "groupInvites": visible_group_invites(username),
            "blockedUsers": blocked_users_for(username),
        }
    )


@socketio.on("connect")
def handle_connect():
    ensure_lobby()


@socketio.on("user:join")
def handle_user_join(data):
    username = (data or {}).get("username", "").strip().lower()

    if not db.session.get(User, username):
        emit("auth:error", {"message": "Önce giriş yapmalısın."})
        return

    connections[request.sid] = username
    user = db.session.get(User, username)
    if user:
        user.last_seen = datetime.now(timezone.utc)
        db.session.commit()

    for chat in Chat.query.all():
        if user_can_see_chat(chat, username):
            join_room(chat.id)

    emit(
        "app:state",
        {
            "user": private_user(username),
            "users": [public_user(user.username) for user in User.query.order_by(User.display_name).all()],
            "chats": visible_chats(username),
            "generalGroup": general_group_state(username),
            "stories": active_stories(),
            "callLogs": visible_call_logs(username),
            "contactRequests": visible_contact_requests(username),
            "groupInvites": visible_group_invites(username),
            "blockedUsers": blocked_users_for(username),
        },
    )
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
    db.session.commit()

    if accept and not is_blocked_between(request_row.from_username, request_row.to_username):
        chat = ensure_direct_chat(request_row.from_username, request_row.to_username)
        for member in chat_member_names(chat):
            for sid in connected_sids_for(member):
                join_room(chat.id, sid=sid)
                emit("chat:upsert", chat_for_user(chat, member), room=sid)

    emit_social_updates(request_row.from_username, request_row.to_username)
    emit("notice", {"message": "İstek kabul edildi." if accept else "İstek reddedildi."})


@socketio.on("user:block")
def handle_user_block(data):
    username = connections.get(request.sid)
    target = (data or {}).get("username", "").strip().lower()
    blocked = bool((data or {}).get("blocked", True))

    if not username or target == username or not db.session.get(User, target):
        emit("notice", {"message": "Kullanıcı bulunamadı."})
        return

    row = BlockedUser.query.filter_by(blocker=username, blocked=target).first()
    if blocked and not row:
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
            db.session.commit()
            emit("notice", {"message": "Mesajlaşma isteği gönderildi. Karşı taraf kabul edince sohbet açılacak."})
            emit_social_updates(username, target)
        return

    chat = ensure_direct_chat(username, target)
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

    add_chat_member(lobby.id, username)
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


@socketio.on("message:send")
def handle_message_send(data):
    username = connections.get(request.sid)
    data = data or {}
    chat = db.session.get(Chat, data.get("chatId"))
    body = (data.get("body") or "").strip()
    attachment = data.get("attachment")
    reply_to = data.get("replyTo")

    if not username or not chat or not user_can_see_chat(chat, username):
        return

    if chat.type == "direct":
        others = [member for member in chat_member_names(chat) if member != username]
        if any(is_blocked_between(username, other) for other in others) or any(not accepted_contact(username, other) for other in others):
            emit("notice", {"message": "Mesaj göndermek için önce istek kabul edilmeli ve engel olmamalı."})
            return

    if not body and not attachment:
        return

    message = Message(
        id=uuid4().hex,
        chat_id=chat.id,
        sender=username,
        body=body,
        attachment=attachment,
        reply_to=reply_to if isinstance(reply_to, dict) else None,
        read_by=[username],
    )
    db.session.add(message)
    db.session.commit()
    emit("message:new", message_to_dict(message), room=chat.id)


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

    updated_ids = []
    for message in chat.messages:
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

    story = Story(
        id=uuid4().hex,
        username=username,
        body=body,
        attachment=attachment,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    db.session.add(story)
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


@socketio.on("disconnect")
def handle_disconnect():
    username = connections.pop(request.sid, None)

    if username:
        user = db.session.get(User, username)
        if user:
            user.last_seen = datetime.now(timezone.utc)
            db.session.commit()

        for chat_id, users_typing in typing_users.items():
            if username in users_typing:
                users_typing.discard(username)
                emit("typing:update", {"chatId": chat_id, "users": sorted(users_typing)}, room=chat_id)

    broadcast_presence()


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
        "deleted_for": "ALTER TABLE message ADD COLUMN deleted_for JSON",
        "deleted_at": "ALTER TABLE message ADD COLUMN deleted_at TIMESTAMP",
        "deleted_by": "ALTER TABLE message ADD COLUMN deleted_by VARCHAR(80)",
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
    }
    for column_name, statement in user_migrations.items():
        if column_name not in user_columns:
            db.session.execute(text(statement))
            db.session.commit()
    db.session.execute(text("UPDATE \"user\" SET last_seen = COALESCE(last_seen, created_at)"))
    db.session.commit()
    ensure_lobby()
    for group_chat in Chat.query.filter_by(type="group").all():
        promote_fallback_group_admin(group_chat)
    db.session.commit()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
