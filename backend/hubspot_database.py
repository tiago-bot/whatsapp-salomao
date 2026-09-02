"""
Módulo de banco de dados para sessões do HubSpot.
Gerencia a persistência de sessões e mapeamento thread->session.
"""

from datetime import datetime
from typing import Optional, Dict, List
from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_KEY


class HubSpotDatabase:
    """
    Gerencia sessões do HubSpot no Supabase.
    Mapeia threads do HubSpot para sessions do Salomão.
    """

    def __init__(self):
        self.client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

    def get_or_create_session(
        self,
        thread_id: str,
        ticket_id: Optional[str] = None,
        visitor_actor_id: Optional[str] = None,
        channel_id: Optional[str] = None,
        channel_account_id: Optional[str] = None,
        visitor_name: Optional[str] = None,
        visitor_email: Optional[str] = None
    ) -> dict:
        """
        Obtém ou cria uma sessão para um thread do HubSpot.

        Args:
            thread_id: ID do thread do HubSpot
            ticket_id: ID do ticket associado
            visitor_actor_id: ID do visitante
            channel_id: ID do canal
            channel_account_id: ID da conta do canal
            visitor_name: Nome do visitante
            visitor_email: Email do visitante

        Returns:
            Dados da sessão
        """
        result = self.client.table("hubspot_sessions")\
            .select("*")\
            .eq("thread_id", thread_id)\
            .execute()

        if result.data:
            session = result.data[0]

            update_data = {"updated_at": datetime.utcnow().isoformat()}
            if ticket_id and not session.get("ticket_id"):
                update_data["ticket_id"] = ticket_id
            if visitor_actor_id and not session.get("visitor_actor_id"):
                update_data["visitor_actor_id"] = visitor_actor_id
            if channel_id and not session.get("channel_id"):
                update_data["channel_id"] = channel_id
            if channel_account_id and not session.get("channel_account_id"):
                update_data["channel_account_id"] = channel_account_id
            if visitor_name and not session.get("visitor_name"):
                update_data["visitor_name"] = visitor_name
            if visitor_email and not session.get("visitor_email"):
                update_data["visitor_email"] = visitor_email

            if len(update_data) > 1:
                self.client.table("hubspot_sessions")\
                    .update(update_data)\
                    .eq("thread_id", thread_id)\
                    .execute()

            return session

        session_id = f"hubspot_thread_{thread_id}"

        new_session = {
            "thread_id": thread_id,
            "ticket_id": ticket_id,
            "session_id": session_id,
            "visitor_actor_id": visitor_actor_id,
            "channel_id": channel_id,
            "channel_account_id": channel_account_id,
            "visitor_name": visitor_name,
            "visitor_email": visitor_email,
            "status": "active",
            "message_count": 0
        }

        result = self.client.table("hubspot_sessions").insert(new_session).execute()
        return result.data[0] if result.data else new_session

    def get_session_by_thread(self, thread_id: str) -> Optional[dict]:
        """Obtém sessão pelo thread_id."""
        result = self.client.table("hubspot_sessions")\
            .select("*")\
            .eq("thread_id", thread_id)\
            .execute()

        return result.data[0] if result.data else None

    def get_session_by_ticket(self, ticket_id: str) -> Optional[dict]:
        """Obtém sessão pelo ticket_id."""
        result = self.client.table("hubspot_sessions")\
            .select("*")\
            .eq("ticket_id", ticket_id)\
            .execute()

        return result.data[0] if result.data else None

    def get_salomao_session_id(self, thread_id: str) -> str:
        """Retorna o session_id do Salomão para um thread."""
        session = self.get_session_by_thread(thread_id)
        if session:
            return session.get("session_id", f"hubspot_thread_{thread_id}")
        return f"hubspot_thread_{thread_id}"

    def update_last_message(self, thread_id: str):
        """Atualiza timestamp da última mensagem."""
        self.client.table("hubspot_sessions")\
            .update({
                "last_message_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat()
            })\
            .eq("thread_id", thread_id)\
            .execute()

    def increment_message_count(self, thread_id: str):
        """Incrementa o contador de mensagens."""
        session = self.get_session_by_thread(thread_id)
        if session:
            current_count = session.get("message_count", 0) or 0
            self.client.table("hubspot_sessions")\
                .update({
                    "message_count": current_count + 1,
                    "last_message_at": datetime.utcnow().isoformat(),
                    "updated_at": datetime.utcnow().isoformat()
                })\
                .eq("thread_id", thread_id)\
                .execute()

    def update_session_status(self, thread_id: str, status: str):
        """Atualiza o status da sessão."""
        self.client.table("hubspot_sessions")\
            .update({
                "status": status,
                "updated_at": datetime.utcnow().isoformat()
            })\
            .eq("thread_id", thread_id)\
            .execute()

    def get_active_sessions(self, limit: int = 100) -> List[dict]:
        """Retorna sessões ativas."""
        result = self.client.table("hubspot_sessions")\
            .select("*")\
            .eq("status", "active")\
            .order("last_message_at", desc=True)\
            .limit(limit)\
            .execute()

        return result.data if result.data else []

    def close_session(self, thread_id: str):
        """Fecha uma sessão."""
        self.update_session_status(thread_id, "closed")

    def get_session_stats(self) -> dict:
        """Retorna estatísticas das sessões."""
        active = self.client.table("hubspot_sessions")\
            .select("id", count="exact")\
            .eq("status", "active")\
            .execute()

        closed = self.client.table("hubspot_sessions")\
            .select("id", count="exact")\
            .eq("status", "closed")\
            .execute()

        total_messages = self.client.table("hubspot_sessions")\
            .select("message_count")\
            .execute()

        total_msg_count = sum(s.get("message_count", 0) or 0 for s in (total_messages.data or []))

        return {
            "active_sessions": active.count if active.count else 0,
            "closed_sessions": closed.count if closed.count else 0,
            "total_messages": total_msg_count
        }


hubspot_db = HubSpotDatabase()
