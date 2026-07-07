from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Any

from app.models import (
    CapturedImage,
    ConsumptionRecord,
    DishRecognition,
    ImageStatusEnum,
    VideoSource,
    VideoSourceStatus,
)
from app.services.video_sources.manager import _channels_from_source_config


MATCHABLE_IMAGE_STATUSES = (
    ImageStatusEnum.pending,
    ImageStatusEnum.identified,
    ImageStatusEnum.matched,
)
PRIMARY_MATCH_WINDOW_SECONDS = 1
FALLBACK_LOOKBACK_SECONDS = 3
DEFAULT_SUGGESTION_DAYS = 30
MAX_SUGGESTION_DAYS = 365
DEFAULT_MIN_SAMPLE_COUNT = 5
SUGGESTION_CONFIDENCE_THRESHOLD = 0.75


@dataclass
class ChannelHitStats:
    hit_count: int = 0
    price_match_count: int = 0
    total_time_diff_seconds: float = 0.0
    total_price_diff: float = 0.0
    evidence: list[dict[str, Any]] = field(default_factory=list)


def normalize_location_text(value: object) -> str:
    return " ".join(str(value or "").strip().split())


class ChannelBindingSuggestionService:
    def __init__(self, config: dict):
        self.config = config
        self.price_tolerance = float(config.get("PRICE_TOLERANCE", 0.5))
        self.time_offset = float(config.get("TIME_OFFSET_CALIBRATION", 0.0))
        self._price_cache: dict[int, float] = {}

    def build_suggestions(
        self,
        *,
        days: int = DEFAULT_SUGGESTION_DAYS,
        min_samples: int = DEFAULT_MIN_SAMPLE_COUNT,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        days = self._validate_days(days)
        min_samples = max(1, int(min_samples))
        window_end = now or datetime.now(timezone.utc)
        window_start = window_end - timedelta(days=days)
        channel_by_id, aliases = self._load_channel_catalog()
        configured_channel_ids = set(channel_by_id)

        if not configured_channel_ids:
            return self._empty_payload(days, min_samples, window_start, window_end, channel_count=0)

        records = ConsumptionRecord.query.filter(
            ConsumptionRecord.transaction_time >= window_start,
            ConsumptionRecord.transaction_time <= window_end,
        ).order_by(
            ConsumptionRecord.transaction_time.asc(),
            ConsumptionRecord.id.asc(),
        ).all()

        records_by_location: dict[str, list[ConsumptionRecord]] = defaultdict(list)
        for record in records:
            location = normalize_location_text(record.channel_id)
            if not location or self._location_already_bound(location, aliases, configured_channel_ids):
                continue
            records_by_location[location].append(record)

        items = [
            self._build_location_suggestion(
                location,
                location_records,
                channel_by_id=channel_by_id,
                configured_channel_ids=configured_channel_ids,
                min_samples=min_samples,
            )
            for location, location_records in records_by_location.items()
        ]
        items.sort(key=self._suggestion_sort_key)

        return {
            "days": days,
            "min_samples": min_samples,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "channel_count": len(configured_channel_ids),
            "items": items,
        }

    def _empty_payload(
        self,
        days: int,
        min_samples: int,
        window_start: datetime,
        window_end: datetime,
        *,
        channel_count: int,
    ) -> dict[str, Any]:
        return {
            "days": days,
            "min_samples": min_samples,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "channel_count": channel_count,
            "items": [],
        }

    def _validate_days(self, days: int) -> int:
        try:
            normalized = int(days)
        except (TypeError, ValueError) as exc:
            raise ValueError("days 必须是整数") from exc
        if normalized < 1 or normalized > MAX_SUGGESTION_DAYS:
            raise ValueError(f"days 必须在 1 到 {MAX_SUGGESTION_DAYS} 之间")
        return normalized

    def _load_channel_catalog(self) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
        sources = VideoSource.query.filter(
            VideoSource.status == VideoSourceStatus.enabled.value,
        ).order_by(
            VideoSource.is_active.desc(),
            VideoSource.id.asc(),
        ).all()

        channel_by_id: dict[str, dict[str, Any]] = {}
        aliases: dict[str, list[str]] = defaultdict(list)
        for source in sources:
            for channel in _channels_from_source_config(source.source_type, source.config_json or {}):
                channel_id = normalize_location_text(channel.get("channel_id"))
                if not channel_id:
                    continue
                channel_by_id.setdefault(channel_id, {
                    "source_id": source.id,
                    "source_name": source.name,
                    "source_type": source.source_type,
                    "channel_id": channel_id,
                    "channel_name": channel.get("name") or f"通道 {channel_id}",
                    "location_alias": normalize_location_text(channel.get("location_alias")),
                })
                alias = normalize_location_text(channel.get("location_alias"))
                if alias and channel_id not in aliases[alias]:
                    aliases[alias].append(channel_id)
        return channel_by_id, dict(aliases)

    def _location_already_bound(self, location: str, aliases: dict[str, list[str]], configured_channel_ids: set[str]) -> bool:
        if location in configured_channel_ids:
            return True
        return any(channel_id in configured_channel_ids for channel_id in aliases.get(location, []))

    def _build_location_suggestion(
        self,
        location: str,
        records: list[ConsumptionRecord],
        *,
        channel_by_id: dict[str, dict[str, Any]],
        configured_channel_ids: set[str],
        min_samples: int,
    ) -> dict[str, Any]:
        stats_by_channel: dict[str, ChannelHitStats] = defaultdict(ChannelHitStats)
        for record in records:
            winner = self._find_best_cross_channel_candidate(record, configured_channel_ids)
            if not winner:
                continue
            stats = stats_by_channel[winner["channel_id"]]
            stats.hit_count += 1
            if winner["price_diff"] <= self.price_tolerance:
                stats.price_match_count += 1
            stats.total_time_diff_seconds += winner["time_diff_seconds"]
            stats.total_price_diff += winner["price_diff"]
            if len(stats.evidence) < 12:
                stats.evidence.append(winner)

        sample_count = len(records)
        channel_summaries = [
            self._build_channel_summary(channel_id, stats, sample_count, channel_by_id)
            for channel_id, stats in stats_by_channel.items()
        ]
        channel_summaries.sort(key=lambda item: (-item["hit_count"], -item["price_match_rate"], item["avg_time_diff_seconds"], item["channel_id"]))

        top = channel_summaries[0] if channel_summaries else None
        second_hit_count = channel_summaries[1]["hit_count"] if len(channel_summaries) > 1 else 0
        confidence = self._calculate_confidence(top, second_hit_count, sample_count) if top else 0.0
        clear_lead = bool(top) and self._has_clear_lead(top["hit_count"], second_hit_count, sample_count)
        status, reason, can_apply = self._resolve_status(top, sample_count, min_samples, confidence, clear_lead)
        evidence = []
        if top:
            evidence = sorted(top.pop("_evidence"), key=lambda item: (item["price_diff"], item["time_diff_seconds"], item["image_id"]))[:5]

        return {
            "id": location,
            "location": location,
            "record_count": sample_count,
            "matched_record_count": top["hit_count"] if top else 0,
            "status": status,
            "reason": reason,
            "confidence": confidence,
            "can_apply": can_apply,
            "recommended_channel": self._public_channel_payload(top) if top else None,
            "top_channels": [self._public_channel_payload(item) for item in channel_summaries[:3]],
            "evidence": evidence,
        }

    def _find_best_cross_channel_candidate(self, record: ConsumptionRecord, configured_channel_ids: set[str]) -> dict[str, Any] | None:
        aligned_tx = record.transaction_time + timedelta(seconds=self.time_offset)
        for lower, upper, include_upper in self._matching_windows(aligned_tx):
            candidates_query = CapturedImage.query.filter(
                CapturedImage.captured_at >= lower,
                CapturedImage.status.in_(MATCHABLE_IMAGE_STATUSES),
                CapturedImage.is_candidate.is_(False),
                CapturedImage.channel_id.in_(configured_channel_ids),
            )
            if include_upper:
                candidates_query = candidates_query.filter(CapturedImage.captured_at <= upper)
            else:
                candidates_query = candidates_query.filter(CapturedImage.captured_at < upper)

            candidates = candidates_query.all()
            if not candidates:
                continue

            scored = []
            record_amount = abs(float(record.amount))
            for image in candidates:
                image_price_total = self._calc_image_price(image.id)
                price_diff = abs(record_amount - image_price_total)
                time_diff = abs((aligned_tx - image.captured_at).total_seconds())
                scored.append((time_diff, price_diff, image.id, image, image_price_total))

            time_diff, price_diff, _image_id, image, image_price_total = min(scored, key=lambda item: (item[0], item[1], item[2]))
            return {
                "consumption_record_id": record.id,
                "transaction_id": record.transaction_id,
                "transaction_time": record.transaction_time.isoformat() if record.transaction_time else None,
                "amount": float(record.amount) if record.amount is not None else None,
                "image_id": image.id,
                "channel_id": image.channel_id,
                "captured_at": image.captured_at.isoformat() if image.captured_at else None,
                "time_diff_seconds": round(float(time_diff), 3),
                "price_diff": round(float(price_diff), 2),
                "image_price_total": round(float(image_price_total), 2),
            }
        return None

    def _matching_windows(self, tx_time: datetime) -> list[tuple[datetime, datetime, bool]]:
        primary_delta = timedelta(seconds=PRIMARY_MATCH_WINDOW_SECONDS)
        windows = [(tx_time - primary_delta, tx_time + primary_delta, True)]
        for seconds in range(PRIMARY_MATCH_WINDOW_SECONDS + 1, FALLBACK_LOOKBACK_SECONDS + 1):
            windows.append((
                tx_time - timedelta(seconds=seconds),
                tx_time - timedelta(seconds=seconds - 1),
                False,
            ))
        return windows

    def _calc_image_price(self, image_id: int) -> float:
        if image_id in self._price_cache:
            return self._price_cache[image_id]

        total = 0.0
        recognitions = DishRecognition.query.filter_by(
            image_id=image_id,
            is_low_confidence=False,
        ).all()
        for recognition in recognitions:
            if recognition.dish_id and recognition.dish and recognition.dish.price is not None:
                total += float(recognition.dish.price)
        self._price_cache[image_id] = total
        return total

    def _build_channel_summary(
        self,
        channel_id: str,
        stats: ChannelHitStats,
        sample_count: int,
        channel_by_id: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        hit_count = stats.hit_count
        hit_rate = hit_count / sample_count if sample_count else 0.0
        price_match_rate = stats.price_match_count / hit_count if hit_count else 0.0
        avg_time_diff = stats.total_time_diff_seconds / hit_count if hit_count else 0.0
        avg_price_diff = stats.total_price_diff / hit_count if hit_count else 0.0
        time_score = max(0.0, 1.0 - (avg_time_diff / max(FALLBACK_LOOKBACK_SECONDS, 1)))
        score = (0.55 * hit_rate) + (0.30 * price_match_rate) + (0.15 * time_score)
        channel = channel_by_id.get(channel_id, {})
        return {
            **channel,
            "channel_id": channel_id,
            "channel_name": channel.get("channel_name") or f"通道 {channel_id}",
            "location_alias": normalize_location_text(channel.get("location_alias")),
            "hit_count": hit_count,
            "sample_count": sample_count,
            "hit_rate": round(hit_rate, 4),
            "price_match_count": stats.price_match_count,
            "price_match_rate": round(price_match_rate, 4),
            "avg_time_diff_seconds": round(avg_time_diff, 3),
            "avg_price_diff": round(avg_price_diff, 2),
            "score": round(score, 4),
            "_evidence": stats.evidence,
        }

    def _calculate_confidence(self, top: dict[str, Any], second_hit_count: int, sample_count: int) -> float:
        if not sample_count or not top:
            return 0.0
        lead_ratio = max(0.0, (top["hit_count"] - second_hit_count) / max(top["hit_count"], 1))
        lead_factor = 0.70 + (0.30 * lead_ratio)
        confidence = float(top["score"]) * lead_factor
        return round(max(0.0, min(0.99, confidence)), 2)

    def _has_clear_lead(self, top_hit_count: int, second_hit_count: int, sample_count: int) -> bool:
        if top_hit_count <= 0:
            return False
        return top_hit_count - second_hit_count >= max(2, ceil(sample_count * 0.2))

    def _resolve_status(
        self,
        top: dict[str, Any] | None,
        sample_count: int,
        min_samples: int,
        confidence: float,
        clear_lead: bool,
    ) -> tuple[str, str, bool]:
        if sample_count < min_samples:
            return "sample_insufficient", "样本不足", False
        if not top:
            return "low_confidence", "没有找到稳定的跨通道候选", False
        if normalize_location_text(top.get("location_alias")):
            return "conflict", "推荐通道已有地点别名", False
        if confidence >= SUGGESTION_CONFIDENCE_THRESHOLD and clear_lead:
            return "suggested", "推荐通道在时间和金额碰撞中明显领先", True
        return "low_confidence", "候选通道差距不足或金额一致性偏低", False

    def _public_channel_payload(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in item.items()
            if not key.startswith("_")
        }

    def _suggestion_sort_key(self, item: dict[str, Any]) -> tuple[int, float, int, str]:
        status_order = {
            "suggested": 0,
            "conflict": 1,
            "low_confidence": 2,
            "sample_insufficient": 3,
        }
        return (
            status_order.get(item["status"], 9),
            -float(item["confidence"]),
            -int(item["record_count"]),
            item["location"],
        )
