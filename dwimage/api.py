from __future__ import annotations

import base64
import json
import mimetypes
from dataclasses import asdict
from pathlib import Path
from typing import Any

import requests

from .models import (
    ApiProfile,
    GenerationRequest,
    GenerationResult,
    ImageGenerationApiMode,
    RequestLogLevel,
)
from .storage import AppStorage


class ApiException(RuntimeError):
    pass


class ImageApiClient:
    def __init__(self, storage: AppStorage) -> None:
        self.storage = storage

    def generate(
        self,
        request: GenerationRequest,
        profile: ApiProfile,
        timeout_seconds: int,
    ) -> list[GenerationResult]:
        if not profile.api_key.strip():
            raise ApiException("API Key 不能为空。")
        if not request.prompt.strip():
            raise ApiException("提示词不能为空。")

        if profile.api_mode == ImageGenerationApiMode.RESPONSES:
            return self._generate_with_responses(request, profile, timeout_seconds)
        return self._generate_with_images(request, profile, timeout_seconds)

    def _generate_with_images(
        self,
        request: GenerationRequest,
        profile: ApiProfile,
        timeout_seconds: int,
    ) -> list[GenerationResult]:
        endpoint = "/v1/images/edits" if request.has_attachments else "/v1/images/generations"
        url = f"{profile.normalized_base_url}{endpoint}"
        headers = {"Authorization": f"Bearer {profile.api_key.strip()}"}

        self.storage.append_log(
            RequestLogLevel.REQUEST,
            f"POST {url}",
            self._pretty_json(
                {
                    "mode": "images",
                    "payload": self._images_payload_preview(request, profile),
                }
            ),
        )

        if request.has_attachments:
            files: list[tuple[str, tuple[str, bytes, str]]] = []
            try:
                for image_path in request.image_paths:
                    path = Path(image_path)
                    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
                    files.append(("image[]", (path.name, path.read_bytes(), mime_type)))
                data = self._build_images_payload(request, profile)
                response = requests.post(
                    url,
                    headers=headers,
                    data=data,
                    files=files,
                    timeout=timeout_seconds,
                )
            finally:
                files.clear()
        else:
            payload = self._build_images_payload(request, profile)
            headers["Content-Type"] = "application/json"
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=timeout_seconds,
            )

        return self._parse_images_response(response, url)

    def _generate_with_responses(
        self,
        request: GenerationRequest,
        profile: ApiProfile,
        timeout_seconds: int,
    ) -> list[GenerationResult]:
        url = f"{profile.normalized_base_url}/v1/responses"
        payload = self._build_responses_payload(request, profile)
        headers = {
            "Authorization": f"Bearer {profile.api_key.strip()}",
            "Content-Type": "application/json",
        }

        self.storage.append_log(
            RequestLogLevel.REQUEST,
            f"POST {url}",
            self._pretty_json({"mode": "responses", "payload": payload}),
        )

        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=timeout_seconds,
        )
        return self._parse_responses_response(response, url)

    def _build_images_payload(self, request: GenerationRequest, profile: ApiProfile) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": profile.model.strip(),
            "prompt": request.prompt.strip(),
            "quality": request.quality.value,
            "output_format": request.output_format.value,
            "n": 1,
        }
        if request.api_size:
            payload["size"] = request.api_size
        return payload

    def _images_payload_preview(self, request: GenerationRequest, profile: ApiProfile) -> dict[str, Any]:
        payload = self._build_images_payload(request, profile)
        if request.has_attachments:
            payload["image_count"] = len(request.image_paths)
            payload["images"] = [Path(item).name for item in request.image_paths]
        return payload

    def _build_responses_payload(self, request: GenerationRequest, profile: ApiProfile) -> dict[str, Any]:
        action = "edit" if request.has_attachments else "generate"
        input_content: list[dict[str, Any]] = [{"type": "input_text", "text": request.prompt.strip()}]
        for image_path in request.image_paths:
            input_content.append({"type": "input_image", "image_url": self._image_path_to_data_url(image_path)})

        tool: dict[str, Any] = {
            "type": "image_generation",
            "action": action,
            "quality": request.quality.value,
            "output_format": request.output_format.value,
        }
        if request.api_size:
            tool["size"] = request.api_size

        return {
            "model": profile.model.strip(),
            "input": [{"type": "message", "role": "user", "content": input_content}],
            "tools": [tool],
            "tool_choice": "required",
        }

    def _parse_images_response(self, response: requests.Response, url: str) -> list[GenerationResult]:
        data = self._decode_response_json(response, url)
        items = data.get("data")
        if not isinstance(items, list) or not items:
            raise ApiException("Images API 没有返回图片结果。")

        results: list[GenerationResult] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            result = GenerationResult(
                b64_json=self._clean_string(item.get("b64_json")),
                image_url=self._clean_string(item.get("url")),
                raw_response_value=self._pretty_json(item),
            )
            if result.b64_json or result.image_url:
                results.append(result)

        if not results:
            raise ApiException("Images API 返回成功，但没有可用的图片数据。")
        return results

    def _parse_responses_response(self, response: requests.Response, url: str) -> list[GenerationResult]:
        data = self._decode_response_json(response, url)
        output = data.get("output")
        if not isinstance(output, list) or not output:
            raise ApiException("Responses API 没有返回 output 图片结果。")

        results: list[GenerationResult] = []
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "image_generation_call":
                continue
            b64_json = self._extract_result_base64(item.get("result"))
            if not b64_json:
                continue
            results.append(
                GenerationResult(
                    b64_json=b64_json,
                    raw_response_value=self._pretty_json(item),
                )
            )

        if not results:
            raise ApiException("Responses API 返回成功，但没有找到 image_generation_call 结果。")
        return results

    def _decode_response_json(self, response: requests.Response, url: str) -> dict[str, Any]:
        response_text = response.text
        try:
            data = response.json()
        except ValueError:
            data = {"raw_text": response_text}

        if response.ok:
            self.storage.append_log(
                RequestLogLevel.RESPONSE,
                f"POST {url}",
                self._pretty_json({"status_code": response.status_code, "body": data}),
            )
            if isinstance(data, dict):
                return data
            raise ApiException("接口返回格式异常。")

        message = self._extract_error_message(data, response_text, response.status_code)
        self.storage.append_log(
            RequestLogLevel.ERROR,
            f"POST {url}",
            self._pretty_json(
                {
                    "status_code": response.status_code,
                    "error": message,
                    "body": data,
                }
            ),
        )
        raise ApiException(message)

    def _extract_error_message(self, data: Any, response_text: str, status_code: int) -> str:
        if isinstance(data, dict):
            error = data.get("error")
            if isinstance(error, dict) and isinstance(error.get("message"), str):
                return f"HTTP {status_code}: {error['message']}"
            if isinstance(data.get("message"), str):
                return f"HTTP {status_code}: {data['message']}"
        text = response_text.strip()
        return f"HTTP {status_code}: {text or '请求失败'}"

    def _image_path_to_data_url(self, image_path: str) -> str:
        path = Path(image_path)
        mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
        encoded = base64.b64encode(path.read_bytes()).decode("utf-8")
        return f"data:{mime_type};base64,{encoded}"

    def _extract_result_base64(self, value: Any) -> str | None:
        if isinstance(value, str):
            cleaned = value.strip()
            return cleaned or None
        if isinstance(value, dict):
            for key in ("b64_json", "base64", "image", "data"):
                raw = value.get(key)
                if isinstance(raw, str) and raw.strip():
                    return raw.strip()
        return None

    def _clean_string(self, value: Any) -> str | None:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return None

    def _pretty_json(self, value: Any) -> str:
        try:
            return json.dumps(value, ensure_ascii=False, indent=2)
        except TypeError:
            return json.dumps(asdict(value), ensure_ascii=False, indent=2)
