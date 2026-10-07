import re
from pathlib import Path

from app.config import Settings

BLUEPRINT = (Path(__file__).resolve().parent.parent / "render.yaml").read_text()
# {"JWT_SECRET": ("generateValue", "true"), "PYTHON_VERSION": ("value", "3.13.15"), ...}
ENV_VARS = {key: (kind, value) for key, kind, value in re.findall(r"- key: (\w+)\n\s+(\w+): (.+)", BLUEPRINT)}
START_COMMAND = BLUEPRINT.split("startCommand: >-\n", 1)[1].split("healthCheckPath", 1)[0]


def test_every_setting_the_app_needs_is_generated_by_render():
    required = [name.upper() for name, field in Settings.model_fields.items() if field.is_required()]
    assert required
    for name in required:
        assert ENV_VARS.get(name) == ("generateValue", "true"), name


def test_only_the_moderator_password_is_typed_in():
    typed_in = {key for key, (kind, _) in ENV_VARS.items() if kind == "sync"}
    assert typed_in == {"MODERATOR_PASSWORD"}


def test_python_version_is_pinned():
    kind, version = ENV_VARS["PYTHON_VERSION"]
    assert kind == "value"
    assert re.fullmatch(r"3\.1[1-9]\.\d+", version)


def test_database_and_evidence_go_in_the_writable_project_folder():
    assert ENV_VARS["DATABASE_URL"] == ("value", "sqlite:////opt/render/project/src/whistledrop.db")
    assert ENV_VARS["EVIDENCE_DIR"] == ("value", "/opt/render/project/src/evidence")


def test_start_command_recreates_the_moderator_and_keeps_logs_quiet():
    assert "python -m app.cli create-moderator" in START_COMMAND
    for flag in ["--host 0.0.0.0", "--port $PORT", "--no-access-log", "--no-server-header"]:
        assert flag in START_COMMAND
