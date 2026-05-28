import os
from datetime import datetime, timezone
from uuid import uuid4

from flask import Flask, jsonify, request, send_from_directory
from flask_socketio import SocketIO, emit, join_room
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "nexaline-dev-secret")
os.makedirs(app.instance_path, exist_ok=True)

database_url = os.environ.get("DATABASE_URL")
if not database_url:
    fallback_db = os.environ.get("SQLITE_PATH", "/tmp/nexaline.db" if os.environ.get("RENDER") else os.path.join(app.instance_path, "nexaline.db"))
    database_url = "sqlite:///" + fallback_db.replace("\\", "/")
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

connections = {}
typing_users = {}


class User(db.Model):
    username = db.Column(db.String(80), primary_key=True)
    password_hash = db.Column(db.String(255), nullable=False)
    display_name = db.Column(db.String(120), nullable=False)
    avatar = db.Column(db.String(8), nullable=False)
    about = db.Column(db.String(255), nullable=False, default="NexaLine kullanıyorum.")
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


class Chat(db.Model):
    id = db.Column(db.String(140), primary_key=True)
    type = db.Column(db.String(20), nullable=False)
    title = db.Column(db.String(160), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    members = db.relationship("ChatMember", backref="chat", cascade="all, delete-orphan")
    messages = db.relationship("Message", backref="chat", cascade="all, delete-orphan", order_by="Message.created_at")


class ChatMember(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    chat_id = db.Column(db.String(140), db.ForeignKey("chat.id"), nullable=False)
    username = db.Column(db.String(80), db.ForeignKey("user.username"), nullable=False)

    user = db.relationship("User")
    __table_args__ = (db.UniqueConstraint("chat_id", "username", name="unique_chat_member"),)


class Message(db.Model):
    id = db.Column(db.String(40), primary_key=True)
    chat_id = db.Column(db.String(140), db.ForeignKey("chat.id"), nullable=False)
    sender = db.Column(db.String(80), db.ForeignKey("user.username"), nullable=False)
    body = db.Column(db.Text, nullable=False, default="")
    attachment = db.Column(db.JSON, nullable=True)
    read_by = db.Column(db.JSON, nullable=False, default=list)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    sender_user = db.relationship("User")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def to_iso(value):
    if value is None:
        return now_iso()

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    return value.isoformat()


def public_user(username):
    user = db.session.get(User, username)
    return {
        "username": username,
        "displayName": user.display_name if user else username,
        "avatar": user.avatar if user else username[:2].upper(),
        "about": user.about if user else "NexaLine kullanıyorum.",
        "online": any(name == username for name in connections.values()),
    }


def message_to_dict(message):
    return {
        "id": message.id,
        "chatId": message.chat_id,
        "sender": message.sender,
        "senderName": message.sender_user.display_name if message.sender_user else message.sender,
        "body": message.body,
        "attachment": message.attachment,
        "createdAt": to_iso(message.created_at),
        "status": "sent",
        "readBy": message.read_by or [],
    }


def ensure_lobby():
    if db.session.get(Chat, "lobby"):
        return

    db.session.add(Chat(id="lobby", type="group", title="NexaLine Genel"))
    db.session.commit()


def direct_chat_id(first_username, second_username):
    return "dm:" + ":".join(sorted([first_username, second_username]))


def ensure_direct_chat(first_username, second_username):
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
    if chat.type == "group":
        return [user.username for user in User.query.order_by(User.display_name).all()]

    return sorted(member.username for member in chat.members)


def chat_for_user(chat, username):
    messages = [message_to_dict(message) for message in chat.messages]
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
        "members": [public_user(member) for member in member_names],
        "lastMessage": last_message,
        "messages": messages,
    }


def user_can_see_chat(chat, username):
    if chat.type == "group":
        return True

    return any(member.username == username for member in chat.members)


def visible_chats(username):
    ensure_lobby()
    result = []

    for chat in Chat.query.all():
        if user_can_see_chat(chat, username):
            result.append(chat_for_user(chat, username))

    return sorted(result, key=lambda item: item["lastMessage"]["createdAt"] if item["lastMessage"] else "", reverse=True)


def broadcast_presence():
    users = [public_user(user.username) for user in User.query.order_by(User.display_name).all()]
    socketio.emit("presence:update", users, namespace="/")


@app.route("/")
def index():
    return send_from_directory("static", "client.html")


@app.route("/client.html")
def client():
    return send_from_directory("static", "client.html")


@app.route("/health")
def health():
    try:
        ensure_lobby()
        user_count = User.query.count()
        chat_count = Chat.query.count()
        return jsonify({"ok": True, "users": user_count, "chats": chat_count})
    except Exception as error:
        app.logger.exception("Health check failed")
        return jsonify({"ok": False, "message": str(error)}), 500


@app.route("/register", methods=["POST"])
def register():
    try:
        data = request.get_json() or {}
        username = (data.get("username") or "").strip().lower()
        display_name = (data.get("displayName") or data.get("username") or "").strip()
        password = data.get("password") or ""

        if len(username) < 3 or len(password) < 3:
            return jsonify({"ok": False, "message": "Kullanıcı adı ve şifre en az 3 karakter olmalı."}), 400

        if db.session.get(User, username):
            return jsonify({"ok": False, "message": "Bu kullanıcı adı zaten kayıtlı."}), 409

        user = User(
            username=username,
            password_hash=generate_password_hash(password),
            display_name=display_name or username,
            avatar=username[:2].upper(),
            about="NexaLine kullanıyorum.",
        )
        db.session.add(user)
        ensure_lobby()
        db.session.commit()

        return jsonify({"ok": True, "message": "Kayıt başarılı.", "user": public_user(username)})
    except Exception:
        db.session.rollback()
        app.logger.exception("Register failed")
        return jsonify({"ok": False, "message": "Sunucuda kayıt hatası oluştu. Render kayıtlarını kontrol et."}), 500


@app.route("/login", methods=["POST"])
def login():
    data = request.get_json() or {}
    username = (data.get("username") or "").strip().lower()
    password = data.get("password") or ""
    user = db.session.get(User, username)

    if not user or not check_password_hash(user.password_hash, password):
        return jsonify({"ok": False, "message": "Kullanıcı adı veya şifre hatalı."}), 401

    return jsonify({"ok": True, "message": "Giriş başarılı.", "user": public_user(username)})


@app.route("/bootstrap/<username>")
def bootstrap(username):
    username = username.strip().lower()
    if not db.session.get(User, username):
        return jsonify({"ok": False, "message": "Kullanıcı bulunamadı."}), 404

    return jsonify(
        {
            "ok": True,
            "user": public_user(username),
            "users": [public_user(user.username) for user in User.query.order_by(User.display_name).all()],
            "chats": visible_chats(username),
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
    join_room("lobby")

    for chat in Chat.query.all():
        if chat.type == "direct" and user_can_see_chat(chat, username):
            join_room(chat.id)

    emit(
        "app:state",
        {
            "user": public_user(username),
            "users": [public_user(user.username) for user in User.query.order_by(User.display_name).all()],
            "chats": visible_chats(username),
        },
    )
    broadcast_presence()


@socketio.on("chat:create")
def handle_chat_create(data):
    username = connections.get(request.sid)
    target = (data or {}).get("target", "").strip().lower()

    if not username or not db.session.get(User, target) or target == username:
        return

    chat = ensure_direct_chat(username, target)
    join_room(chat.id)
    emit("chat:upsert", chat_for_user(chat, username), room=request.sid)

    for sid, connected_user in connections.items():
        if connected_user == target:
            join_room(chat.id, sid=sid)
            emit("chat:upsert", chat_for_user(chat, target), room=sid)


@socketio.on("message:send")
def handle_message_send(data):
    username = connections.get(request.sid)
    data = data or {}
    chat_id = data.get("chatId")
    body = (data.get("body") or "").strip()
    attachment = data.get("attachment")
    chat = db.session.get(Chat, chat_id)

    if not username or not chat or not user_can_see_chat(chat, username):
        return

    if not body and not attachment:
        return

    message = Message(
        id=uuid4().hex,
        chat_id=chat.id,
        sender=username,
        body=body,
        attachment=attachment,
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
        {
            "chatId": chat_id,
            "users": sorted(typing_users.get(chat_id, set())),
        },
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
        emit(
            "message:read",
            {"chatId": chat_id, "reader": username, "messageIds": updated_ids},
            room=chat_id,
        )


@socketio.on("disconnect")
def handle_disconnect():
    username = connections.pop(request.sid, None)

    if username:
        for chat_id, users_typing in typing_users.items():
            if username in users_typing:
                users_typing.discard(username)
                emit(
                    "typing:update",
                    {"chatId": chat_id, "users": sorted(users_typing)},
                    room=chat_id,
                )

    broadcast_presence()


with app.app_context():
    db.create_all()
    ensure_lobby()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
