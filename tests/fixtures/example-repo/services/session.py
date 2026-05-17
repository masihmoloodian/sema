from dataclasses import dataclass
import uuid


@dataclass
class Session:
    id: str
    user_id: str
    active: bool = True


class SessionService:
    """Manages user sessions in memory."""

    def __init__(self):
        self._sessions: dict[str, Session] = {}

    def create(self, user_id: str) -> Session:
        """Create a new session for a user."""
        session_id = str(uuid.uuid4())
        session = Session(id=session_id, user_id=user_id)
        self._sessions[session_id] = session
        return session

    def invalidate(self, session_id: str) -> bool:
        """Invalidate an existing session. Returns True if found."""
        session = self._sessions.get(session_id)
        if session:
            session.active = False
            return True
        return False

    def get(self, session_id: str) -> Session | None:
        """Get a session by ID, or None if not found."""
        return self._sessions.get(session_id)
