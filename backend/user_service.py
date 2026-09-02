"""
Serviço de integração com API de verificação de usuário inRadar.
Permite consultar detalhes de usuários para diagnóstico de problemas de acesso.

IMPORTANTE: Este serviço lida com dados sensíveis. Problemas críticos devem
ser encaminhados para o suporte humano sem expor detalhes ao usuário.
"""

import requests
import logging
from typing import Optional, List, Union

logger = logging.getLogger('salomao.users')

INRADAR_USER_API_URL = "https://www.inradar.com.br/api/v1/webhook/operations/get_user_id/"
from config import INRADAR_AUTH_TOKEN

SUPPORT_WHATSAPP = "+55 21 99352-8752"
SUPPORT_EMAIL = "falecom@inchurch.com.br"

USER_TYPES = {
    "inchurch_team_admin": {
        "name": "Administrador Local",
        "description": "Administrador que pode acessar tudo da igreja local (como um pastor da igreja)",
        "level": "local"
    },
    "inchurch_team_member": {
        "name": "Membro de Equipe",
        "description": "Usuário com permissões específicas para alguns módulos ou operações",
        "level": "limited"
    },
    "inchurch_admin": {
        "name": "Administrador de Denominação",
        "description": "Maior privilégio - pode fazer TUDO na plataforma (como pastor de todas as igrejas)",
        "level": "denomination"
    },
    "inradar_user": {
        "name": "Usuário comum",
        "description": "Usuário sem permissões administrativas",
        "level": "user"
    }
}


def fetch_user_by_email(email: str) -> Union[dict, List[dict], None]:
    """
    Busca dados de um usuário na API do inRadar pelo email.

    Args:
        email: Email do usuário a ser consultado

    Returns:
        - dict: Se encontrar apenas 1 registro
        - List[dict]: Se encontrar múltiplos registros (múltiplas igrejas)
        - None: Se não encontrar ou falhar
    """
    try:
        response = requests.post(
            INRADAR_USER_API_URL,
            headers={
                "Authorization": INRADAR_AUTH_TOKEN,
                "Content-Type": "application/json"
            },
            json={"email": email.strip().lower()},
            timeout=10
        )

        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list):
                if len(data) == 0:
                    logger.warning(f"Resposta vazia para email {email}")
                    return None
                elif len(data) == 1:
                    return data[0]
                else:
                    logger.info(f"Usuário {email} encontrado em {len(data)} igrejas")
                    return data
            elif isinstance(data, dict):
                return data
            else:
                logger.warning(f"Resposta inesperada para email {email}")
                return None
        else:
            logger.error(f"Erro ao buscar usuário {email}: Status {response.status_code}")
            return None

    except Exception as e:
        logger.error(f"Exceção ao buscar usuário {email}: {str(e)}")
        return None


def get_user_type_info(user_type: str) -> dict:
    """
    Retorna informações sobre o tipo de usuário.

    Args:
        user_type: Tipo do usuário retornado pela API

    Returns:
        Dicionário com nome, descrição e nível do tipo de usuário
    """
    if user_type in USER_TYPES:
        return USER_TYPES[user_type]

    # Qualquer outro tipo é considerado administrador regional
    return {
        "name": "Administrador Regional",
        "description": "Pode fazer tudo dentro da regional que faz parte",
        "level": "regional"
    }


def format_multiple_churches_response(users_data: List[dict]) -> dict:
    """
    Formata resposta quando usuário está cadastrado em múltiplas igrejas.

    Args:
        users_data: Lista de registros do usuário em diferentes igrejas

    Returns:
        Dicionário com a lista de igrejas e instrução para perguntar ao usuário
    """
    churches = []

    for i, user in enumerate(users_data, 1):
        subgroup = user.get("subgroup", {}) or {}
        church_name = subgroup.get("name", "Igreja não identificada")
        user_type = user.get("user_type", "")
        user_type_info = get_user_type_info(user_type)
        full_name = user.get("full_name", "")

        churches.append({
            "index": i,
            "church_name": church_name,
            "user_type": user_type_info["name"],
            "full_name": full_name,
            "raw_data": user
        })

    return {
        "multiple_churches": True,
        "church_count": len(churches),
        "churches": churches
    }


def get_user_by_church_index(users_data: List[dict], index: int) -> Optional[dict]:
    """
    Retorna os dados do usuário para uma igreja específica pelo índice.

    Args:
        users_data: Lista de registros do usuário
        index: Índice da igreja (1-based)

    Returns:
        Dados do usuário para a igreja selecionada ou None
    """
    if 1 <= index <= len(users_data):
        return users_data[index - 1]
    return None


def analyze_user_issues(user_data: dict) -> dict:
    """
    Analisa os dados do usuário e identifica problemas.

    IMPORTANTE: Problemas sensíveis (bloqueio, verificação) devem ser
    tratados com cuidado - encaminhar para suporte sem expor detalhes.

    Args:
        user_data: Dados do usuário retornados pela API

    Returns:
        Dicionário com análise detalhada
    """
    analysis = {
        "user_id": user_data.get("user_id"),
        "full_name": user_data.get("full_name"),
        "email": user_data.get("email"),
        "requires_support": False,
        "support_reason": None,
        "problems": [],
        "warnings": [],
        "info": [],
        "admin_permissions": [],
        "groups": [],
        "status": "ok"
    }

    # === VERIFICAÇÕES CRÍTICAS (encaminhar para suporte) ===

    # Usuário bloqueado
    if user_data.get("is_blocked", False):
        analysis["requires_support"] = True
        analysis["support_reason"] = "account_issue"
        analysis["status"] = "critical"
        logger.info(f"Usuário {user_data.get('email')} está bloqueado")
        return analysis

    # Usuário não verificado
    if not user_data.get("is_verified", True):
        analysis["requires_support"] = True
        analysis["support_reason"] = "account_verification"
        analysis["status"] = "critical"
        logger.info(f"Usuário {user_data.get('email')} não está verificado")
        return analysis

    # === VERIFICAÇÃO DE SECURITY SCORE ===
    security_score = user_data.get("security_score", 100)

    if security_score < 10:
        analysis["problems"].append("security_score_low")
        analysis["warnings"].append(
            "Seu perfil está com restrições de segurança que impedem algumas ações, "
            "como compras de ingressos. Isso pode ocorrer quando o sistema detecta "
            "atividade incomum na conta."
        )
        analysis["status"] = "warning"
    elif security_score < 50:
        analysis["info"].append(
            "Seu perfil tem algumas restrições de segurança. Algumas funcionalidades "
            "podem ter limitações."
        )

    # === VERIFICAÇÃO DE MEMBRO ===
    member = user_data.get("member", {})
    if member:
        member_status = member.get("status", "")
        profile_type = member.get("profile_type", "")

        if member_status == "approved":
            analysis["info"].append(f"Status de membro: Aprovado ✅")
        elif member_status == "pending":
            analysis["warnings"].append(
                "Seu cadastro de membro ainda está pendente de aprovação. "
                "Algumas funcionalidades podem estar limitadas até a aprovação."
            )
            if analysis["status"] == "ok":
                analysis["status"] = "warning"
        elif member_status == "rejected":
            analysis["requires_support"] = True
            analysis["support_reason"] = "member_status"
            analysis["status"] = "critical"
            return analysis

    # === VERIFICAÇÃO DE PERMISSÕES ADMIN ===
    admin_access = user_data.get("admin_access", [])
    if admin_access:
        for access in admin_access:
            content_name = access.get("content_name", "")
            actions = access.get("actions", [])
            level = access.get("level", "")

            permissions = []
            if "create" in actions:
                permissions.append("criar")
            if "read" in actions:
                permissions.append("visualizar")
            if "update" in actions:
                permissions.append("editar")
            if "delete" in actions:
                permissions.append("excluir")

            if permissions:
                analysis["admin_permissions"].append({
                    "module": content_name,
                    "permissions": permissions,
                    "level": level
                })

    # === VERIFICAÇÃO DE GRUPOS ===
    small_groups = user_data.get("small_group_memberships", [])
    if small_groups:
        for membership in small_groups[:10]:
            group = membership.get("small_group", {})
            if group:
                analysis["groups"].append({
                    "name": group.get("name", ""),
                    "approved": membership.get("approved", False)
                })

        if len(small_groups) > 10:
            analysis["info"].append(f"Participante de {len(small_groups)} grupos")

    # === INFORMAÇÕES DA IGREJA ===
    subgroup = user_data.get("subgroup", {})
    if subgroup:
        analysis["info"].append(f"Igreja: {subgroup.get('name', 'Não identificada')}")

    tertiarygroup = user_data.get("tertiarygroup", {})
    if tertiarygroup:
        analysis["info"].append(f"Rede/Regional: {tertiarygroup.get('name', '')}")

    # === TIPO DE USUÁRIO ===
    user_type = user_data.get("user_type", "")
    user_type_info = get_user_type_info(user_type)
    analysis["user_type"] = user_type
    analysis["user_type_name"] = user_type_info["name"]
    analysis["user_type_level"] = user_type_info["level"]

    # Adicionar informação amigável sobre o tipo
    if user_type_info["level"] == "denomination":
        analysis["info"].append(f"Tipo: {user_type_info['name']} 👑 (acesso total à plataforma)")
    elif user_type_info["level"] == "local":
        analysis["info"].append(f"Tipo: {user_type_info['name']} (acesso total à igreja local)")
    elif user_type_info["level"] == "regional":
        analysis["info"].append(f"Tipo: {user_type_info['name']} (acesso total à regional)")
    elif user_type_info["level"] == "limited":
        analysis["info"].append(f"Tipo: {user_type_info['name']} (permissões específicas)")
    else:
        analysis["info"].append(f"Tipo: {user_type_info['name']}")

    return analysis


def format_user_analysis_response(analysis: dict, context: str = "general") -> str:
    """
    Formata a análise do usuário em uma resposta amigável.

    Args:
        analysis: Dicionário com a análise do usuário
        context: Contexto do problema relatado pelo usuário

    Returns:
        String formatada para resposta ao usuário
    """
    response_parts = []

    # Se requer suporte, encaminhar imediatamente
    if analysis["requires_support"]:
        response_parts.append(
            f"Olá, **{analysis['full_name'].split()[0]}**! Identifiquei uma situação "
            "na sua conta que precisa ser tratada pela nossa equipe de suporte."
        )
        response_parts.append("")
        response_parts.append(
            "Para resolver isso da melhor forma, por favor entre em contato diretamente:"
        )
        response_parts.append("")
        response_parts.append(f"📱 **WhatsApp:** {SUPPORT_WHATSAPP}")
        response_parts.append(f"📧 **Email:** {SUPPORT_EMAIL}")
        response_parts.append("")
        response_parts.append(
            "Nossa equipe vai te ajudar rapidamente! 😊"
        )
        return "\n".join(response_parts)

    # Saudação
    first_name = analysis['full_name'].split()[0] if analysis.get('full_name') else "usuário"
    response_parts.append(f"Olá, **{first_name}**! Verifiquei sua conta e aqui está o que encontrei:")
    response_parts.append("")

    # Problemas/Avisos
    if analysis["warnings"]:
        response_parts.append("⚠️ **Atenção:**")
        for warning in analysis["warnings"]:
            response_parts.append(f"• {warning}")
        response_parts.append("")

    # Contexto específico: problema com compra de ingresso
    if "security_score_low" in analysis.get("problems", []):
        if "compra" in context.lower() or "ingresso" in context.lower() or "robô" in context.lower():
            response_parts.append("💡 **Sobre o erro de compra:**")
            response_parts.append(
                "O sistema de segurança identificou uma restrição temporária na sua conta. "
                "Isso pode acontecer por diversos motivos de proteção."
            )
            response_parts.append("")
            response_parts.append("Para liberar sua conta para compras, entre em contato:")
            response_parts.append(f"📱 **WhatsApp:** {SUPPORT_WHATSAPP}")
            response_parts.append(f"📧 **Email:** {SUPPORT_EMAIL}")
            response_parts.append("")
            return "\n".join(response_parts)

    # Informações positivas
    if analysis["info"]:
        response_parts.append("ℹ️ **Informações da conta:**")
        for info in analysis["info"]:
            response_parts.append(f"• {info}")
        response_parts.append("")

    # Permissões de admin (se tiver)
    if analysis["admin_permissions"]:
        response_parts.append("🔐 **Suas permissões de administração:**")
        for perm in analysis["admin_permissions"]:
            perms_str = ", ".join(perm["permissions"])
            response_parts.append(f"• **{perm['module']}**: {perms_str}")
        response_parts.append("")

    # Status geral
    if analysis["status"] == "ok":
        response_parts.append("✅ Sua conta parece estar funcionando normalmente!")
        response_parts.append("")
        response_parts.append(
            "Se ainda está enfrentando problemas, me descreva com mais detalhes "
            "o que está acontecendo para que eu possa ajudar melhor."
        )

    return "\n".join(response_parts)


def check_user_and_respond(email: str, context: str = "general") -> str:
    """
    Função principal que busca o usuário e retorna uma resposta formatada.

    Args:
        email: Email do usuário
        context: Contexto do problema relatado

    Returns:
        Resposta formatada para o usuário
    """
    user_data = fetch_user_by_email(email)

    if user_data is None:
        return (
            f"❌ Não encontrei uma conta com o email **{email}**. "
            "Por favor, verifique se o email está correto ou se é o mesmo usado no cadastro do app."
        )

    analysis = analyze_user_issues(user_data)
    return format_user_analysis_response(analysis, context)


def check_user_permissions(email: str, module: str) -> dict:
    """
    Verifica se o usuário tem permissão para um módulo específico.

    Args:
        email: Email do usuário
        module: Nome do módulo (ex: 'cells_management', 'events', etc)

    Returns:
        Dicionário com as permissões encontradas
    """
    user_data = fetch_user_by_email(email)

    if user_data is None:
        return {"found": False, "permissions": []}

    admin_access = user_data.get("admin_access", [])

    for access in admin_access:
        if access.get("content_alias") == module or module.lower() in access.get("content_name", "").lower():
            return {
                "found": True,
                "permissions": access.get("actions", []),
                "level": access.get("level", ""),
                "module_name": access.get("content_name", "")
            }

    return {"found": False, "permissions": []}


# Para testes diretos
if __name__ == "__main__":
    test_email = "deboracafranco@gmail.com"

    print(f"\n{'='*60}")
    print(f"TESTANDO USUÁRIO: {test_email}")
    print('='*60)

    result = check_user_and_respond(test_email, "não consigo comprar ingresso, diz que sou robô")
    print(result)

    print(f"\n{'='*60}")
    print("TESTANDO PERMISSÕES")
    print('='*60)

    perms = check_user_permissions(test_email, "cells_management")
    print(f"Permissões para Gestão de Células: {perms}")
