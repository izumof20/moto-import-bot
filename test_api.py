import os
import ssl
from openai import OpenAI
from dotenv import load_dotenv

# Вимикаємо перевірку SSL, як ми робили для Mac
ssl._create_default_https_context = ssl._create_unverified_context

# Завантажуємо ключ з файлу .env
load_dotenv()
api_key = os.getenv("OPENAI_API_KEY")

print("-----------------------------------")
if api_key:
    print(f"🔑 Ключ знайдено! Починається на: {api_key[:8]}...")
else:
    print("❌ Ключ НЕ знайдено у файлі .env!")

print("Відправляємо тестовий запит до ChatGPT...")

try:
    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "Привіт! Скажи одне слово: Працюю."}],
        max_tokens=10
    )
    print("✅ ВІДПОВІДЬ OpenAI:", response.choices[0].message.content)
    print("🎉 Все працює ідеально, гроші на балансі є!")
except Exception as e:
    print("❌ ПОМИЛКА OpenAI:", e)
print("-----------------------------------")