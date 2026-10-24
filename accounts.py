# =====================================================================
#  accounts.py  -  register, log in and log out (customers and sellers)
#
#  In "supabase" mode the password is checked by Supabase and only
#  CONFIRMED emails can get in. Our own MySQL table is filled the first
#  time a confirmed person logs in.
# =====================================================================
import re
from flask import render_template, request, redirect, url_for, session, flash
import db
import auth
from auth import AuthError

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

CUSTOMER_FIELDS = ["full_name", "phone", "address", "city", "pincode"]
SELLER_FIELDS = ["business_name", "contact_name", "phone", "address", "city", "pincode", "gst_number"]


def clean_profile(form, fields):
    return {name: (form.get(name) or "").strip() for name in fields}


def check_profile(profile, email, password, required):
    """Returns an error text, or None when everything looks fine."""
    for name in required:
        if not profile.get(name):
            return "Please fill in every field."
    if not EMAIL_PATTERN.match(email):
        return "Please enter a valid email address."
    if len(password) < 6:
        return "Password must be at least 6 characters."
    if not re.fullmatch(r"\d{10}", profile["phone"]):
        return "Phone number must be exactly 10 digits."
    if not re.fullmatch(r"\d{6}", profile["pincode"]):
        return "Pincode must be exactly 6 digits."
    return None


def find_account(table, email):
    if table == "customer":
        return db.query_one("SELECT customer_id, full_name FROM customer WHERE email = %s", (email,))
    return db.query_one("SELECT seller_id, business_name FROM seller WHERE email = %s", (email,))


def email_in_use(email):
    return bool(find_account("customer", email) or find_account("seller", email))


def create_account(role, email, profile, password=None):
    """Inserts the customer / seller row. password=None means 'Supabase checks it'."""
    hash_sql = "SHA2(%s, 256)" if password is not None else "SHA2(UUID(), 256)"
    hash_params = (password,) if password is not None else ()
    if role == "customer":
        db.execute(
            "INSERT INTO customer (full_name, email, password_hash, phone, address, city, pincode) "
            "VALUES (%s, %s, " + hash_sql + ", %s, %s, %s, %s)",
            (profile["full_name"], email) + hash_params +
            (profile["phone"], profile["address"], profile["city"], profile["pincode"]))
    else:
        db.execute(
            "INSERT INTO seller (business_name, contact_name, email, password_hash, phone, address, "
            "city, pincode, gst_number) VALUES (%s, %s, %s, " + hash_sql + ", %s, %s, %s, %s, %s)",
            (profile["business_name"], profile["contact_name"], email) + hash_params +
            (profile["phone"], profile["address"], profile["city"], profile["pincode"],
             profile["gst_number"] or None))


def start_session(role, account):
    session.clear()
    if role == "customer":
        session["customer_id"] = account["customer_id"]
        session["name"] = account["full_name"]
    else:
        session["seller_id"] = account["seller_id"]
        session["seller_name"] = account["business_name"]


def account_from_supabase(role, user):
    """After Supabase confirms the person: find (or create) their row.
    Returns (account, error_text)."""
    email = user["email"]
    meta = user["metadata"]
    other = "seller" if role == "customer" else "customer"

    account = find_account(role, email)
    if account:
        return account, None
    if find_account(other, email):
        return None, "This email belongs to a %s account. Please use the %s login." % (other, other)
    if meta.get("role") != role:
        return None, "This email was not registered as a %s. Please register first." % role

    fields = CUSTOMER_FIELDS if role == "customer" else SELLER_FIELDS
    profile = {name: str(meta.get(name) or "").strip() for name in fields}
    try:
        create_account(role, email, profile)
    except db.DBError as e:
        return None, db.error_message(e)
    return find_account(role, email), None


def init(app):

    # ----------------------------------------------------------- customers
    @app.route("/register", methods=["GET", "POST"])
    def register():
        return register_page("customer")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        return login_page("customer")

    # ------------------------------------------------------------- sellers
    @app.route("/seller/register", methods=["GET", "POST"])
    def seller_register():
        return register_page("seller")

    @app.route("/seller/login", methods=["GET", "POST"])
    def seller_login():
        return login_page("seller")

    # -------------------------------------------------------------- shared
    def register_page(role):
        template = "register.html" if role == "customer" else "seller_register.html"
        fields = CUSTOMER_FIELDS if role == "customer" else SELLER_FIELDS
        required = [f for f in fields if f != "gst_number"]
        if request.method == "GET":
            return render_template(template, form={})

        form = request.form
        email = (form.get("email") or "").strip().lower()
        password = form.get("password") or ""
        profile = clean_profile(form, fields)

        problem = check_profile(profile, email, password, required)
        if not problem and email_in_use(email):
            problem = "This email is already registered. Please log in."
        if problem:
            flash(problem, "error")
            return render_template(template, form=form)

        if auth.MODE == "local":
            try:
                create_account(role, email, profile, password)
            except db.DBError as e:
                flash(db.error_message(e), "error")
                return render_template(template, form=form)
            if role == "seller":
                flash("Account created. You can log in now; the admin must approve it before you can list products.", "success")
            else:
                flash("Account created! Please log in.", "success")
            return redirect(url_for("login" if role == "customer" else "seller_login"))

        if auth.MODE != "supabase":
            flash("Sign-up is not available: the website is missing its Supabase settings.", "error")
            return render_template(template, form=form)

        try:
            result = auth.sign_up(email, password, dict(profile, role=role),
                                  auth.site_url() + url_for("auth_confirmed"))
        except AuthError as e:
            flash(e.message, "error")
            return render_template(template, form=form)
        if result["already_registered"]:
            flash("This email is already registered. Please log in.", "error")
            return redirect(url_for("login" if role == "customer" else "seller_login"))
        return render_template("check_email.html", email=email, role=role)

    def login_page(role):
        template = "login.html" if role == "customer" else "seller_login.html"
        if request.method == "GET":
            return render_template(template, need_confirm=False, email="")

        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        account = None

        if auth.MODE == "local":
            if role == "customer":
                account = db.query_one(
                    "SELECT customer_id, full_name FROM customer "
                    "WHERE email = %s AND password_hash = SHA2(%s, 256)", (email, password))
            else:
                account = db.query_one(
                    "SELECT seller_id, business_name FROM seller "
                    "WHERE email = %s AND password_hash = SHA2(%s, 256)", (email, password))
            if not account:
                flash("Wrong email or password.", "error")
        elif auth.MODE == "supabase":
            try:
                user = auth.sign_in(email, password)
            except AuthError as e:
                flash(e.message, "error")
                return render_template(template, need_confirm=e.needs_confirmation, email=email)
            account, problem = account_from_supabase(role, user)
            if problem:
                flash(problem, "error")
        else:
            flash("Login is not available: the website is missing its Supabase settings.", "error")

        if account:
            start_session(role, account)
            return redirect(url_for("shop" if role == "customer" else "seller_dashboard"))
        return render_template(template, need_confirm=False, email=email)

    @app.route("/logout")
    def logout():
        session.clear()
        flash("You have been logged out.", "success")
        return redirect(url_for("home"))

    @app.route("/auth/confirmed")
    def auth_confirmed():
        # Supabase sends people here after they click the link in the email
        return render_template("auth_confirmed.html")

    @app.route("/auth/resend", methods=["POST"])
    def auth_resend():
        email = (request.form.get("email") or "").strip().lower()
        back = request.form.get("role", "customer")
        if auth.MODE == "supabase" and email:
            try:
                auth.resend_confirmation(email, auth.site_url() + url_for("auth_confirmed"))
                flash("A new confirmation email has been sent. Please check your inbox.", "success")
            except AuthError as e:
                flash(e.message, "error")
        return redirect(url_for("seller_login" if back == "seller" else "login"))
