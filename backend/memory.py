"""
Memory management for the Offline AI Assistant.

Short-term memory  : sliding-window list of {role, content} dicts.
Long-term memory   : optional FAISS vector store (enable in config.py).
"""

from __future__ import annotations

import json
import logging
from collections import deque
from pathlib import Path
from typing import List, Dict, Any

from config import (
    MAX_HISTORY_PAIRS,
    MAX_PROMPT_TOKENS,
    CHARS_PER_TOKEN,
    ENABLE_LONG_TERM_MEMORY,
    LTM_EMBEDDING_MODEL,
    LTM_TOP_K,
    SYSTEM_PROMPT,
)

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
# Short-term memory
# ──────────────────────────────────────────────────────────────

class ShortTermMemory:
    """Sliding-window conversation history (user / assistant pairs)."""

    def __init__(self) -> None:
        # Each element: {"role": "user"|"assistant", "content": str}
        self._messages: deque[Dict[str, str]] = deque()

    # ── public API ──────────────────────────────────────────

    def add(self, role: str, content: str) -> None:
        """Append a new message and trim if necessary."""
        self._messages.append({"role": role, "content": content})
        self._trim()

    def get_messages(self) -> List[Dict[str, str]]:
        """Return all messages in chronological order."""
        return list(self._messages)

    def clear(self) -> None:
        self._messages.clear()

    def to_serialisable(self) -> List[Dict[str, str]]:
        return list(self._messages)

    def load_from_serialisable(self, data: List[Dict[str, str]]) -> None:
        self._messages = deque(data)
        self._trim()

    # ── private helpers ─────────────────────────────────────

    def _trim(self) -> None:
        """
        Remove the oldest *pair* (user + assistant) when either
        the pair count or a rough token estimate exceeds the configured limits.
        """
        # Pair-count cap
        while len(self._messages) > MAX_HISTORY_PAIRS * 2:
            self._pop_oldest_pair()

        # Token-estimate cap
        while self._estimated_tokens() > MAX_PROMPT_TOKENS and len(self._messages) >= 2:
            self._pop_oldest_pair()

    def _pop_oldest_pair(self) -> None:
        """Remove the two oldest messages (one user turn + one assistant turn)."""
        if len(self._messages) >= 2:
            self._messages.popleft()
            self._messages.popleft()
        elif self._messages:
            self._messages.popleft()

    def _estimated_tokens(self) -> int:
        total_chars = sum(len(m["content"]) for m in self._messages)
        total_chars += len(SYSTEM_PROMPT)
        return total_chars // CHARS_PER_TOKEN

    def __len__(self) -> int:
        return len(self._messages)


# ──────────────────────────────────────────────────────────────
# Long-term memory (FAISS) — optional
# ──────────────────────────────────────────────────────────────

class LongTermMemory:
    """
    FAISS-backed vector store for long-term semantic memory.
    Each stored entry is a short summary of a conversation exchange.
    Only used when ENABLE_LONG_TERM_MEMORY is True in config.py.
    """

    def __init__(self) -> None:
        self._available = False
        if not ENABLE_LONG_TERM_MEMORY:
            return

        try:
            import faiss  # type: ignore
            from sentence_transformers import SentenceTransformer  # type: ignore

            self._faiss = faiss
            self._model = SentenceTransformer(LTM_EMBEDDING_MODEL)
            dim = self._model.get_sentence_embedding_dimension()
            self._index = faiss.IndexFlatL2(dim)
            self._texts: List[str] = []
            self._available = True
            logger.info("Long-term FAISS memory initialised (dim=%d).", dim)
        except ImportError:
            logger.warning(
                "faiss-cpu or sentence-transformers not installed. "
                "Long-term memory disabled. Run: pip install faiss-cpu sentence-transformers"
            )

    # ── public API ──────────────────────────────────────────

    @property
    def available(self) -> bool:
        return self._available

    def store(self, text: str) -> None:
        """Embed and store a piece of text."""
        if not self._available:
            return
        vec = self._model.encode([text], convert_to_numpy=True)
        self._index.add(vec)
        self._texts.append(text)

    def retrieve(self, query: str, top_k: int = LTM_TOP_K) -> List[str]:
        """Return the top-k most semantically similar stored texts."""
        if not self._available or self._index.ntotal == 0:
            return []
        vec = self._model.encode([query], convert_to_numpy=True)
        k = min(top_k, self._index.ntotal)
        _, indices = self._index.search(vec, k)
        return [self._texts[i] for i in indices[0] if i < len(self._texts)]

    def clear(self) -> None:
        if self._available:
            dim = self._index.d
            self._index = self._faiss.IndexFlatL2(dim)
            self._texts.clear()

    def __len__(self) -> int:
        return len(self._texts)


# ──────────────────────────────────────────────────────────────
# Unified memory facade
# ──────────────────────────────────────────────────────────────

class ConversationMemory:
    """
    Unified memory manager used by the chat engine.
    Combines short-term (always on) and long-term (optional) memory.
    """

    def __init__(self) -> None:
        self.short = ShortTermMemory()
        self.long = LongTermMemory()

    def add_user(self, content: str) -> None:
        self.short.add("user", content)

    def add_assistant(self, content: str) -> None:
        self.short.add("assistant", content)
        # Optionally summarise the last exchange and store in LTM
        if self.long.available and len(self.short) >= 2:
            msgs = self.short.get_messages()
            last_user = next(
                (m["content"] for m in reversed(msgs) if m["role"] == "user"), ""
            )
            summary = f"User asked: {last_user[:200]} | Assistant said: {content[:200]}"
            self.long.store(summary)

    def build_context(self, latest_query: str) -> List[Dict[str, str]]:
        """
        Build the full message list to send to Ollama:
          [system] + optional LTM context injection + short-term history
        """
        messages: List[Dict[str, str]] = []

        # Inject long-term memories as a system note (if available)
        if self.long.available:
            memories = self.long.retrieve(latest_query)
            if memories:
                ltm_text = "Relevant memories from earlier in this session:\n" + "\n".join(
                    f"- {m}" for m in memories
                )
                messages.append({"role": "system", "content": ltm_text})

        messages.extend(self.short.get_messages())
        return messages

    def clear(self) -> None:
        self.short.clear()
        self.long.clear()

    def to_dict(self) -> Dict[str, Any]:
        return {"messages": self.short.to_serialisable()}

    def load_dict(self, data: Dict[str, Any]) -> None:
        self.short.load_from_serialisable(data.get("messages", []))

    @property
    def message_count(self) -> int:
        return len(self.short)
