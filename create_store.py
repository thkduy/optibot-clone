
import os

from dotenv import load_dotenv
from google import genai

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    raise SystemExit("Set GEMINI_API_KEY in .env first.")

client = genai.Client(api_key=api_key)

store = client.file_search_stores.create(
    config={
        "display_name": "OptiBot OptiSigns Support Articles",
    }
)

print("File Search store created successfully.")
print(f"Store name: {store.name}")
print("Copy this name into GEMINI_FILE_SEARCH_STORE in .env.")
