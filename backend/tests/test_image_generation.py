from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from backend.app.image_generation import OpenAIReferenceImageGenerator


class FakeImages:
    def __init__(self) -> None:
        self.kwargs = None

    def edit(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            data=[SimpleNamespace(b64_json=base64.b64encode(b"generated-png").decode())],
            usage=SimpleNamespace(
                input_tokens_details=SimpleNamespace(text_tokens=31, image_tokens=420),
                output_tokens=780,
            ),
            _request_id="req_image_test",
        )


class ImageGenerationTests(unittest.TestCase):
    def test_reference_image_is_sent_and_usage_is_returned(self) -> None:
        images = FakeImages()
        client = SimpleNamespace(images=images)
        generator = OpenAIReferenceImageGenerator(
            api_key="",
            model="gpt-image-2.5-sunburst",
            quality="medium",
            timeout_seconds=30,
            client=client,
        )
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "reference.png"
            output = Path(directory) / "output.png"
            reference.write_bytes(b"reference")
            call = generator.generate(
                reference,
                output,
                prompt="Create a visual without text",
                size="1536x1024",
            )
            self.assertEqual(output.read_bytes(), b"generated-png")
        self.assertEqual(images.kwargs["model"], "gpt-image-2.5-sunburst")
        self.assertEqual(images.kwargs["quality"], "medium")
        self.assertEqual(images.kwargs["size"], "1536x1024")
        self.assertEqual(images.kwargs["output_format"], "png")
        self.assertEqual(call.input_text_tokens, 31)
        self.assertEqual(call.input_image_tokens, 420)
        self.assertEqual(call.output_image_tokens, 780)
        self.assertEqual(call.request_id, "req_image_test")


if __name__ == "__main__":
    unittest.main()
