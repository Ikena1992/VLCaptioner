import hashlib
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "data"))
import booru_downloader as downloader
import gelbooru_client
from fetch_danbooru_tags import save_tags
from refresh_tags_from_danbooru import post_to_tags


class Response:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def raise_for_status(self):
        pass

    def iter_content(self, size):
        yield b"original image"


class DownloaderTests(unittest.TestCase):
    def setUp(self):
        workspace_temp = Path(__file__).resolve().parents[1] / ".test-tmp"
        workspace_temp.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=workspace_temp)
        self.addCleanup(self.temp.cleanup)
        self.images = Path(self.temp.name) / "images"
        self.done = Path(self.temp.name) / "done"
        self.images.mkdir()
        self.done.mkdir()
        self.cancel = threading.Event()
        cache_patch = patch.object(gelbooru_client, "CACHE_PATH", Path(self.temp.name) / "categories.sqlite3")
        cache_patch.start()
        self.addCleanup(cache_patch.stop)
        self.md5 = hashlib.md5(b"original image").hexdigest()
        self.post = {"md5": self.md5, "file_url": "https://example.com/original.png"}

    def save(self, post=None):
        return downloader.save_original(post or self.post, "Gelbooru", self.images,
                                        self.done, set(), self.cancel)

    @patch.object(downloader.requests, "get", return_value=Response())
    def test_verified_original_and_duplicate(self, get):
        self.assertEqual(self.save(), "downloaded")
        self.assertEqual((self.images / f"{self.md5}.png").read_bytes(), b"original image")
        self.assertEqual(self.save(), "skipped")
        self.assertEqual(get.call_count, 1)
        self.assertNotIn("auth", get.call_args.kwargs)
        self.assertFalse(list(self.images.glob("*.part")))

    @patch.object(downloader.requests, "get")
    def test_recursive_converted_done_duplicate(self, get):
        nested = self.done / "nested"
        nested.mkdir()
        (nested / f"{self.md5.upper()}.webp").write_bytes(b"converted")
        self.assertEqual(self.save(), "skipped")
        get.assert_not_called()

    @patch.object(downloader.requests, "get", return_value=Response())
    def test_danbooru_sidecars_match_captioner_and_preserve_txt(self, get):
        post = {**self.post, "tag_string_character": "some_character",
                "tag_string_artist": "some_artist", "tag_string_general": "1girl blue_eyes",
                "tag_string_meta": "highres", "rating": "g", "score": 100,
                "created_at": "2026-09-23T12:00:00+00:00", "id": 123}
        expected = Path(self.temp.name) / "expected"
        expected.mkdir()
        save_tags(self.md5, post_to_tags(post), image_folder=expected)
        self.assertEqual(downloader.save_original(post, "Danbooru", self.images, self.done, set(), self.cancel), "downloaded")
        for extension in ("txt", "csv"):
            name = f"{self.md5}.{extension}"
            self.assertEqual((self.images / name).read_bytes(), (expected / name).read_bytes())
        (self.images / f"{self.md5}.png").unlink()
        (self.images / f"{self.md5}.txt").write_text("custom tags\n", encoding="utf-8")
        self.assertEqual(downloader.save_original(post, "Danbooru", self.images, self.done, set(), self.cancel), "downloaded")
        self.assertEqual((self.images / f"{self.md5}.txt").read_text(), "custom tags\n")

    @patch.object(downloader.requests, "get", return_value=Response())
    def test_gelbooru_sidecars_use_captioner_categories(self, get):
        post = {**self.post, "tags": "some_character blue_eyes", "rating": "general", "score": 7}
        cache = gelbooru_client.TagCategoryCache(gelbooru_client.CACHE_PATH)
        cache["some_character"] = 4
        cache["blue_eyes"] = 0
        self.assertEqual(self.save(post), "downloaded")
        content = (self.images / f"{self.md5}.txt").read_text()
        self.assertIn("some character, blue eyes", content)
        self.assertTrue((self.images / f"{self.md5}.csv").exists())

    @patch.object(downloader.requests, "get", return_value=Response())
    def test_detailed_log_reports_download_and_done_skip(self, get):
        messages = []
        self.assertEqual(downloader.save_original(self.post, "Gelbooru", self.images,
                         self.done, set(), self.cancel, messages.append), "downloaded")
        self.assertTrue(any(f"Downloading original to images/{self.md5}.png" in m for m in messages))
        self.assertTrue(any("TXT/CSV tags ready" in m for m in messages))
        (self.images / f"{self.md5}.png").rename(self.done / f"{self.md5}.png")
        messages.clear()
        self.assertEqual(downloader.save_original(self.post, "Gelbooru", self.images,
                         self.done, set(), self.cancel, messages.append), "skipped")
        self.assertTrue(any("already in done/" in m for m in messages))

    def test_failure_details_do_not_expose_secret_urls(self):
        response = downloader.requests.Response()
        response.status_code = 403
        error = downloader.requests.HTTPError("https://example.com/?api_key=secret", response=response)
        self.assertIn("HTTP 403", downloader.failure_reason(error))
        self.assertNotIn("secret", downloader.failure_reason(error))
        self.assertNotIn("secret", downloader.failure_reason(RuntimeError("api_key=secret")))

    @patch.object(downloader.requests, "get", return_value=Response())
    def test_mismatch_cleans_partial(self, get):
        with self.assertRaises(RuntimeError):
            self.save({**self.post, "md5": "a" * 32})
        self.assertEqual(list(self.images.iterdir()), [])

    @patch.object(downloader.requests, "get", return_value=Response())
    def test_cancel_cleans_partial(self, get):
        self.cancel.set()
        self.assertEqual(self.save(), "cancelled")
        self.assertEqual(list(self.images.iterdir()), [])

    @patch.object(downloader, "save_original", return_value="skipped")
    @patch.object(downloader, "fetch_posts")
    def test_blacklist_filters_both_sites_before_download(self, fetch, save):
        blacklist = downloader.parse_blacklist(" watermark  COMIC \t\n ")
        for source, field in (("Danbooru", "tag_string"), ("Gelbooru", "tags")):
            with self.subTest(source=source):
                save.reset_mock()
                fetch.return_value = [{"id": 1, field: "sky Watermark"},
                                      {"id": 2, field: "comic sky"},
                                      {"id": 3, field: "watermark_request sky"}]
                counts = downloader.run_job(downloader.DownloadJob(source, "sky", 3, blacklist),
                                            self.images, self.done, set(), self.cancel, lambda *args: None)
                self.assertEqual(counts["blacklisted"], 2)
                self.assertEqual(counts["skipped"], 1)
                self.assertEqual(save.call_count, 1)
                self.assertEqual(save.call_args.args[0]["id"], 3)

    def test_blacklist_uses_search_style_whitespace_and_underscores(self):
        tags = downloader.parse_blacklist("  tall_image\twatermark\nCOMIC tall_image  ")
        self.assertEqual(tags, frozenset({"tall_image", "watermark", "comic"}))
        self.assertTrue(downloader.is_blacklisted({"tags": "sky tall_image"}, tags))
        self.assertFalse(downloader.is_blacklisted({"tags": "tall_image_request"}, tags))
        self.assertEqual(downloader.parse_blacklist(" \t\n"), frozenset())

    def test_index_avoids_rescanning_for_known_duplicates(self):
        (self.done / f"{self.md5}.webp").write_bytes(b"converted")
        index = downloader.ImageIndex(self.images, self.done)
        with patch.object(downloader, "existing_hashes", side_effect=AssertionError("unexpected scan")):
            result = downloader.save_original(self.post, "Danbooru", self.images, self.done, index, self.cancel)
        self.assertEqual(result, "skipped")

    @patch.object(downloader.requests, "get", return_value=Response())
    def test_cancel_during_tags_leaves_no_orphan_files(self, get):
        def cancel_after_tags(*args, **kwargs):
            save_tags(*args, **kwargs)
            self.cancel.set()
        with patch.object(downloader, "save_tags", side_effect=cancel_after_tags):
            self.assertEqual(self.save(), "cancelled")
        self.assertEqual(list(self.images.iterdir()), [])

    @patch.object(downloader.requests, "get", return_value=Response())
    def test_failed_image_publication_restores_existing_sidecars(self, get):
        txt = self.images / f"{self.md5}.txt"
        csv = self.images / f"{self.md5}.csv"
        txt.write_text("custom tags\n", encoding="utf-8")
        csv.write_text("md5,general\n" + self.md5 + ",custom tags\n", encoding="utf-8")
        before = {p.name: p.read_bytes() for p in self.images.iterdir()}
        with patch.object(downloader.os, "rename" if downloader.os.name == "nt" else "link",
                          side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                self.save()
        self.assertEqual({p.name: p.read_bytes() for p in self.images.iterdir()}, before)

    @patch.object(downloader.requests, "get", return_value=Response())
    def test_failed_image_publication_removes_new_sidecars(self, get):
        with patch.object(downloader.os, "rename" if downloader.os.name == "nt" else "link",
                          side_effect=FileExistsError("concurrent image")):
            self.assertEqual(self.save(), "skipped")
        self.assertEqual(list(self.images.iterdir()), [])

    @patch.object(downloader, "save_original", return_value="skipped")
    @patch.object(downloader, "fetch_posts")
    def test_pagination_post_limit_and_repeated_page(self, fetch, save):
        fetch.side_effect = [[{"id": i} for i in range(100)], [{"id": 100}, {"id": 101}]]
        counts = downloader.run_job(downloader.DownloadJob("Danbooru", "sky", 101),
                                    self.images, self.done, set(), self.cancel, lambda *args: None)
        self.assertEqual(counts["skipped"], 101)
        self.assertEqual(fetch.call_args.args[1], 1)
        fetch.side_effect = [[{"id": 1}], [{"id": 1}]]
        counts = downloader.run_job(downloader.DownloadJob("Danbooru", "sky", 10),
                                    self.images, self.done, set(), self.cancel, lambda *args: None)
        self.assertEqual(counts["skipped"], 1)


if __name__ == "__main__":
    unittest.main()
