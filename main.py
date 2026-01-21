from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.config import AstrBotConfig
from astrbot.core.star.filter.custom_filter import CustomFilter

VERSION = "0.3.0"


@dataclass(frozen=True)
class MappingEntry:
    alias_raw: str
    target_raw: str
    reply_mode: str
    reply_text: str


class _State:
    enabled: bool = True
    mappings: list[MappingEntry] = []


STATE = _State()


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def _read_str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _read_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _get_wake_prefixes(cfg: AstrBotConfig) -> list[str]:
    prefixes = cfg.get("wake_prefix", [])
    if isinstance(prefixes, str):
        prefixes = [prefixes]
    if not isinstance(prefixes, list):
        return []
    return [p for p in prefixes if isinstance(p, str) and p]


def _strip_wake_prefix(text: str, prefixes: list[str]) -> str:
    for prefix in prefixes:
        if prefix and text.startswith(prefix):
            return text[len(prefix) :].strip()
    return text


def _parse_mapping_line(line: str) -> MappingEntry | None:
    if not line or not isinstance(line, str):
        return None
    parts = [part.strip() for part in line.split("||") if part.strip()]
    if not parts:
        return None

    mapping_part = parts[0]
    if "=>" in mapping_part:
        target_raw, alias_raw = mapping_part.split("=>", 1)
    elif "->" in mapping_part:
        target_raw, alias_raw = mapping_part.split("->", 1)
    else:
        return None

    alias_raw = alias_raw.strip()
    target_raw = target_raw.strip()
    if not alias_raw or not target_raw:
        return None

    reply_mode = "keep"
    reply_text = ""

    for opt in parts[1:]:
        opt_strip = opt.strip()
        lower = opt_strip.lower()

        if lower in ("silent", "mute", "no_reply") or opt_strip in (
            "静默",
            "不回复",
            "不回",
            "无回复",
        ):
            reply_mode = "silent"
            continue
        if lower in ("keep", "default", "passthrough") or opt_strip in (
            "保留",
            "默认",
            "原样",
        ):
            reply_mode = "keep"
            continue
        if lower in ("custom",) or opt_strip in ("自定义", "自订"):
            reply_mode = "custom"
            continue

        if lower.startswith("reply_mode=") or opt_strip.startswith("回复模式="):
            mode = opt_strip.split("=", 1)[1].strip()
            mode_lower = mode.lower()
            if mode_lower in ("silent", "custom", "keep"):
                reply_mode = mode_lower
            elif mode in ("静默", "不回复", "不回", "无回复"):
                reply_mode = "silent"
            elif mode in ("自定义", "自订"):
                reply_mode = "custom"
            elif mode in ("保留", "默认", "原样"):
                reply_mode = "keep"
            continue

        if (
            lower.startswith("reply=")
            or lower.startswith("reply_text=")
            or opt_strip.startswith("回复=")
            or opt_strip.startswith("回复文本=")
        ):
            reply_text = opt_strip.split("=", 1)[1].strip()
            reply_mode = "custom"
            continue
        if lower.startswith("text=") or opt_strip.startswith("文本="):
            reply_text = opt_strip.split("=", 1)[1].strip()
            reply_mode = "custom"
            continue
        if opt_strip.startswith("回复:"):
            reply_text = opt_strip.split(":", 1)[1].strip()
            reply_mode = "custom"
            continue

        reply_text = opt_strip
        reply_mode = "custom"

    return MappingEntry(
        alias_raw=alias_raw,
        target_raw=target_raw,
        reply_mode=reply_mode,
        reply_text=reply_text,
    )


def _build_mapping_from_config(
    command: Any,
    alias_text: Any,
    silent: Any,
    reply_text: Any,
) -> MappingEntry | None:
    command_str = _read_str(command)
    alias_str = _read_str(alias_text)
    if not command_str or not alias_str:
        return None
    reply_text_str = _read_text(reply_text)
    reply_mode = "silent" if bool(silent) else ("custom" if reply_text_str else "keep")
    return MappingEntry(
        alias_raw=alias_str,
        target_raw=command_str,
        reply_mode=reply_mode,
        reply_text=reply_text_str,
    )


def _build_fixed_mapping(command: str, data: Any) -> MappingEntry | None:
    if not isinstance(data, dict):
        return None
    return _build_mapping_from_config(
        command,
        data.get("alias_text"),
        data.get("silent", False),
        data.get("reply_text"),
    )


def _build_custom_mappings(raw_list: Any) -> list[MappingEntry]:
    mappings: list[MappingEntry] = []
    if not isinstance(raw_list, list):
        return mappings
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        entry = _build_mapping_from_config(
            item.get("command"),
            item.get("alias_text") or item.get("alias") or item.get("mask"),
            item.get("silent", False),
            item.get("reply_text") or item.get("reply") or item.get("回复"),
        )
        if entry:
            mappings.append(entry)
    return mappings


def _build_mappings(raw_list: list[Any]) -> list[MappingEntry]:
    mappings: list[MappingEntry] = []
    for item in raw_list:
        if isinstance(item, str):
            entry = _parse_mapping_line(item)
            if entry:
                mappings.append(entry)
            continue
        if isinstance(item, dict):
            command = (
                item.get("command")
                or item.get("target")
                or item.get("真实指令")
                or item.get("真實指令")
            )
            alias = (
                item.get("alias")
                or item.get("mask")
                or item.get("伪装指令")
                or item.get("偽裝指令")
            )
            command_str = _read_str(command)
            alias_str = _read_str(alias)
            if not command_str or not alias_str:
                continue
            reply_mode = _read_text(
                item.get("reply_mode") or item.get("回复模式") or "keep",
            )
            reply_text = _read_text(item.get("reply_text") or item.get("回复") or "")
            entry = _parse_mapping_line(
                f"{command_str} => {alias_str} || reply_mode={reply_mode} || reply={reply_text}",
            )
            if entry:
                mappings.append(entry)
    return mappings


def _build_mappings_from_text(text: str) -> list[MappingEntry]:
    mappings: list[MappingEntry] = []
    if not text or not isinstance(text, str):
        return mappings
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#") or line.startswith("//") or line.startswith(";"):
            continue
        entry = _parse_mapping_line(line)
        if entry:
            mappings.append(entry)
    return mappings


def _apply_mapping(event: AstrMessageEvent, cfg: AstrBotConfig) -> bool:
    if not STATE.enabled or not STATE.mappings:
        return False
    if event.get_extra("_cmdmask_applied", False):
        return True
    if not event.is_at_or_wake_command:
        return False

    msg = _normalize_text(event.get_message_str())
    if not msg:
        return False

    prefixes = _get_wake_prefixes(cfg)

    for entry in STATE.mappings:
        alias_norm = _strip_wake_prefix(_normalize_text(entry.alias_raw), prefixes)
        if not alias_norm:
            continue
        if msg == alias_norm or msg.startswith(alias_norm + " "):
            target_norm = _strip_wake_prefix(
                _normalize_text(entry.target_raw),
                prefixes,
            )
            if not target_norm:
                continue
            suffix = msg[len(alias_norm) :].strip()
            new_msg = target_norm if not suffix else f"{target_norm} {suffix}"

            event.set_extra("_cmdmask_applied", True)
            event.set_extra("_cmdmask_reply_mode", entry.reply_mode)
            event.set_extra("_cmdmask_reply_text", entry.reply_text)
            event.set_extra("_cmdmask_alias", alias_norm)
            event.set_extra("_cmdmask_target", target_norm)
            event.set_extra("_cmdmask_original_message", event.get_message_str())

            if new_msg != event.message_str:
                event.message_str = new_msg

            event.should_call_llm(True)
            return True

    return False


class _CommandMaskFilter(CustomFilter):
    def filter(self, event: AstrMessageEvent, cfg: AstrBotConfig) -> bool:
        try:
            _apply_mapping(event, cfg)
        except Exception as exc:
            logger.warning(f"[CmdMask] mapping error: {exc}")
        return False


class CmdMask(Star):
    def __init__(
        self,
        context: Context,
        config: AstrBotConfig | None = None,
    ) -> None:
        super().__init__(context)
        self._config = config
        self._load_config()
        logger.info(f"[CmdMask] loaded v{VERSION} with {len(STATE.mappings)} mappings")

    def _cfg(self, key: str, default: Any = None) -> Any:
        if self._config is None:
            return default
        return self._config.get(key, default)

    def _load_config(self) -> None:
        enabled = bool(self._cfg("enable", True))
        reset_rule = self._cfg("reset_rule", {})
        new_rule = self._cfg("new_rule", {})
        custom_rules = self._cfg("custom_rules", [])
        rules_text = self._cfg("rules_text", "")
        raw_mappings = self._cfg("mappings", [])
        if not isinstance(raw_mappings, list):
            raw_mappings = []

        mappings: list[MappingEntry] = []
        reset_entry = _build_fixed_mapping("/reset", reset_rule)
        if reset_entry:
            mappings.append(reset_entry)
        new_entry = _build_fixed_mapping("/new", new_rule)
        if new_entry:
            mappings.append(new_entry)
        mappings.extend(_build_custom_mappings(custom_rules))
        mappings.extend(_build_mappings_from_text(rules_text))
        mappings.extend(_build_mappings(raw_mappings))

        STATE.enabled = enabled
        STATE.mappings = mappings

    @filter.event_message_type(filter.EventMessageType.ALL, priority=100000)
    @filter.custom_filter(_CommandMaskFilter)
    async def _mapping_probe(self, event: AstrMessageEvent):
        return

    @filter.on_decorating_result(priority=100000)
    async def _override_reply(self, event: AstrMessageEvent):
        if not event.get_extra("_cmdmask_applied", False):
            return

        mode = event.get_extra("_cmdmask_reply_mode", "keep")
        if mode == "keep":
            return

        if mode == "silent":
            event.set_result(event.make_result())
            return

        if mode == "custom":
            text = event.get_extra("_cmdmask_reply_text", "")
            if not text or not str(text).strip():
                event.set_result(event.make_result())
                return
            event.set_result(event.plain_result(str(text)))
            return

    async def terminate(self) -> None:
        logger.info("[CmdMask] terminated")
