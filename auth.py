# =====================================================================
#  auth.py  -  who is allowed in, and where uploaded pictures go
#
#  Three modes:
#    "supabase"      real emails only: Supabase sends a confirmation link
#    "local"         for testing on your own computer (emails NOT verified)
#    "misconfigured" running online without Supabase keys: sign-in is refused
#
#  Settings (environment variables on Vercel, or local_config.py locally):
#    SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY, SUPABASE_SECRET_KEY,
#    SUPABASE_BUCKET (default product-images), SITE_URL
# =====================================================================
import os
import uuid
import requests
from flask import request
from db import setting

SUPABASE_URL = (setting("SUPABASE_URL", "") or "").rstrip("/")
PUBLISHABLE_KEY = setting("SUPABASE_PUBLISHABLE_KEY", "") or ""
SECRET_KEY = setting("SUPABASE_SECRET_KEY", "") or ""
BUCKET = setting("SUPABASE_BUCKET", "product-images") or "product-images"
SITE_URL = (setting("SITE_URL", "") or "").rstrip("/")

if SUPABASE_URL and PUBLISHABLE_KEY:
    MODE = "supabase"
elif os.environ.get("VERCEL"):
    MODE = "misconfigured"      # never fall back to unverified sign-ups on the live site
else:
    MODE = "local"

UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public", "uploads")


class AuthError(Exception):
    def __init__(self, message, code="", needs_confirmation=False):
        super().__init__(message)
        self.message = message
        self.code = code
        self.needs_confirmation = needs_confirmation


def site_url():
    """The address of this website (used in the confirmation email link)."""
    return SITE_URL or request.host_url.rstrip("/")


def _headers():
    # Publishable keys go in the "apikey" header only
    return {"apikey": PUBLISHABLE_KEY, "Content-Type": "application/json"}


def _read_error(response):
    try:
        data = response.json()
    except ValueError:
        data = {}
    text = data.get("msg") or data.get("error_description") or data.get("message") or "Authentication failed."
    code = data.get("error_code") or data.get("error") or ""
    return text, str(code)


def _post(path, **kwargs):
    try:
        return requests.post(SUPABASE_URL + path, headers=_headers(), timeout=15, **kwargs)
    except requests.RequestException:
        raise AuthError("Could not reach the sign-in service. Please try again in a moment.")


# ---------------------------------------------------------------------
#  Supabase: sign up, sign in, resend the confirmation email
# ---------------------------------------------------------------------
def sign_up(email, password, metadata, redirect_to):
    """Creates the Supabase user. Supabase emails a confirmation link."""
    r = _post("/auth/v1/signup", params={"redirect_to": redirect_to},
              json={"email": email, "password": password, "data": metadata})
    if r.status_code >= 400:
        text, code = _read_error(r)
        low = text.lower()
        if r.status_code == 429 or "rate_limit" in code:
            raise AuthError("Too many emails were sent just now. Please wait a few minutes and try again.", code)
        if "email_address_invalid" in code or "invalid" in low and "email" in low:
            raise AuthError("Please enter a valid email address.", code)
        if "weak_password" in code or "password" in low and "least" in low:
            raise AuthError("That password is too weak. Use at least 6 characters.", code)
        if "already" in low or code == "user_already_exists":
            return {"already_registered": True}
        raise AuthError(text, code)

    data = r.json()
    user = data.get("user", data)
    identities = user.get("identities")
    already = identities is not None and len(identities) == 0   # Supabase hides duplicates this way
    confirmed_now = bool(user.get("email_confirmed_at") or user.get("confirmed_at"))
    if confirmed_now and not already:
        # A brand-new user must NOT be confirmed yet. If they are, "Confirm email"
        # is switched off in Supabase and nobody's email is being verified.
        raise AuthError("Email confirmation is switched off in Supabase. "
                        "Switch on 'Confirm email' (Authentication > Sign In / Providers > Email) and try again.")
    return {"already_registered": already}


def sign_in(email, password):
    """Checks the password with Supabase. Only confirmed emails can sign in."""
    r = _post("/auth/v1/token", params={"grant_type": "password"},
              json={"email": email, "password": password})
    if r.status_code >= 400:
        text, code = _read_error(r)
        low = text.lower()
        if code == "email_not_confirmed" or "not confirmed" in low:
            raise AuthError("Please confirm your email first. Check your inbox for the confirmation link.",
                            code, needs_confirmation=True)
        if r.status_code == 429:
            raise AuthError("Too many attempts. Please wait a few minutes and try again.", code)
        if code in ("invalid_credentials", "invalid_grant") or "invalid login" in low:
            raise AuthError("Wrong email or password.", code)
        raise AuthError(text, code)
    user = r.json().get("user") or {}
    if not (user.get("email_confirmed_at") or user.get("confirmed_at")):
        raise AuthError("Please confirm your email first. Check your inbox for the confirmation link.",
                        needs_confirmation=True)
    return {"email": (user.get("email") or email).strip().lower(),
            "metadata": user.get("user_metadata") or {}}


def resend_confirmation(email, redirect_to):
    r = _post("/auth/v1/resend", params={"redirect_to": redirect_to},
              json={"type": "signup", "email": email})
    if r.status_code == 429:
        raise AuthError("Please wait a minute before asking for another email.")
    if r.status_code >= 400:
        text, code = _read_error(r)
        raise AuthError(text, code)


# ---------------------------------------------------------------------
#  Picture upload (sellers add product images)
#   supabase mode : stored in a public Supabase Storage bucket
#   local mode    : stored in public/uploads on your own computer
# ---------------------------------------------------------------------
ALLOWED = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
MAX_BYTES = 2 * 1024 * 1024


def _looks_like_image(data):
    return (data.startswith(b"\x89PNG") or data.startswith(b"\xff\xd8\xff")
            or (data[:4] == b"RIFF" and data[8:12] == b"WEBP"))


def save_image(file_storage, folder):
    """Returns the picture's address, or None if no file was chosen.
    Raises ValueError with a clear message if the file is not acceptable."""
    if not file_storage or not file_storage.filename:
        return None
    mime = file_storage.mimetype
    if mime not in ALLOWED:
        raise ValueError("Please upload a PNG, JPG or WEBP picture.")
    data = file_storage.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("The picture is too large (maximum 2 MB).")
    if not _looks_like_image(data):
        raise ValueError("That file is not a valid picture.")

    name = "%s/%s.%s" % (folder, uuid.uuid4().hex, ALLOWED[mime])

    if MODE == "supabase":
        if not SECRET_KEY:
            raise ValueError("Picture upload is not set up (SUPABASE_SECRET_KEY is missing).")
        try:
            r = requests.post("%s/storage/v1/object/%s/%s" % (SUPABASE_URL, BUCKET, name),
                              headers={"apikey": SECRET_KEY, "Content-Type": mime},
                              data=data, timeout=30)
        except requests.RequestException:
            raise ValueError("Picture upload failed. Please try again.")
        if r.status_code >= 400:
            raise ValueError("Picture upload failed. Check that the Storage bucket exists and is public.")
        return "%s/storage/v1/object/public/%s/%s" % (SUPABASE_URL, BUCKET, name)

    if MODE == "local":
        path = os.path.join(UPLOAD_FOLDER, *name.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return "/uploads/" + name

    raise ValueError("Picture upload is not available.")
