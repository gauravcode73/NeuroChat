"""
Configuration settings for the Offline AI Assistant.
Edit this file to customise model, memory limits, and server settings.
"""

import os
from dotenv import load_dotenv

# Load local .env file if present
load_dotenv()

# ──────────────────────────────────────────────
# Gemini API connection
# ──────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# Default model to use.
DEFAULT_MODEL = "gemini-2.5-flash"

# ──────────────────────────────────────────────
# Memory / context window
# ──────────────────────────────────────────────
# Maximum number of *user+assistant message pairs* to keep in context.
# Older pairs are automatically trimmed to stay within the model's window.
MAX_HISTORY_PAIRS = 20

# Rough token estimate per character (used for trimming heuristic).
CHARS_PER_TOKEN = 4

# Hard cap on estimated total prompt tokens before trimming kicks in.
MAX_PROMPT_TOKENS = 3500

# ──────────────────────────────────────────────
# System prompt
# ──────────────────────────────────────────────
SYSTEM_PROMPT = (
    "You are a highly capable AI assistant running locally on the user's machine. "
    "Your task is to provide accurate, clear, and helpful responses. "
    "You must avoid hallucinations, admit uncertainty when you are not sure, "
    "and provide step-by-step explanations for complex problems. "
    "Keep responses structured, logical, and useful. "
    "When writing code, always include comments and proper indentation. "
    "When explaining concepts, provide concrete examples. "
    "You are running entirely offline — do not reference live internet data."
)

# ──────────────────────────────────────────────
# Long-term memory (FAISS) — set to True to enable
# ──────────────────────────────────────────────
ENABLE_LONG_TERM_MEMORY = False          # Requires: faiss-cpu, sentence-transformers
LTM_EMBEDDING_MODEL = "all-MiniLM-L6-v2"
LTM_TOP_K = 3                            # Number of past memories to retrieve

# ──────────────────────────────────────────────
# Server & Authentication
# ──────────────────────────────────────────────
import os
import pyotp

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", 8000))

AUTH_SECRET = os.environ.get("AUTH_SECRET", "I5QXK4TBOZKXAYLENB4WC6I")
if not AUTH_SECRET:
    AUTH_SECRET = pyotp.random_base32()
    print("\n" + "="*60)
    print(" ! ACTION REQUIRED: NO AUTH_SECRET SET!")
    print(f" ! Your new Google Authenticator Secret is: {AUTH_SECRET}")
    print(" ! Add this code to your Google Authenticator App manually.")
    print(" ! Important: Set this as an Environment Variable in Render!")
    print("="*60 + "\n")

# ──────────────────────────────────────────────
# Conversations save directory (relative to project root)
# ──────────────────────────────────────────────
CONVERSATIONS_DIR = "conversations"
