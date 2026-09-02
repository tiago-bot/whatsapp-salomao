"""
Serviço de polling para monitorar novas mensagens no HubSpot.
Alternativa ao WebSocket quando webhooks não estão disponíveis.
"""

import os
import time
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Set, List

from dotenv import load_dotenv
load_dotenv()

from hubspot_service import (
    get_tickets_in_pipeline,
    get_conversation_thread_by_ticket,
    get_thread_messages,
    parse_incoming_messages,
    TARGET_PIPELINE,
    TARGET_STATUS
)
from hubspot_bot import hubspot_bot

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger('salomao.polling')


class HubSpotPollingService:
    """
    Serviço que monitora conversas do HubSpot por polling.
    Processa novas mensagens automaticamente usando o Salomão.
    """

    def __init__(self, poll_interval: int = 10):
        """
        Inicializa o serviço de polling.

        Args:
            poll_interval: Intervalo entre polls em segundos
        """
        self.poll_interval = poll_interval
        self.processed_messages: Set[str] = set()
        self.active_threads: Dict[str, datetime] = {}
        self.running = False
        self.stats = {
            "polls": 0,
            "messages_processed": 0,
            "responses_sent": 0,
            "errors": 0,
            "started_at": None
        }

    def _load_processed_messages(self):
        """Carrega mensagens já processadas (para não reprocessar ao reiniciar)."""
        self.processed_messages = set(hubspot_bot.processed_messages.keys())
        logger.info(f"📋 {len(self.processed_messages)} mensagens já processadas carregadas")

    def _get_active_tickets(self) -> List[dict]:
        """Busca tickets ativos na pipeline alvo."""
        return get_tickets_in_pipeline(TARGET_PIPELINE, TARGET_STATUS)

    def _check_thread_for_new_messages(self, thread_id: str) -> List[dict]:
        """
        Verifica se há novas mensagens de visitantes em um thread.

        Args:
            thread_id: ID do thread

        Returns:
            Lista de novas mensagens não processadas
        """
        messages = get_thread_messages(thread_id, limit=10)
        processed = parse_incoming_messages(messages)

        new_messages = []
        for msg in processed:
            if msg["is_from_visitor"] and msg["id"] not in self.processed_messages:
                new_messages.append(msg)

        return new_messages

    def _process_new_message(self, thread_id: str, message: dict) -> bool:
        """
        Processa uma nova mensagem e envia resposta.

        Args:
            thread_id: ID do thread
            message: Dados da mensagem

        Returns:
            True se processou com sucesso
        """
        try:
            message_id = message["id"]
            message_text = message["text"]

            logger.info(f"📩 Nova mensagem no thread {thread_id}: {message_text[:50]}...")

            response = hubspot_bot.process_message(thread_id, message)

            if response:
                from hubspot_service import reply_to_visitor
                result = reply_to_visitor(thread_id, response)

                if result:
                    self.processed_messages.add(message_id)
                    self.stats["responses_sent"] += 1
                    logger.info(f"✅ Resposta enviada para thread {thread_id}")
                    return True
                else:
                    logger.error(f"❌ Falha ao enviar resposta para thread {thread_id}")
                    self.stats["errors"] += 1
                    return False
            else:
                self.processed_messages.add(message_id)
                logger.warning(f"⚠️ Nenhuma resposta gerada para mensagem {message_id}")
                return True

        except Exception as e:
            logger.error(f"❌ Erro ao processar mensagem: {str(e)}")
            self.stats["errors"] += 1
            return False

    def _poll_once(self):
        """Executa um ciclo de polling."""
        self.stats["polls"] += 1

        try:
            tickets = self._get_active_tickets()

            if not tickets:
                return

            for ticket in tickets:
                ticket_id = ticket.get("id")

                thread = get_conversation_thread_by_ticket(ticket_id)
                if not thread:
                    continue

                thread_id = thread.get("id")

                new_messages = self._check_thread_for_new_messages(thread_id)

                for message in new_messages:
                    self._process_new_message(thread_id, message)
                    self.stats["messages_processed"] += 1

        except Exception as e:
            logger.error(f"❌ Erro no polling: {str(e)}")
            self.stats["errors"] += 1

    def start(self):
        """Inicia o serviço de polling."""
        self.running = True
        self.stats["started_at"] = datetime.utcnow().isoformat()
        self._load_processed_messages()

        logger.info("="*60)
        logger.info("🚀 INICIANDO SERVIÇO DE POLLING HUBSPOT")
        logger.info(f"   Pipeline: {TARGET_PIPELINE}")
        logger.info(f"   Status: {TARGET_STATUS}")
        logger.info(f"   Intervalo: {self.poll_interval}s")
        logger.info("="*60)

        try:
            while self.running:
                self._poll_once()
                time.sleep(self.poll_interval)

        except KeyboardInterrupt:
            logger.info("\n⏹️ Polling interrompido pelo usuário")
        finally:
            self.stop()

    def stop(self):
        """Para o serviço de polling."""
        self.running = False

        logger.info("\n" + "="*60)
        logger.info("📊 ESTATÍSTICAS DO POLLING")
        logger.info(f"   Polls realizados: {self.stats['polls']}")
        logger.info(f"   Mensagens processadas: {self.stats['messages_processed']}")
        logger.info(f"   Respostas enviadas: {self.stats['responses_sent']}")
        logger.info(f"   Erros: {self.stats['errors']}")
        logger.info("="*60)

    def get_stats(self) -> dict:
        """Retorna estatísticas do serviço."""
        return self.stats


polling_service = HubSpotPollingService()


def start_polling(interval: int = 10):
    """Inicia o serviço de polling."""
    polling_service.poll_interval = interval
    polling_service.start()


def stop_polling():
    """Para o serviço de polling."""
    polling_service.stop()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="HubSpot Polling Service")
    parser.add_argument(
        "--interval",
        type=int,
        default=10,
        help="Intervalo de polling em segundos (default: 10)"
    )

    args = parser.parse_args()

    print("\n" + "="*60)
    print("  SALOMÃO HUBSPOT POLLING SERVICE")
    print("="*60)
    print(f"  Pressione Ctrl+C para parar")
    print("="*60 + "\n")

    start_polling(args.interval)
