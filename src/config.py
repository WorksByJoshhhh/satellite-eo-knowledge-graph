"""Configuration read from environment variables / a local .env file (never commit .env)."""
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

DB_USER = os.getenv("EO_DB_USER")
DB_PASSWORD = os.getenv("EO_DB_PASSWORD")
DB_DSN = os.getenv("EO_DB_DSN", "localhost:1521/XEPDB1")
SECRET_KEY = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(16)
