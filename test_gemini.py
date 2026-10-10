import os, time
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
# Long timeout so we can see how long a model really takes
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"),
                      http_options=types.HttpOptions(timeout=90000))

for model in ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash"]:
    start = time.time()
    try:
        r = client.models.generate_content(model=model, contents="Say hello in five words.")
        print(f"{model}: OK in {time.time() - start:.1f}s -> {r.text.strip()}")
    except Exception as e:
        print(f"{model}: FAILED after {time.time() - start:.1f}s -> {str(e)[:150]}")