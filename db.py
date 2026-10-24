# =====================================================================
#  db.py  -  everything that talks to MySQL lives in this file
#
#  The connection details are NOT written here. They are read from:
#    1. Environment variables (this is how Vercel gives them to us), or
#    2. a file called local_config.py (only on your own computer)
# =====================================================================
import os
import mysql.connector

try:
    import local_config          # exists only on your own computer
except ImportError:
    local_config = None


def setting(name, default=None):
    """Read a setting from the environment first, then from local_config.py."""
    value = os.environ.get(name)
    if value is None and local_config is not None:
        value = getattr(local_config, name, None)
    return default if value is None else value


DB_CONFIG = {
    "host": setting("DB_HOST", "localhost"),
    "port": int(setting("DB_PORT", 3306)),
    "user": setting("DB_USER", "root"),
    "password": setting("DB_PASSWORD", ""),
    "database": setting("DB_NAME", "ecommerce_db"),
    "connection_timeout": 10,
}

# Cloud databases need an encrypted connection
if str(setting("DB_SSL", "false")).lower() == "true":
    DB_CONFIG["ssl_disabled"] = False

# Any MySQL error can be caught with:  except db.DBError as e
DBError = mysql.connector.Error


def get_connection():
    """Open a new connection to the database."""
    return mysql.connector.connect(**DB_CONFIG)


def query_all(sql, params=()):
    """Run a SELECT and return ALL rows as a list of dictionaries."""
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(sql, params)
        return cur.fetchall()
    finally:
        cur.close()
        conn.close()


def query_one(sql, params=()):
    """Run a SELECT and return ONE row as a dictionary (or None)."""
    conn = get_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(sql, params)
        return cur.fetchone()
    finally:
        cur.close()
        conn.close()


def execute(sql, params=()):
    """Run INSERT / UPDATE / DELETE, save it, return the new row id."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        conn.commit()
        return cur.lastrowid
    finally:
        cur.close()
        conn.close()


def call_procedure(name, args=()):
    """
    Call a stored procedure. For an OUT parameter pass 0 as a placeholder.
    Returns the list of arguments, with OUT values filled in by MySQL.
    Example: result = call_procedure("sp_place_order", (1, "UPI", None, 0))
             order_id = result[3]
    """
    conn = get_connection()
    cur = conn.cursor()
    try:
        result = cur.callproc(name, args)
        conn.commit()
        return list(result)
    finally:
        cur.close()
        conn.close()


def error_message(e):
    """Turn a MySQL error into a short message the user can understand."""
    text = getattr(e, "msg", str(e))
    code = getattr(e, "errno", None)

    if code == 1062:
        return "This email is already registered."
    if code == 3819:                      # a CHECK constraint failed
        if "phone" in text:
            return "Phone number must be exactly 10 digits."
        if "pincode" in text:
            return "Pincode must be exactly 6 digits."
        if "email" in text:
            return "Please enter a valid email address."
        return "Some of the entered values are not valid."
    if code == 1644:                      # our own SIGNAL messages from triggers/procedures
        return text
    return text
