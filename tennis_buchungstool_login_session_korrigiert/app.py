from datetime import datetime, date, time, timedelta
from functools import wraps
import calendar
import secrets
import string

from flask import Flask, render_template, redirect, url_for, request, flash, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config["SECRET_KEY"] = "bitte-aendern-produktionsschluessel"
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///tennisbuchung.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SESSION_PERMANENT"] = False

db = SQLAlchemy(app)

COURTS = [1, 2, 3]
DEFAULT_SETTINGS = {
    "max_days_in_advance": "14",
    "max_booking_minutes": "120",
    "max_bookings_per_day": "2",
    "max_hours_per_week": "4",
}


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(160), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    is_approved = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    must_change_password = db.Column(db.Boolean, default=True)
    temporary_password = db.Column(db.String(80), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    bookings_made = db.relationship("Booking", foreign_keys="Booking.user_id", backref="user", lazy=True)
    partner_bookings = db.relationship("Booking", foreign_keys="Booking.partner_id", backref="partner", lazy=True)

    def set_password(self, password: str, temporary: bool = False) -> None:
        self.password_hash = generate_password_hash(password, method="pbkdf2:sha256")
        self.must_change_password = temporary
        self.temporary_password = password if temporary else None

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    court = db.Column(db.Integer, nullable=False)
    start = db.Column(db.DateTime, nullable=False)
    end = db.Column(db.DateTime, nullable=False)
    note = db.Column(db.String(240), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    partner_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)


class CourtBlock(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    court = db.Column(db.Integer, nullable=False)
    start = db.Column(db.DateTime, nullable=False)
    end = db.Column(db.DateTime, nullable=False)
    reason = db.Column(db.String(240), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Setting(db.Model):
    key = db.Column(db.String(80), primary_key=True)
    value = db.Column(db.String(240), nullable=False)


def setting_int(key: str) -> int:
    setting = db.session.get(Setting, key)
    return int(setting.value) if setting else int(DEFAULT_SETTINGS[key])


def current_user():
    uid = session.get("user_id")
    return db.session.get(User, uid) if uid else None


@app.context_processor
def inject_user():
    try:
        settings = {k: setting_int(k) for k in DEFAULT_SETTINGS}
    except Exception:
        settings = {k: int(v) for k, v in DEFAULT_SETTINGS.items()}
    return {"current_user": current_user(), "courts": COURTS, "settings": settings}


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            flash("Bitte melde dich zuerst an.", "warning")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def password_change_checked(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user and user.must_change_password and request.endpoint != "change_password":
            flash("Bitte ändere zuerst dein Einmalpasswort.", "warning")
            return redirect(url_for("change_password"))
        return view(*args, **kwargs)
    return wrapped


def approved_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user:
            flash("Bitte melde dich zuerst an.", "warning")
            return redirect(url_for("login"))
        if user.must_change_password and request.endpoint != "change_password":
            flash("Bitte ändere zuerst dein Einmalpasswort.", "warning")
            return redirect(url_for("change_password"))
        if not user.is_active:
            flash("Dein Benutzerkonto ist deaktiviert.", "danger")
            return redirect(url_for("login"))
        if not user.is_approved and not user.is_admin:
            flash("Dein Konto ist noch nicht als Vereinsmitglied freigegeben.", "warning")
            return redirect(url_for("belegung"))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user or not user.is_admin:
            flash("Für diese Seite sind Adminrechte erforderlich.", "danger")
            return redirect(url_for("belegung"))
        if user.must_change_password and request.endpoint != "change_password":
            flash("Bitte ändere zuerst dein Einmalpasswort.", "warning")
            return redirect(url_for("change_password"))
        return view(*args, **kwargs)
    return wrapped


def generate_temp_password(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def init_db():
    db.create_all()
    for key, value in DEFAULT_SETTINGS.items():
        if not db.session.get(Setting, key):
            db.session.add(Setting(key=key, value=value))

    if not User.query.filter_by(email="admin@verein.de").first():
        admin = User(name="Administrator", email="admin@verein.de", is_admin=True, is_approved=True, is_active=True)
        admin.set_password("admin123", temporary=True)
        db.session.add(admin)

    db.session.commit()


def overlaps(court: int, start: datetime, end: datetime) -> bool:
    if Booking.query.filter(Booking.court == court, Booking.start < end, Booking.end > start).first():
        return True
    return CourtBlock.query.filter(CourtBlock.court == court, CourtBlock.start < end, CourtBlock.end > start).first() is not None


def user_bookings_between(user_id: int, start: datetime, end: datetime):
    return Booking.query.filter(
        ((Booking.user_id == user_id) | (Booking.partner_id == user_id)),
        Booking.start < end,
        Booking.end > start,
    ).all()


def minutes_booked_by_user_between(user_id: int, start: datetime, end: datetime) -> int:
    total = 0
    for b in user_bookings_between(user_id, start, end):
        total += int((min(b.end, end) - max(b.start, start)).total_seconds() // 60)
    return total


def daily_booking_count(user_id: int, day: date) -> int:
    day_start = datetime.combine(day, time(0, 0))
    day_end = day_start + timedelta(days=1)
    return Booking.query.filter(
        ((Booking.user_id == user_id) | (Booking.partner_id == user_id)),
        Booking.start >= day_start,
        Booking.start < day_end,
    ).count()


def limit_summary_for_user(user: User, reference_day=None):
    if reference_day is None:
        reference_day = date.today()
    week_start_date = reference_day - timedelta(days=reference_day.weekday())
    week_start = datetime.combine(week_start_date, time(0, 0))
    week_end = week_start + timedelta(days=7)
    used_day = daily_booking_count(user.id, reference_day)
    max_day = setting_int("max_bookings_per_day")
    used_week_min = minutes_booked_by_user_between(user.id, week_start, week_end)
    max_week_min = setting_int("max_hours_per_week") * 60
    return {
        "user": user,
        "used_day": used_day,
        "remaining_day": max(0, max_day - used_day),
        "max_day": max_day,
        "used_week_hours": round(used_week_min / 60, 2),
        "remaining_week_hours": round(max(0, max_week_min - used_week_min) / 60, 2),
        "max_week_hours": setting_int("max_hours_per_week"),
    }


def validate_limit_for_user(user: User, start: datetime, duration_minutes: int):
    if user.is_admin:
        return None

    if daily_booking_count(user.id, start.date()) >= setting_int("max_bookings_per_day"):
        return f"{user.name} hat das tägliche Buchungslimit erreicht."

    week_start = datetime.combine(start.date() - timedelta(days=start.weekday()), time(0, 0))
    week_end = week_start + timedelta(days=7)
    if minutes_booked_by_user_between(user.id, week_start, week_end) + duration_minutes > setting_int("max_hours_per_week") * 60:
        return f"{user.name} würde das Limit „Maximale Stunden im Voraus buchbar“ überschreiten."

    return None


def query_items(start_dt: datetime, end_dt: datetime):
    bookings = Booking.query.filter(Booking.start < end_dt, Booking.end > start_dt).order_by(Booking.court, Booking.start).all()
    blocks = CourtBlock.query.filter(CourtBlock.start < end_dt, CourtBlock.end > start_dt).order_by(CourtBlock.court, CourtBlock.start).all()
    return bookings, blocks


def month_matrix(year: int, month: int):
    return calendar.Calendar(firstweekday=0).monthdatescalendar(year, month)


@app.route("/")
def root():
    session.clear()
    return redirect(url_for("login"))


@app.route("/auto-logout", methods=["POST"])
def auto_logout():
    session.clear()
    return ("", 204)


@app.route("/belegung")
@login_required
@password_change_checked
def belegung():
    view = request.args.get("view", "day")
    selected_date_str = request.args.get("date", date.today().isoformat())
    try:
        selected_date = date.fromisoformat(selected_date_str)
    except ValueError:
        selected_date = date.today()

    if view == "week":
        start_day = selected_date - timedelta(days=selected_date.weekday())
        end_day = start_day + timedelta(days=7)
        bookings, blocks = query_items(datetime.combine(start_day, time(0, 0)), datetime.combine(end_day, time(0, 0)))
        days = [start_day + timedelta(days=i) for i in range(7)]
        return render_template("belegung.html", view=view, selected_date=selected_date, days=days, bookings=bookings, blocks=blocks, hours=list(range(24)))

    if view == "month":
        first_day = selected_date.replace(day=1)
        last_day_num = calendar.monthrange(first_day.year, first_day.month)[1]
        month_start = first_day
        month_end = first_day.replace(day=last_day_num) + timedelta(days=1)
        bookings, blocks = query_items(datetime.combine(month_start, time(0, 0)), datetime.combine(month_end, time(0, 0)))
        weeks = month_matrix(first_day.year, first_day.month)
        prev_month = (first_day - timedelta(days=1)).replace(day=1)
        next_month = month_end
        return render_template("belegung.html", view=view, selected_date=selected_date, first_day=first_day, weeks=weeks, bookings=bookings, blocks=blocks, prev_month=prev_month, next_month=next_month)

    day_start = datetime.combine(selected_date, time(0, 0))
    day_end = day_start + timedelta(days=1)
    bookings, blocks = query_items(day_start, day_end)
    return render_template("belegung.html", view="day", selected_date=selected_date, bookings=bookings, blocks=blocks, hours=list(range(24)))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not name or not email or not password:
            flash("Bitte alle Felder ausfüllen.", "warning")
            return redirect(url_for("register"))

        if len(password) < 8:
            flash("Das Passwort muss mindestens 8 Zeichen lang sein.", "warning")
            return redirect(url_for("register"))

        if User.query.filter_by(email=email).first():
            flash("Diese E-Mail-Adresse ist bereits registriert.", "warning")
            return redirect(url_for("register"))

        user = User(name=name, email=email, is_approved=False, is_active=True)
        user.set_password(password, temporary=False)
        db.session.add(user)
        db.session.commit()

        flash("Registrierung gespeichert. Buchungen sind erst nach Freigabe durch den Verein möglich.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        session.clear()

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()

        if not user or not user.check_password(password):
            flash("E-Mail oder Passwort ist nicht korrekt.", "danger")
            return redirect(url_for("login"))

        if not user.is_active:
            flash("Dieses Konto ist deaktiviert.", "danger")
            return redirect(url_for("login"))

        session["user_id"] = user.id
        session.permanent = False

        if user.must_change_password:
            flash("Bitte ändere dein Einmalpasswort.", "warning")
            return redirect(url_for("change_password"))

        flash("Du bist angemeldet.", "success")
        return redirect(url_for("belegung"))

    return render_template("login.html")


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    generated_password = None

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email).first()

        if not user or not user.is_active:
            flash("Für diese E-Mail-Adresse konnte kein aktives Konto gefunden werden.", "warning")
            return redirect(url_for("forgot_password"))

        generated_password = generate_temp_password()
        user.set_password(generated_password, temporary=True)
        db.session.commit()
        flash("Ein Einmalpasswort wurde erzeugt. Bitte damit einloggen und direkt ändern.", "success")

    return render_template("forgot_password.html", generated_password=generated_password)


@app.route("/logout")
def logout():
    session.clear()
    flash("Du bist abgemeldet.", "info")
    return redirect(url_for("login"))


@app.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    user = current_user()

    if request.method == "POST":
        old_password = request.form.get("old_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not user.check_password(old_password):
            flash("Das bisherige Passwort ist nicht korrekt.", "danger")
            return redirect(url_for("change_password"))

        if len(new_password) < 8:
            flash("Das neue Passwort muss mindestens 8 Zeichen lang sein.", "warning")
            return redirect(url_for("change_password"))

        if new_password != confirm_password:
            flash("Die neuen Passwörter stimmen nicht überein.", "warning")
            return redirect(url_for("change_password"))

        user.set_password(new_password, temporary=False)
        db.session.commit()
        flash("Passwort wurde geändert.", "success")
        return redirect(url_for("belegung"))

    return render_template("change_password.html")


@app.route("/book", methods=["GET", "POST"])
@approved_required
def book():
    max_days = setting_int("max_days_in_advance")
    max_booking_minutes = setting_int("max_booking_minutes")
    eligible_partners = User.query.filter(User.is_active == True, User.is_approved == True, User.id != current_user().id).order_by(User.name).all()

    if request.method == "POST":
        court = int(request.form.get("court"))
        partner_id = int(request.form.get("partner_id"))
        booking_date = request.form.get("date")
        start_time = request.form.get("start_time")
        duration_minutes = int(request.form.get("duration_minutes", 60))
        note = request.form.get("note", "").strip()

        partner = db.session.get(User, partner_id)
        if not partner or not partner.is_active or not partner.is_approved or partner.id == current_user().id:
            flash("Bitte wähle ein aktives, freigegebenes Mitglied als Spielpartner aus.", "danger")
            return redirect(url_for("book"))

        try:
            start = datetime.fromisoformat(f"{booking_date}T{start_time}")
        except ValueError:
            flash("Ungültiges Datum oder ungültige Uhrzeit.", "danger")
            return redirect(url_for("book"))

        end = start + timedelta(minutes=duration_minutes)

        if court not in COURTS:
            flash("Ungültiger Platz.", "danger")
            return redirect(url_for("book"))

        if duration_minutes <= 0 or duration_minutes > max_booking_minutes:
            flash(f"Eine Buchung darf maximal {max_booking_minutes} Minuten dauern.", "warning")
            return redirect(url_for("book"))

        if start.date() < date.today():
            flash("Buchungen in der Vergangenheit sind nicht möglich.", "warning")
            return redirect(url_for("book"))

        if start.date() > date.today() + timedelta(days=max_days):
            flash(f"Buchungen sind maximal {max_days} Tage im Voraus möglich.", "warning")
            return redirect(url_for("book"))

        if end.date() != start.date():
            flash("Buchungen müssen am selben Kalendertag enden.", "warning")
            return redirect(url_for("book"))

        if overlaps(court, start, end):
            flash("Dieser Platz ist in diesem Zeitraum bereits gebucht oder gesperrt.", "danger")
            return redirect(url_for("book"))

        for person in [current_user(), partner]:
            error = validate_limit_for_user(person, start, duration_minutes)
            if error:
                flash(error, "warning")
                return redirect(url_for("book"))

        booking = Booking(court=court, start=start, end=end, note=note, user_id=current_user().id, partner_id=partner.id)
        db.session.add(booking)
        db.session.commit()

        flash("Buchung wurde erfolgreich für beide Spieler angelegt.", "success")
        return redirect(url_for("my_bookings"))

    durations = [d for d in [30, 60, 90, 120, 150, 180, 240] if d <= max_booking_minutes]
    return render_template("book.html", today=date.today(), max_date=date.today() + timedelta(days=max_days), durations=durations, eligible_partners=eligible_partners)


@app.route("/my-bookings")
@login_required
@password_change_checked
def my_bookings():
    user = current_user()
    bookings = Booking.query.filter(((Booking.user_id == user.id) | (Booking.partner_id == user.id)), Booking.end >= datetime.now()).order_by(Booking.start).all()
    my_limits = limit_summary_for_user(user, date.today())
    return render_template("my_bookings.html", bookings=bookings, my_limits=my_limits)


@app.route("/cancel/<int:booking_id>", methods=["POST"])
@login_required
@password_change_checked
def cancel_booking(booking_id):
    booking = db.session.get(Booking, booking_id)

    if not booking:
        flash("Buchung wurde nicht gefunden.", "warning")
        return redirect(url_for("belegung"))

    user = current_user()
    if booking.user_id != user.id and booking.partner_id != user.id and not user.is_admin:
        flash("Diese Buchung darfst du nicht löschen.", "danger")
        return redirect(url_for("my_bookings"))

    db.session.delete(booking)
    db.session.commit()
    flash("Buchung wurde gelöscht.", "info")
    return redirect(request.referrer or url_for("belegung"))


@app.route("/admin")
@admin_required
def admin():
    bookings = Booking.query.order_by(Booking.start.desc()).limit(200).all()
    users = User.query.order_by(User.name).all()
    blocks = CourtBlock.query.order_by(CourtBlock.start.desc()).limit(100).all()
    pending_users = User.query.filter_by(is_approved=False, is_active=True).order_by(User.created_at.desc()).all()
    member_limits = [limit_summary_for_user(u, date.today()) for u in users if u.is_active and u.is_approved and not u.is_admin]
    return render_template("admin.html", bookings=bookings, users=users, blocks=blocks, pending_users=pending_users, member_limits=member_limits)


@app.route("/admin/settings", methods=["POST"])
@admin_required
def admin_settings():
    for key in DEFAULT_SETTINGS:
        value = request.form.get(key, "").strip()

        if not value.isdigit():
            flash("Alle Einstellungen müssen ganze Zahlen sein.", "danger")
            return redirect(url_for("admin"))

        setting = db.session.get(Setting, key)
        if not setting:
            db.session.add(Setting(key=key, value=value))
        else:
            setting.value = value

    db.session.commit()
    flash("Buchungseinstellungen wurden gespeichert.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/member/add", methods=["POST"])
@admin_required
def admin_add_member():
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    is_admin = request.form.get("is_admin") == "on"

    if not name or not email:
        flash("Name und E-Mail sind erforderlich.", "warning")
        return redirect(url_for("admin"))

    if User.query.filter_by(email=email).first():
        flash("Dieses Mitglied existiert bereits.", "warning")
        return redirect(url_for("admin"))

    password = generate_temp_password()
    user = User(name=name, email=email, is_admin=is_admin, is_approved=True, is_active=True)
    user.set_password(password, temporary=True)
    db.session.add(user)
    db.session.commit()

    flash(f"Mitglied wurde angelegt. Einmaliges Startpasswort: {password}", "success")
    return redirect(url_for("admin"))


@app.route("/admin/member/import", methods=["POST"])
@admin_required
def admin_import_members():
    raw = request.form.get("members", "").strip()
    skipped = 0
    passwords = []

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue

        if ";" not in line:
            skipped += 1
            continue

        name, email = [x.strip() for x in line.split(";", 1)]
        email = email.lower()

        if not name or not email or User.query.filter_by(email=email).first():
            skipped += 1
            continue

        password = generate_temp_password()
        user = User(name=name, email=email, is_approved=True, is_active=True)
        user.set_password(password, temporary=True)
        db.session.add(user)
        passwords.append(f"{name} <{email}>: {password}")

    db.session.commit()

    if passwords:
        flash("Import abgeschlossen. Einmalige Startpasswörter: " + " | ".join(passwords), "success")
    else:
        flash("Keine neuen Mitglieder importiert.", "warning")

    if skipped:
        flash(f"{skipped} Zeile(n) wurden übersprungen.", "info")

    return redirect(url_for("admin"))


@app.route("/admin/member/<int:user_id>/approve", methods=["POST"])
@admin_required
def admin_approve_member(user_id):
    user = db.session.get(User, user_id)
    if not user:
        flash("Mitglied nicht gefunden.", "warning")
        return redirect(url_for("admin"))

    user.is_approved = True
    user.is_active = True
    db.session.commit()
    flash(f"{user.name} wurde für Buchungen freigeschaltet.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/member/<int:user_id>/toggle-approved", methods=["POST"])
@admin_required
def admin_toggle_approved(user_id):
    user = db.session.get(User, user_id)
    if user and not user.is_admin:
        user.is_approved = not user.is_approved
        db.session.commit()
        flash("Buchungsberechtigung wurde geändert.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/member/<int:user_id>/toggle-active", methods=["POST"])
@admin_required
def admin_toggle_active(user_id):
    user = db.session.get(User, user_id)
    if user and user.id != current_user().id:
        user.is_active = not user.is_active
        db.session.commit()
        flash("Kontostatus wurde geändert.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/block/add", methods=["POST"])
@admin_required
def admin_add_block():
    court = int(request.form.get("court"))
    block_date = request.form.get("date")
    start_time = request.form.get("start_time")
    end_time = request.form.get("end_time")
    reason = request.form.get("reason", "").strip()

    try:
        start = datetime.fromisoformat(f"{block_date}T{start_time}")
        end = datetime.fromisoformat(f"{block_date}T{end_time}")
    except ValueError:
        flash("Ungültiges Datum oder ungültige Zeit.", "danger")
        return redirect(url_for("admin"))

    if court not in COURTS or not reason or end <= start:
        flash("Bitte gültige Platzsperre eingeben.", "warning")
        return redirect(url_for("admin"))

    if end.date() != start.date():
        flash("Sperren müssen am selben Kalendertag enden.", "warning")
        return redirect(url_for("admin"))

    if overlaps(court, start, end):
        flash("Die Sperre überschneidet sich mit einer bestehenden Buchung oder Sperre.", "danger")
        return redirect(url_for("admin"))

    db.session.add(CourtBlock(court=court, start=start, end=end, reason=reason))
    db.session.commit()
    flash("Platzsperre wurde angelegt.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/block/<int:block_id>/delete", methods=["POST"])
@admin_required
def admin_delete_block(block_id):
    block = db.session.get(CourtBlock, block_id)
    if block:
        db.session.delete(block)
        db.session.commit()
        flash("Platzsperre wurde gelöscht.", "info")
    return redirect(url_for("admin"))


if __name__ == "__main__":
    with app.app_context():
        init_db()
    app.run(debug=True)
