from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ImageGenerationCall:
    provider: str
    model: str
    request_id: str | None
    input_text_tokens: int
    input_image_tokens: int
    output_image_tokens: int


class OpenAIReferenceImageGenerator:
    provider = "openai"

    def __init__(
        self,
        api_key: str,
        model: str,
        quality: str,
        timeout_seconds: float,
        client: Any | None = None,
    ) -> None:
        if client is None:
            from openai import OpenAI

            client = OpenAI(api_key=api_key, timeout=timeout_seconds, max_retries=0)
        self.client = client
        self.model = model
        self.quality = quality

    def generate(
        self,
        reference_path: Path,
        output_path: Path,
        *,
        prompt: str,
        size: str,
    ) -> ImageGenerationCall:
        with reference_path.open("rb") as reference:
            response = self.client.images.edit(
                model=self.model,
                image=reference,
                prompt=prompt,
                size=size,
                quality=self.quality,
                output_format="png",
            )
        data = response.data or []
        if not data or not data[0].b64_json:
            raise RuntimeError("OpenAI n'a renvoyé aucune image.")
        output_path.write_bytes(base64.b64decode(data[0].b64_json, validate=True))
        usage = response.usage
        details = usage.input_tokens_details if usage else None
        return ImageGenerationCall(
            provider=self.provider,
            model=self.model,
            request_id=getattr(response, "_request_id", None),
            input_text_tokens=int(details.text_tokens if details else 0),
            input_image_tokens=int(details.image_tokens if details else 0),
            output_image_tokens=int(usage.output_tokens if usage else 0),
        )
