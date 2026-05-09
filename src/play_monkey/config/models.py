"""Configuration models using Pydantic for type-safe validation."""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class Platform(str, Enum):
    """Supported platforms."""
    ANDROID = "android"
    IOS = "ios"


class EventRatios(BaseModel):
    """Event type ratios configuration."""
    tap: float = Field(ge=0.0, le=1.0, description="Tap event ratio")
    swipe: float = Field(ge=0.0, le=1.0, description="Swipe event ratio")

    @field_validator("tap", "swipe")
    @classmethod
    def validate_ratio(cls, v: float) -> float:
        """Validate ratio is between 0 and 1."""
        if not 0.0 <= v <= 1.0:
            raise ValueError("Ratio must be between 0.0 and 1.0")
        return v

    @model_validator(mode="after")
    def validate_sum(self) -> "EventRatios":
        """Validate that ratios sum to 1.0."""
        total = self.tap + self.swipe
        if not abs(total - 1.0) < 0.001:  # Allow small floating point error
            raise ValueError(f"Event ratios must sum to 1.0, got {total}")
        return self


class BoundsConfig(BaseModel):
    """Coordinate bounds configuration."""
    x_min: int = Field(ge=0, description="Minimum X coordinate")
    x_max: int = Field(gt=0, description="Maximum X coordinate")
    y_min: int = Field(ge=0, description="Minimum Y coordinate")
    y_max: int = Field(gt=0, description="Maximum Y coordinate")

    @model_validator(mode="after")
    def validate_bounds(self) -> "BoundsConfig":
        """Validate that min < max for both axes."""
        if self.x_min >= self.x_max:
            raise ValueError(f"x_min ({self.x_min}) must be less than x_max ({self.x_max})")
        if self.y_min >= self.y_max:
            raise ValueError(f"y_min ({self.y_min}) must be less than y_max ({self.y_max})")
        return self


class ExclusionZone(BaseModel):
    """Exclusion zone (forbidden area) configuration."""
    x_min: int = Field(ge=0, description="Minimum X coordinate")
    x_max: int = Field(gt=0, description="Maximum X coordinate")
    y_min: int = Field(ge=0, description="Minimum Y coordinate")
    y_max: int = Field(gt=0, description="Maximum Y coordinate")

    @model_validator(mode="after")
    def validate_zone(self) -> "ExclusionZone":
        """Validate that min < max for both axes."""
        if self.x_min >= self.x_max:
            raise ValueError(f"x_min ({self.x_min}) must be less than x_max ({self.x_max})")
        if self.y_min >= self.y_max:
            raise ValueError(f"y_min ({self.y_min}) must be less than y_max ({self.y_max})")
        return self


class DurationConfig(BaseModel):
    """Test duration configuration. event_count takes priority over duration_seconds."""
    event_count: Optional[int] = Field(None, gt=0, description="Total number of events to execute")
    duration_seconds: Optional[int] = Field(None, gt=0, description="Test duration in seconds")

    @model_validator(mode="after")
    def validate_at_least_one(self) -> "DurationConfig":
        """Validate that at least one termination condition is specified."""
        if self.event_count is None and self.duration_seconds is None:
            raise ValueError("Must specify either event_count or duration_seconds")
        return self


class MonitoringConfig(BaseModel):
    """Performance monitoring configuration."""
    enabled: bool = Field(default=True, description="Enable performance monitoring")
    sample_interval_seconds: float = Field(default=2.0, gt=0, description="Sampling interval")
    metrics: list[str] = Field(default=["cpu", "memory", "fps", "battery"], description="Metrics to collect")


class StabilityConfig(BaseModel):
    """Stability monitoring configuration."""
    monitor_crashes: bool = Field(default=True, description="Monitor application crashes")
    monitor_anr: bool = Field(default=True, description="Monitor ANR/Hang events")
    monitor_errors: bool = Field(default=True, description="Monitor error logs")
    continue_on_crash: bool = Field(default=True, description="Continue test after crash")
    continue_on_anr: bool = Field(default=True, description="Continue test after ANR")
    anr_threshold_seconds: float = Field(default=5.0, gt=0, description="ANR detection threshold")
    max_crash_count: Optional[int] = Field(default=None, gt=0, description="Max crashes before stopping")


class SwipeDurationConfig(BaseModel):
    """Swipe gesture duration configuration.

    The actual duration of each swipe is sampled uniformly from [min_ms, max_ms].
    Set min_ms == max_ms to produce swipes of a fixed duration.
    """
    min_ms: int = Field(default=100, gt=0, description="Minimum swipe duration in milliseconds")
    max_ms: int = Field(default=500, gt=0, description="Maximum swipe duration in milliseconds")

    @model_validator(mode="after")
    def validate_range(self) -> "SwipeDurationConfig":
        if self.min_ms > self.max_ms:
            raise ValueError(
                f"swipe duration min_ms ({self.min_ms}) must be <= max_ms ({self.max_ms})"
            )
        return self


class ReportConfig(BaseModel):
    """Report generation configuration."""
    output_path: str = Field(default="./report.html", description="Report output file path")
    include_raw_data: bool = Field(default=False, description="Include raw metric data in report")


class TestConfig(BaseModel):
    """Root test configuration model."""
    platform: Platform = Field(description="Target platform (android or ios)")
    device_id: str = Field(description="Device identifier")
    app_package: str = Field(description="App package name (Android) or bundle ID (iOS)")

    event_ratios: EventRatios = Field(description="Event type ratios")
    interval_ms: int = Field(default=500, gt=0, description="Interval between events in milliseconds")
    swipe_duration: SwipeDurationConfig = Field(
        default_factory=SwipeDurationConfig,
        description="Duration range for swipe gestures",
    )

    event_count: Optional[int] = Field(None, gt=0, description="Total events (priority)")
    duration_seconds: Optional[int] = Field(None, gt=0, description="Test duration in seconds")

    bounds: Optional[BoundsConfig] = Field(None, description="Coordinate bounds")
    exclusion_zones: list[ExclusionZone] = Field(default_factory=list, description="Forbidden areas")

    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)
    stability: StabilityConfig = Field(default_factory=StabilityConfig)
    report: ReportConfig = Field(default_factory=ReportConfig)

    @model_validator(mode="after")
    def validate_duration(self) -> "TestConfig":
        """Validate that at least one termination condition is specified."""
        if self.event_count is None and self.duration_seconds is None:
            raise ValueError("Must specify either event_count or duration_seconds")
        return self
