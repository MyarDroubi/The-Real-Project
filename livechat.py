from flask import Flask, render_template, request, session, redirect, url_for, flash, g
from flask_socketio import join_room, send, SocketIO
import random
import json
import os
from string import ascii_uppercase
from huggingface_hub import InferenceClient
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import timedelta

# Get base directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)
socketio = SocketIO(app,async_mode="eventlet")

app.config["SECRET_KEY"] = "THISISACODE"
socketio = SocketIO(app)

# Initialize the bot client
bot_client = InferenceClient(
    provider="novita",
    api_key="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"
)

# SQLite Database Setup
DATABASE = os.path.join(BASE_DIR, "users.db")

def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with app.app_context():
        db = get_db_connection()
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL
            )
        """)
        db.commit()
        db.close()

init_db()

# Room management
rooms = {}
ROOMS_FILE = os.path.join(BASE_DIR, "rooms.json")

def save_rooms_to_file():
    with open(ROOMS_FILE, "w") as f:
        json.dump(rooms, f)

def generate_unique_code(length):
    while True:
        code = "".join(random.choice(ascii_uppercase) for _ in range(length))
        if code not in rooms:
            return code

def bot_interaction(room, user_message=None):
    """Bot interacts if there is only one person in the room."""
    if rooms[room]["members"] == 1:
        if user_message:
            messages = [{"role": "user", "content": user_message}]
            try:
                completion = bot_client.chat.completions.create(
                    model="deepseek-ai/DeepSeek-R1-Distill-Llama-8B",
                    messages=messages,
                    max_tokens=500,
                )
                bot_response = completion.choices[0].message.content
                final_answer = bot_response.split("</think>")[-1].strip() if "</think>" in bot_response else bot_response
            except Exception as e:
                print(f"Error in bot interaction: {e}")  # Log the exception
                final_answer = "I'm having trouble responding right now."

        else:
            final_answer = "Hello! What would you like to talk about?"

        send({"name": "Bot", "message": final_answer}, to=room)
        rooms[room]["messages"].append({"name": "Bot", "message": final_answer})

@app.before_request
def load_logged_in_user():
    user_id = session.get('user_id')
    if user_id is not None:
        g.user = user_id
    else:
        g.user = None

@app.route("/", methods=["GET", "POST"])
def inloggning():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        if not email or not password:
            flash("E-postadress och lösenord krävs!", "error")
            return redirect(url_for("inloggning"))

        db = get_db_connection()
        user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        db.close()

        if user and check_password_hash(user["password"], password):
            # Set session variables
            session["user_id"] = user["id"]
            session["email"] = user["email"]
            session.permanent = True
            app.permanent_session_lifetime = timedelta(days=30)
            return redirect(url_for("index"))

        flash("Fel e-postadress eller lösenord!", "error")
    return render_template("Inloggning.html")

@app.route('/om_oss')
def om_oss():
    return render_template('om_oss.html')

@app.route("/index")
def index():
  
    print("Session data:", session)

    # Check if the user is logged in
    if "user_id" not in session:
        flash("Vänligen logga in.", "error")
        return redirect(url_for("inloggning"))

    try:
        # Render the template
        return render_template("Index.html")
    except Exception as e:
        # Debug: Print the error if rendering fails
        print(f"Error rendering template: {e}")
        return "An error occurred while rendering the page.", 500


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        email, password = request.form.get("email"), request.form.get("password")
        if not email or not password:
            flash("E-post och lösenord krävs!", "error")
            return redirect(url_for("signup"))

        hashed_password = generate_password_hash(password)

        try:
            db = get_db_connection()
            db.execute("INSERT INTO users (email, password) VALUES (?, ?)", (email, hashed_password))
            db.commit()
            db.close()
            flash("Kontot har skapats! Vänligen logga in.", "success")
            return redirect(url_for("inloggning"))
        except sqlite3.IntegrityError:
            flash("E-postadressen finns redan!", "error")

    return render_template("signup.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("Du har loggat ut.", "success")
    return redirect(url_for("inloggning"))

@socketio.on("disconnect")
def handle_disconnect():
    room = session.get("room")
    name = session.get("name")
    
    if room in rooms:
        rooms[room]["members"] -= 1
        
        # Remove room if empty
        if rooms[room]["members"] <= 0:
            del rooms[room]
            save_rooms_to_file()
            
        send({"name": name, "message": "has left the room"}, to=room)
        
    # Emit event to refresh rooms list for all clients
    socketio.emit("rooms_updated", broadcast=True)
    
@app.route("/home", methods=["POST", "GET"])
def home():
    if "user_id" not in session:
        flash("Vänligen logga in för att komma åt denna sida.", "error")
        return redirect(url_for("inloggning"))

    if request.method == "POST":
        name = request.form.get("name")
        code = request.form.get("code")
        subject = request.form.get("subject")
        join_action = 'join' in request.form  # Check if "Join" button was clicked
        create_action = 'create' in request.form  # Check if "Create" button was clicked

        # Validate name for all actions
        if not name.strip():
            return render_template("home.html", error="Please enter your name!", code=code, name=name, rooms=rooms)

        # Handle "Create Room" action
        if create_action:
            if not subject.strip():
                return render_template("home.html", error="Please enter a Subject!", code=code, name=name, subject=subject, rooms=rooms)
            
            # Generate a new room code
            room = generate_unique_code(4)
            rooms[room] = {"members": 0, "messages": [], "subject": subject, "creator": name}
            save_rooms_to_file()
            session["room"] = room
            session["name"] = name
            session["subject"] = subject
            return redirect(url_for("room"))

        # Handle "Join Room" action
        elif join_action:
            if not code:
                return render_template("home.html", error="Please enter a room Code", code=code, name=name, rooms=rooms)
            elif code not in rooms:
                return render_template("home.html", error="Room does not exist", code=code, name=name, rooms=rooms)
            
            # Join existing room
            session["room"] = code
            session["name"] = name
            session["subject"] = rooms[code]["subject"]  # Inherit the room's subject
            return redirect(url_for("room"))

    # Handle GET requests or invalid actions
    return render_template("home.html", rooms=rooms)

@app.route("/room")
def room():
    room, name, subject = session.get("room"), session.get("name"), session.get("subject")
    if room not in rooms:
        return redirect(url_for("home"))
    return render_template("room.html", code=room, messages=rooms[room]["messages"], name=name, subject=subject)

@socketio.on("message")
def message(data):
    room = session.get("room")
    if room in rooms:
        content = {"name": session.get("name"), "subject": session.get("subject"), "message": data["data"]}
        send(content, to=room)
        rooms[room]["messages"].append(content)
        bot_interaction(room, user_message=data["data"])

@socketio.on("connect")
def connect(auth):
    room, name = session.get("room"), session.get("name")
    if room in rooms:
        join_room(room)
        send({"name": name, "message": "has entered the room"}, to=room)
        rooms[room]["members"] += 1
        bot_interaction(room)

if __name__ == "__main__":
    import eventlet
    import eventlet.wsgi
    eventlet.monkey_patch()

    socketio.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
