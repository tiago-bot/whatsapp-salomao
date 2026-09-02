# Guia Completo para Criação de Agentes de IA com Agno

## Introdução

A Agno é uma plataforma unificada projetada para construir, executar e gerenciar sistemas multiagentes de forma segura e escalável em ambientes de produção. O foco principal da Agno é transformar modelos de linguagem (LLMs) em **Agentes de IA** capazes de realizar ações no mundo real, interagir com sistemas externos e manter estado e contexto ao longo do tempo [^1].

Um **Agente Agno** é um programa de IA onde um modelo de linguagem controla o fluxo de execução. O agente opera em um *loop* iterativo, guiado por instruções, onde decide se deve raciocinar, usar ferramentas ou responder ao usuário [^2].

## 1. O Agente Agno: Componentes Fundamentais

O núcleo de um Agente Agno é composto por três elementos principais:

| Componente | Função | Detalhes |
| :--- | :--- | :--- |
| **Modelo (Model)** | Controla o fluxo de execução. | Decide se deve raciocinar, usar ferramentas ou responder. A Agno suporta diversos modelos de linguagem (e.g., Claude, OpenAI) [^2]. |
| **Instruções (Instructions)** | Guia o modelo sobre como usar ferramentas e responder. | Define o papel, a personalidade e as regras de comportamento do agente. |
| **Ferramentas (Tools)** | Permite que o modelo interaja com sistemas externos. | Habilita o agente a realizar ações práticas, como buscar na web, executar código, enviar e-mails ou chamar APIs. |

## 2. Construindo um Agente Simples

A Agno incentiva a abordagem de começar com um agente simples e adicionar funcionalidades conforme a necessidade. A classe central é `agno.agent.Agent`.

Um exemplo mínimo de um agente de geração de relatórios que usa um *toolkit* de terceiros:

```python
from agno.agent import Agent
from agno.models.anthropic import Claude
from agno.tools.hackernews import HackerNewsTools

# 1. Instancia o Agente
agent = Agent(
    model=Claude(id="claude-sonnet-4-5"),
    tools=[HackerNewsTools()],
    instructions="Escreva um relatório sobre o tópico. Saia apenas o relatório.",
    markdown=True,
)

# 2. Executa o Agente
agent.print_response("Startups e produtos em alta.", stream=True)
```

Os principais parâmetros da classe `Agent` permitem configurar suas capacidades avançadas:

*   `model`: O modelo de linguagem a ser usado.
*   `tools`: Uma lista de funções ou *toolkits* que o agente pode usar.
*   `instructions`: O *prompt* do sistema para guiar o comportamento do agente.
*   `db`: Uma conexão de banco de dados para persistência de estado e memória.
*   `enable_user_memories`: Habilita a memória automática do usuário.
*   `reasoning`: Habilita o raciocínio estruturado (Chain-of-Thought).
*   `knowledge`: Uma base de conhecimento para pesquisa em tempo de execução (RAG).

## 3. Execução do Agente

Para execução em desenvolvimento, o método `Agent.print_response()` é útil para visualizar a resposta no terminal. Para uso em produção, os métodos principais são `Agent.run()` e `Agent.arun()` (assíncrono) [^3].

O fluxo de execução do agente é o seguinte:
1.  O agente constrói o contexto (mensagens, histórico, memória, estado) e o envia ao modelo.
2.  O modelo responde com uma mensagem ou uma chamada de ferramenta.
3.  Se houver uma chamada de ferramenta, o agente a executa e retorna o resultado ao modelo.
4.  O modelo processa o contexto atualizado, repetindo o *loop* até produzir uma mensagem final sem chamadas de ferramenta.
5.  O agente retorna a resposta final ao chamador.

A Agno suporta **Streaming** (`stream=True`), que retorna um iterador de objetos `RunOutputEvent`, permitindo que você lide com eventos internos do agente (como início de chamada de ferramenta ou etapas de raciocínio) à medida que ocorrem, o que é crucial para construir experiências de usuário responsivas [^3].

## 4. Capacidades Avançadas do Agente

Para que o agente funcione como um verdadeiro "agente", ele precisa de capacidades que vão além da simples geração de texto.

### 4.1. Ferramentas (Tools)

As ferramentas são o que capacitam os agentes a realizar ações no mundo real. A Agno fornece mais de 120 *toolkits* pré-construídos e permite a criação de ferramentas customizadas usando a anotação `@tool()` [^4].

A Agno converte automaticamente as funções Python em definições de ferramentas (esquemas JSON) que o LLM pode entender e chamar. Um recurso poderoso é a **Execução Concorrente de Ferramentas**, onde o agente pode executar múltiplas chamadas de ferramentas solicitadas pelo modelo simultaneamente, melhorando a eficiência [^4].

### 4.2. Conhecimento (Knowledge)

O Conhecimento permite que o agente acesse informações específicas e atualizadas, superando as limitações dos dados de treinamento do LLM (respostas genéricas, desatualizadas ou alucinações) [^5].

*   **Conceito:** O agente pesquisa informações em uma base de conhecimento (armazenada em um banco de dados vetorial) em tempo de execução. Esse padrão é conhecido como **RAG Agêntico** (*Agentic RAG*).
*   **Exemplos de Uso:** Agentes de suporte ao cliente que acessam manuais, assistentes internos que conhecem políticas da empresa, ou agentes Text-to-SQL que acessam esquemas de banco de dados [^5].

### 4.3. Memória (Memory)

A Memória dá aos agentes a capacidade de lembrar preferências, contexto e interações passadas do usuário, permitindo experiências personalizadas [^6].

**Memória vs. Histórico de Sessão:**
*   **Histórico de Sessão:** Armazena as mensagens da conversa para continuidade imediata ("o que acabamos de discutir?").
*   **Memória:** Armazena fatos aprendidos sobre o usuário ("Sarah prefere e-mail") para uso em interações futuras.

A Agno oferece duas abordagens para gerenciar a memória:

| Abordagem | Descrição | Melhor Para |
| :--- | :--- | :--- |
| **Memória Automática** (`enable_user_memories=True`) | A Agno lida automaticamente com a extração, armazenamento e recuperação de memórias após cada execução do agente. | Suporte ao cliente, assistentes pessoais, onde o comportamento de memória consistente é necessário. |
| **Memória Agêntica** (`enable_agentic_memory=True`) | O agente recebe ferramentas embutidas para gerenciar memórias, decidindo quando criar, atualizar ou excluir memórias com base no contexto. | Fluxos de trabalho complexos, onde o agente precisa de flexibilidade para decidir o que vale a pena lembrar. |

### 4.4. Raciocínio (Reasoning)

O Raciocínio transforma o agente de um respondedor rápido em um solucionador de problemas cuidadoso, permitindo que ele pense e analise os resultados de suas ações antes de responder [^7].

| Abordagem | Como Funciona | Melhor Para |
| :--- | :--- | :--- |
| **Modelos de Raciocínio** | Usa modelos pré-treinados que pensam internamente (Chain-of-Thought). | Problemas complexos de tiro único (matemática, codificação) onde o raciocínio interno é suficiente. |
| **Ferramentas de Raciocínio** | Fornece ferramentas explícitas (`think()`, `analyze()`) para estruturar o processo de raciocínio. | Adicionar raciocínio a modelos não-raciocínio ou quando a visibilidade do processo de raciocínio é necessária. |
| **Agentes de Raciocínio** (`reasoning=True`) | Transforma qualquer modelo em um sistema de raciocínio estruturado via *prompt engineering* especializado (ReAct). | Tarefas complexas que exigem múltiplas chamadas de ferramentas sequenciais e autocorreção. |

## Conclusão

A Agno fornece uma estrutura robusta para a criação de agentes de IA, desde a implementação básica até a incorporação de capacidades avançadas como ferramentas, conhecimento, memória e raciocínio. Para começar, o foco deve ser na definição clara das **Instruções** e na atribuição das **Ferramentas** necessárias. A partir daí, recursos como **Memória** e **Raciocínio** podem ser adicionados para transformar o agente em um sistema autônomo e contextualizado.

Para os próximos passos, a Agno recomenda consultar o *quickstart* e a galeria de exemplos para construir aplicações do mundo real [^1].

***

## Referências

[^1]: [What is Agno? - Agno](https://docs.agno.com/introduction)
[^2]: [Agents - Agno](https://docs.agno.com/basics/agents/overview)
[^3]: [Running Agents - Agno](https://docs.agno.com/basics/agents/running-agents)
[^4]: [What are Tools? - Agno](https://docs.agno.com/basics/tools/overview)
[^5]: [Introduction to Knowledge - Agno](https://docs.agno.com/basics/knowledge/overview)
[^6]: [What is Memory? - Agno](https://docs.agno.com/basics/memory/overview)
[^7]: [What is Reasoning? - Agno](https://docs.agno.com/basics/reasoning/overview)
