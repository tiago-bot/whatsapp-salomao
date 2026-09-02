from dotenv import load_dotenv
load_dotenv()

from knowledge_base import knowledge_base

query = "como criar um membro"
print(f"Query: {query}")
print("="*60)

results = knowledge_base.search(query, top_k=5)

for i, r in enumerate(results):
    print(f"\n--- Resultado {i+1} (Score: {r.get('score', 0):.2f}) ---")
    print("Keys:", r.keys())
    content = r.get('text') or r.get('content') or r.get('metadata', {}).get('text') or str(r)[:800]
    print(content[:800] if isinstance(content, str) else str(content)[:800])
