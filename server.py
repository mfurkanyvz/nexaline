import os
from datetime import datetime, timezone
from uuid import uuid4

from flask import Flask, jsonify, request, send_from_directory
from flask_socketio import SocketIO, emit, join_room

app = Flask(__name__)
app.config["SECRET_KEY"] = "nexaline-dev-secret"
socketio = SocketIO(app, cors_allowed_origins="*")

users = {}
connections = {}
chats = {}
typing_users = {}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def public_user(username):
    user = users.get(username, {})
    return {
        "username": username,
        "displayName": user.get("displayName", username),
        "avatar": user.get("avatar", username[:2].upper()),
        "about": user.get("about", "NexaLine kullanıyorum."),
        "online": any(name == username for name in connections.values()),
    }


def ensure_lobby():
    if "lobby" not in chats:
        chats["lobby"] = {
            "id": "lobby",
            "type": "group",
            "title": "NexaLine Genel",
            "members": [],
            "messages": [],
            "createdAt": now_iso(),
        }


def direct_chat_id(first_username, second_username):
    return "dm:" + ":".join(sorted([first_username, second_username]))


def ensure_direct_chat(first_username, second_username):
    chat_id = direct_chat_id(first_username, second_username)
    if chat_id not in chats:
        chats[chat_id] = {
            "id": chat_id,
            "type": "direct",
            "title": second_username,
            "members": sorted([first_username, second_username]),
            "messages": [],
            "createdAt": now_iso(),
        }
    return chats[chat_id]


def chat_for_user(chat, username):
    last_message = chat["messages"][-1] if chat["messages"] else None
    title = chat["title"]
    members = chat["members"]

    if chat["type"] == "direct":
        other_users = [member for member in chat["members"] if member != username]
        title = public_user(other_users[0])["displayName"] if other_users else "Kişisel sohbet"
    elif chat["type"] == "group" and not members:
        members = sorted(users.keys())

    return {
        "id": chat["id"],
        "type": chat["type"],
        "title": title,
        "members": [public_user(member) for member in members],
        "lastMessage": last_message,
        "messages": chat["messages"],
    }


def visible_chats(username):
    ensure_lobby()
    result = []

    for chat in chats.values():
        if chat["type"] == "group" or username in chat["members"]:
            result.append(chat_for_user(chat, username))

    return sorted(result, key=lambda item: item["lastMessage"]["createdAt"] if item["lastMessage"] else "", reverse=True)


def broadcast_presence():
    emit("presence:update", [public_user(username) for username in users], broadcast=True, namespace="/")


@app.route("/")
def index():
    return send_from_directory("static", "client.html")


@app.route("/client.html")
def client():
    return send_from_directory("static", "client.html")


@app.route("/register", methods=["POST"])
def register():
    data = request.get_json() or {}
    username = (data.get("username") or "").strip().lower()
    display_name = (data.get("displayName") or data.get("username") or "").strip()
    password = data.get("password") or ""

    if len(username) < 3 or len(password) < 3:
        return jsonify({"ok": False, "message": "Kullanıcı adı ve şifre en az 3 karakter olmalı."}), 400

    if username in users:
        return jsonify({"ok": False, "message": "Bu kullanıcı adı zaten kayıtlı."}), 409

    users[username] = {
        "password": password,
        "displayName": display_name or username,
        "avatar": username[:2].upper(),
        "about": "NexaLine kullanıyorum.",
        "createdAt": now_iso(),
    }
    ensure_lobby()

    return jsonify({"ok": True, "message": "Kayıt başarılı.", "user": public_user(username)})


@app.route("/login", methods=["POST"])
def login():
    data = request.get_json() or {}
    username = (data.get("username") or "").strip().lower()
    password = data.get("password") or ""

    if users.get(username, {}).get("password") != password:
        return jsonify({"ok": False, "message": "Kullanıcı adı veya şifre hatalı."}), 401

    return jsonify({"ok": True, "message": "Giriş başarılı.", "user": public_user(username)})


@app.route("/bootstrap/<username>")
def bootstrap(username):
    username = username.strip().lower()
    if username not in users:
        return jsonify({"ok": False, "message": "Kullanıcı bulunamadı."}), 404

    return jsonify(
        {
            "ok": True,
            "user": public_user(username),
            "users": [public_user(user) for user in users],
            "chats": visible_chats(username),
        }
    )


@socketio.on("connect")
def handle_connect():
    ensure_lobby()


@socketio.on("user:join")
def handle_user_join(data):
    username = (data or {}).get("username", "").strip().lower()

    if username not in users:
        emit("auth:error", {"message": "Önce giriş yapmalısın."})
        return

    connections[request.sid] = username
    join_room("lobby")

    for chat in chats.values():
        if username in chat["members"]:
            join_room(chat["id"])

    emit(
        "app:state",
        {
            "user": public_user(username),
            "users": [public_user(user) for user in users],
            "chats": visible_chats(username),
        },
    )
    broadcast_presence()


@socketio.on("chat:create")
def handle_chat_create(data):
    username = connections.get(request.sid)
    target = (data or {}).get("target", "").strip().lower()

    if not username or target not in users or target == username:
        return

    chat = ensure_direct_chat(username, target)
    join_room(chat["id"])
    emit("chat:upsert", chat_for_user(chat, username), room=request.sid)

    for sid, connected_user in connections.items():
        if connected_user == target:
            join_room(chat["id"], sid=sid)
            emit("chat:upsert", chat_for_user(chat, target), room=sid)


@socketio.on("message:send")
def handle_message_send(data):
    username = connections.get(request.sid)
    data = data or {}
    chat_id = data.get("chatId")
    body = (data.get("body") or "").strip()
    attachment = data.get("attachment")

    if not username or chat_id not in chats:
        return

    chat = chats[chat_id]
    if chat["type"] == "direct" and username not in chat["members"]:
        return

    if not body and not attachment:
        return

    message = {
        "id": uuid4().hex,
        "chatId": chat_id,
        "sender": username,
        "senderName": public_user(username)["displayName"],
        "body": body,
        "attachment": attachment,
        "createdAt": now_iso(),
        "status": "sent",
        "readBy": [username],
    }
    chat["messages"].append(message)
    emit("message:new", message, room=chat_id)


@socketio.on("typing")
def handle_typing(data):
    username = connections.get(request.sid)
    data = data or {}
    chat_id = data.get("chatId")

    if not username or chat_id not in chats:
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

    if not username or chat_id not in chats:
        return

    updated_ids = []
    for message in chats[chat_id]["messages"]:
        if username not in message["readBy"]:
            message["readBy"].append(username)
            updated_ids.append(message["id"])

    if updated_ids:
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


if __name__ == "__main__":
    ensure_lobby()
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
