import hashlib
from typing import Optional
from openai import OpenAI
from pinecone import Pinecone
from published_knowledge import published_knowledge, contextual_query, context_relevant_articles
from config import (
    EMBEDDING_MODEL,
    OPENAI_API_KEY,
    PINECONE_API_KEY,
    PINECONE_HOST,
    PINECONE_INDEX_NAME,
    PINECONE_SCORE_THRESHOLD,
)


class KnowledgeBase:
    """
    Classe para gerenciar a base de conhecimento usando Pinecone.
    Implementa cache de embeddings e busca semântica inteligente.
    """

    def __init__(self):
        self.openai_client = OpenAI(api_key=OPENAI_API_KEY)
        self.pinecone_client = Pinecone(api_key=PINECONE_API_KEY)
        self.index = (
            self.pinecone_client.Index(host=PINECONE_HOST)
            if PINECONE_HOST
            else self.pinecone_client.Index(PINECONE_INDEX_NAME)
        )
        self._embedding_cache: dict[str, list[float]] = {}

    def _get_cache_key(self, text: str) -> str:
        """Gera uma chave de cache baseada no hash do texto."""
        return hashlib.md5(text.encode()).hexdigest()

    def _get_embedding(self, text: str) -> list[float]:
        """
        Gera embedding para o texto usando OpenAI.
        Implementa cache para evitar requisições desnecessárias.
        """
        cache_key = self._get_cache_key(text)

        if cache_key in self._embedding_cache:
            return self._embedding_cache[cache_key]

        response = self.openai_client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=text
        )

        embedding = response.data[0].embedding
        self._embedding_cache[cache_key] = embedding

        return embedding

    def _extract_keywords(self, query: str) -> list[str]:
        """
        Extrai palavras-chave relevantes da query para melhorar a busca.
        Remove stopwords comuns em português.
        """
        stopwords = {
            'como', 'fazer', 'para', 'que', 'qual', 'quais', 'onde', 'quando',
            'por', 'porque', 'porquê', 'de', 'da', 'do', 'das', 'dos', 'um',
            'uma', 'uns', 'umas', 'o', 'a', 'os', 'as', 'em', 'no', 'na', 'nos',
            'nas', 'com', 'sem', 'se', 'é', 'são', 'foi', 'ser', 'ter', 'está',
            'estão', 'tem', 'têm', 'eu', 'você', 'ele', 'ela', 'nós', 'vocês',
            'eles', 'elas', 'meu', 'minha', 'seu', 'sua', 'isso', 'isto', 'aquilo',
            'esse', 'essa', 'este', 'esta', 'muito', 'mais', 'menos', 'bem', 'mal',
            'só', 'também', 'já', 'ainda', 'sempre', 'nunca', 'aqui', 'ali', 'lá',
            'cá', 'assim', 'então', 'agora', 'hoje', 'ontem', 'amanhã', 'posso',
            'pode', 'podemos', 'podem', 'consigo', 'consegue', 'quero', 'quer',
            'queremos', 'gostaria', 'preciso', 'precisa', 'precisamos', 'me',
            'te', 'lhe', 'nos', 'vos', 'lhes', 'sobre'
        }

        words = query.lower().split()
        keywords = [w for w in words if w not in stopwords and len(w) > 2]

        return keywords

    def search(
        self,
        query: str,
        top_k: int = 5,
        score_threshold: float = PINECONE_SCORE_THRESHOLD
    ) -> list[dict]:
        """
        Busca semântica na base de conhecimento.

        Args:
            query: Pergunta ou texto para buscar
            top_k: Número máximo de resultados
            score_threshold: Score mínimo de similaridade

        Returns:
            Lista de artigos relevantes com seus metadados
        """
        published = published_knowledge.search(query, top_k=top_k)
        if published:
            return published
        embedding = self._get_embedding(query)

        results = self.index.query(
            vector=embedding,
            top_k=top_k,
            include_metadata=True
        )

        articles = []
        published_by_url = published_knowledge.by_url()
        seen = set()
        for match in results.matches:
            if match.score >= score_threshold:
                metadata = match.metadata or {}
                url = metadata.get("article_url", metadata.get("url", ""))
                article_id = metadata.get("article_id", match.id)
                if article_id in seen:
                    continue
                seen.add(article_id)
                if url in published_by_url:
                    articles.append({**published_by_url[url], "score": match.score, "retrieval": "semantic_published"})
                    continue
                articles.append({
                    "id": article_id,
                    "score": match.score,
                    "title": metadata.get("article_title", metadata.get("title", "Sem título")),
                    "content": metadata.get("text", metadata.get("content", "")),
                    "url": url,
                    "category": metadata.get("category_name", metadata.get("category", ""))
                })

        return articles

    def search_referenced(self, urls: list[str]) -> list[dict]:
        """Refresh cited articles from the trusted catalog/index, not the web."""
        catalog = published_knowledge.by_url()
        found = [catalog[url] for url in urls if url in catalog]
        for url in urls:
            if url in catalog:
                continue
            # Legacy deployments may have no published Supabase catalog configured.
            # Restrict the semantic index lookup to the exact official article.
            results = self.index.query(vector=self._get_embedding(url), top_k=8,
                filter={"article_url": {"$eq": url}}, include_metadata=True)
            chunks = [match.metadata for match in results.matches
                      if (match.metadata or {}).get("article_url") == url]
            if chunks:
                first = chunks[0]
                found.append({"id": str(first.get("article_id") or url),
                    "title": first.get("article_title", "Documentação inChurch"), "url": url,
                    "content": "\n\n".join(dict.fromkeys(c.get("text", "") for c in chunks)),
                    "category": first.get("category_name", ""), "retrieval": "previous_source"})
        return found

    def search_with_context(
        self,
        query: str,
        conversation_context: Optional[str] = None,
        top_k: int = 5
    ) -> list[dict]:
        """
        Busca semântica com contexto da conversa para melhorar relevância.

        Args:
            query: Pergunta atual
            conversation_context: Contexto das mensagens anteriores
            top_k: Número máximo de resultados

        Returns:
            Lista de artigos relevantes
        """
        if conversation_context:
            enhanced_query = contextual_query(query, conversation_context)
        else:
            enhanced_query = query

        return context_relevant_articles(self.search(enhanced_query, top_k=top_k), query, conversation_context or "")

    def get_formatted_context(
        self,
        query: str,
        conversation_context: Optional[str] = None,
        max_articles: int = 3
    ) -> str:
        """
        Retorna o contexto formatado para ser usado pelo agente.

        Args:
            query: Pergunta do usuário
            conversation_context: Contexto da conversa
            max_articles: Número máximo de artigos a incluir

        Returns:
            String formatada com os artigos relevantes
        """
        articles = self.search_with_context(query, conversation_context, top_k=max_articles)

        if not articles:
            return "Nenhum artigo relevante encontrado na base de conhecimento."

        formatted = "**BASE DE CONHECIMENTO:**\n\n"

        for i, article in enumerate(articles, 1):
            formatted += f"📚 **Artigo {i}: {article['title']}**\n"
            formatted += f"Relevância: {article['score']:.0%}\n"
            if article['content']:
                content = article['content'][:1500]
                if len(article['content']) > 1500:
                    content += "..."
                formatted += f"Conteúdo:\n{content}\n"
            if article['url']:
                formatted += f"URL: {article['url']}\n"
            formatted += "\n---\n\n"

        return formatted


knowledge_base = KnowledgeBase()
