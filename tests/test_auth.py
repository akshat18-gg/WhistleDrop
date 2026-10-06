import re
import uuid
from datetime import timedelta

import jwt
import pytest

from app.config import get_settings
from app.main import app
from app.models import utcnow
from conftest import MOD_PASSWORD


def moderator_routes():
    """Every documented /api/moderator route, so new routes get checked automatically."""
    for path, operations in app.openapi()["paths"].items():
        if path.startswith("/api/moderator"):
            for method in operations:
                yield method.upper(), re.sub(r"\{\w+\}", lambda _: str(uuid.uuid4()), path)


def make_token(secret=None, **claims):
    return jwt.encode(claims, secret or get_settings().jwt_secret, algorithm="HS256")


def test_login_returns_a_token_for_the_moderator(client, moderator):
    response = client.post("/api/auth/login", json={"username": "mod", "password": MOD_PASSWORD})
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 3600
    claims = jwt.decode(body["access_token"], get_settings().jwt_secret, algorithms=["HS256"])
    assert claims["sub"] == str(moderator.id)
    assert claims["exp"] - claims["iat"] == 3600


def test_wrong_username_and_wrong_password_look_identical(client, moderator):
    wrong_user = client.post("/api/auth/login", json={"username": "nobody", "password": MOD_PASSWORD})
    wrong_pass = client.post("/api/auth/login", json={"username": "mod", "password": "not-the-password"})
    assert wrong_user.status_code == wrong_pass.status_code == 401
    assert wrong_user.content == wrong_pass.content
    strip_date = lambda headers: {k: v for k, v in headers.items() if k != "date"}  # noqa: E731
    assert strip_date(wrong_user.headers) == strip_date(wrong_pass.headers)
    assert wrong_user.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_deactivated_moderator_cannot_log_in(client, make_moderator):
    make_moderator(username="gone", is_active=False)
    response = client.post("/api/auth/login", json={"username": "gone", "password": MOD_PASSWORD})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_login_rejects_extra_fields(client, moderator):
    body = {"username": "mod", "password": MOD_PASSWORD, "role": "admin"}
    assert client.post("/api/auth/login", json=body).status_code == 422


def test_there_are_moderator_routes_to_check():
    assert len(list(moderator_routes())) >= 2


@pytest.mark.parametrize("method, path", list(moderator_routes()))
def test_every_moderator_route_needs_a_token(client, method, path):
    response = client.request(method, path)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "NOT_AUTHENTICATED"


@pytest.mark.parametrize("method, path", list(moderator_routes()))
def test_every_moderator_route_rejects_a_bad_token(client, method, path):
    response = client.request(method, path, headers={"Authorization": "Bearer not.a.token"})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("method, path", list(moderator_routes()))
def test_every_moderator_route_rejects_an_expired_token(client, moderator, method, path):
    token = make_token(sub=str(moderator.id), exp=utcnow() - timedelta(seconds=1))
    response = client.request(method, path, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_EXPIRED"


@pytest.mark.parametrize("method, path", list(moderator_routes()))
def test_every_moderator_route_rejects_a_deactivated_moderator(client, auth, moderator, session, method, path):
    moderator.is_active = False
    session.commit()
    response = client.request(method, path, headers=auth)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "header",
    [
        "Bearer",
        "Bearer ",
        "Basic bW9kOnBhc3N3b3Jk",
        "bearer-without-space",
        "Bearer " + make_token(secret="some-other-secret-that-is-long-enough", sub="1", exp=utcnow() + timedelta(minutes=5)),
        "Bearer " + make_token(sub="1"),
        "Bearer " + make_token(exp=utcnow() + timedelta(minutes=5)),
        "Bearer " + make_token(sub="not-a-number", exp=utcnow() + timedelta(minutes=5)),
        "Bearer " + jwt.encode({"sub": "1", "exp": utcnow() + timedelta(minutes=5)}, key=None, algorithm="none"),
    ],
    ids=["empty", "blank", "basic auth", "no space", "wrong secret", "no expiry", "no subject", "bad subject", "alg none"],
)
def test_malformed_and_forged_tokens_are_rejected(client, moderator, header):
    response = client.get("/api/moderator/reports", headers={"Authorization": header})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_expired_token_is_rejected(client, moderator):
    token = make_token(sub=str(moderator.id), exp=utcnow() - timedelta(seconds=1))
    response = client.get("/api/moderator/reports", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_EXPIRED"


def test_token_for_missing_moderator_is_rejected(client, moderator):
    token = make_token(sub="999", exp=utcnow() + timedelta(minutes=5))
    response = client.get("/api/moderator/reports", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_deactivating_a_moderator_kills_their_token(client, auth, moderator, session):
    assert client.get("/api/moderator/reports", headers=auth).status_code == 200
    moderator.is_active = False
    session.commit()
    response = client.get("/api/moderator/reports", headers=auth)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_valid_token_gets_in(client, auth):
    assert client.get("/api/moderator/reports", headers=auth).status_code == 200
