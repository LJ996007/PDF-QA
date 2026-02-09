import sys
import unittest
from pathlib import Path


# Ensure `import app...` works when running tests from repo root.
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


from app.services.vision_gateway import normalize_base_url, build_chat_completions_payload
from app.services.vision_utils import select_candidate_pages


class TestVisionGateway(unittest.TestCase):
    def test_normalize_base_url(self):
        self.assertEqual(normalize_base_url("https://host"), "https://host/v1")
        self.assertEqual(normalize_base_url("https://host/"), "https://host/v1")
        self.assertEqual(normalize_base_url("https://host/v1"), "https://host/v1")
        self.assertEqual(normalize_base_url("https://host/v1/"), "https://host/v1")

    def test_build_payload_shape(self):
        payload = build_chat_completions_payload(
            "model-x",
            "prompt-y",
            "AAAABASE64",
        )
        self.assertEqual(payload["model"], "model-x")
        self.assertIn("messages", payload)
        self.assertIsInstance(payload["messages"], list)
        self.assertEqual(payload["messages"][0]["role"], "user")
        content = payload["messages"][0]["content"]
        self.assertEqual(content[0]["type"], "text")
        self.assertEqual(content[0]["text"], "prompt-y")
        self.assertEqual(content[1]["type"], "image_url")
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))


class TestVisionPageSelection(unittest.TestCase):
    def test_explicit_pages_win(self):
        pages = select_candidate_pages(
            explicit_pages=[3, 2, 3],
            chunk_pages=[9, 8, 7],
            max_pages=2,
            total_pages=10,
        )
        self.assertEqual(pages, [3, 2])

    def test_unique_preserve_order_from_chunks(self):
        pages = select_candidate_pages(
            explicit_pages=None,
            chunk_pages=[2, 2, 5, 1, 5],
            max_pages=10,
            total_pages=10,
        )
        self.assertEqual(pages, [2, 5, 1])

    def test_fallback_to_page_1(self):
        pages = select_candidate_pages(
            explicit_pages=None,
            chunk_pages=[],
            max_pages=2,
            total_pages=10,
        )
        self.assertEqual(pages, [1])

    def test_filter_out_of_range(self):
        pages = select_candidate_pages(
            explicit_pages=[999, -1, 2],
            chunk_pages=[],
            max_pages=5,
            total_pages=3,
        )
        self.assertEqual(pages, [2])

    def test_respects_max_pages(self):
        pages = select_candidate_pages(
            explicit_pages=None,
            chunk_pages=[1, 2, 3, 4],
            max_pages=2,
            total_pages=10,
        )
        self.assertEqual(pages, [1, 2])


if __name__ == "__main__":
    unittest.main()

