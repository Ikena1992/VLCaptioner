"""Shared Ollama API and image-encoding helpers."""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import requests
from PIL import Image, ImageOps


class OllamaUnavailableError(RuntimeError):
    """A shared server failure that should stop the current pipeline."""


class OllamaClient:
    def __init__(self, url: str, timeout: int):
        self.url = url.rstrip("/")
        self.timeout = timeout
        self._verified_models: set[str] = set()
        self._encoded_image_key = None
        self._encoded_image = None

    @staticmethod
    def image_to_base64(path: Path, max_size=(1024, 1024)) -> str:
        """Encode a resized inference copy as RGB JPEG; preserve the source file."""
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image).convert("RGB")
            image.thumbnail(max_size)
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=95)
        return base64.b64encode(buffer.getvalue()).decode("ascii")

    def encoded_image(self, path: Path) -> str:
        """Reuse the current image across a stage's requests and retries.

        Stages process images sequentially, so keeping only the latest image
        avoids retaining a whole dataset in memory. File changes invalidate it.
        """
        path = Path(path).resolve()
        stat = path.stat()
        key = (path, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        if key != self._encoded_image_key:
            encoded = self.image_to_base64(path)
            self._encoded_image_key = key
            self._encoded_image = encoded
        return self._encoded_image

    @staticmethod
    def _raise_for_status(response) -> None:
        try:
            response.raise_for_status()
        except requests.HTTPError as error:
            try:
                detail = response.json().get("error", response.text)
            except ValueError:
                detail = response.text
            detail = str(detail).strip() or "no error details returned"
            raise RuntimeError(
                f"Ollama returned HTTP {response.status_code}: {detail}"
            ) from error

    def running_model(self) -> str:
        response = requests.get(
            f"{self.url}/api/ps", timeout=min(self.timeout, 3)
        )
        self._raise_for_status(response)
        models = response.json().get("models", [])
        if not models:
            raise RuntimeError("Ollama has no model currently loaded")
        model = models[0].get("name") or models[0].get("model")
        if not model:
            raise RuntimeError("Ollama did not return a name for the loaded model")
        return model

    def ensure_model(self, model: str, status=print) -> None:
        if model in self._verified_models:
            return
        try:
            response = requests.get(
                f"{self.url}/api/tags", timeout=min(self.timeout, 10)
            )
            self._raise_for_status(response)
        except (requests.RequestException, RuntimeError) as error:
            raise OllamaUnavailableError(
                f"Could not reach Ollama at {self.url}: {error}"
            ) from error

        installed = {
            name.casefold()
            for item in response.json().get("models", [])
            if (name := item.get("name") or item.get("model"))
        }
        if model.casefold() not in installed:
            status(f"Ollama model is not installed; pulling {model}...")
            try:
                with requests.post(
                    f"{self.url}/api/pull",
                    json={"model": model, "stream": True},
                    stream=True,
                    timeout=(10, self.timeout),
                ) as pull_response:
                    self._raise_for_status(pull_response)
                    last_status = None
                    completed = False
                    for raw_line in pull_response.iter_lines():
                        if not raw_line:
                            continue
                        try:
                            update = json.loads(raw_line)
                        except (TypeError, ValueError) as error:
                            raise OllamaUnavailableError(
                                f"Ollama returned invalid JSON while pulling {model}"
                            ) from error
                        if update.get("error"):
                            raise OllamaUnavailableError(
                                f"Ollama could not pull {model}: {update['error']}"
                            )
                        current = update.get("status")
                        if current == "success":
                            completed = True
                        if current and current != last_status:
                            status(f"Ollama pull: {current}")
                            last_status = current
                    if not completed:
                        raise OllamaUnavailableError(
                            f"Ollama model pull ended before completing {model}"
                        )
            except (requests.RequestException, RuntimeError) as error:
                raise OllamaUnavailableError(f"Failed to pull Ollama model {model}: {error}") from error
        self._verified_models.add(model)

    def chat(
        self,
        model: str,
        prompt: str,
        image_path: Path | None,
        options: dict,
        system_prompt: str | None = None,
    ) -> tuple[str, dict]:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        user_message = {"role": "user", "content": prompt}
        if image_path is not None:
            user_message["images"] = [self.encoded_image(image_path)]
        messages.append(user_message)
        try:
            response = requests.post(
                f"{self.url}/api/chat",
                json={
                    "model": model,
                    "stream": False,
                    "think": False,
                    "messages": messages,
                    "options": options,
                },
                timeout=self.timeout,
            )
            self._raise_for_status(response)
        except requests.Timeout as error:
            # A slow image is recoverable if the server still answers health requests.
            try:
                health = requests.get(f"{self.url}/api/tags", timeout=min(self.timeout, 5))
                self._raise_for_status(health)
            except (requests.RequestException, RuntimeError) as health_error:
                raise OllamaUnavailableError(f"Ollama is unavailable: {health_error}") from health_error
            raise RuntimeError("This image timed out; Ollama is still available") from error
        except requests.ConnectionError as error:
            raise OllamaUnavailableError(f"Ollama is unavailable: {error}") from error
        except requests.RequestException as error:
            raise RuntimeError(f"Ollama request failed: {error}") from error
        payload = response.json()
        text = payload.get("message", {}).get("content", "").strip()
        if not text and payload.get("done_reason") != "length":
            raise RuntimeError(
                "Ollama returned an empty response "
                f"(done_reason={payload.get('done_reason', 'unknown')})"
            )
        return text, payload
