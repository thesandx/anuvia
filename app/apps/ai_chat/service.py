from sqlalchemy.ext.asyncio import AsyncSession

from app.apps.ai_chat.models import ChatMessage, ChatSession


class AIChatService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_or_create_session(self, user_id: int, session_id: int | None) -> ChatSession:
        if session_id:
            result = await self.db.get(ChatSession, session_id)
            if result and result.user_id == user_id:
                return result

        session = ChatSession(user_id=user_id, title="New chat")
        self.db.add(session)
        await self.db.commit()
        await self.db.refresh(session)
        return session

    async def chat(self, user_id: int, message: str, session_id: int | None) -> dict:
        session = await self.get_or_create_session(user_id, session_id)

        user_msg = ChatMessage(session_id=session.id, role="user", content=message)
        self.db.add(user_msg)

        # Plug in Anthropic / OpenAI here
        reply = f"Echo: {message}"

        ai_msg = ChatMessage(session_id=session.id, role="assistant", content=reply)
        self.db.add(ai_msg)

        await self.db.commit()
        return {"session_id": session.id, "reply": reply}
