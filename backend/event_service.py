"""
Serviço de integração com API de eventos inRadar.
Permite consultar detalhes de eventos para diagnóstico.
"""

import httpx
import logging
from typing import Optional
from datetime import datetime
from config import INRADAR_API_URL, INRADAR_AUTH_TOKEN

logger = logging.getLogger('salomao.events')


def fetch_event_details(event_id: int) -> Optional[dict]:
    """
    Busca detalhes de um evento na API do inRadar.

    Args:
        event_id: ID do evento a ser consultado

    Returns:
        Dicionário com os dados do evento ou None se falhar
    """
    if not INRADAR_AUTH_TOKEN:
        logger.warning("INRADAR_AUTH_TOKEN nao configurado; diagnostico de evento indisponivel")
        return None

    try:
        response = httpx.post(
            INRADAR_API_URL,
            headers={
                "Authorization": INRADAR_AUTH_TOKEN,
                "Content-Type": "application/json"
            },
            json={"id": event_id},
            timeout=10
        )

        if response.status_code == 200:
            return response.json()
        else:
            logger.error(f"Erro ao buscar evento {event_id}: Status {response.status_code}")
            return None

    except Exception as e:
        logger.error(f"Exceção ao buscar evento {event_id}: {str(e)}")
        return None


def analyze_event_visibility(event_data: dict) -> dict:
    """
    Analisa os dados do evento e identifica possíveis problemas de visibilidade.

    Args:
        event_data: Dados do evento retornados pela API

    Returns:
        Dicionário com análise detalhada do evento
    """
    analysis = {
        "event_id": event_data.get("id"),
        "event_name": event_data.get("name"),
        "problems": [],
        "warnings": [],
        "info": [],
        "status": "ok"
    }

    # Verificar se o evento está ativo
    if not event_data.get("is_active"):
        analysis["problems"].append("O evento está DESATIVADO (is_active: false)")
        analysis["status"] = "critical"

    if not event_data.get("is_enabled"):
        analysis["problems"].append("O evento está DESABILITADO (is_enabled: false)")
        analysis["status"] = "critical"

    if not event_data.get("is_available"):
        analysis["problems"].append("O evento NÃO está disponível (is_available: false)")
        analysis["status"] = "critical"

    # Verificar publicação
    published_for = event_data.get("published_for", "")
    subgroup_id = event_data.get("subgroup_id")
    regional_id = event_data.get("regional_id")
    tertiarygroup_id = event_data.get("tertiarygroup_id")

    if published_for == "Denominação":
        analysis["info"].append(f"Publicado para: TODA A DENOMINAÇÃO")
    elif published_for == "Igreja Local":
        analysis["warnings"].append(f"Publicado APENAS para Igreja Local (subgroup_id: {subgroup_id})")
        analysis["info"].append("⚠️ Usuários de outras igrejas NÃO verão este evento")
        if analysis["status"] == "ok":
            analysis["status"] = "warning"
    elif published_for == "Regional":
        analysis["warnings"].append(f"Publicado APENAS para Regional (regional_id: {regional_id})")
        if analysis["status"] == "ok":
            analysis["status"] = "warning"
    elif published_for == "Terciário":
        analysis["warnings"].append(f"Publicado APENAS para grupo terciário (tertiarygroup_id: {tertiarygroup_id})")
        if analysis["status"] == "ok":
            analysis["status"] = "warning"

    # Verificar datas
    now = datetime.now()

    start_str = event_data.get("start")
    end_str = event_data.get("end")

    if start_str:
        try:
            start_date = datetime.fromisoformat(start_str.replace("Z", ""))
            analysis["info"].append(f"Data de início: {start_date.strftime('%d/%m/%Y às %H:%M')}")

            if start_date < now:
                analysis["info"].append("O evento já começou")
        except:
            pass

    if end_str:
        try:
            end_date = datetime.fromisoformat(end_str.replace("Z", ""))
            analysis["info"].append(f"Data de término: {end_date.strftime('%d/%m/%Y às %H:%M')}")

            if end_date < now:
                analysis["warnings"].append("⚠️ O evento já ENCERROU")
                if analysis["status"] == "ok":
                    analysis["status"] = "warning"
        except:
            pass

    # Verificar status de inscrição
    subscription_status = event_data.get("subscription_status")
    if subscription_status == "expired":
        analysis["warnings"].append("As inscrições estão EXPIRADAS")
        if analysis["status"] == "ok":
            analysis["status"] = "warning"
    elif subscription_status == "not_started":
        analysis["warnings"].append("As inscrições AINDA NÃO COMEÇARAM")
        if analysis["status"] == "ok":
            analysis["status"] = "warning"
    elif subscription_status == "active":
        analysis["info"].append("✅ Inscrições ATIVAS")

    # Verificar ingressos
    has_active_tickets = event_data.get("has_active_tickets", False)
    ticket_types = event_data.get("ticket_types", [])

    if not has_active_tickets:
        analysis["problems"].append("NÃO há ingressos ativos para este evento")
        if analysis["status"] != "critical":
            analysis["status"] = "critical"
    else:
        analysis["info"].append(f"Total de tipos de ingresso: {len(ticket_types)}")

        for ticket in ticket_types:
            ticket_name = ticket.get("name", "Sem nome")
            ticket_active = ticket.get("is_active", False)
            ticket_enabled = ticket.get("is_enabled", False)
            ticket_published = ticket.get("published_for", "")
            ticket_price = ticket.get("price", 0)
            ticket_end = ticket.get("end")

            if not ticket_active or not ticket_enabled:
                analysis["warnings"].append(f"Ingresso '{ticket_name}' está desativado")

            if ticket_published == "Igreja Local":
                analysis["warnings"].append(f"Ingresso '{ticket_name}' publicado apenas para Igreja Local")

            if ticket_end:
                try:
                    ticket_end_date = datetime.fromisoformat(ticket_end.replace("Z", ""))
                    if ticket_end_date < now:
                        analysis["warnings"].append(f"Ingresso '{ticket_name}' com vendas ENCERRADAS ({ticket_end_date.strftime('%d/%m/%Y')})")
                except:
                    pass

            analysis["info"].append(f"Ingresso '{ticket_name}': R$ {ticket_price:.2f}")

    # Informações adicionais
    if event_data.get("external_url"):
        analysis["info"].append(f"Link externo: {event_data.get('external_url')}")

    if event_data.get("send_push"):
        analysis["info"].append("✅ Push notification habilitado")
    else:
        analysis["warnings"].append("Push notification DESABILITADO")

    return analysis


def format_event_analysis_response(analysis: dict) -> str:
    """
    Formata a análise do evento em uma resposta amigável para o usuário.

    Args:
        analysis: Dicionário com a análise do evento

    Returns:
        String formatada para resposta ao usuário
    """
    response_parts = []

    # Header
    status_emoji = {
        "ok": "✅",
        "warning": "⚠️",
        "critical": "🚨"
    }

    emoji = status_emoji.get(analysis["status"], "ℹ️")
    response_parts.append(f"{emoji} **Análise do Evento: {analysis['event_name']}**")
    response_parts.append(f"ID: {analysis['event_id']}")
    response_parts.append("")

    # Problemas críticos
    if analysis["problems"]:
        response_parts.append("🚨 **PROBLEMAS ENCONTRADOS:**")
        for problem in analysis["problems"]:
            response_parts.append(f"• {problem}")
        response_parts.append("")

    # Avisos
    if analysis["warnings"]:
        response_parts.append("⚠️ **ATENÇÃO:**")
        for warning in analysis["warnings"]:
            response_parts.append(f"• {warning}")
        response_parts.append("")

    # Informações
    if analysis["info"]:
        response_parts.append("ℹ️ **Informações:**")
        for info in analysis["info"]:
            response_parts.append(f"• {info}")
        response_parts.append("")

    # Sugestões baseadas nos problemas
    response_parts.append("💡 **Sugestões:**")

    if analysis["status"] == "critical":
        response_parts.append("• Verifique se o evento está publicado corretamente no painel")
        response_parts.append("• Confirme que o evento e os ingressos estão ativos")

    if any("Igreja Local" in w for w in analysis["warnings"]):
        response_parts.append("• Para que TODOS os usuários vejam o evento, publique para 'Denominação'")
        response_parts.append("• No painel, edite o evento e altere 'Publicado para' → 'Denominação'")

    if any("EXPIRADAS" in w or "ENCERRADAS" in w for w in analysis["warnings"]):
        response_parts.append("• As inscrições/vendas de ingresso estão encerradas")
        response_parts.append("• Edite as datas de venda dos ingressos se deseja reabrir")

    if analysis["status"] == "ok":
        response_parts.append("• O evento parece estar configurado corretamente!")
        response_parts.append("• Se ainda assim não aparece, peça ao usuário limpar o cache do app")

    return "\n".join(response_parts)


def check_event_and_respond(event_id: int) -> str:
    """
    Função principal que busca o evento e retorna uma resposta formatada.

    Args:
        event_id: ID do evento

    Returns:
        Resposta formatada para o usuário
    """
    event_data = fetch_event_details(event_id)

    if event_data is None:
        return f"❌ Não foi possível encontrar o evento com ID {event_id}. Verifique se o ID está correto."

    analysis = analyze_event_visibility(event_data)
    return format_event_analysis_response(analysis)


# Para testes diretos
if __name__ == "__main__":
    test_ids = [685088, 1098845, 1085686]

    for event_id in test_ids:
        print(f"\n{'='*60}")
        print(f"TESTANDO EVENTO ID: {event_id}")
        print('='*60)
        result = check_event_and_respond(event_id)
        print(result)
