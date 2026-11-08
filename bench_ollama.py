import time
from anvil_and_ink.llm_client import OllamaClient

PROMPT = """[SYSTEM]
Ты рассказчик. Опиши в 2-4 предложениях.

[SCENE]
Бетонная коробка, тусклая лампа, на полу фляга.

[THIS TURN]
Игрок написал: "осмотреться"
Результат: действие удалось.

[TASK]
Опиши это в 2-4 предложениях."""

client = OllamaClient(timeout=60.0)

for i in range(3):
    start = time.monotonic()
    result = client(PROMPT, None)
    elapsed = time.monotonic() - start
    print(f"call {i+1}: ok={result.get('ok')} latency={elapsed:.1f}s text={result.get('text', result.get('error_class', '?'))[:80]}")