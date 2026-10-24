# =====================================================================
#  helpers.py  -  small tools shared by all the page files
# =====================================================================
from decimal import Decimal, InvalidOperation
from functools import wraps
from flask import session, flash, redirect, url_for, request, g
import db


def to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def to_decimal(value):
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, TypeError):
        return None


def friendly(e):
    """Turn a MySQL error into a message the user can understand."""
    text = getattr(e, "msg", str(e))
    code = getattr(e, "errno", None)
    if code == 1062:
        return "This value already exists. Please use a different one."
    if code == 1451:
        return ("This record is used by other data (for example past orders), "
                "so it cannot be deleted.")
    if code == 3819:                                  # a CHECK constraint failed
        if "price" in text:
            return "Price must be greater than 0."
        if "reorder" in text:
            return "Reorder level cannot be negative."
        if "listing_min" in text:
            return "Minimum order quantity must be at least 1."
        if "qty" in text or "inventory" in text:
            return "Quantity cannot be negative."
    return db.error_message(e)


def add_pct(rows, key):
    """Adds a 'pct' (0-100) to every row so the report pages can draw bars."""
    top = max([float(r[key]) for r in rows] or [0]) or 1
    for r in rows:
        r["pct"] = round(float(r[key]) / top * 100)
    return rows


def safe_next(default_endpoint):
    """Where to go after a form: the 'next' address if it is on our own site."""
    target = request.form.get("next", "")
    if target.startswith("/") and not target.startswith("//"):
        return target
    return url_for(default_endpoint)


# ---------------------------------------------------------------------
#  Login guards: put one of these above a route to protect it
# ---------------------------------------------------------------------
def customer_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "customer_id" not in session:
            flash("Please log in to continue.", "error")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("is_admin"):
            flash("Admin login required.", "error")
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return wrapper


def seller_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "seller_id" not in session:
            flash("Please log in as a seller to continue.", "error")
            return redirect(url_for("seller_login"))
        # read the seller again on every page, so an admin's approval or
        # suspension takes effect immediately
        seller = db.query_one(
            "SELECT seller_id, business_name, contact_name, email, phone, address, city, "
            "pincode, gst_number, status FROM seller WHERE seller_id = %s", (session["seller_id"],))
        if not seller:
            session.clear()
            flash("Seller account not found.", "error")
            return redirect(url_for("seller_login"))
        g.seller = seller
        return f(*args, **kwargs)
    return wrapper
