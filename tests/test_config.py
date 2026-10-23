import pytest

from app import create_app
from app.config import database_url

from .conftest import TEST_CONFIG


@pytest.mark.parametrize(
    "given, expected",
    [
        ("postgres://u:p@host/db", "postgresql+psycopg://u:p@host/db"),
        ("postgresql://u:p@host/db?sslmode=require", "postgresql+psycopg://u:p@host/db?sslmode=require"),
        ("postgresql+psycopg://u:p@host/db", "postgresql+psycopg://u:p@host/db"),
        ("sqlite:///app.db", "sqlite:///app.db"),
    ],
)
def test_database_url_uses_psycopg3(monkeypatch, given, expected):
    monkeypatch.setenv("DATABASE_URL", given)
    assert database_url() == expected


def test_vercel_refuses_sqlite(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    overrides = {k: v for k, v in TEST_CONFIG.items() if k != "SQLALCHEMY_DATABASE_URI"}
    monkeypatch.setenv("DATABASE_URL", "sqlite:///app.db")
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        create_app(overrides)
