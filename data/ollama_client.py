"""Shared Ollama API and image-encoding helpers."""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import requests
from PIL import Image


class OllamaClient:
    def __init__(self, url: str, timeout: int):
        self.url = url.rstrip("/")
        self.timeout = timeout
        self._verified_models: set[str] = set()

    @staticmethod
    def image_to_base64(path: Path, max_size=(1024, 1024)) -> str:
        with Image.open(path) as image:
            image = image.convert("RGB")
            image.thumbnail(max_size)
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=95)
        return base64.b64encode(buffer.getvalue()).decode("ascii")

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
        except requests.RequestException as error:
            raise RuntimeError(
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
                    for raw_line in pull_response.iter_lines():
                        if not raw_line:
                            continue
                        try:
                            update = json.loads(raw_line)
                        except (TypeError, ValueError) as error:
                            raise RuntimeError(
                                f"Ollama returned invalid JSON while pulling {model}"
                            ) from error
                        if update.get("error"):
                            raise RuntimeError(
                                f"Ollama could not pull {model}: {update['error']}"
                            )
                        current = update.get("status")
                        if current and current != last_status:
                            status(f"Ollama pull: {current}")
                            last_status = current
            except requests.RequestException as error:
                raise RuntimeError(f"Failed to pull Ollama model {model}: {error}") from error
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
            user_message["images"] = [self.image_to_base64(image_path)]
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
        except requests.RequestException as error:
            raise RuntimeError(f"Ollama request failed: {error}") from error
        payload = response.json()
        text = payload.get("message", {}).get("content", "").strip()
        if not text:
            raise RuntimeError(
                "Ollama returned an empty response "
                f"(done_reason={payload.get('done_reason', 'unknown')})"
            )
        return text, payload
