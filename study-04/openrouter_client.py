import base64
import requests
from config import OPENROUTER_API_KEY

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"


def chat(message: str, model: str = "deepseek/deepseek-r1-0528:free") -> str:
    """Send a message to OpenRouter and get a response."""
    response = requests.post(
        OPENROUTER_API_URL,
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [{"role": "user", "content": message}],
        },
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def analyze_image(image_path: str, prompt: str = "이 이미지를 설명해주세요.", model: str = "google/gemma-3-27b-it:free") -> str:
    """Analyze an image using a vision model."""
    with open(image_path, "rb") as f:
        image_data = base64.b64encode(f.read()).decode("utf-8")

    ext = image_path.split(".")[-1].lower()
    mime_type = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp"}.get(ext, "image/jpeg")

    response = requests.post(
        OPENROUTER_API_URL,
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_data}"}},
                    {"type": "text", "text": prompt},
                ],
            }],
        },
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


if __name__ == "__main__":
    reply = chat("Hello! Please introduce yourself briefly.")
    print(reply)
