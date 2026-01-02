from __future__ import annotations
import base64
from typing import List, Dict, Any, Optional
from openai import OpenAI
from .config import SETTINGS

_client: Optional[OpenAI] = None

def client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=SETTINGS.openai_api_key)
    return _client

def embed_text(texts: List[str]) -> List[List[float]]:
    resp = client().embeddings.create(model=SETTINGS.embed_model, input=texts)
    return [d.embedding for d in resp.data]

def image_to_data_url(image_bytes: bytes, mime: str) -> str:
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime};base64,{b64}"

def run_reasoning_with_vision(system_prompt: str, user_text: str, image_inputs: List[Dict[str, Any]] | None = None) -> str:
    content: List[Dict[str, Any]] = [{"type": "input_text", "text": user_text}]
    if image_inputs:
        for img in image_inputs:
            data_url = image_to_data_url(img["bytes"], img["mime"])
            item: Dict[str, Any] = {"type": "input_image", "image_url": data_url}
            if img.get("detail"):
                item["detail"] = img["detail"]
            content.append(item)

    resp = client().responses.create(
        model=SETTINGS.reason_model,
        input=[
            {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
            {"role": "user", "content": content},
        ],
    )
    return resp.output_text
