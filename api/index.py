"""Vercel entry point: Vercel runs this file as a serverless function and hands every
non-page request (/config, /orders, /admin/...) to the FastAPI app in backend/main.py."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from main import app  # noqa: E402,F401
