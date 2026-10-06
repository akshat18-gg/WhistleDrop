import json
from pathlib import Path

from app.main import app

SAVED_SPEC = Path(__file__).resolve().parent.parent / "docs" / "openapi.json"


def test_saved_openapi_spec_matches_the_code():
    assert json.loads(SAVED_SPEC.read_text()) == app.openapi(), (
        "docs/openapi.json is out of date. Regenerate it with the command in the README."
    )


def test_swagger_ui_is_served(client):
    response = client.get("/docs")
    assert response.status_code == 200
    assert "swagger" in response.text.lower()


def test_errors_are_documented_with_our_shape():
    spec = app.openapi()
    assert "HTTPValidationError" not in spec["components"]["schemas"]
    for path, operations in spec["paths"].items():
        for operation in operations.values():
            for status, response in operation["responses"].items():
                if status.startswith(("4", "5")):
                    schema = response["content"]["application/json"]["schema"]
                    assert schema == {"$ref": "#/components/schemas/ErrorOut"}, (path, status)


def test_moderator_routes_show_the_authorize_lock():
    for path, operations in app.openapi()["paths"].items():
        for operation in operations.values():
            assert bool(operation.get("security")) == path.startswith("/api/moderator"), path
