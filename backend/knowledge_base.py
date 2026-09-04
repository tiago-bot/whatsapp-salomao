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

    @staticmethod
    def _chunk_order(metadata: dict) -> tuple[int, str]:
        raw = metadata.get("chunk_index", metadata.get("chunk", metadata.get("position", 10**9)))
        try:
            return int(raw), str(metadata.get("text", metadata.get("content", "")))
        except (TypeError, ValueError):
            return 10**9, str(raw)

    @classmethod
    def _combine_chunks(cls, chunks: list[dict]) -> str:
        """Reassemble a legacy article in document order, not similarity order."""
        ordered = sorted(chunks, key=cls._chunk_order)
        content = []
        seen = set()
        for chunk in ordered:
            text = str(chunk.get("text", chunk.get("content", ""))).strip()
            if text and text not in seen:
                seen.add(text)
                content.append(text)
        return "\n\n".join(content)

    def _article_chunks(self, embedding: list[float], url: str, article_id: str) -> list[dict]:
        """Fetch every indexed chunk for one selected article."""
        field, value = ("article_url", url) if url else ("article_id", article_id)
        try:
            result = self.index.query(
                vector=embedding,
                top_k=100,
                filter={field: {"$eq": value}},
                include_metadata=True,
            )
            return [match.metadata or {} for match in result.matches
                    if str((match.metadata or {}).get(field, "")) == str(value)]
        except Exception:
            return []

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
            # Retrieve enough candidates to rank articles instead of ranking
            # isolated chunks from the same article.
            top_k=max(12, top_k * 4),
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
                chunks = self._article_chunks(embedding, url, str(article_id))
                content = self._combine_chunks(chunks) or metadata.get("text", metadata.get("content", ""))
                articles.append({
                    "id": article_id,
                    "score": match.score,
                    "title": metadata.get("article_title", metadata.get("title", "Sem título")),
                    "content": content,
                    "url": url,
                    "category": metadata.get("category_name", metadata.get("category", ""))
                })
                if len(articles) >= top_k:
                    break

        # The top legacy result is the one most likely to shape the answer.
        # Replace its lossy chunks with the current, public official article.
        if articles and articles[0].get("retrieval") != "semantic_published":
            live = published_knowledge.hydrate_official_article(articles[0].get("url", ""))
            if live:
                articles[0]["content"] = live
                articles[0]["retrieval"] = "official_live"

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
            chunks = [match.metadata or {} for match in results.matches
                      if (match.metadata or {}).get("article_url") == url]
            if chunks:
                chunks.sort(key=self._chunk_order)
                first = chunks[0]
                content = published_knowledge.hydrate_official_article(url) or self._combine_chunks(chunks)
                found.append({"id": str(first.get("article_id") or url),
                    "title": first.get("article_title", "Documentação inChurch"), "url": url,
                    "content": content,
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
