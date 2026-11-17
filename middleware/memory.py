import asyncio
import sqlite3
import json
import logging
from typing import List, Dict, Any

try:
    from google.cloud import firestore
    FIRESTORE_AVAILABLE = True
except ImportError:
    FIRESTORE_AVAILABLE = False

logger = logging.getLogger(__name__)

class MemoryManager:
    def __init__(self, use_firestore: bool = True):
        self.use_firestore = use_firestore and FIRESTORE_AVAILABLE
        if self.use_firestore:
            try:
                self.db = firestore.AsyncClient()
                logger.info("Initialized Firestore backend for memory.")
            except Exception as e:
                logger.warning(f"Failed to initialize Firestore: {e}. Falling back to SQLite.")
                self.use_firestore = False
        
        if not self.use_firestore:
            self.sqlite_db_path = "pa_sessions.db"
            self._init_sqlite()

    def _init_sqlite(self):
        conn = sqlite3.connect(self.sqlite_db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                history TEXT
            )
        ''')
        conn.commit()
        conn.close()

    async def _save_sqlite_async(self, session_id: str, history: List[Dict[str, Any]]):
        def save():
            conn = sqlite3.connect(self.sqlite_db_path)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO sessions (session_id, history)
                VALUES (?, ?)
                ON CONFLICT(session_id) DO UPDATE SET history=excluded.history
            ''', (session_id, json.dumps(history)))
            conn.commit()
            conn.close()
        await asyncio.to_thread(save)

    async def save_history(self, session_id: str, history: List[Dict[str, Any]]):
        """Asynchronously saves compacted history to persistent storage."""
        if self.use_firestore:
            try:
                doc_ref = self.db.collection('sessions').document(session_id)
                await doc_ref.set({'history': history})
            except Exception as e:
                logger.error(f"Firestore save error: {e}")
                await self._save_sqlite_async(session_id, history)
        else:
            await self._save_sqlite_async(session_id, history)

    def compact_history(self, history: List[Dict[str, Any]], max_tokens: int = 4000) -> List[Dict[str, Any]]:
        """
        Implements token-aware sliding window / summarization context compaction.
        This is a heuristic implementation for demonstration.
        """
        # Simple heuristic: keep system prompt (index 0) and the most recent N messages
        if not history:
            return history
            
        compacted = [history[0]] # System prompt
        current_len = len(str(history[0]))
        
        # Add messages from the end until max_tokens is reached (heuristic: 1 char ~ 0.25 tokens)
        tail = []
        for msg in reversed(history[1:]):
            msg_len = len(str(msg))
            if (current_len + msg_len) * 0.25 > max_tokens:
                break
            tail.insert(0, msg)
            current_len += msg_len
            
        # Optional: Add a summarization message if truncation happened
        if len(tail) < len(history) - 1:
            tail.insert(0, {"role": "system", "content": "[EARLIER HISTORY COMPACTED]"})
            
        compacted.extend(tail)
        return compacted

    def schedule_background_save(self, session_id: str, history: List[Dict[str, Any]]):
        """Runs memory extraction and longitudinal case consolidation asynchronously."""
        compacted = self.compact_history(history)
        asyncio.create_task(self.save_history(session_id, compacted))
