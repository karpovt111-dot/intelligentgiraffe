import base64, hashlib, hmac, os
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

SECRET_KEY = os.getenv("SECRET_KEY", "CHANGE_ME_IN_PRODUCTION_1234567890")
serializer = URLSafeTimedSerializer(SECRET_KEY, salt="giraffic-auth")
COOKIE_NAME = "giraffic_session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 7
PBKDF2_ITERATIONS = 260_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode(),
        base64.b64encode(dk).decode(),
    )


def verify_password(password: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        algo, iters, salt_b64, hash_b64 = hashed.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iters))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def create_token(user_id: int) -> str:
    return serializer.dumps({"uid": user_id})


def decode_token(token: str):
    try:
        return serializer.loads(token, max_age=COOKIE_MAX_AGE).get("uid")
    except (BadSignature, SignatureExpired):
        return None