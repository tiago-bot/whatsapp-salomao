from collections import Counter, defaultdict
from datetime import datetime, timedelta
import json
from typing import Optional
from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_KEY


class ConversationDatabase:
    """
    Gerencia o histórico de conversas e memórias no Supabase.
    """

    def __init__(self):
        self.client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

    def get_or_create_session(self, session_id: str, user_identifier: Optional[str] = None) -> dict:
        """Obtém ou cria uma sessão de conversa."""
        result = self.client.table("salomao_sessions").select("*").eq("session_id", session_id).execute()

        if result.data:
            return result.data[0]

        new_session = {
            "session_id": session_id,
            "user_identifier": user_identifier,
            "message_count": 0,
            "topics_discussed": []
        }

        result = self.client.table("salomao_sessions").insert(new_session).execute()
        return result.data[0] if result.data else new_session

    def update_session_activity(self, session_id: str, topics: list[str] = None):
        """Atualiza a última atividade da sessão."""
        update_data = {
            "last_activity_at": datetime.utcnow().isoformat()
        }

        if topics:
            result = self.client.table("salomao_sessions").select("topics_discussed").eq("session_id", session_id).execute()
            if result.data:
                existing_topics = result.data[0].get("topics_discussed") or []
                new_topics = list(set(existing_topics + topics))
                update_data["topics_discussed"] = new_topics

        self.client.table("salomao_sessions").update(update_data).eq("session_id", session_id).execute()

    def increment_message_count(self, session_id: str):
        """Incrementa o contador de mensagens."""
        result = self.client.table("salomao_sessions").select("message_count").eq("session_id", session_id).execute()
        if result.data:
            current_count = result.data[0].get("message_count", 0) or 0
            self.client.table("salomao_sessions").update({
                "message_count": current_count + 1
            }).eq("session_id", session_id).execute()

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        has_image: bool = False,
        has_audio: bool = False,
        audio_transcription: Optional[str] = None,
        model_used: Optional[str] = None,
        transfer_requested: bool = False
    ) -> dict:
        """Adiciona uma mensagem ao histórico."""
        message = {
            "session_id": session_id,
            "role": role,
            "content": content,
            "has_image": has_image,
            "has_audio": has_audio,
            "audio_transcription": audio_transcription,
            "model_used": model_used,
            "transfer_requested": transfer_requested
        }

        result = self.client.table("salomao_messages").insert(message).execute()
        self.increment_message_count(session_id)

        return result.data[0] if result.data else message

    def get_conversation_history(self, session_id: str, limit: int = 20) -> list[dict]:
        """Obtém as mensagens mais recentes, devolvidas em ordem cronológica."""
        result = self.client.table("salomao_messages")\
            .select("*")\
            .eq("session_id", session_id)\
            .order("created_at", desc=True)\
            .limit(limit)\
            .execute()

        return list(reversed(result.data)) if result.data else []

    def get_message_count(self, session_id: str) -> int:
        """Retorna o número de mensagens na sessão."""
        result = self.client.table("salomao_sessions").select("message_count").eq("session_id", session_id).execute()
        if result.data:
            return result.data[0].get("message_count", 0) or 0
        return 0

    def get_session_info(self, session_id: str) -> dict:
        """
        Obtém informações da sessão incluindo tópicos discutidos e última atividade.
        Útil para detectar continuação de conversa.
        """
        result = self.client.table("salomao_sessions")\
            .select("*")\
            .eq("session_id", session_id)\
            .execute()

        if result.data:
            return result.data[0]
        return None

    def get_last_conversation_summary(self, session_id: str) -> dict:
        """
        Retorna um resumo da última conversa para continuidade.
        Inclui tópicos discutidos e tempo desde última atividade.
        """
        session = self.get_session_info(session_id)
        if not session:
            return None

        topics = session.get("topics_discussed", [])
        last_activity = session.get("last_activity_at")
        message_count = session.get("message_count", 0)

        # Buscar últimas mensagens para contexto
        history = self.get_conversation_history(session_id, limit=5)

        last_topic = None
        if history:
            # Pegar a última mensagem do usuário
            user_messages = [m for m in history if m.get("role") == "user"]
            if user_messages:
                last_topic = user_messages[-1].get("content", "")[:100]

        return {
            "topics": topics,
            "last_activity": last_activity,
            "message_count": message_count,
            "last_user_message": last_topic,
            "has_previous_conversation": message_count > 0
        }

    def add_memory(self, session_id: str, memory_type: str, memory_content: str):
        """Adiciona uma memória sobre o usuário."""
        memory = {
            "session_id": session_id,
            "memory_type": memory_type,
            "memory_content": memory_content
        }

        self.client.table("salomao_memories").insert(memory).execute()

    def get_memories(self, session_id: str) -> list[dict]:
        """Obtém as memórias de uma sessão."""
        result = self.client.table("salomao_memories")\
            .select("*")\
            .eq("session_id", session_id)\
            .execute()

        return result.data if result.data else []

    def clear_session(self, session_id: str):
        """Limpa uma sessão e suas mensagens."""
        self.client.table("salomao_messages").delete().eq("session_id", session_id).execute()
        self.client.table("salomao_memories").delete().eq("session_id", session_id).execute()
        self.client.table("salomao_sessions").delete().eq("session_id", session_id).execute()

    def rate_message(self, message_id: str, session_id: str, rating: str) -> dict:
        """Adiciona ou atualiza avaliação de uma mensagem (like/dislike)."""
        existing = self.client.table("salomao_message_ratings")\
            .select("*")\
            .eq("message_id", message_id)\
            .execute()

        if existing.data:
            result = self.client.table("salomao_message_ratings")\
                .update({"rating": rating})\
                .eq("message_id", message_id)\
                .execute()
        else:
            result = self.client.table("salomao_message_ratings").insert({
                "message_id": message_id,
                "session_id": session_id,
                "rating": rating
            }).execute()

        return result.data[0] if result.data else {"message_id": message_id, "rating": rating}

    def get_message_rating(self, message_id: str) -> Optional[str]:
        """Obtém a avaliação de uma mensagem."""
        result = self.client.table("salomao_message_ratings")\
            .select("rating")\
            .eq("message_id", message_id)\
            .execute()

        if result.data:
            return result.data[0].get("rating")
        return None

    def submit_session_feedback(
        self,
        session_id: str,
        rating: int,
        comment: Optional[str] = None,
        transfer_requested: bool = True
    ) -> dict:
        """Envia avaliação final do atendimento."""
        existing = self.client.table("salomao_session_feedback")\
            .select("*")\
            .eq("session_id", session_id)\
            .execute()

        feedback_data = {
            "session_id": session_id,
            "rating": rating,
            "comment": comment,
            "transfer_requested": transfer_requested
        }

        if existing.data:
            result = self.client.table("salomao_session_feedback")\
                .update(feedback_data)\
                .eq("session_id", session_id)\
                .execute()
        else:
            result = self.client.table("salomao_session_feedback")\
                .insert(feedback_data)\
                .execute()

        return result.data[0] if result.data else feedback_data

    def get_session_feedback(self, session_id: str) -> Optional[dict]:
        """Obtém a avaliação final de uma sessão."""
        result = self.client.table("salomao_session_feedback")\
            .select("*")\
            .eq("session_id", session_id)\
            .execute()

        return result.data[0] if result.data else None

    def get_message_rating_stats(self, days: int = 30) -> dict:
        since = self._since_iso(days)
        result = self.client.table("salomao_message_ratings")\
            .select("*")\
            .gte("created_at", since)\
            .execute()
        ratings = result.data or []
        liked = sum(1 for item in ratings if item.get("rating") == "like")
        disliked = sum(1 for item in ratings if item.get("rating") == "dislike")
        total = liked + disliked
        return {
            "total": total,
            "like_count": liked,
            "dislike_count": disliked,
            "satisfaction_rate": round((liked / total) * 100, 2) if total else 0,
            "rating_breakdown": self._counter_to_rows(Counter(item.get("rating") or "unknown" for item in ratings)),
        }

    def get_message_ratings(self, days: int = 30, limit: int = 50) -> list[dict]:
        since = self._since_iso(days)
        result = self.client.table("salomao_message_ratings")\
            .select("*")\
            .gte("created_at", since)\
            .order("created_at", desc=True)\
            .limit(limit)\
            .execute()
        ratings = result.data or []
        if not ratings:
            return []

        message_ids = [item.get("message_id") for item in ratings if item.get("message_id")]
        messages_by_id: dict[str, dict] = {}
        if message_ids:
            messages_result = self.client.table("salomao_messages")\
                .select("id, session_id, role, content, created_at")\
                .in_("id", message_ids)\
                .execute()
            messages_by_id = {str(item.get("id")): item for item in messages_result.data or []}

        rows = []
        for rating in ratings:
            message = messages_by_id.get(str(rating.get("message_id"))) or {}
            user_message = ""
            if message.get("session_id") and message.get("created_at"):
                previous = self.client.table("salomao_messages")\
                    .select("content, created_at")\
                    .eq("session_id", message.get("session_id"))\
                    .eq("role", "user")\
                    .lte("created_at", message.get("created_at"))\
                    .order("created_at", desc=True)\
                    .limit(1)\
                    .execute()
                if previous.data:
                    user_message = previous.data[0].get("content") or ""

            rows.append({
                "id": rating.get("id"),
                "message_id": rating.get("message_id"),
                "session_id": rating.get("session_id"),
                "rating": rating.get("rating"),
                "user_message": user_message,
                "assistant_message": message.get("content") or "",
                "created_at": rating.get("created_at"),
                "updated_at": rating.get("updated_at") or rating.get("created_at"),
            })
        return rows

    def add_conversation_turn(
        self,
        session_id: str,
        user_message: str,
        assistant_message: str,
        route: Optional[str] = None,
        priority: Optional[str] = None,
        tags: Optional[list[str]] = None,
        model_used: Optional[str] = None,
        latency_ms: int = 0,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        out_of_scope: bool = False,
        requires_handoff: bool = False,
        has_image: bool = False,
        has_audio: bool = False,
        message_id: Optional[str] = None,
        answer_status: str = "answered",
        source_count: int = 0,
    ) -> dict:
        """Registra metricas internas de um turno da conversa."""
        payload = {
            "session_id": session_id,
            "message_id": message_id,
            "answer_status": answer_status,
            "source_count": source_count,
            "user_message": user_message,
            "assistant_message": assistant_message,
            "route": route,
            "priority": priority,
            "tags": tags or [],
            "model_used": model_used,
            "latency_ms": max(0, int(latency_ms or 0)),
            "prompt_tokens": max(0, int(prompt_tokens or 0)),
            "completion_tokens": max(0, int(completion_tokens or 0)),
            "total_tokens": max(0, int(total_tokens or 0)),
            "out_of_scope": out_of_scope,
            "requires_handoff": requires_handoff,
            "has_image": has_image,
            "has_audio": has_audio,
        }
        payload["created_at"] = datetime.utcnow().isoformat()
        result = self.client.table("salomao_memories").insert({
            "session_id": session_id,
            "memory_type": "analytics_turn",
            "memory_content": self._json_dumps(payload),
        }).execute()
        return result.data[0] if result.data else payload

    def upsert_conversation_summary(self, summary: dict) -> dict:
        """Cria ou atualiza o resumo interno de uma sessao."""
        summary["updated_at"] = datetime.utcnow().isoformat()
        existing = self.client.table("salomao_memories")\
            .select("*")\
            .eq("session_id", summary["session_id"])\
            .eq("memory_type", "conversation_summary")\
            .execute()
        payload = {"memory_content": self._json_dumps(summary)}
        if existing.data:
            result = self.client.table("salomao_memories")\
                .update(payload)\
                .eq("id", existing.data[0]["id"])\
                .execute()
        else:
            result = self.client.table("salomao_memories").insert({
                "session_id": summary["session_id"],
                "memory_type": "conversation_summary",
                **payload,
            }).execute()
        return self._memory_to_payload(result.data[0]) if result.data else summary

    def get_conversation_turns(self, session_id: str, limit: int = 50) -> list[dict]:
        result = self.client.table("salomao_memories")\
            .select("*")\
            .eq("session_id", session_id)\
            .eq("memory_type", "analytics_turn")\
            .order("created_at", desc=True)\
            .limit(limit)\
            .execute()
        return [self._memory_to_payload(item) for item in reversed(result.data)] if result.data else []

    def get_conversation_summary(self, session_id: str) -> Optional[dict]:
        result = self.client.table("salomao_memories")\
            .select("*")\
            .eq("session_id", session_id)\
            .eq("memory_type", "conversation_summary")\
            .order("created_at", desc=True)\
            .limit(1)\
            .execute()
        return self._memory_to_payload(result.data[0]) if result.data else None

    def get_conversation_previews(self, session_ids: list[str], limit: int = 30) -> list[dict]:
        cleaned_ids = []
        for session_id in session_ids:
            if session_id and session_id not in cleaned_ids:
                cleaned_ids.append(session_id)
            if len(cleaned_ids) >= limit:
                break
        if not cleaned_ids:
            return []

        sessions_result = self.client.table("salomao_sessions")\
            .select("session_id,message_count,last_activity_at")\
            .in_("session_id", cleaned_ids)\
            .execute()
        sessions = sessions_result.data or []
        if not sessions:
            return []

        summary_result = self.client.table("salomao_memories")\
            .select("*")\
            .eq("memory_type", "conversation_summary")\
            .in_("session_id", cleaned_ids)\
            .execute()
        summaries = {
            item.get("session_id"): self._memory_to_payload(item)
            for item in (summary_result.data or [])
        }

        user_messages_result = self.client.table("salomao_messages")\
            .select("session_id,content,created_at")\
            .in_("session_id", cleaned_ids)\
            .eq("role", "user")\
            .order("created_at", desc=False)\
            .execute()
        first_user_messages: dict[str, str] = {}
        for message in user_messages_result.data or []:
            session_id = message.get("session_id")
            if session_id and session_id not in first_user_messages:
                first_user_messages[session_id] = message.get("content") or ""

        rows = []
        for session in sessions:
            session_id = session.get("session_id")
            summary = summaries.get(session_id) or {}
            first_user = first_user_messages.get(session_id, "")
            title_source = first_user or summary.get("module") or "Nova conversa"
            problem = summary.get("problem") or first_user
            summary_text = summary.get("summary") or problem or "Conversa salva no Salomao."
            rows.append({
                "session_id": session_id,
                "title": self._compact_text(title_source, 48),
                "summary": self._compact_text(summary_text, 180),
                "problem": problem,
                "message_count": session.get("message_count") or summary.get("message_count") or 0,
                "module": summary.get("module"),
                "topic": summary.get("topic"),
                "created_at": summary.get("first_message_at") or session.get("last_activity_at"),
                "updated_at": summary.get("updated_at") or session.get("last_activity_at"),
            })

        order = {session_id: index for index, session_id in enumerate(cleaned_ids)}
        return sorted(rows, key=lambda item: order.get(item.get("session_id"), 9999))

    def get_analytics_overview(self, days: int = 7) -> dict:
        turns = self._get_recent_turns(days=days, limit=2000)
        summaries = self._get_recent_summaries(days=days, limit=1000)
        stored_sessions = self._get_recent_sessions(days=days, limit=2000)
        total_turns = len(turns)
        session_ids = {turn.get("session_id") for turn in turns if turn.get("session_id")}
        latencies = [turn.get("latency_ms") or 0 for turn in turns]
        out_of_scope = sum(1 for turn in turns if turn.get("out_of_scope"))
        handoffs = sum(1 for turn in turns if turn.get("requires_handoff"))
        needs_review = sum(1 for item in summaries if item.get("needs_review"))

        route_counts = Counter(turn.get("route") or "nao_classificado" for turn in turns)
        priority_counts = Counter(turn.get("priority") or "normal" for turn in turns)
        tag_counts = Counter(tag for turn in turns for tag in (turn.get("tags") or []))
        module_counts = Counter(item.get("module") or "Geral" for item in summaries)
        resolution_counts = Counter(item.get("resolution_status") or "desconhecido" for item in summaries)
        latency_counts = Counter(self._latency_bucket_name(latency) for latency in latencies)
        daily = self._build_daily_trend(turns)
        message_counts = [session.get("message_count") or 0 for session in stored_sessions]

        return {
            "period_days": days,
            "total_turns": total_turns,
            "total_sessions": len(stored_sessions) or len(session_ids),
            "stored_conversations_count": len(stored_sessions),
            "summarized_conversations_count": len(summaries),
            "avg_messages_per_conversation": round(sum(message_counts) / len(message_counts), 2) if message_counts else 0,
            "avg_latency_ms": round(sum(latencies) / total_turns, 2) if total_turns else 0,
            "p95_latency_ms": self._percentile(latencies, 95),
            "max_latency_ms": max(latencies) if latencies else 0,
            "out_of_scope_count": out_of_scope,
            "in_scope_count": max(total_turns - out_of_scope, 0),
            "handoff_count": handoffs,
            "needs_review_count": needs_review,
            "route_breakdown": self._counter_to_rows(route_counts),
            "priority_breakdown": self._counter_to_rows(priority_counts),
            "tag_breakdown": self._counter_to_rows(tag_counts, limit=15),
            "module_breakdown": self._counter_to_rows(module_counts),
            "resolution_breakdown": self._counter_to_rows(resolution_counts),
            "latency_buckets": self._counter_to_rows(latency_counts),
            "daily_trend": daily,
            "message_feedback": self.get_message_rating_stats(days=days),
        }

    def get_topic_breakdown(self, days: int = 30, limit: int = 20) -> list[dict]:
        summaries = self._get_recent_summaries(days=days, limit=2000)
        counts = Counter((item.get("topic") or item.get("module") or "Geral") for item in summaries)
        return self._counter_to_rows(counts, limit=limit)

    def get_analytics_sessions(self, days: int = 30, limit: int = 50) -> list[dict]:
        since = self._since_iso(days)
        result = self.client.table("salomao_memories")\
            .select("*")\
            .eq("memory_type", "conversation_summary")\
            .gte("created_at", since)\
            .order("created_at", desc=True)\
            .limit(limit)\
            .execute()
        return [self._memory_to_payload(item) for item in result.data] if result.data else []

    def get_out_of_scope_turns(self, limit: int = 50) -> list[dict]:
        turns = self._get_recent_turns(days=365, limit=1000)
        filtered = [item for item in turns if item.get("out_of_scope")]
        return filtered[:limit]

    def _get_recent_turns(self, days: int, limit: int) -> list[dict]:
        since = self._since_iso(days)
        result = self.client.table("salomao_memories")\
            .select("*")\
            .eq("memory_type", "analytics_turn")\
            .gte("created_at", since)\
            .order("created_at", desc=True)\
            .limit(limit)\
            .execute()
        return [self._memory_to_payload(item) for item in result.data] if result.data else []

    def _get_recent_summaries(self, days: int, limit: int) -> list[dict]:
        since = self._since_iso(days)
        result = self.client.table("salomao_memories")\
            .select("*")\
            .eq("memory_type", "conversation_summary")\
            .gte("created_at", since)\
            .order("created_at", desc=True)\
            .limit(limit)\
            .execute()
        return [self._memory_to_payload(item) for item in result.data] if result.data else []

    def _get_recent_sessions(self, days: int, limit: int) -> list[dict]:
        since = self._since_iso(days)
        result = self.client.table("salomao_sessions")\
            .select("session_id,message_count,last_activity_at")\
            .gte("last_activity_at", since)\
            .order("last_activity_at", desc=True)\
            .limit(limit)\
            .execute()
        return result.data or []

    def _since_iso(self, days: int) -> str:
        safe_days = max(1, min(int(days or 7), 365))
        return (datetime.utcnow() - timedelta(days=safe_days)).isoformat()

    def _counter_to_rows(self, counter: Counter, limit: int = 10) -> list[dict]:
        total = sum(counter.values()) or 1
        return [
            {"name": name, "count": count, "percentage": round((count / total) * 100, 2)}
            for name, count in counter.most_common(limit)
        ]

    def _latency_bucket_name(self, latency_ms: int) -> str:
        latency = max(0, int(latency_ms or 0))
        if latency <= 3000:
            return "ate_3s"
        if latency <= 8000:
            return "3s_a_8s"
        if latency <= 15000:
            return "8s_a_15s"
        return "acima_15s"

    def _percentile(self, values: list[int], percentile: int) -> int:
        cleaned = sorted(max(0, int(value or 0)) for value in values)
        if not cleaned:
            return 0
        index = min(len(cleaned) - 1, max(0, round((percentile / 100) * (len(cleaned) - 1))))
        return cleaned[index]

    def _build_daily_trend(self, turns: list[dict]) -> list[dict]:
        days: dict[str, dict] = defaultdict(lambda: {
            "turns": 0,
            "sessions": set(),
            "latency_total": 0,
            "out_of_scope": 0,
        })
        for turn in turns:
            day = self._day_key(turn.get("created_at"))
            item = days[day]
            item["turns"] += 1
            item["latency_total"] += max(0, int(turn.get("latency_ms") or 0))
            if turn.get("session_id"):
                item["sessions"].add(turn.get("session_id"))
            if turn.get("out_of_scope"):
                item["out_of_scope"] += 1

        return [
            {
                "date": day,
                "turns": item["turns"],
                "sessions": len(item["sessions"]),
                "out_of_scope": item["out_of_scope"],
                "avg_latency_ms": round(item["latency_total"] / item["turns"], 2) if item["turns"] else 0,
            }
            for day, item in sorted(days.items())
        ]

    def _day_key(self, value: Optional[str]) -> str:
        if not value:
            return datetime.utcnow().date().isoformat()
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            return datetime.utcnow().date().isoformat()

    def _json_dumps(self, payload: dict) -> str:
        return json.dumps(payload, ensure_ascii=False, default=str)

    def _memory_to_payload(self, memory: dict) -> dict:
        try:
            payload = json.loads(memory.get("memory_content") or "{}")
        except json.JSONDecodeError:
            payload = {}
        payload.setdefault("id", memory.get("id"))
        payload.setdefault("session_id", memory.get("session_id"))
        payload.setdefault("created_at", memory.get("created_at"))
        payload.setdefault("updated_at", payload.get("created_at") or memory.get("created_at"))
        return payload

    def _compact_text(self, value: Optional[str], limit: int) -> str:
        text = " ".join(str(value or "").split())
        if len(text) <= limit:
            return text
        return f"{text[: max(0, limit - 3)].rstrip()}..."


db = ConversationDatabase()
