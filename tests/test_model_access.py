import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "data"))

from model_access import GatedRepoError, HfHubHTTPError, LocalEntryNotFoundError, ModelAccessError, load_with_access_error


class ModelAccessTests(unittest.TestCase):
    def test_wrapped_403_is_detected(self):
        error = LocalEntryNotFoundError("download unavailable")
        error.__cause__ = HfHubHTTPError("forbidden", response=Mock(status_code=403, headers={}))
        with self.assertRaises(ModelAccessError):
            load_with_access_error(Mock(side_effect=error), "owner/model")

    def test_denials_include_recovery_instructions(self):
        for error in (
            GatedRepoError("gated", response=Mock(status_code=403, headers={})),
            HfHubHTTPError("unauthorized", response=Mock(status_code=401, headers={})),
            HfHubHTTPError("forbidden", response=Mock(status_code=403, headers={})),
        ):
            with self.subTest(error=error):
                with self.assertRaises(ModelAccessError) as caught:
                    load_with_access_error(Mock(side_effect=error), "owner/model")
                self.assertIn("https://huggingface.co/owner/model", str(caught.exception))
                self.assertIn("hf auth login", str(caught.exception))
                self.assertIs(caught.exception.__cause__, error)

    def test_other_failures_are_not_access_denials(self):
        for error in (
            HfHubHTTPError("server failure", response=Mock(status_code=500, headers={})),
            HfHubHTTPError("missing", response=Mock(status_code=404, headers={})),
            ConnectionError("offline"),
        ):
            with self.subTest(error=error):
                with self.assertRaises(type(error)) as caught:
                    load_with_access_error(Mock(side_effect=error), "owner/model")
                self.assertIs(caught.exception, error)

    def test_success_returns_loaded_model(self):
        model = object()
        self.assertIs(load_with_access_error(lambda: model, "owner/model"), model)


if __name__ == "__main__":
    unittest.main()
