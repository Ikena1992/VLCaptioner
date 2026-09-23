import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "data"))
import gelbooru_client as client
import fetch_danbooru_tags as fetch


class GelbooruTests(unittest.TestCase):
    def test_categories_persist_across_lookups(self):
        md5 = "a" * 32
        post = {"post": [{"md5": md5, "id": 12, "tags": "some_artist some_character sky",
                          "rating": "general", "score": "7",
                          "created_at": "Wed Sep 23 12:00:00 +0000 2026"}]}
        categories = {"tag": [{"name": "some_artist", "type": "1"},
                              {"name": "some_character", "type": 4},
                              {"name": "sky", "type": 0}]}
        with tempfile.TemporaryDirectory() as directory, patch.object(client, "CACHE_PATH", Path(directory) / "types.sqlite3"):
            with patch.object(client, "gelbooru_get", side_effect=[post, categories]) as request:
                result = client.lookup_gelbooru(md5)
                self.assertEqual(result["artists"], "some_artist")
                self.assertEqual(result["characters"], "some_character")
                self.assertEqual(result["general"], "sky")
                self.assertEqual(result["rating"], "g")
                self.assertEqual(result["score"], 7)
                self.assertEqual(result["created_at"], "2026-09-23T12:00:00+00:00")
                self.assertEqual(request.call_count, 2)
            with patch.object(client, "gelbooru_get", return_value=post) as request:
                self.assertEqual(client.lookup_gelbooru(md5), result)
                request.assert_called_once_with("post", tags=f"md5:{md5}", limit=1)

    def test_rejects_wrong_md5(self):
        with patch.object(client, "gelbooru_get", return_value={"post": [{"md5": "b" * 32}]}) as request:
            self.assertIsNone(client.lookup_gelbooru("a" * 32))
            self.assertEqual(request.call_count, 1)

    def test_pipeline_fallback_and_precedence(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(fetch, "IMAGE_FOLDER", directory):
            Path(directory, "a" * 32 + ".webp").touch()
            for primary in (None, {"source": "danbooru"}):
                with patch.object(fetch, "lookup_danbooru", return_value=primary), patch.object(fetch, "lookup_gelbooru", return_value={"source": "gelbooru"}) as fallback, patch.object(fetch, "save_tags") as save:
                    fetch.main()
                    self.assertEqual(fallback.call_count, 0 if primary else 1)
                    self.assertEqual(save.call_args.args[1]["source"], "danbooru" if primary else "gelbooru")

    def test_errors_do_not_expose_credentials(self):
        with patch.object(client.requests, "get", side_effect=client.requests.RequestException("secret-api-key")), patch.object(client.time, "sleep"):
            with self.assertRaises(RuntimeError) as error:
                client.gelbooru_get("post")
            self.assertNotIn("secret-api-key", str(error.exception))


if __name__ == "__main__":
    unittest.main()
