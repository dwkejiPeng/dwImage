from __future__ import annotations

import base64
from pathlib import Path
from urllib.parse import urlparse

import requests

from .models import GenerationResult, ImageOutputFormat
from .storage import OUTPUT_DIR


class ImageStore:
    def store_result(
        self,
        record_id: str,
        result: GenerationResult,
        output_format: ImageOutputFormat,
    ) -> tuple[str | None, str | None]:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        if result.b64_json:
            file_path = OUTPUT_DIR / f"{record_id}.{output_format.value}"
            file_path.write_bytes(base64.b64decode(result.b64_json))
            return str(file_path), result.image_url

        if result.image_url:
            local_path = self._download_image(record_id, result.image_url)
            if local_path:
                return local_path, None
            return None, result.image_url

        return None, result.image_url

    def _download_image(self, record_id: str, image_url: str) -> str | None:
        try:
            response = requests.get(image_url, timeout=120)
            response.raise_for_status()
        except requests.RequestException:
            return None

        suffix = self._resolve_suffix(image_url, response.headers.get("Content-Type"))
        file_path = OUTPUT_DIR / f"{record_id}.{suffix}"
        file_path.write_bytes(response.content)
        return str(file_path)

    def _resolve_suffix(self, image_url: str, content_type: str | None) -> str:
        parsed = urlparse(image_url)
        extension = Path(parsed.path).suffix.lstrip(".").lower()
        if extension and len(extension) <= 5:
            return extension

        content_type = (content_type or "").lower()
        if "jpeg" in content_type:
            return "jpg"
        if "webp" in content_type:
            return "webp"
        if "gif" in content_type:
            return "gif"
        return "png"
