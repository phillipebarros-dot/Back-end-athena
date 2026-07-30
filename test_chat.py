"""Teste direto do agente — captura o erro real."""
import asyncio
import traceback
import sys
import os

# Garante que o .env é carregado
os.chdir(os.path.dirname(os.path.abspath(__file__)))

async def test():
    try:
        from app.agent.graph import build_agent
        from app.agent.tools import get_all_tools
        
        print("1. Carregando tools...")
        tools = get_all_tools()
        print(f"   Tools locais: {len(tools)}")
        
        print("2. Construindo agente...")
        agent = await build_agent(local_tools=tools)
        print("   Agente criado!")
        
        print("3. Invocando agente...")
        config = {"configurable": {"thread_id": "test-direct"}}
        result = await agent.ainvoke(
            {"messages": [("user", "Ola, quem e voce?")]},
            config=config,
        )
        print(f"4. Resposta: {result['messages'][-1].content[:200]}")
        
    except Exception as e:
        print(f"\n*** ERRO: {type(e).__name__}: {e}")
        traceback.print_exc()

asyncio.run(test())
