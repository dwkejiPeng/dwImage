from __future__ import annotations

import os
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from typing import Callable

from PIL import Image

from .api import ImageApiClient
from .image_store import ImageStore
from .models import (
    ApiProfile,
    AttachmentBatchMode,
    GenerationRequest,
    ImageRecord,
    ImageRecordStatus,
    RequestLogLevel,
    SettingsModel,
)
from .prompt_opt import PromptOptimizer
from .storage import AppStorage


class MintImageService:
    def __init__(self, storage: AppStorage) -> None:
        self.storage = storage
        self.image_api = ImageApiClient(storage)
        self.image_store = ImageStore()
        self.prompt_optimizer = PromptOptimizer(storage)

    def optimize_prompt(
        self,
        prompt: str,
        direction: str,
        settings: SettingsModel,
    ) -> str:
        profile = settings.active_prompt_optimization_profile
        if profile is None:
            raise RuntimeError("未配置提示词优化资料。")
        return self.prompt_optimizer.optimize(
            prompt=prompt,
            direction=direction,
            profile=profile,
            timeout_seconds=settings.request_timeout_seconds,
        )

    def submit_generation(
        self,
        request: GenerationRequest,
        settings: SettingsModel,
        progress_callback: Callable[[dict], None] | None = None,
    ) -> dict:
        return self.submit_generation_batch([request], settings, progress_callback)

    def submit_generation_batch(
        self,
        requests: list[GenerationRequest],
        settings: SettingsModel,
        progress_callback: Callable[[dict], None] | None = None,
    ) -> dict:
        if not requests:
            return {
                "records": [],
                "total_groups": 0,
                "total_tasks": 0,
                "succeeded_tasks": 0,
                "failed_tasks": 0,
            }

        overall_groups = 0
        overall_tasks = 0
        for request in requests:
            overall_groups += len(request.expand_for_submission())
            overall_tasks += sum(item.count for item in request.expand_for_submission())

        if progress_callback:
            progress_callback(
                {
                    "type": "batch_started",
                    "total_groups": overall_groups,
                    "total_tasks": overall_tasks,
                    "split_mode": any(item.should_split_attachments for item in requests),
                    "attachment_count": max((len(item.image_paths) for item in requests), default=0),
                    "count_per_group": max((item.count for item in requests), default=1),
                    "prompt": requests[0].prompt,
                }
            )

        all_records: list[ImageRecord] = []
        completed_tasks = 0
        succeeded_tasks = 0
        failed_tasks = 0
        global_group_index = 0
        for request in requests:
            summary = self._submit_single_request(
                request=request,
                settings=settings,
                start_group_index=global_group_index,
                start_task_index=completed_tasks,
                total_groups=overall_groups,
                total_tasks=overall_tasks,
                progress_callback=progress_callback,
            )
            all_records.extend(summary["records"])
            completed_tasks += int(summary["total_tasks"])
            succeeded_tasks += int(summary["succeeded_tasks"])
            failed_tasks += int(summary["failed_tasks"])
            global_group_index += int(summary["total_groups"])

        result = {
            "records": all_records,
            "total_groups": overall_groups,
            "total_tasks": overall_tasks,
            "succeeded_tasks": succeeded_tasks,
            "failed_tasks": failed_tasks,
        }
        if progress_callback:
            progress_callback({"type": "batch_finished", **result})
        return result

    def _submit_single_request(
        self,
        request: GenerationRequest,
        settings: SettingsModel,
        start_group_index: int,
        start_task_index: int,
        total_groups: int,
        total_tasks: int,
        progress_callback: Callable[[dict], None] | None = None,
    ) -> dict:
        profile = self._find_profile(settings, request.api_profile_id)
        submitted_requests = request.expand_for_submission()
        local_total_groups = len(submitted_requests)
        local_total_tasks = sum(item.count for item in submitted_requests)
        records: list[ImageRecord] = []
        completed_tasks = start_task_index
        succeeded_tasks = 0
        failed_tasks = 0
        for local_group_index, expanded_request in enumerate(submitted_requests, start=1):
            group_index = start_group_index + local_group_index
            for copy_index in range(1, expanded_request.count + 1):
                single_request = replace(expanded_request, count=1, attachment_batch_mode=AttachmentBatchMode.COMBINED)
                record = ImageRecord.pending(single_request, profile.model)
                self.storage.upsert_record(record)
                if progress_callback:
                    progress_callback(
                        {
                            "type": "task_started",
                            "record_id": record.id,
                            "task_index": completed_tasks + 1,
                            "total_tasks": total_tasks,
                            "group_index": group_index,
                            "total_groups": total_groups,
                            "copy_index": copy_index,
                            "copies_in_group": expanded_request.count,
                            "source_image_path": single_request.image_paths[0] if single_request.image_paths else None,
                        }
                    )
                final_record = self._execute_single_task(
                    record,
                    single_request,
                    profile,
                    settings.request_timeout_seconds,
                )
                records.append(final_record)
                completed_tasks += 1
                if final_record.status == ImageRecordStatus.DONE:
                    succeeded_tasks += 1
                else:
                    failed_tasks += 1
                if progress_callback:
                    progress_callback(
                        {
                            "type": "task_finished",
                            "record_id": final_record.id,
                            "task_index": completed_tasks,
                            "total_tasks": total_tasks,
                            "group_index": group_index,
                            "total_groups": total_groups,
                            "copy_index": copy_index,
                            "copies_in_group": expanded_request.count,
                            "status": final_record.status.value,
                            "error_message": final_record.error_message,
                            "result_image_path": final_record.result_image_path,
                            "succeeded_tasks": succeeded_tasks,
                            "failed_tasks": failed_tasks,
                        }
                    )
        return {
            "records": records,
            "total_groups": local_total_groups,
            "total_tasks": local_total_tasks,
            "succeeded_tasks": succeeded_tasks,
            "failed_tasks": failed_tasks,
        }

    def _execute_single_task(
        self,
        record: ImageRecord,
        request: GenerationRequest,
        profile: ApiProfile,
        timeout_seconds: int,
    ) -> ImageRecord:
        started_at = time.perf_counter()
        loading_record = replace(record, status=ImageRecordStatus.LOADING)
        self.storage.upsert_record(loading_record)
        try:
            results = self.image_api.generate(request, profile, timeout_seconds)
            if not results:
                raise RuntimeError("接口没有返回图片结果。")
            result = results[0]
            image_path, image_url = self.image_store.store_result(
                record.id,
                result,
                request.output_format,
            )
            completed = replace(
                loading_record,
                result_image_path=image_path,
                result_image_url=image_url,
                result_b64=result.b64_json,
                raw_api_response_value=result.raw_response_value,
                status=ImageRecordStatus.DONE,
                duration_ms=int((time.perf_counter() - started_at) * 1000),
                used_single_image_fallback=result.retried_with_single_image,
            )
            if image_path and (loading_record.width <= 0 or loading_record.height <= 0):
                width, height = self._read_image_size(image_path)
                completed = replace(completed, width=width, height=height)
            self.storage.upsert_record(completed)
            return completed
        except Exception as exc:
            errored = replace(
                loading_record,
                status=ImageRecordStatus.ERROR,
                error_message=str(exc),
                duration_ms=int((time.perf_counter() - started_at) * 1000),
            )
            self.storage.upsert_record(errored)
            self.storage.append_log(RequestLogLevel.ERROR, "生成失败", str(exc))
            return errored

    def save_clipboard_image(self, image: Image.Image) -> str:
        temp_dir = Path(tempfile.gettempdir()) / "dwimage_paste"
        temp_dir.mkdir(parents=True, exist_ok=True)
        target = temp_dir / f"pasted_{int(time.time() * 1000)}.png"
        image.save(target, format="PNG")
        return str(target)

    def normalize_image_files(self, paths: list[str]) -> list[str]:
        result: list[str] = []
        for path in paths:
            if not path:
                continue
            normalized = os.path.abspath(path)
            if not os.path.isfile(normalized):
                continue
            suffix = Path(normalized).suffix.lower()
            if suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"} and normalized not in result:
                result.append(normalized)
        return result

    def _read_image_size(self, image_path: str) -> tuple[int, int]:
        with Image.open(image_path) as image:
            return image.size

    def _find_profile(self, settings: SettingsModel, profile_id: str) -> ApiProfile:
        for profile in settings.profiles:
            if profile.id == profile_id:
                return profile
        return settings.active_profile
