from __future__ import annotations

import json
from typing import Any

import requests

from .api import ApiException
from .models import PromptOptimizationProfile, PromptOptimizationProtocol
from .models import RequestLogLevel
from .storage import AppStorage


class PromptOptimizationDirection:
    STRENGTHEN = "strengthen"
    EDGE_EXPLORE = "edge_explore"
    STRENGTHEN_TO_ENGLISH = "strengthen_to_english"
    CLASSICAL_CHINESE = "classical_chinese"
    POETIC = "poetic"


_DIRECTION_LABELS = {
    PromptOptimizationDirection.STRENGTHEN: "强化",
    PromptOptimizationDirection.EDGE_EXPLORE: "探索边界",
    PromptOptimizationDirection.STRENGTHEN_TO_ENGLISH: "强化后转英文",
    PromptOptimizationDirection.CLASSICAL_CHINESE: "转为文言文",
    PromptOptimizationDirection.POETIC: "诗意强化",
}


class PromptOptimizer:
    def __init__(self, storage: AppStorage) -> None:
        self.storage = storage

    def optimize(
        self,
        prompt: str,
        direction: str,
        profile: PromptOptimizationProfile,
        timeout_seconds: int,
    ) -> str:
        if not profile.api_key.strip():
            raise ApiException("提示词优化 API Key 不能为空。")
        if not prompt.strip():
            raise ApiException("提示词不能为空。")

        url, headers, payload = self._build_request(profile, prompt, direction)
        self.storage.append_log(RequestLogLevel.REQUEST, f"POST {url}", self._pretty_json(payload))

        response = requests.post(url, headers=headers, json=payload, timeout=timeout_seconds)
        try:
            data = response.json()
        except ValueError:
            data = {"raw_text": response.text}

        if not response.ok:
            raise ApiException(self._extract_error_message(data, response.text, response.status_code))

        self.storage.append_log(RequestLogLevel.RESPONSE, f"POST {url}", self._pretty_json(data))
        text = self._parse_text_response(data, profile.protocol).strip()
        if not text:
            raise ApiException("提示词优化接口没有返回有效文本。")
        return self._strip_code_fence(text)

    def _build_request(
        self,
        profile: PromptOptimizationProfile,
        prompt: str,
        direction: str,
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        system_prompt = self._system_prompt(direction)
        user_prompt = self._user_prompt(direction, prompt)

        if profile.protocol == PromptOptimizationProtocol.OPENAI_CHAT:
            return (
                self._append_path(profile.normalized_base_url, "v1/chat/completions"),
                {
                    "Authorization": f"Bearer {profile.api_key.strip()}",
                    "Content-Type": "application/json",
                },
                {
                    "model": profile.model.strip(),
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.7,
                },
            )

        if profile.protocol == PromptOptimizationProtocol.OPENAI_RESPONSES:
            return (
                self._append_path(profile.normalized_base_url, "v1/responses"),
                {
                    "Authorization": f"Bearer {profile.api_key.strip()}",
                    "Content-Type": "application/json",
                },
                {
                    "model": profile.model.strip(),
                    "input": [
                        {
                            "type": "message",
                            "role": "system",
                            "content": [{"type": "input_text", "text": system_prompt}],
                        },
                        {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": user_prompt}],
                        },
                    ],
                    "temperature": 0.7,
                },
            )

        if profile.protocol == PromptOptimizationProtocol.CLAUDE:
            return (
                self._append_path(profile.normalized_base_url, "v1/messages"),
                {
                    "x-api-key": profile.api_key.strip(),
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                {
                    "model": profile.model.strip(),
                    "max_tokens": 1200,
                    "temperature": 0.7,
                    "messages": [
                        {
                            "role": "user",
                            "content": [{"type": "text", "text": f"{system_prompt}\n\n{user_prompt}"}],
                        }
                    ],
                },
            )

        model = profile.model.strip()
        if not model.startswith("models/"):
            model = f"models/{model}"
        return (
            self._append_path(profile.normalized_base_url, f"v1beta/{model}:generateContent"),
            {
                "x-goog-api-key": profile.api_key.strip(),
                "Content-Type": "application/json",
            },
            {
                "contents": [
                    {
                        "role": "user",
                        "parts": [{"text": f"{system_prompt}\n\n{user_prompt}"}],
                    }
                ],
                "generationConfig": {"temperature": 0.7},
            },
        )

    def _parse_text_response(self, data: dict[str, Any], protocol: PromptOptimizationProtocol) -> str:
        output_text = data.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text

        if protocol == PromptOptimizationProtocol.OPENAI_CHAT:
            choices = data.get("choices")
            if isinstance(choices, list) and choices:
                first = choices[0]
                if isinstance(first, dict):
                    message = first.get("message")
                    if isinstance(message, dict):
                        return self._extract_content_text(message.get("content"))

        if protocol == PromptOptimizationProtocol.OPENAI_RESPONSES:
            buffer: list[str] = []
            output = data.get("output")
            if isinstance(output, list):
                for item in output:
                    if isinstance(item, dict):
                        text = self._extract_content_text(item.get("content"))
                        if text:
                            buffer.append(text)
            return "\n".join(buffer).strip()

        if protocol == PromptOptimizationProtocol.CLAUDE:
            return self._extract_content_text(data.get("content"))

        candidates = data.get("candidates")
        if isinstance(candidates, list) and candidates:
            first = candidates[0]
            if isinstance(first, dict):
                content = first.get("content")
                if isinstance(content, dict):
                    return self._extract_content_text(content.get("parts"))
        return ""

    def _extract_content_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str) and item.strip():
                    parts.append(item.strip())
                    continue
                if not isinstance(item, dict):
                    continue
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
            return "\n".join(parts).strip()
        return ""

    def _extract_error_message(self, data: Any, raw_text: str, status_code: int) -> str:
        if isinstance(data, dict):
            error = data.get("error")
            if isinstance(error, dict) and isinstance(error.get("message"), str):
                return f"HTTP {status_code}: {error['message']}"
            if isinstance(data.get("message"), str):
                return f"HTTP {status_code}: {data['message']}"
        text = raw_text.strip()
        return f"HTTP {status_code}: {text or '请求失败'}"

    def _append_path(self, base_url: str, path: str) -> str:
        return f"{base_url.rstrip('/')}/{path.lstrip('/')}"

    def _strip_code_fence(self, text: str) -> str:
        stripped = text.strip()
        if not stripped.startswith("```"):
            return stripped
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else stripped
        if stripped.endswith("```"):
            stripped = stripped[:-3]
        return stripped.strip()

    def _pretty_json(self, value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, indent=2)

    def _system_prompt(self, direction: str) -> str:
        style_instruction = {
            PromptOptimizationDirection.STRENGTHEN: "强化主体、构图、镜头语言、光影、色彩、材质、环境和细节。",
            PromptOptimizationDirection.EDGE_EXPLORE: "在合规安全范围内提升张力、风格辨识度和视觉冲击力。",
            PromptOptimizationDirection.STRENGTHEN_TO_ENGLISH: "先强化画面表达，再转写为自然专业的英文提示词。",
            PromptOptimizationDirection.CLASSICAL_CHINESE: "保留画面意图，转为凝练古雅的文言文风格。",
            PromptOptimizationDirection.POETIC: "强化细节并加入诗意氛围、意象和审美描述。",
        }.get(direction, "强化提示词表现力。")
        return (
            "你是专业的图像生成提示词优化器。"
            "请根据用户原始提示词输出一段更适合图像生成的提示词。"
            f"{style_instruction}"
            "不要输出解释、标题或 Markdown，只返回优化后的提示词正文。"
        )

    def _user_prompt(self, direction: str, prompt: str) -> str:
        return f"优化方向：{_DIRECTION_LABELS.get(direction, direction)}\n原始提示词：\n{prompt}\n\n请直接返回优化后的提示词。"
