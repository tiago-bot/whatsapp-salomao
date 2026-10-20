"""Deterministic routing for requests the knowledge base cannot resolve.

Contracts, sales, implementation meetings and account-specific billing depend on
data and decisions that only the inChurch team has. Searching help articles for
them produced unrelated tutorials, so these intents are routed before the model.
A closing message ("deu certo", "obrigado") ends the turn without a search.
"""
from dataclasses import dataclass
import re
import unicodedata


@dataclass(frozen=True)
class RoutedIntent:
    intent: str
    response: str
    transfer: bool
    reason: str = ""


COMMERCIAL = RoutedIntent(
    "commercial",
    "Que bom que você quer conhecer a inChurch para a sua igreja! Vou encaminhar seu contato "
    "para a nossa equipe, que vai apresentar os planos e valores e tirar suas dúvidas.",
    True, "Interesse comercial: planos, valores ou contratação da inChurch.")
CONTRACT = RoutedIntent(
    "contract",
    "Entendi, você quer tratar do contrato da sua igreja com a inChurch. Vou encaminhar seu "
    "atendimento para a equipe responsável, que vai dar sequência ao seu pedido.",
    True, "Cancelamento, suspensão ou condições do contrato da igreja com a inChurch.")
IMPLEMENTATION = RoutedIntent(
    "implementation",
    "Entendi, é sobre o acompanhamento da sua igreja com a nossa equipe. Vou encaminhar seu "
    "atendimento para quem acompanha a sua igreja, que vai confirmar os detalhes com você.",
    True, "Reunião, implantação ou pessoa específica da equipe inChurch.")
BILLING = RoutedIntent(
    "billing",
    "Entendi, o valor cobrado está diferente do que você esperava. Vou encaminhar seu "
    "atendimento para a equipe que consegue consultar as cobranças da sua igreja.",
    True, "Divergência de valor cobrado da igreja (mensalidade, boleto ou taxas).")
CLOSING = RoutedIntent(
    "closing",
    "Que bom! Fico à disposição. Se precisar de algo mais, é só me chamar por aqui.",
    False)


def normalize(text):
    text = unicodedata.normalize("NFKD", str(text or "").casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9$,.:?\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


_COMMERCIAL = re.compile(
    r"\b(?:assinar|contratar|adquirir|comprar)\s+(?:a\s+|o\s+)?(?:plataforma|inchurch|sistema|aplicativo|app)\b"
    r"|\bplanos?\s+e\s+(?:os\s+)?(?:valor(?:es)?|precos?)\b"
    r"|\b(?:quanto\s+custa\s+(?:a\s+|o\s+)?(?:plataforma|inchurch|sistema|aplicativo|app)|tabela\s+de\s+precos?|proposta\s+comercial|apresentacao\s+comercial)\b"
    r"|\borcamento\s+(?:da|do)\s+(?:plataforma|inchurch|sistema|aplicativo|app)\b"
    r"|\b(?:valor(?:es)?|precos?|planos?)\s+da\s+(?:plataforma|inchurch)\b")
_PRICE_QUESTION = re.compile(r"\b(?:qual|quais)\s+(?:e\s+|sao\s+)?(?:o\s+|os\s+)?(?:valor|valores|preco|precos)\b|\bquanto\s+(?:e|fica|sai)\b")
_PROSPECT_CONTEXT = re.compile(r"\b(?:conhecer|como funciona|nao conheco|planos?|assinar|contratar|implantacao)\b")
_ACCOUNT_BILLING_CONTEXT = re.compile(r"\b(?:boleto|mensalidade|fatura|cobranca|taxa|repasse|ingresso|inscricao|evento)\b")

_CONTRACT = re.compile(
    # "cancelar ou suspender o contrato": the word contrato itself is decisive.
    r"\b(?:cancel\w*|suspen\w*|encerr\w*|rescin\w*|rescis\w*)\b[^.?\n]{0,60}\bcontrato\b"
    r"|\bcontrato\b[^.?\n]{0,40}\b(?:cancel\w*|suspen\w*|encerr\w*|rescin\w*)\b"
    # "cancelamento da plataforma", "cancelamento da assinatura do plano":
    # adjacent only, so "cancelar a transmissão na plataforma" is not a contract.
    r"|\bcancel\w*\s+(?:d[aoe]\s+|a\s+|o\s+|nossa\s+|nosso\s+|minha\s+|meu\s+)?"
    r"(?:assinatura\s+d[aoe]\s+(?:nosso\s+|meu\s+)?)?(?:plataforma|sistema|inchurch|mensalidade|plano)\b")
_CONTRACT_EXPLICIT = re.compile(
    r"\bfidelidade\b|\bmulta\s+(?:de|por|para)\s+(?:cancel\w*|rescis\w*)|\bprotocolo\s+do\s+cancelamento\b"
    r"|\bnao\s+(?:quero|queremos)\s+(?:mais\s+)?continuar\s+(?:na|com\s+a)\s+inchurch\b")
# Cancelling something INSIDE the platform is a product question, not the contract.
_CONTRACT_EXCLUDE = re.compile(
    r"\b(?:inscric\w*|ingresso\w*|evento\w*|boleto\w*|transac\w*|pagamento\w*|doac\w*|pedidos?\s+de\s+oracao|compra\w*|"
    r"membro\w*|usuario\w*|notificac\w*|celula\w*|assinatura\s+de\s+(?:um|uma)|plano\s+de\s+assinatura)\b")

_MEETING = re.compile(r"\breuni(?:ao|oes)\b")
_MEETING_SCHEDULE = re.compile(
    r"\b(?:agendad\w*|marcad\w*|reagend\w*|remarc\w*|horario|link|confirm\w*|hoje|amanha|"
    r"implanta\w*|onboarding|\d{1,2}\s*(?:h|hs|horas|:\d{2}))\b")
_CELL_MEETING = re.compile(r"\b(?:celula|celulas|relatorio|relatorios|culto|ministerio)\b")
_IMPLEMENTATION = re.compile(r"\b(?:onboarding|implantacao|implementacao|consultor\w*\s+de\s+implanta\w*)\b")
# A named team member ("dúvida com o Carlos"), checked on the original casing.
_NAMED_PERSON = re.compile(r"\b(?:com|pro|pra|para)\s+(?:o|a)\s+([A-ZÀ-Ý][a-zà-ÿ]{2,})\b")
# Capitalised product and partner words are not team members ("dúvida com o Pix").
_NOT_A_PERSON = {"salomao", "inchurch", "igreja", "plataforma", "equipe", "suporte", "pix", "app",
                 "aplicativo", "painel", "boleto", "cartao", "evento", "eventos", "financeiro", "safe2pay",
                 "safe", "superlogica", "apple", "google", "android", "iphone", "whatsapp", "instagram",
                 "site", "sistema", "cadastro", "login", "pagamento", "checkout", "celula", "membro",
                 "relatorio", "jornadas", "kids", "control", "multigestor", "zoom", "meet", "youtube"}

# The church's own charge (not a ticket price configured by the church).
_ACCOUNT_CHARGE = re.compile(r"\b(?:boleto|mensalidade|fatura|cobranca|cobrado|cobraram|taxa|taxas|descontado|descontaram)\b")
_AMOUNT = re.compile(r"\b(?:valor|valores|preco)\b")
_STRONG_PROBLEM = re.compile(r"\b(?:diverg\w*|nao\s+bate\w*|nao\s+batem|indevid\w*|a\s+mais)\b")
_WEAK_PROBLEM = re.compile(r"\b(?:diferent\w*|errad\w*|aument\w*|mais\s+veio|veio\s+(?:com\s+)?(?:outro\s+)?(?:valor|r?\$?\s*\d))")
_HOW_TO = re.compile(r"^(?:como|onde|e\s+possivel|da\s+pra|da\s+para|tem\s+como|posso|consigo)\b")
_MONEY = re.compile(r"(?:r\$\s*)?\b\d{2,5}(?:[.,]\d{2})?\b")

_CLOSING = re.compile(
    r"\b(?:obrigad[oa]s?|brigad[oa]|obg|vlw|valeu|grat[oa]|agradec\w*|deu\s+certo|consegui|funcionou|resolv\w*|"
    r"agora\s+(?:entrou|foi|deu|funcionou)|ja\s+(?:entrou|consegui|resolvi|deu)|era\s+(?:so\s+)?isso|"
    r"seria\s+(?:so\s+)?isso|so\s+isso|tudo\s+certo)\b")
_OPEN_PROBLEM = re.compile(
    r"\?|\b(?:nao|nem|mas|porem|erro|problema|ainda|continua|aparece|como|onde|quando|qual|quais|"
    r"preciso|gostaria|quero|pode|poderia|ajuda)\b")


def _closing(text):
    words = text.split()
    if not words or len(words) > 25 or not _CLOSING.search(text):
        return False
    # "obrigado, mas ainda não consegui" is not a closing message.
    return not _OPEN_PROBLEM.search(_CLOSING.sub(" ", text))


def _named_person(raw):
    for match in _NAMED_PERSON.finditer(str(raw or "")):
        if normalize(match.group(1)) not in _NOT_A_PERSON:
            return True
    return False


def route_intent(message, recent_customer_messages=(), assistant_spoke=False):
    """Return a RoutedIntent or None. Only the current message can open a route;
    recent customer messages resolve short follow-ups such as "Qual é o valor?"."""
    text = normalize(message)
    if not text:
        return None
    recent = normalize(" ".join(str(m or "") for m in list(recent_customer_messages)[-4:]))
    window = (recent + " " + text).strip()

    if _CONTRACT_EXPLICIT.search(text) or (_CONTRACT.search(text) and not _CONTRACT_EXCLUDE.search(text)):
        return CONTRACT
    if _COMMERCIAL.search(text):
        return COMMERCIAL
    if (_PRICE_QUESTION.search(text) and _PROSPECT_CONTEXT.search(window)
            and not _ACCOUNT_BILLING_CONTEXT.search(window)):
        return COMMERCIAL
    if _MEETING.search(text) and not _CELL_MEETING.search(window) and (
            _MEETING_SCHEDULE.search(window) or len(text.split()) <= 3):
        return IMPLEMENTATION
    if _IMPLEMENTATION.search(text):
        return IMPLEMENTATION
    if _named_person(message) and re.search(r"\b(?:falar|duvida|conversar|tratar|reuniao)\b", text):
        return IMPLEMENTATION
    if not _HOW_TO.search(text):
        # The charge must be named in this message; earlier messages only count
        # for short follow-ups ("Tá errado"), so "a senha não bate" is not billing.
        short = len(text.split()) <= 4
        charge = _ACCOUNT_CHARGE.search(text) or (short and _ACCOUNT_CHARGE.search(window))
        if _STRONG_PROBLEM.search(text) and (_AMOUNT.search(text) or charge):
            return BILLING
        if _WEAK_PROBLEM.search(text) and charge:
            return BILLING
        # "Meu boleto 397" ... "Mais veio 416,90": two amounts for one charge.
        if (_ACCOUNT_CHARGE.search(window) and len(set(_MONEY.findall(window))) >= 2
                and _MONEY.search(text) and re.search(r"\b(?:veio|mas|mais|esperava|era)\b", text)):
            return BILLING
    if assistant_spoke and _closing(text):
        return CLOSING
    return None
