from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from .models import (
    ApiProfile,
    FavoriteFolder,
    FavoriteFolderMembership,
    ImageGenerationApiMode,
    ImageRecord,
    ImageRecordStatus,
    PromptOptimizationProfile,
    PromptOptimizationProtocol,
    RequestLogEntry,
    RequestLogLevel,
    SettingsModel,
    SizePreset,
    ImageQuality,
    ImageOutputFormat,
)


APP_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "dwImage"
LEGACY_APP_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "MintImagePython"
DB_PATH = APP_DIR / "mint_image.db"
SETTINGS_PATH = APP_DIR / "settings.json"
LOG_PATH = APP_DIR / "request_logs.jsonl"
OUTPUT_DIR = APP_DIR / "generated_images"


def _enum_value(value: object) -> object:
    return value.value if hasattr(value, "value") else value


class AppStorage:
    def __init__(self) -> None:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._init_db()
        self._maybe_migrate_legacy_data()

    def _init_db(self) -> None:
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                """
                create table if not exists image_records (
                  id text primary key,
                  prompt text not null,
                  api_profile_id text not null,
                  source_image_path text,
                  source_image_paths text,
                  result_image_path text,
                  result_image_url text,
                  result_b64 text,
                  width integer not null,
                  height integer not null,
                  quality text not null,
                  output_format text not null,
                  model text not null,
                  status text not null,
                  error_message text,
                  raw_api_response_value text,
                  created_at text not null,
                  duration_ms integer,
                  used_single_image_fallback integer not null default 0,
                  is_favorite integer not null default 0
                )
                """
            )
            cur.execute(
                """
                create table if not exists favorite_folders (
                  id text primary key,
                  title text not null,
                  is_default integer not null default 0,
                  created_at text not null
                )
                """
            )
            cur.execute(
                """
                create table if not exists favorite_folder_items (
                  folder_id text not null,
                  record_id text not null,
                  created_at text not null,
                  primary key (folder_id, record_id)
                )
                """
            )
            self.conn.commit()
        self._ensure_default_folder()

    def _ensure_default_folder(self) -> None:
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("select 1 from favorite_folders where id = ?", ("default",))
            if cur.fetchone():
                return
            cur.execute(
                "insert into favorite_folders (id, title, is_default, created_at) values (?, ?, ?, ?)",
                ("default", "Default Favorites", 1, datetime.fromtimestamp(0).isoformat()),
            )
            self.conn.commit()

    def _maybe_migrate_legacy_data(self) -> None:
        if not LEGACY_APP_DIR.exists() or LEGACY_APP_DIR == APP_DIR:
            return
        self._migrate_legacy_settings()
        self._migrate_legacy_logs()
        self._migrate_legacy_generated_images()
        self._migrate_legacy_database()

    def _migrate_legacy_settings(self) -> None:
        legacy_settings = LEGACY_APP_DIR / "settings.json"
        if SETTINGS_PATH.exists() or not legacy_settings.exists():
            return
        shutil.copy2(legacy_settings, SETTINGS_PATH)

    def _migrate_legacy_logs(self) -> None:
        legacy_logs = LEGACY_APP_DIR / "request_logs.jsonl"
        if LOG_PATH.exists() or not legacy_logs.exists():
            return
        shutil.copy2(legacy_logs, LOG_PATH)

    def _migrate_legacy_generated_images(self) -> None:
        legacy_output_dir = LEGACY_APP_DIR / "generated_images"
        if not legacy_output_dir.exists():
            return
        if any(OUTPUT_DIR.iterdir()):
            return
        for item in legacy_output_dir.iterdir():
            target = OUTPUT_DIR / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copy2(item, target)

    def _migrate_legacy_database(self) -> None:
        legacy_db = LEGACY_APP_DIR / "mint_image.db"
        if not legacy_db.exists():
            return
        with self._lock:
            current_record_count = self._table_count("image_records")
            current_folder_count = self._table_count("favorite_folders")
            current_membership_count = self._table_count("favorite_folder_items")
            if current_record_count > 0 or current_folder_count > 1 or current_membership_count > 0:
                return
            legacy_conn = sqlite3.connect(legacy_db)
            legacy_conn.row_factory = sqlite3.Row
            try:
                self._copy_table_rows(
                    legacy_conn,
                    "image_records",
                    [
                        "id",
                        "prompt",
                        "api_profile_id",
                        "source_image_path",
                        "source_image_paths",
                        "result_image_path",
                        "result_image_url",
                        "result_b64",
                        "width",
                        "height",
                        "quality",
                        "output_format",
                        "model",
                        "status",
                        "error_message",
                        "raw_api_response_value",
                        "created_at",
                        "duration_ms",
                        "used_single_image_fallback",
                        "is_favorite",
                    ],
                )
                self._copy_table_rows(
                    legacy_conn,
                    "favorite_folders",
                    ["id", "title", "is_default", "created_at"],
                )
                self._copy_table_rows(
                    legacy_conn,
                    "favorite_folder_items",
                    ["folder_id", "record_id", "created_at"],
                )
                self.conn.commit()
            finally:
                legacy_conn.close()

    def _table_count(self, table_name: str) -> int:
        try:
            row = self.conn.execute(f"select count(*) from {table_name}").fetchone()
        except sqlite3.DatabaseError:
            return 0
        return int(row[0]) if row else 0

    def _copy_table_rows(self, legacy_conn: sqlite3.Connection, table_name: str, columns: list[str]) -> None:
        try:
            rows = legacy_conn.execute(f"select {', '.join(columns)} from {table_name}").fetchall()
        except sqlite3.DatabaseError:
            return
        if not rows:
            return
        placeholders = ", ".join("?" for _ in columns)
        self.conn.executemany(
            f"insert or ignore into {table_name} ({', '.join(columns)}) values ({placeholders})",
            [tuple(row[column] for column in columns) for row in rows],
        )

    def load_settings(self) -> SettingsModel:
        if not SETTINGS_PATH.exists():
            return SettingsModel()
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        profiles = [
            ApiProfile(
                id=item["id"],
                name=item["name"],
                base_url=item["base_url"],
                api_key=item["api_key"],
                model=item["model"],
                api_mode=ImageGenerationApiMode(item.get("api_mode", "images")),
            )
            for item in data.get("profiles", [])
        ]
        if not profiles:
            profiles = [ApiProfile.initial()]
        prompt_profiles = [
            PromptOptimizationProfile(
                id=item["id"],
                name=item["name"],
                base_url=item["base_url"],
                api_key=item["api_key"],
                model=item["model"],
                protocol=PromptOptimizationProtocol(item["protocol"]),
            )
            for item in data.get("prompt_optimization_profiles", [])
        ]
        return SettingsModel(
            profiles=profiles,
            active_profile_id=data.get("active_profile_id") or profiles[0].id,
            prompt_optimization_profiles=prompt_profiles,
            active_prompt_optimization_profile_id=data.get("active_prompt_optimization_profile_id"),
            response_format=data.get("response_format"),
            request_timeout_seconds=int(data.get("request_timeout_seconds", 600)),
            last_size_preset=SizePreset(data.get("last_size_preset", SizePreset.AUTO.value)),
            last_custom_width=int(data.get("last_custom_width", 0)),
            last_custom_height=int(data.get("last_custom_height", 0)),
            last_quality=ImageQuality(data.get("last_quality", ImageQuality.AUTO.value)),
            last_output_format=ImageOutputFormat(data.get("last_output_format", ImageOutputFormat.PNG.value)),
            preview_info_collapsed=bool(data.get("preview_info_collapsed", False)),
        )

    def save_settings(self, settings: SettingsModel) -> None:
        payload = {
            "profiles": [
                {
                    "id": p.id,
                    "name": p.name,
                    "base_url": p.base_url,
                    "api_key": p.api_key,
                    "model": p.model,
                    "api_mode": _enum_value(p.api_mode),
                }
                for p in settings.profiles
            ],
            "active_profile_id": settings.active_profile_id,
            "prompt_optimization_profiles": [
                {
                    "id": p.id,
                    "name": p.name,
                    "base_url": p.base_url,
                    "api_key": p.api_key,
                    "model": p.model,
                    "protocol": _enum_value(p.protocol),
                }
                for p in settings.prompt_optimization_profiles
            ],
            "active_prompt_optimization_profile_id": settings.active_prompt_optimization_profile_id,
            "response_format": settings.response_format,
            "request_timeout_seconds": settings.request_timeout_seconds,
            "last_size_preset": _enum_value(settings.last_size_preset),
            "last_custom_width": settings.last_custom_width,
            "last_custom_height": settings.last_custom_height,
            "last_quality": _enum_value(settings.last_quality),
            "last_output_format": _enum_value(settings.last_output_format),
            "preview_info_collapsed": settings.preview_info_collapsed,
        }
        SETTINGS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_records(self) -> list[ImageRecord]:
        with self._lock:
            cur = self.conn.cursor()
            rows = cur.execute("select * from image_records order by datetime(created_at) desc").fetchall()
        result: list[ImageRecord] = []
        for row in rows:
            result.append(
                ImageRecord(
                    id=row["id"],
                    prompt=row["prompt"],
                    api_profile_id=row["api_profile_id"],
                    source_image_path=row["source_image_path"],
                    source_image_paths=json.loads(row["source_image_paths"] or "[]"),
                    result_image_path=row["result_image_path"],
                    result_image_url=row["result_image_url"],
                    result_b64=row["result_b64"],
                    width=row["width"],
                    height=row["height"],
                    quality=row["quality"],
                    output_format=row["output_format"],
                    model=row["model"],
                    status=ImageRecordStatus(row["status"]),
                    error_message=row["error_message"],
                    raw_api_response_value=row["raw_api_response_value"],
                    created_at=row["created_at"],
                    duration_ms=row["duration_ms"],
                    used_single_image_fallback=bool(row["used_single_image_fallback"]),
                    is_favorite=bool(row["is_favorite"]),
                )
            )
        return result

    def upsert_record(self, record: ImageRecord) -> None:
        with self._lock:
            self.conn.execute(
                """
                insert into image_records (
                  id, prompt, api_profile_id, source_image_path, source_image_paths,
                  result_image_path, result_image_url, result_b64, width, height,
                  quality, output_format, model, status, error_message,
                  raw_api_response_value, created_at, duration_ms,
                  used_single_image_fallback, is_favorite
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(id) do update set
                  prompt=excluded.prompt,
                  api_profile_id=excluded.api_profile_id,
                  source_image_path=excluded.source_image_path,
                  source_image_paths=excluded.source_image_paths,
                  result_image_path=excluded.result_image_path,
                  result_image_url=excluded.result_image_url,
                  result_b64=excluded.result_b64,
                  width=excluded.width,
                  height=excluded.height,
                  quality=excluded.quality,
                  output_format=excluded.output_format,
                  model=excluded.model,
                  status=excluded.status,
                  error_message=excluded.error_message,
                  raw_api_response_value=excluded.raw_api_response_value,
                  created_at=excluded.created_at,
                  duration_ms=excluded.duration_ms,
                  used_single_image_fallback=excluded.used_single_image_fallback,
                  is_favorite=excluded.is_favorite
                """,
                (
                    record.id,
                    record.prompt,
                    record.api_profile_id,
                    record.source_image_path,
                    json.dumps(record.source_image_paths, ensure_ascii=False),
                    record.result_image_path,
                    record.result_image_url,
                    record.result_b64,
                    record.width,
                    record.height,
                    record.quality,
                    record.output_format,
                    record.model,
                    record.status.value,
                    record.error_message,
                    record.raw_api_response_value,
                    record.created_at,
                    record.duration_ms,
                    int(record.used_single_image_fallback),
                    int(record.is_favorite),
                ),
            )
            self.conn.commit()

    def delete_record(self, record_id: str) -> None:
        with self._lock:
            self.conn.execute("delete from image_records where id = ?", (record_id,))
            self.conn.execute("delete from favorite_folder_items where record_id = ?", (record_id,))
            self.conn.commit()

    def load_favorite_snapshot(self) -> tuple[list[FavoriteFolder], list[FavoriteFolderMembership]]:
        with self._lock:
            folders = [
                FavoriteFolder(
                    id=row["id"],
                    title=row["title"],
                    is_default=bool(row["is_default"]),
                    created_at=row["created_at"],
                )
                for row in self.conn.execute(
                    "select * from favorite_folders order by is_default desc, datetime(created_at) asc"
                ).fetchall()
            ]
            memberships = [
                FavoriteFolderMembership(
                    folder_id=row["folder_id"],
                    record_id=row["record_id"],
                    created_at=row["created_at"],
                )
                for row in self.conn.execute("select * from favorite_folder_items").fetchall()
            ]
        return folders, memberships

    def create_folder(self, title: str) -> FavoriteFolder:
        folder = FavoriteFolder(
            id=str(datetime.now().timestamp()).replace(".", ""),
            title=title,
            is_default=False,
            created_at=datetime.now().isoformat(),
        )
        with self._lock:
            self.conn.execute(
                "insert into favorite_folders (id, title, is_default, created_at) values (?, ?, ?, ?)",
                (folder.id, folder.title, int(folder.is_default), folder.created_at),
            )
            self.conn.commit()
        return folder

    def add_record_to_folder(self, folder_id: str, record_id: str) -> None:
        with self._lock:
            self.conn.execute(
                "insert or ignore into favorite_folder_items (folder_id, record_id, created_at) values (?, ?, ?)",
                (folder_id, record_id, datetime.now().isoformat()),
            )
            self.conn.execute("update image_records set is_favorite = 1 where id = ?", (record_id,))
            self.conn.commit()

    def remove_record_from_folder(self, folder_id: str, record_id: str) -> None:
        with self._lock:
            self.conn.execute(
                "delete from favorite_folder_items where folder_id = ? and record_id = ?",
                (folder_id, record_id),
            )
            cur = self.conn.execute(
                "select 1 from favorite_folder_items where record_id = ? limit 1",
                (record_id,),
            )
            self.conn.execute(
                "update image_records set is_favorite = ? where id = ?",
                (1 if cur.fetchone() else 0, record_id),
            )
            self.conn.commit()

    def append_log(self, level: RequestLogLevel, title: str, details: str) -> None:
        entry = RequestLogEntry(
            timestamp=datetime.now().isoformat(),
            level=level,
            title=title,
            details=details,
        )
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")

    def load_logs(self) -> list[RequestLogEntry]:
        if not LOG_PATH.exists():
            return []
        entries: list[RequestLogEntry] = []
        for line in LOG_PATH.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            data = json.loads(line)
            entries.append(
                RequestLogEntry(
                    timestamp=data["timestamp"],
                    level=RequestLogLevel(data["level"]),
                    title=data["title"],
                    details=data["details"],
                )
            )
        return entries[-1000:]

    def clear_logs(self) -> None:
        if LOG_PATH.exists():
            LOG_PATH.unlink()

    def clear_all_generated_images(self) -> None:
        if OUTPUT_DIR.exists():
            shutil.rmtree(OUTPUT_DIR, ignore_errors=True)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
