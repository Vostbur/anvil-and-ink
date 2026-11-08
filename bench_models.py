import re
import time

from anvil_and_ink.llm_client import OllamaClient


PROMPT = """[SYSTEM]
Ты — рассказчик текстовой приключенческой игры.

Задача: превратить сухие факты в 2–4 предложения атмосферной прозы.

Жёсткие правила:
- Всегда пиши от второго лица: «вы», «ваш», «вам».
- Никогда не используй слова: «игрок», «он», «она», «они», «персонаж», «герой».
- Не придумывай предметы, людей, события и детали, которых нет в данных.
- Для команды «осмотреться» не пиши «вы входите». Правильно: «вы осматриваетесь», «вы оглядываетесь», «вы смотрите по сторонам».
- Пиши мрачно, сдержанно, без пафоса.
- Не используй числа и числительные без крайней необходимости.

Хороший пример:
Вы осматриваетесь. Бетонная коробка, тусклая лампа, на полу — фляга. Воздух пахнет сыростью и ржавчиной.

Плохой пример:
Игрок осмотрелся. Он увидел флягу.

[SCENE]
Локация: Убежище
Бетонная коробка, тусклая лампа, на полу — фляга.

[THIS TURN]
Игрок написал: "осмотреться"
Результат: действие удалось.

[TASK]
Опиши это в 2–4 предложениях.
"""


MODELS = [
    "qwen2.5:3b",
    # "llama3.2:3b",
    # "gemma3:4b",
]


BAD_PATTERN = re.compile(
    r"игрок|\bон\b|\bона\b|\bони\b|персонаж|герой|вы входите",
    re.IGNORECASE,
)

YOU_PATTERN = re.compile(r"\bвы\b|\bваш\b|\bвам\b|\bвами\b", re.IGNORECASE)


def check_text(text: str) -> list[str]:
    issues = []

    if not YOU_PATTERN.search(text):
        issues.append("нет «вы»")

    bad = BAD_PATTERN.search(text)
    if bad:
        issues.append(f"запрещённое: {bad.group()!r}")

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    if len(sentences) < 2 or len(sentences) > 4:
        issues.append(f"не 2–4 предложения: {len(sentences)}")

    return issues


def main():
    for model in MODELS:
        print(f"\n=== {model} ===")

        client = OllamaClient(
            model=model,
            timeout=90.0,
            max_tokens=120,
            temperature=0.7,
        )

        for i in range(2):
            start = time.monotonic()
            result = client(PROMPT, None)
            elapsed = time.monotonic() - start

            if not result.get("ok"):
                print(
                    f"call {i + 1}: FAILED "
                    f"latency={elapsed:.1f}s "
                    f"error={result.get('error_class')} "
                    f"detail={result.get('detail')}"
                )
                continue

            text = result.get("text", "").strip()
            issues = check_text(text)

            print(
                f"call {i + 1}: OK "
                f"latency={elapsed:.1f}s "
                f"tokens={result.get('tokens')} "
                f"issues={issues if issues else ['нет']}"
            )
            print(text)
            print("-" * 60)


if __name__ == "__main__":
    main()