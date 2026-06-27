"""
Core chat engine for the Offline AI Assistant.

Responsibilities:
  - Maintain conversation memory (delegates to memory.py)
  - Format prompts before sending to Gemini API
  - Stream tokens from Gemini API back to the caller
  - Check API availability and list supported models
"""

from __future__ import annotations

import json
import logging
from typing import AsyncIterator, List, Dict

import httpx

from config import GEMINI_API_KEY, DEFAULT_MODEL, SYSTEM_PROMPT
from memory import ConversationMemory

logger = logging.getLogger(__name__)


class ChatEngine:
    """
    Manages one conversation session.
    Each browser tab / session gets its own ChatEngine instance.
    """

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        self.model = model
        self.memory = ConversationMemory()

    # ──────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────

    async def stream_response(self, user_message: str) -> AsyncIterator[str]:
        """
        Store the user message, call Gemini with streaming enabled,
        yield each token chunk, and finally store the full assistant reply.
        """
        self.memory.add_user(user_message)
        contents = self._build_messages(user_message)

        payload = {
            "contents": contents,
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "generationConfig": {
                "temperature": 0.7,
                "topP": 0.9,
            },
        }

        gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:streamGenerateContent?alt=sse&key={GEMINI_API_KEY}"

        full_reply = ""
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("POST", gemini_url, json=payload) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                            
                        data_str = line[6:].strip()
                        if not data_str or data_str == "[DONE]":
                            continue
                            
                        try:
                            chunk = json.loads(data_str)
                            if "candidates" in chunk and chunk["candidates"]:
                                candidate = chunk["candidates"][0]
                                parts = candidate.get("content", {}).get("parts", [])
                                for part in parts:
                                    text = part.get("text", "")
                                    if text:
                                        full_reply += text
                                        yield text
                        except json.JSONDecodeError:
                            continue
        except httpx.ConnectError:
            error_msg = (
                "⚠️ **Cannot connect to Gemini API.**\n\n"
                "Please check your internet connection."
            )
            yield error_msg
            full_reply = error_msg
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 400:
                error_msg = f"⚠️ **Bad Request:** {exc.response.text}"
            elif exc.response.status_code == 401 or exc.response.status_code == 403:
                error_msg = "⚠️ **API Key Error:** The provided Gemini API Key is invalid or lacks permissions."
            elif exc.response.status_code == 404:
                error_msg = f"⚠️ **Model `{self.model}` not found.**"
            else:
                error_msg = f"⚠️ **API error:** {exc.response.status_code} — {exc.response.text}"
            yield error_msg
            full_reply = error_msg
        except Exception as exc:
            error_msg = f"⚠️ **Unexpected error:** {exc}"
            logger.exception("Unexpected streaming error")
            yield error_msg
            full_reply = error_msg
        finally:
            if full_reply:
                self.memory.add_assistant(full_reply)

    # ──────────────────────────────────────────────────────
    # Utility
    # ──────────────────────────────────────────────────────

    def _build_messages(self, latest_query: str) -> List[Dict]:
        """Convert standard memory history into Gemini's payload format."""
        # memory.py yields {"role": "system"|"user"|"assistant", "content": "..."}
        raw_history = self.memory.build_context(latest_query)
        
        contents = []
        for msg in raw_history:
            role = msg["role"]
            content = msg["content"]
            
            # Gemini doesn't take system messages in the contents array.
            # However, memory.py might inject LTM context as a 'system' message.
            if role == "system":
                # We can append this to the next user message, or treat it as a user message
                contents.append({"role": "user", "parts": [{"text": f"[System Note]: {content}"}]})
                # Add a dummy model response so the alternating pattern is maintained
                contents.append({"role": "model", "parts": [{"text": "Acknowledged."}]})
            else:
                gemini_role = "model" if role == "assistant" else "user"
                contents.append({"role": gemini_role, "parts": [{"text": content}]})
                
        return contents

    # ── static helpers ──────────────────────────────────────

    @staticmethod
    async def list_models() -> List[str]:
        """Return names of available Gemini models."""
        return [
            "gemini-2.5-flash",
            "gemini-2.5-pro",
            "gemini-2.0-flash"
        ]

    @staticmethod
    async def is_api_reachable() -> bool:
        """Check if we can reach Google APIs."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get("https://generativelanguage.googleapis.com/")
                # 404 means the base URL is reachable but no route at root, which is fine.
                return resp.status_code in [200, 404]
        except Exception:
            return False

