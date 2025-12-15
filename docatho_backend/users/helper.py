from docatho_backend.users.models import User


def generate_otp() -> str:
    # if settings.DEBUG:
    return "1234"
    # return f"{secrets.randbelow(10000):04d}"


