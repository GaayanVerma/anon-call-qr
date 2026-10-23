# Vercel entry point: every path is rewritten here (see vercel.json) and served by Flask.
from app import create_app

app = create_app()
