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


def _response_object(payload, context: str) -> dict:
    """Validate decoded API objects before callers inspect their fields."""
    if not isinstance(payload, dict):
        raise RuntimeError(f"Ollama returned an invalid response object {context}")
    if payload.get("error"):
        raise RuntimeError(f"Ollama returned an error {context}: {payload['error']}")
    return payload


def _response_json(response, context: str) -> dict:
    try:
        payload = response.json()
    except ValueError as error:
        raise RuntimeError(f"Ollama returned invalid JSON {context}") from error
    return _response_object(payload, context)


def _model_names(response, context: str) -> list[str]:
    models = _response_json(response, context).get("models")
    if not isinstance(models, list):
        raise RuntimeError(f"Ollama returned an invalid model list {context}")
    names = []
    for item in models:
        name = (item.get("name") or item.get("model")) if isinstance(item, dict) else None
        if not isinstance(name, str) or not name.strip():
            raise RuntimeError(f"Ollama returned an invalid model name {context}")
        names.append(name)
    return names


class OllamaClient:
    def __init__(self, url: str, timeout: int):
        self.url = url.rstrip("/")
        self.timeout = timeout
        self._verified_models: set[str] = set()
        self._encoded_image_key = None
        self._encoded_image = None

    @staticmethod
    def inference_image(path: Path, max_size=(1024, 1024)) -> Image.Image:
        """Return the oriented RGB preview used for inference, without cropping."""
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail(max_size)
            return image

    @staticmethod
    def image_to_base64(path: Path, max_size=(1024, 1024)) -> str:
        """Encode a resized inference copy as RGB JPEG; preserve the source file."""
        with OllamaClient.inference_image(path, max_size) as image:
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
                payload = response.json()
                detail = payload.get("error") if isinstance(payload, dict) else None
                detail = detail or response.text
            except ValueError:
                detail = response.text
            detail = str(detail).strip() or "no error details returned"
            raise RuntimeError(
                f"Ollama returned HTTP {response.status_code}: {detail}"
            ) from error

    def running_model(self) -> str:
        try:
            response = requests.get(
                f"{self.url}/api/ps", timeout=min(self.timeout, 3)
            )
            self._raise_for_status(response)
            models = _model_names(response, "while finding the loaded model")
        except (requests.RequestException, RuntimeError) as error:
            raise OllamaUnavailableError(
                f"Could not query Ollama at {self.url}: {error}"
            ) from error
        if not models:
            raise RuntimeError("Ollama has no model currently loaded")
        return models[0]

    def ensure_model(self, model: str, status=print) -> None:
        if model in self._verified_models:
            return
        try:
            response = requests.get(
                f"{self.url}/api/tags", timeout=min(self.timeout, 10)
            )
            self._raise_for_status(response)
            installed = {name.casefold() for name in _model_names(response, "while listing models")}
        except (requests.RequestException, RuntimeError) as error:
            raise OllamaUnavailableError(
                f"Could not reach Ollama at {self.url}: {error}"
            ) from error

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
                            update = _response_object(json.loads(raw_line), f"while pulling {model}")
                        except (TypeError, ValueError) as error:
                            raise OllamaUnavailableError(
                                f"Ollama returned invalid JSON while pulling {model}"
                            ) from error
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
        think: bool = False,
        response_format: str | dict | None = None,
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
                    "think": think,
                    **({"format": response_format} if response_format is not None else {}),
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
        payload = _response_json(response, "while generating a response")
        message = payload.get("message", {})
        if not isinstance(message, dict) or not isinstance(message.get("content", ""), str):
            raise RuntimeError("Ollama returned invalid message content")
        text = message.get("content", "").strip()
        if not text and payload.get("done_reason") != "length" and not (think and message.get("thinking")):
            raise RuntimeError(
                "Ollama returned an empty response "
                f"(done_reason={payload.get('done_reason', 'unknown')})"
            )
        return text, payload
