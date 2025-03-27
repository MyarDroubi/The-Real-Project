from flask import Flask, render_template, request, session, redirect, url_for, flash, g
from flask_socketio import join_room, send, SocketIO
import random
import json
import os
import eventlet
import eventlet.wsgi
from string import ascii_uppercase
from huggingface_hub import InferenceClient
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)
socketio = SocketIO(app,async_mode="eventlet")

app.config["SECRET_KEY"] = "THISISACODE"

#socketio = SocketIO(app)

#Vi hämtar klienten för Huggingface för att prata med AI-boten
bot_client = InferenceClient(
    provider="novita",
    api_key="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"
)


DATABASE = os.path.join(BASE_DIR, "users.databas")

#Här vi connectar till databas
def Databas_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

#Här vi skapar användare om de inte finns
def databas_inneholl():
    with app.app_context():
        databas = Databas_connection()
        databas.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL
            )
        """)
        databas.commit()
        databas.close()

databas_inneholl()


"""
#Spara historik i databas eller js
def Spara_historik():
    room = session.get("room")
    name = session.get("name")

    
#Funktion som vissas historik för chatt
def Visa_historik():
    pass
"""

rooms = {}
filerooms = os.path.join(BASE_DIR, "rooms.json")

#Den funktionen för att spara rum för att visa de senare
def Spara_room():
    with open(filerooms, "w") as f:
        json.dump(rooms, f)

#För att genererara slumpmässig kod för rum chatten
def Skapa_kod(length):
    code = "".join(random.choice(ascii_uppercase) for _ in range(length))
    if code not in rooms:
        return code

#Den funktioner används för  kommunicera med AI chatt
def Bot_connection(room, user_message=None):
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
                print(f"Error in bot interaction: {e}") 
                final_answer = "I'm having trouble responding right now."

        else:
            final_answer = "Hello! What would you like to talk about?"

        send({"name": "Bot", "message": final_answer}, to=room)
        rooms[room]["messages"].append({"name": "Bot", "message": final_answer})


@app.before_request
def visa_user():
    user_id = session.get('user_id')
    if user_id is not None:
        g.user = user_id
    else:
        g.user = None

#Hanterar inloggning sida
@app.route("/", methods=["GET", "POST"])
def inloggning():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        if not email or not password:
            flash("E-postadress och lösenord krävs!", "error")
            return redirect(url_for("inloggning"))

        databas = Databas_connection()
        user = databas.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        databas.close()

        if user and check_password_hash(user["password"], password):
            session["user_id"] = user["id"]
            session["email"] = user["email"]
            session.permanent = True
            app.permanent_session_lifetime = timedelta(days=1)
            return redirect(url_for("index"))

        flash("Fel e-postadress eller lösenord!", "error")
    return render_template("Inloggning.html")

#Hanterar Om_oss sida
@app.route('/om_oss')
def om_oss():
    if "user_id" not in session:
        flash("Vänligen logga in.", "error")
        return redirect(url_for("inloggning"))
    return render_template('om_oss.html')

#Hanterar Index/Introduktion sida
@app.route("/index")
def index():
    print("Session data:", session)
    if "user_id" not in session:
        flash("Vänligen logga in.", "error")
        return redirect(url_for("inloggning"))
    return render_template("Index.html")


#Hanterar signup sida 
@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        email, password = request.form.get("email"), request.form.get("password")
        if not email or not password:
            flash("E-post och lösenord krävs!", "error")
            return redirect(url_for("signup"))

        hashed_password = generate_password_hash(password)

        try:
            databas = Databas_connection()
            databas.execute("INSERT INTO users (email, password) VALUES (?, ?)", (email, hashed_password))
            databas.commit()
            databas.close()
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

#Det här för om man lämnar in chat room
@socketio.on("disconnect")
def handle_disconnect():
    room = session.get("room")
    name = session.get("name")
    
    if room in rooms:
        rooms[room]["members"] -= 1
        
        if rooms[room]["members"] <= 0:
            del rooms[room]
            Spara_room()
            
        send({"name": name, "message": "has left the room"}, to=room)
        
    socketio.emit("rooms_updated", broadcast=True)


#Hanterar livechatt html för att skapa eller ansluta till ett rum
@app.route("/livechatt", methods=["POST", "GET"])
def livechatt():
    if "user_id" not in session:
        flash("Vänligen logga in för att komma åt denna sida.", "error")
        return redirect(url_for("inloggning"))

    if request.method == "POST":
        name = request.form.get("name")
        code = request.form.get("code")
        subject = request.form.get("subject")
        join_action = 'join' in request.form  
        create_action = 'create' in request.form  

        if not name.strip():
            return render_template("livechatt.html", error="Please enter your name!", code=code, name=name, rooms=rooms)

        if create_action:
            if not subject.strip():
                return render_template("livechatt.html", error="Please enter a Subject!", code=code, name=name, subject=subject, rooms=rooms)
            
            room = Skapa_kod(4)
            rooms[room] = {"members": 0, "messages": [], "subject": subject, "creator": name}
            Spara_room()
            session["room"] = room
            session["name"] = name
            session["subject"] = subject
            return redirect(url_for("room"))

        elif join_action:
            if not code:
                return render_template("livechatt.html", error="Please enter a room Code", code=code, name=name, rooms=rooms)
            elif code not in rooms:
                return render_template("livechatt.html", error="Room does not exist", code=code, name=name, rooms=rooms)
            
            session["room"] = code
            session["name"] = name
            session["subject"] = rooms[code]["subject"]
            return redirect(url_for("room"))

    return render_template("livechatt.html", rooms=rooms)

#room.html delen för att chatta med AI eller person
@app.route("/room")
def room():
    room = session.get("room") 
    name = session.get("name")
    subject = session.get("subject")
    if room not in rooms:
        return redirect(url_for("livechatt"))
    return render_template("room.html", code=room, messages=rooms[room]["messages"], name=name, subject=subject)

#Sköter meddelande som sker
@socketio.on("message")
def message(data):
    room = session.get("room")
    name = session.get("name")
    subject = session.get("subject")
    if room in rooms:
        content = {"name": name, "subject": subject, "message": data["data"]}
        send(content, to=room)
        rooms[room]["messages"].append(content)
        Bot_connection(room, user_message=data["data"])

#Jag har skapat det här funktionen för användaren ska ansluta till rätt rum
@socketio.on("connect")
def connect(auth):
    room = session.get("room")
    name = session.get("name")
    if room in rooms:
        join_room(room)
        send({"name": name, "message": "has entered the room"}, to=room)
        rooms[room]["members"] += 1
        Bot_connection(room)

#Här vi startar hela appen/webbsida med eventlet som asynkront körsystem
if __name__ == "__main__":
    eventlet.monkey_patch()
    socketio.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
