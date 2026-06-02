from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4


class ImageGenerationApiMode(str, Enum):
    IMAGES = "images"
    RESPONSES = "responses"

    @property
    def default_model(self) -> str:
        return {
            ImageGenerationApiMode.IMAGES: "gpt-image-2",
            ImageGenerationApiMode.RESPONSES: "gpt-5.5",
        }[self]


class PromptOptimizationProtocol(str, Enum):
    OPENAI_CHAT = "openai_chat_completions"
    OPENAI_RESPONSES = "openai_responses"
    CLAUDE = "claude_messages"
    GEMINI = "gemini_generate_content"


class SizePreset(str, Enum):
    AUTO = "auto"
    SQUARE_1K = "square-1k"
    POSTER_PORTRAIT = "poster-portrait"
    POSTER_LANDSCAPE = "poster-landscape"
    STORY_916 = "story-9-16"
    VIDEO_169 = "video-16-9"
    WIDE_2K = "wide-2k"
    PORTRAIT_2K = "portrait-2k"
    SQUARE_2K = "square-2k"
    PORTRAIT_4K = "portrait-4k"
    WIDE_4K = "wide-4k"
    CUSTOM = "custom"

    @property
    def size(self) -> tuple[int, int]:
        return {
            SizePreset.AUTO: (0, 0),
            SizePreset.SQUARE_1K: (1024, 1024),
            SizePreset.POSTER_PORTRAIT: (1024, 1536),
            SizePreset.POSTER_LANDSCAPE: (1536, 1024),
            SizePreset.STORY_916: (1088, 1920),
            SizePreset.VIDEO_169: (1920, 1088),
            SizePreset.WIDE_2K: (2560, 1440),
            SizePreset.PORTRAIT_2K: (1440, 2560),
            SizePreset.SQUARE_2K: (2048, 2048),
            SizePreset.PORTRAIT_4K: (2160, 3840),
            SizePreset.WIDE_4K: (3840, 2160),
            SizePreset.CUSTOM: (1024, 1024),
        }[self]


class ImageQuality(str, Enum):
    AUTO = "auto"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ImageOutputFormat(str, Enum):
    PNG = "png"
    JPEG = "jpeg"
    WEBP = "webp"


class AttachmentBatchMode(str, Enum):
    COMBINED = "combined"
    SPLIT_PER_IMAGE = "split_per_image"


class ImageRecordStatus(str, Enum):
    PENDING = "pending"
    LOADING = "loading"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


class RequestLogLevel(str, Enum):
    INFO = "info"
    REQUEST = "request"
    RESPONSE = "response"
    ERROR = "error"


@dataclass
class ApiProfile:
    id: str
    name: str
    base_url: str
    api_key: str
    model: str
    api_mode: ImageGenerationApiMode = ImageGenerationApiMode.IMAGES

    @staticmethod
    def initial() -> "ApiProfile":
        mode = ImageGenerationApiMode.IMAGES
        return ApiProfile(
            id=str(uuid4()),
            name="Default",
            base_url="https://api.openai.com",
            api_key="",
            model=mode.default_model,
            api_mode=mode,
        )

    @property
    def normalized_base_url(self) -> str:
        return self.base_url.rstrip("/")


@dataclass
class PromptOptimizationProfile:
    id: str
    name: str
    base_url: str
    api_key: str
    model: str
    protocol: PromptOptimizationProtocol

    @property
    def normalized_base_url(self) -> str:
        return self.base_url.rstrip("/")


@dataclass
class GenerationRequest:
    prompt: str
    image_paths: list[str]
    size_preset: SizePreset
    custom_width: int
    custom_height: int
    quality: ImageQuality
    output_format: ImageOutputFormat
    count: int
    api_profile_id: str
    attachment_batch_mode: AttachmentBatchMode = AttachmentBatchMode.COMBINED

    @property
    def has_attachments(self) -> bool:
        return bool(self.image_paths)

    @property
    def is_auto_size(self) -> bool:
        return self.size_preset == SizePreset.AUTO

    @property
    def resolved_width(self) -> int:
        return self.custom_width if self.size_preset == SizePreset.CUSTOM else self.size_preset.size[0]

    @property
    def resolved_height(self) -> int:
        return self.custom_height if self.size_preset == SizePreset.CUSTOM else self.size_preset.size[1]

    @property
    def api_size(self) -> str | None:
        if self.is_auto_size:
            return None
        return f"{self.resolved_width}x{self.resolved_height}"

    @property
    def should_split_attachments(self) -> bool:
        return len(self.image_paths) > 1 and self.attachment_batch_mode == AttachmentBatchMode.SPLIT_PER_IMAGE

    @property
    def expected_task_count(self) -> int:
        return self.count * (len(self.image_paths) if self.should_split_attachments else 1)

    def expand_for_submission(self) -> list["GenerationRequest"]:
        if not self.should_split_attachments:
            return [self]
        return [
            GenerationRequest(
                prompt=self.prompt,
                image_paths=[path],
                size_preset=self.size_preset,
                custom_width=self.custom_width,
                custom_height=self.custom_height,
                quality=self.quality,
                output_format=self.output_format,
                count=self.count,
                api_profile_id=self.api_profile_id,
                attachment_batch_mode=AttachmentBatchMode.COMBINED,
            )
            for path in self.image_paths
        ]


@dataclass
class GenerationResult:
    b64_json: str | None = None
    image_url: str | None = None
    raw_response_value: str | None = None
    retried_with_single_image: bool = False


@dataclass
class ImageRecord:
    id: str
    prompt: str
    api_profile_id: str
    source_image_path: str | None
    source_image_paths: list[str]
    result_image_path: str | None
    result_image_url: str | None
    result_b64: str | None
    width: int
    height: int
    quality: str
    output_format: str
    model: str
    status: ImageRecordStatus
    error_message: str | None
    raw_api_response_value: str | None
    created_at: str
    duration_ms: int | None
    used_single_image_fallback: bool
    is_favorite: bool

    @staticmethod
    def pending(request: GenerationRequest, model: str, is_favorite: bool = False) -> "ImageRecord":
        return ImageRecord(
            id=str(uuid4()),
            prompt=request.prompt,
            api_profile_id=request.api_profile_id,
            source_image_path=request.image_paths[0] if request.image_paths else None,
            source_image_paths=list(request.image_paths),
            result_image_path=None,
            result_image_url=None,
            result_b64=None,
            width=request.resolved_width,
            height=request.resolved_height,
            quality=request.quality.value,
            output_format=request.output_format.value,
            model=model,
            status=ImageRecordStatus.PENDING,
            error_message=None,
            raw_api_response_value=None,
            created_at=datetime.now().isoformat(),
            duration_ms=None,
            used_single_image_fallback=False,
            is_favorite=is_favorite,
        )

    @property
    def source_attachment_paths(self) -> list[str]:
        return self.source_image_paths or ([self.source_image_path] if self.source_image_path else [])

    @property
    def can_retry(self) -> bool:
        return self.status == ImageRecordStatus.ERROR and bool(self.prompt.strip())

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "ImageRecord":
        data = dict(data)
        data["status"] = ImageRecordStatus(data["status"])
        return ImageRecord(**data)


@dataclass
class FavoriteFolder:
    id: str
    title: str
    is_default: bool
    created_at: str


@dataclass
class FavoriteFolderMembership:
    folder_id: str
    record_id: str
    created_at: str


@dataclass
class RequestLogEntry:
    timestamp: str
    level: RequestLogLevel
    title: str
    details: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "level": self.level.value,
            "title": self.title,
            "details": self.details,
        }


@dataclass
class SettingsModel:
    profiles: list[ApiProfile] = field(default_factory=lambda: [ApiProfile.initial()])
    active_profile_id: str = ""
    prompt_optimization_profiles: list[PromptOptimizationProfile] = field(default_factory=list)
    active_prompt_optimization_profile_id: str | None = None
    response_format: str | None = None
    request_timeout_seconds: int = 600
    last_size_preset: SizePreset = SizePreset.AUTO
    last_custom_width: int = 0
    last_custom_height: int = 0
    last_quality: ImageQuality = ImageQuality.AUTO
    last_output_format: ImageOutputFormat = ImageOutputFormat.PNG
    preview_info_collapsed: bool = False

    def __post_init__(self) -> None:
        if not self.active_profile_id and self.profiles:
            self.active_profile_id = self.profiles[0].id

    @property
    def active_profile(self) -> ApiProfile:
        for profile in self.profiles:
            if profile.id == self.active_profile_id:
                return profile
        return self.profiles[0]

    @property
    def active_prompt_optimization_profile(self) -> PromptOptimizationProfile | None:
        if not self.active_prompt_optimization_profile_id:
            return None
        for profile in self.prompt_optimization_profiles:
            if profile.id == self.active_prompt_optimization_profile_id:
                return profile
        return self.prompt_optimization_profiles[0] if self.prompt_optimization_profiles else None


def dataclass_to_jsonable(obj: Any) -> Any:
    if isinstance(obj, Enum):
        return obj.value
    if hasattr(obj, "__dataclass_fields__"):
        data = {}
        for key, value in asdict(obj).items():
            data[key] = dataclass_to_jsonable(value)
        return data
    if isinstance(obj, list):
        return [dataclass_to_jsonable(item) for item in obj]
    if isinstance(obj, dict):
        return {key: dataclass_to_jsonable(value) for key, value in obj.items()}
    return obj
