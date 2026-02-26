from flask import Flask, render_template, request, redirect, session, url_for, flash
import sqlite3
from apscheduler.schedulers.background import BackgroundScheduler
import os
# Absolute database path (VERY IMPORTANT)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "database.db")

app = Flask(__name__)
app.secret_key = "supersecretkey"

print("Database Path:", os.path.abspath("database.db"))

# ---------------------------
# Database Setup
# ---------------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Patients Table (with department column)
    c.execute('''
        CREATE TABLE IF NOT EXISTS patients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            phone TEXT,
            token INTEGER,
            status TEXT,
            priority TEXT,
            department TEXT
        )
    ''')

    # Department-wise Current Token Table
    c.execute('''
        CREATE TABLE IF NOT EXISTS current_token (
            department TEXT PRIMARY KEY,
            token INTEGER
        )
    ''')

    # Default Departments
    departments = ["General", "Dental", "Pediatrics", "Cardiology"]

    # Insert departments if not exists
    for dept in departments:
        c.execute("""
            INSERT OR IGNORE INTO current_token (department, token)
            VALUES (?, ?)
        """, (dept, 0))

    conn.commit()
    conn.close()
init_db()

# ---------------------------
# Booking Page
# ---------------------------

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        name = request.form['name']
        phone = request.form['phone']
        department = request.form.get('department')

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()

        # Generate token per department
        c.execute("SELECT MAX(token) FROM patients WHERE department=?", (department,))
        last_token = c.fetchone()[0]
        token = 1 if last_token is None else last_token + 1

        c.execute("""INSERT INTO patients 
                     (name, phone, token, status, priority, department) 
                     VALUES (?, ?, ?, ?, ?, ?)""",
                  (name, phone, token, "Waiting", "Normal", department))

       # Check current token for this department
        c.execute("SELECT token FROM current_token WHERE department=?", (department,))
        current = c.fetchone()[0]

        # If no active token (0), set first booking as current
        if current == 0:
            c.execute("""
                UPDATE current_token
                SET token=?
                WHERE department=?
            """, (token, department))

        conn.commit()
        conn.close()

        session['token_number'] = f"{department[:2].upper()}-{token}"
        return redirect(url_for('index'))

    token_number = session.pop('token_number', None)
    return render_template("index.html", token_number=token_number)
# ---------------------------
# Login
# ---------------------------

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form['username'] == "admin" and request.form['password'] == "1234":
            session['admin'] = True
            return redirect(url_for('admin'))
        else:
            return "Invalid Credentials"

    return render_template("login.html")

# ---------------------------
# Logout
# ---------------------------

@app.route('/logout')
def logout():
    session.pop('admin', None)
    return redirect(url_for('login'))

# ---------------------------
# Admin Panel
# ---------------------------
@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if 'admin' not in session:
        return redirect(url_for('login'))

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # -----------------------------------
    # HANDLE BUTTON ACTIONS
    # -----------------------------------
    if request.method == 'POST':
        action = request.form.get('action')
        patient_id = request.form.get('patient_id')

        # -------------------------
        # EMERGENCY OVERRIDE
        # -------------------------
        if action == "emergency" and patient_id:

            c.execute("SELECT department, token FROM patients WHERE id=?", (patient_id,))
            row = c.fetchone()

            if row:
                department, emergency_token = row

                # Set emergency priority
                c.execute("UPDATE patients SET priority='Emergency' WHERE id=?", (patient_id,))

                # Immediately override current token
                c.execute("""
                    UPDATE current_token
                    SET token=?
                    WHERE department=?
                """, (emergency_token, department))

        # -------------------------
        # COMPLETE PATIENT
        # -------------------------
        elif action == "complete" and patient_id:

            c.execute("SELECT department FROM patients WHERE id=?", (patient_id,))
            row = c.fetchone()

            if row:
                department = row[0]

                # Mark completed
                c.execute("UPDATE patients SET status='Completed' WHERE id=?", (patient_id,))

                # Find next patient (Emergency first)
                c.execute("""
                    SELECT token FROM patients
                    WHERE status='Waiting' AND department=?
                    ORDER BY
                        CASE WHEN priority='Emergency' THEN 0 ELSE 1 END,
                        token ASC
                    LIMIT 1
                """, (department,))

                next_patient = c.fetchone()

                if next_patient:
                    next_token = next_patient[0]
                else:
                    next_token = 0  # No patients left

                # Update department current token
                c.execute("""
                    UPDATE current_token
                    SET token=?
                    WHERE department=?
                """, (next_token, department))

        # -------------------------
        # RESET DAY
        # -------------------------
        elif action == "reset":
            c.execute("DELETE FROM patients")
            c.execute("UPDATE current_token SET token=0")

        conn.commit()

    # -----------------------------------
    # FETCH WAITING PATIENTS
    # -----------------------------------
    c.execute("""
        SELECT * FROM patients
        WHERE status='Waiting'
        ORDER BY
            department,
            CASE WHEN priority='Emergency' THEN 0 ELSE 1 END,
            token ASC
    """)
    patients_raw = c.fetchall()

    # -----------------------------------
    # FETCH CURRENT TOKENS PER DEPARTMENT
    # -----------------------------------
    c.execute("SELECT department, token FROM current_token")
    dept_tokens_raw = c.fetchall()

    department_tokens = {d[0]: d[1] for d in dept_tokens_raw}

    # -----------------------------------
    # CALCULATE WAITING TIME PER DEPARTMENT
    # -----------------------------------
    patients = []
    dept_group = {}

    # Group by department
    for p in patients_raw:
        dept = p[6]
        if dept not in dept_group:
            dept_group[dept] = []
        dept_group[dept].append(p)

    for dept, patient_list in dept_group.items():

        # Sort inside department (Emergency first)
        sorted_list = sorted(
            patient_list,
            key=lambda x: (0 if x[5] == "Emergency" else 1, x[3])
        )

        for index, p in enumerate(sorted_list):
            estimated_time = index * 10

            patients.append({
                "id": p[0],
                "name": p[1],
                "phone": p[2],
                "token": p[3],
                "status": p[4],
                "priority": p[5],
                "department": p[6],
                "estimated": estimated_time
            })

    conn.close()

    return render_template("admin.html",
                           patients=patients,
                           department_tokens=department_tokens)
# ---------------------------
# Waiting Room Display
# ---------------------------

@app.route('/display')
@app.route('/display/<department>')
def display(department="General"):

    department = department.title()

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Get current token for selected department
    c.execute("SELECT token FROM current_token WHERE department=?", (department,))
    result = c.fetchone()

    if result:
        current_token = result[0]
    else:
        current_token = 0

    # Get waiting patients for that department
    c.execute("""
        SELECT * FROM patients
        WHERE status='Waiting' AND department=?
        ORDER BY
        CASE WHEN priority='Emergency' THEN 0 ELSE 1 END,
        token ASC
    """, (department,))

    waiting_patients = c.fetchall()

    # Build upcoming list
    upcoming = []
    for p in waiting_patients:
        if p[3] != current_token:
            upcoming.append({
                "token": p[3],
                "priority": p[5]
            })

    conn.close()

    return render_template("display.html",
                           current_token=current_token,
                           next_patients=upcoming[:5],
                           department=department)
# ---------------------------
# Patient Tracking
# ---------------------------

@app.route('/track', methods=['GET', 'POST'])
def track():
    result = None

    if request.method == 'POST':
        token = int(request.form['token'])
        department = request.form.get('department')

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()

        c.execute("SELECT token FROM current_token WHERE department=?", (department,))
        row = c.fetchone()

        if row:
            current_token = row[0]
        else:
            current_token = 0

        c.execute("""
            SELECT * FROM patients 
            WHERE token=? AND department=? AND status='Waiting'
        """, (token, department))

        patient = c.fetchone()

        if patient:
            position = token - current_token
            if position < 0:
                position = 0

            result = {
                "token": token,
                "current": current_token,
                "position": position,
                "estimated": position * 10
            }

        conn.close()

    return render_template("track.html", result=result)
# ---------------------------
# Automatic Daily Reset
# ---------------------------

def daily_reset():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Delete all patients
    c.execute("DELETE FROM patients")

    # Reset all departments to 0
    c.execute("UPDATE current_token SET token=0")

    conn.commit()
    conn.close()

scheduler = BackgroundScheduler()
scheduler.add_job(daily_reset, 'cron', hour=0, minute=0)
scheduler.start()

# ---------------------------
# Twilio Configuration
# ---------------------------

account_sid = "ACxx"
auth_token = "1xxx"
twilio_number = "123"

# ---------------------------
# Run App
# ---------------------------

if __name__ == '__main__':
    app.run(debug=True, use_reloader=False)




