import sys
import unittest
import tempfile
import json
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "data"))
import tag_images_with_wd14 as tagger


class FallbackTests(unittest.TestCase):
    def test_model_config_rejects_reordered_or_missing_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"num_classes": 2, "tags": ["a", "b"]}))
            tagger.validate_model_config(path, ["a", "b"])
            for names in (["b", "a"], ["a"]):
                with self.subTest(names=names), self.assertRaises(ValueError):
                    tagger.validate_model_config(path, names)

    def test_invalid_label_tables_are_rejected(self):
        for labels in (
            tagger.pd.DataFrame({"name": ["a"], "category": [4.9]}),
            tagger.pd.DataFrame({"name": [""], "category": [0]}),
            tagger.pd.DataFrame({"name": ["a", "a"], "category": [0, 4]}),
            tagger.pd.DataFrame({"name": ["a"]}),
            tagger.pd.DataFrame({"name": [], "category": []}),
        ):
            with self.subTest(labels=labels), patch.object(tagger.pd, "read_csv", return_value=labels):
                with self.assertRaises(ValueError):
                    tagger.read_label_table("labels.csv")

    def test_images_sharing_a_sidecar_fail_before_loading_model(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for extension in ("png", "jpg"):
                Image.new("RGB", (2, 2)).save(folder / ("image." + extension))
            with patch.object(tagger, "IMAGE_FOLDER", folder), patch.object(tagger, "load_preferred_model") as load, patch.object(sys, "argv", ["tagger"]):
                with self.assertRaisesRegex(ValueError, "share the same tag file"):
                    tagger.main()
            load.assert_not_called()
            self.assertFalse((folder / "image.txt").exists())

    def test_blank_and_metadata_only_files_are_untagged(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for name, content in (("blank", "\ufeff  , \n"), ("metadata", '\n' + tagger.MARKER + '{"id": 1}'), ("tagged", "1girl")):
                Image.new("RGB", (2, 2)).save(folder / (name + ".png"))
                (folder / (name + ".txt")).write_text(content, encoding="utf-8")
            self.assertEqual([p.stem for p in tagger.find_images_without_txt(folder)], ["blank", "metadata"])
            self.assertEqual([p.stem for p in tagger.find_images_with_txt(folder)], ["tagged"])

    def test_metadata_only_file_is_tagged_even_when_existing_tag_pass_is_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            Image.new("RGB", (2, 2)).save(folder / "image.png")
            suffix = '\n' + tagger.MARKER + '{"id": 1}'
            (folder / "image.txt").write_text(suffix, encoding="utf-8")
            model = {"repo_id": "test", "device": "cpu", "threshold_description": "test"}
            with patch.object(tagger, "IMAGE_FOLDER", folder), patch.object(tagger, "load_preferred_model", return_value=model), patch.object(tagger, "predict_tags", return_value=["1girl"]) as predict, patch.object(sys, "argv", ["tagger", "--skip-high-confidence-missing-tags"]):
                tagger.main()
            self.assertEqual((folder / "image.txt").read_text(), "1girl" + suffix)
            self.assertEqual(predict.call_count, 1)
            self.assertNotIn("threshold", predict.call_args.kwargs)

    def test_directory_at_txt_path_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            Image.new("RGB", (2, 2)).save(folder / "image.png")
            (folder / "image.txt").mkdir()
            with self.assertRaises(IsADirectoryError):
                tagger.find_images_without_txt(folder)

    def test_failed_atomic_replacement_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tags.txt"
            path.write_text("original")
            with patch.object(tagger.os, "replace", side_effect=PermissionError("locked")):
                with self.assertRaises(PermissionError):
                    tagger.write_tags_atomically(path, "replacement")
            self.assertEqual(path.read_text(), "original")
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_empty_prediction_does_not_create_a_tag_file(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            Image.new("RGB", (2, 2)).save(folder / "image.png")
            model = {"repo_id": "test", "device": "cpu", "threshold_description": "test"}
            with patch.object(tagger, "IMAGE_FOLDER", folder), patch.object(tagger, "load_preferred_model", return_value=model), patch.object(tagger, "predict_tags", return_value=[]), patch.object(sys, "argv", ["tagger"]):
                with self.assertRaises(SystemExit) as caught:
                    tagger.main()
            self.assertEqual(caught.exception.code, 1)
            self.assertFalse((folder / "image.txt").exists())
            self.assertEqual(tagger.find_images_without_txt(folder), [folder / "image.png"])

    def test_exif_orientation_is_applied_before_padding(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "image.png"
            image = Image.new("RGB", (2, 3), "red")
            image.putpixel((0, 0), (0, 0, 255))
            exif = Image.Exif()
            exif[274] = 6
            image.save(path, exif=exif)
            prepared = tagger.prepare_image(path)
            self.assertEqual(prepared.size, (3, 3))
            self.assertEqual(prepared.getpixel((2, 0)), (0, 0, 255))
            self.assertEqual(prepared.getpixel((0, 2)), (255, 255, 255))

    def test_metadata_is_preserved_with_leading_whitespace(self):
        from source_tag_file import MARKER, read_source_metadata
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tags.txt"
            suffix = '\n' + MARKER + '{"id": 123}\n'
            path.write_text("  1girl  " + suffix, encoding="utf-8")
            tagger.append_missing_tags(path, ["blue hair"])
            content = path.read_text(encoding="utf-8")
            self.assertEqual(content, "1girl, blue hair" + suffix)
            self.assertEqual(read_source_metadata(content), {"id": 123})

    def test_invalid_scores_are_rejected_before_tags_are_returned(self):
        model = {"tag_names": ["1girl"], "general_indexes": [0], "character_indexes": [], "best_thresholds": [0.35]}
        for scores in (np.array([np.nan]), np.array([np.inf]), np.array([-0.1]), np.array([1.1]), np.array([[0.5]])):
            with self.subTest(scores=scores), patch.object(tagger, "prepare_image"), patch.object(tagger, "predict_scores", return_value=scores):
                with self.assertRaises(ValueError):
                    tagger.predict_tags(Path("unused"), model)

    def test_rating_exclusion_and_high_confidence_override(self):
        model = {"tag_names": ["safe", "1girl", "character"], "general_indexes": [1], "character_indexes": [2], "best_thresholds": [0.35, 0.35, 0.85]}
        with patch.object(tagger, "prepare_image"), patch.object(tagger, "predict_scores", return_value=np.array([0.99, 0.96, 0.9])):
            self.assertEqual(tagger.predict_tags(Path("unused"), model), ["1girl", "character"])
            self.assertEqual(tagger.predict_tags(Path("unused"), model, threshold=0.95), ["1girl"])

    def test_invalid_primary_thresholds_use_category_defaults(self):
        labels = tagger.pd.DataFrame({"name": ["a", "b", "c", "d", "e"], "category": [0, 4, 0, 4, 0], "best_threshold": [float("inf"), -0.1, 1.1, "", 0.7]})
        with patch.object(tagger, "validate_model_config"), patch.object(tagger, "resolve_cached_file", side_effect=["config-cache/config.json", "weight-cache/model.safetensors"]), patch.object(tagger, "download_model_files", return_value="labels.csv"), patch.object(tagger.pd, "read_csv", return_value=labels), patch.object(tagger.timm, "create_model", return_value=Mock()):
            model = tagger.load_model_and_labels(local_files_only=True)
            self.assertEqual(model["best_thresholds"], [0.38, 0.51, 0.38, 0.51, 0.7])

    def test_failed_image_stops_pipeline_and_keeps_successful_output(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for name in ("bad.png", "good.png"):
                Image.new("RGB", (2, 2)).save(folder / name)
            model = {"repo_id": "test", "device": "cpu", "threshold_description": "test"}
            with patch.object(tagger, "IMAGE_FOLDER", folder), patch.object(tagger, "load_preferred_model", return_value=model), patch.object(tagger, "predict_tags", side_effect=[ValueError("invalid scores"), ["1girl"]]), patch.object(sys, "argv", ["tagger"]):
                with self.assertRaises(SystemExit) as caught:
                    tagger.main()
            self.assertEqual(caught.exception.code, 1)
            self.assertFalse((folder / "bad.txt").exists())
            self.assertEqual((folder / "good.txt").read_text(), "1girl")

    def test_direct_cached_file_never_contacts_hub(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "config.json").write_text("{}")
            with patch.object(tagger, "hf_hub_download") as download:
                self.assertEqual(tagger.resolve_cached_file("owner/model", "config.json", folder), str(folder / "config.json"))
                download.assert_not_called()

    def test_default_hub_cache_is_checked(self):
        with patch.object(tagger, "hf_hub_download", side_effect=[tagger.LocalEntryNotFoundError("missing"), "global/config.json"]) as download:
            self.assertEqual(tagger.resolve_cached_file("owner/model", "config.json", Path("project")), "global/config.json")
            self.assertEqual(download.call_args.kwargs, {"cache_dir": None, "local_files_only": True})

    def test_cached_weights_are_loaded_from_their_actual_path(self):
        with patch.object(tagger, "validate_model_config") as validate, patch.object(tagger, "resolve_cached_file", side_effect=["config-cache/config.json", "weight-cache/model.safetensors"]), patch.object(tagger, "download_model_files", return_value="labels.csv"), patch.object(tagger.pd, "read_csv", return_value=tagger.pd.DataFrame({"name": ["nan"], "category": [0]})), patch.object(tagger.timm, "create_model", return_value=Mock()) as create:
            model = tagger.load_model_and_labels(local_files_only=True)
            self.assertEqual(create.call_args.args[0], "local-dir:config-cache")
            self.assertFalse(create.call_args.kwargs["pretrained"])
            self.assertEqual(create.call_args.kwargs["checkpoint_path"], "weight-cache/model.safetensors")
            self.assertEqual(model["tag_names"], ["nan"])
            validate.assert_called_once_with("config-cache/config.json", ["nan"])

    def test_fallback_reuses_cached_files(self):
        import onnxruntime as ort
        session = Mock()
        session.get_providers.return_value = ["CPUExecutionProvider"]
        labels = tagger.pd.DataFrame({"name": ["safe", "1girl", "character"], "category": [9, 0, 4]})
        with patch.object(tagger, "resolve_cached_file", side_effect=["labels.csv", "model.onnx"]), patch.object(tagger, "hf_hub_download") as download, patch.object(tagger.pd, "read_csv", return_value=labels), patch.object(ort, "InferenceSession", return_value=session):
            model = tagger.load_fallback_model()
            download.assert_not_called()
            self.assertEqual(model["general_indexes"], [1])
            self.assertEqual(model["character_indexes"], [2])
            self.assertEqual(model["best_thresholds"], [0.35, 0.35, 0.85])

    def test_cached_primary_wins(self):
        with patch.object(tagger, "load_model_and_labels", return_value="primary") as primary, patch.object(tagger, "load_fallback_model") as fallback:
            self.assertEqual(tagger.load_preferred_model(), "primary")
            primary.assert_called_once_with(local_files_only=True)
            fallback.assert_not_called()

    def test_accessible_primary_downloads_when_not_cached(self):
        with patch.object(tagger, "load_model_and_labels", side_effect=[tagger.LocalEntryNotFoundError("missing"), "primary"]) as primary, patch.object(tagger, "load_fallback_model") as fallback:
            self.assertEqual(tagger.load_preferred_model(), "primary")
            self.assertEqual(primary.call_count, 2)
            fallback.assert_not_called()

    def test_denied_primary_uses_fallback(self):
        from model_access import GatedRepoError
        denial = GatedRepoError("denied", response=Mock(status_code=403, headers={}))
        with patch.object(tagger, "load_model_and_labels", side_effect=[tagger.LocalEntryNotFoundError("missing"), denial]), patch.object(tagger, "load_fallback_model", return_value="fallback") as fallback:
            self.assertEqual(tagger.load_preferred_model(), "fallback")
            fallback.assert_called_once_with()

    def test_network_failure_does_not_switch(self):
        with patch.object(tagger, "load_model_and_labels", side_effect=[tagger.LocalEntryNotFoundError("missing"), ConnectionError("offline")]), patch.object(tagger, "load_fallback_model") as fallback:
            with self.assertRaises(ConnectionError):
                tagger.load_preferred_model()
            fallback.assert_not_called()

    def test_onnx_receives_bgr_and_returns_probabilities_unchanged(self):
        session = Mock()
        session.get_inputs.return_value = [Mock(name="input", shape=[None, 2, 2, 3])]
        session.get_inputs.return_value[0].name = "input"
        scores = np.array([[0.1, 0.9]], dtype=np.float32)
        session.run.return_value = [scores]
        result = tagger.predict_scores(Image.new("RGB", (2, 2), (10, 20, 30)), {"onnx_session": session})
        batch = session.run.call_args.args[1]["input"]
        self.assertEqual(batch.shape, (1, 2, 2, 3))
        self.assertEqual(batch.dtype, np.float32)
        np.testing.assert_array_equal(batch[0, 0, 0], [30, 20, 10])
        np.testing.assert_array_equal(result, scores[0])


if __name__ == "__main__":
    unittest.main()
