"""Deterministic claim interpretation; consumes engine results without calculating indicators."""

import calendar
import re
from datetime import UTC, datetime

from app.schemas.evidence import TechnicalClaim, VerificationStatus

CANONICAL_CLAIM_TYPES = {
    "rsi_above_70": "rsi_overbought",
    "rsi_below_30": "rsi_oversold",
    "rsi_overbought_in_divergence": "historical_rsi_overbought",
    "rsi_oversold_in_divergence": "historical_rsi_oversold",
}

POINT_RSI_CLAIMS = {"rsi_overbought", "rsi_oversold"}
HISTORICAL_RSI_CLAIMS = {"historical_rsi_overbought", "historical_rsi_oversold"}
DIVERGENCE_CLAIMS = {
    "rsi_bullish_divergence",
    "rsi_bearish_divergence",
    "macd_bullish_divergence",
    "macd_bearish_divergence",
}

HISTORICAL_MARKERS = (
    "trong quá trình",
    "giai đoạn",
    "hình thành",
    "lịch sử",
    "trước đó",
    "during",
    "formation",
    "historical",
    "earlier",
)


class TechnicalVerificationService:
    """Never equate an image or increased volume with verified institutional inflow."""

    def __init__(
        self,
        divergence_lookback_days: int = 60,
        rsi_overbought: float = 70.0,
        rsi_oversold: float = 30.0,
    ):
        """Configure the recent-divergence window and RSI thresholds."""
        self.divergence_lookback_days = divergence_lookback_days
        self.rsi_overbought = rsi_overbought
        self.rsi_oversold = rsi_oversold

    def verify(
        self, claims: list[TechnicalClaim], snapshot: dict, evidence_id: str
    ) -> list[TechnicalClaim]:
        """Return copies with explicit scope, insufficiency and linked evidence identifiers."""
        result = []
        for original in claims:
            claim = original.model_copy(deep=True)
            canonical = CANONICAL_CLAIM_TYPES.get(claim.claim_type, claim.claim_type)
            claim.evidence_ids = [evidence_id]
            claim.verification_status = VerificationStatus.INSUFFICIENT_EVIDENCE
            if canonical in ("money_inflow", "other"):
                claim.verification_status = VerificationStatus.NOT_VERIFIABLE
                claim.verification_reason = "Price/volume alone cannot establish institutional inflow or this unsupported assertion."
            elif snapshot.get("status") != "ok":
                claim.verification_reason = snapshot.get(
                    "reason", "No sufficient closed historical market data."
                )
            elif not self._parameters_match(claim, snapshot, canonical):
                # Extracted parameters are untrusted; never silently verify against different settings.
                claim.verification_reason = "Claim-specific parameters require review; current snapshot uses recorded engine defaults."
            elif canonical in ("bullish_engulfing", "bearish_engulfing"):
                self._set(
                    claim,
                    snapshot.get(canonical),
                    "Native CDLENGULFING on latest closed candle (not a profitability prediction).",
                )
            elif canonical == "volume_increase":
                ratio = snapshot.get("filter_values", {}).get("rvol")
                self._set(
                    claim,
                    None if ratio is None else ratio > 1,
                    "Volume compared with the recorded inclusive volume SMA; no institutional-flow inference.",
                )
            elif canonical in POINT_RSI_CLAIMS:
                self._verify_point_rsi(claim, canonical, snapshot)
            elif canonical in HISTORICAL_RSI_CLAIMS:
                self._verify_historical_rsi(claim, canonical, snapshot)
            elif canonical in DIVERGENCE_CLAIMS:
                self._verify_divergence(claim, canonical, snapshot)
            elif canonical in ("breakout", "bearish_breakout"):
                direction = "BEARISH" if canonical == "bearish_breakout" else "BULLISH"
                hits = [
                    e
                    for e in snapshot.get("structure_events", [])
                    if direction in str(e.get("event_type", ""))
                    and "FAILED" not in str(e.get("event_type", ""))
                    and datetime.fromisoformat(str(e["broken_at"]))
                    == datetime.fromisoformat(snapshot["as_of"])
                ]
                self._set(
                    claim,
                    bool(hits),
                    "Existing SMC engine close-confirmed bullish break at latest closed bar.",
                )
            result.append(claim)
        return result

    def _verify_point_rsi(self, claim: TechnicalClaim, canonical: str, snapshot: dict) -> None:
        """Verify a threshold claim at its resolved scope, not always at analyzed_at."""
        scope = self._resolve_scope(claim, snapshot)
        if scope is None:
            if self._looks_historical(claim.original_text):
                claim.verification_status = VerificationStatus.NOT_VERIFIABLE
                claim.verification_reason = (
                    "Claim appears historical but no reliable temporal scope was extracted; "
                    "refusing to compare it with RSI at analyzed_at."
                )
                return
            scope = ("analysis", None, None)

        kind, start, end = scope
        if kind == "analysis":
            rsi = snapshot.get("filter_values", {}).get("rsi")
            if rsi is None:
                rsi = snapshot.get("rsi_at_analysis")
            if rsi is None:
                claim.verification_reason = "Recorded RSI is unavailable at the analysis timestamp."
                return
            self._set_rsi_threshold(claim, canonical, float(rsi), "analyzed_at", snapshot)
            return
        if kind in ("divergence", "explicit"):
            direction = "bearish" if canonical == "rsi_overbought" else "bullish"
            events = [
                event
                for event in self._events_in_scope(snapshot, start, end)
                if event.get("direction") == direction
            ]
            stats = self._historical_rsi_stats(events)
            if stats is None:
                claim.verification_status = VerificationStatus.NOT_VERIFIABLE
                claim.verification_reason = (
                    "Historical RSI series is not retained for this exact scope; "
                    "no matching confirmed divergence window was found."
                )
                return
            self._set_rsi_threshold_from_stats(claim, canonical, stats, kind, snapshot)
            return
        claim.verification_status = VerificationStatus.NOT_VERIFIABLE
        claim.verification_reason = (
            "Claim temporal scope is unknown; no reliable date was invented."
        )

    def _verify_historical_rsi(self, claim: TechnicalClaim, canonical: str, snapshot: dict) -> None:
        """Verify RSI extremes inside a confirmed divergence or explicit historical window."""
        scope = self._resolve_scope(claim, snapshot)
        if scope is None:
            events = self._events_in_scope(snapshot, None, None)
            if not events:
                claim.verification_status = VerificationStatus.NOT_VERIFIABLE
                claim.verification_reason = (
                    "Historical RSI claim has no extracted temporal scope and no recent "
                    "confirmed divergence window to anchor it."
                )
                return
            scope = ("divergence", None, None)
        kind, start, end = scope
        if kind == "analysis":
            claim.verification_status = VerificationStatus.NOT_VERIFIABLE
            claim.verification_reason = (
                "Historical RSI claim cannot be verified against only the analysis timestamp."
            )
            return
        if kind == "unknown":
            claim.verification_status = VerificationStatus.NOT_VERIFIABLE
            claim.verification_reason = (
                "Claim temporal scope is unknown; no reliable date was invented."
            )
            return
        direction = "bearish" if canonical == "historical_rsi_overbought" else "bullish"
        events = [
            event
            for event in self._events_in_scope(snapshot, start, end)
            if event.get("direction") == direction
        ]
        stats = self._historical_rsi_stats(events)
        if stats is None:
            claim.verification_status = VerificationStatus.NOT_VERIFIABLE
            claim.verification_reason = (
                "No confirmed "
                f"{direction} divergence window with retained RSI pivot/extreme data "
                "matches the claimed historical scope."
            )
            return
        self._set_rsi_threshold_from_stats(claim, canonical, stats, kind, snapshot)

    def _verify_divergence(self, claim: TechnicalClaim, canonical: str, snapshot: dict) -> None:
        """Use a recent/scope-bounded confirmation window instead of latest bar only."""
        indicator = "RSI" if canonical.startswith("rsi") else "MACD"
        direction = "bullish" if "bullish" in canonical else "bearish"
        scope = self._resolve_scope(claim, snapshot)
        if scope is not None and scope[0] == "analysis":
            claim.verification_reason = (
                "Divergence requires its confirmed formation window, not only analyzed_at."
            )
            return
        if scope is not None and scope[0] == "unknown":
            claim.verification_status = VerificationStatus.NOT_VERIFIABLE
            claim.verification_reason = (
                "Claim temporal scope is unknown; no reliable date was invented."
            )
            return
        start, end = (None, None) if scope is None else (scope[1], scope[2])
        recent = self._recent_divergences(snapshot, direction, indicator, start, end)
        if not recent:
            claim.verification_reason = (
                f"No {indicator} {direction} divergence confirmed within "
                f"{self.divergence_lookback_days} days before as_of; an older or "
                "unconfirmed swing pair requires review."
            )
            return
        confirmed = ", ".join(
            str(event.get("confirmed_at")) for event in recent[:3] if event.get("confirmed_at")
        )
        window_label = (
            "the resolved temporal scope"
            if start is not None and end is not None
            else f"the {self.divergence_lookback_days}-day confirmation window"
        )
        self._set(
            claim,
            True,
            f"Confirmed {indicator} {direction} divergence ({confirmed}) inside "
            f"{window_label}; timestamps and closed pivots are respected and no future data is used.",
        )

    def _resolve_scope(
        self, claim: TechnicalClaim, snapshot: dict
    ) -> tuple[str, datetime | None, datetime | None] | None:
        """Resolve a claim's scope without inventing dates; None means infer conservatively."""
        scope = claim.temporal_scope
        if scope is None:
            return None
        if scope.kind == "analysis":
            return ("analysis", None, None)
        if scope.kind == "divergence":
            # Extraction may provide the narrative dates even when the type is
            # "divergence". Use those dates instead of collapsing to the recent window.
            if not scope.start or not scope.end:
                inferred = self._infer_month_range(claim.original_text, snapshot)
                if inferred is not None:
                    return ("divergence", inferred[0], inferred[1])
            if scope.start and scope.end:
                as_of = self._parse_dt(snapshot.get("as_of"))
                if as_of is None or scope.start > scope.end or scope.end > as_of:
                    return ("unknown", None, None)
                return ("divergence", scope.start, scope.end)
            events = self._events_in_scope(snapshot, None, None)
            if not events:
                return ("unknown", None, None)
            starts: list[datetime] = []
            ends: list[datetime] = []
            for event in events:
                start = self._parse_dt(event.get("start_time"))
                end = self._parse_dt(event.get("end_time"))
                if start is not None:
                    starts.append(start)
                if end is not None:
                    ends.append(end)
            if not starts or not ends:
                return ("unknown", None, None)
            return ("divergence", min(starts), max(ends))
        if scope.kind == "explicit":
            as_of = self._parse_dt(snapshot.get("as_of"))
            if not scope.start or not scope.end or as_of is None:
                return ("unknown", None, None)
            if scope.start > scope.end or scope.end > as_of:
                return ("unknown", None, None)
            return ("explicit", scope.start, scope.end)
        return ("unknown", None, None)

    def _events_in_scope(
        self,
        snapshot: dict,
        start: datetime | None,
        end: datetime | None,
    ) -> list[dict]:
        """Return confirmed events inside an explicit window or the configured recent window."""
        as_of = self._parse_dt(snapshot.get("as_of"))
        if as_of is None:
            return []
        events = []
        for event in snapshot.get("divergence_events", []):
            confirmed = self._parse_dt(event.get("confirmed_at"))
            if confirmed is None or confirmed > as_of:
                continue
            if start is not None and end is not None:
                event_start = self._parse_dt(event.get("start_time"))
                event_end = self._parse_dt(event.get("end_time"))
                if event_start is None or event_end is None:
                    continue
                if event_end < start or event_start > end:
                    continue
            elif (as_of - confirmed).days > self.divergence_lookback_days:
                continue
            events.append(event)
        return events

    def _recent_divergences(
        self,
        snapshot: dict,
        direction: str,
        indicator: str,
        start: datetime | None,
        end: datetime | None,
    ) -> list[dict]:
        """Filter scope-valid events by direction and oscillator."""
        return [
            event
            for event in self._events_in_scope(snapshot, start, end)
            if event.get("direction") == direction
            and any(
                oscillator.get("indicator") == indicator
                for oscillator in event.get("oscillators", [])
            )
        ]

    @staticmethod
    def _rsi_stats(events: list[dict]) -> dict | None:
        """Aggregate retained RSI pivot/extreme values across matching event windows."""
        first_values, second_values, minima, maxima = [], [], [], []
        for event in events:
            for oscillator in event.get("oscillators", []):
                if oscillator.get("indicator") != "RSI":
                    continue
                context = oscillator.get("rsi_context") or {}
                first = context.get("rsi_at_first_pivot", oscillator.get("start_value"))
                second = context.get("rsi_at_second_pivot", oscillator.get("end_value"))
                low = context.get("rsi_min_during_window", context.get("min"))
                high = context.get("rsi_max_during_window", context.get("max"))
                if (
                    low is None
                    and isinstance(first, (int, float))
                    and isinstance(second, (int, float))
                ):
                    low, high = min(first, second), max(first, second)
                if isinstance(first, (int, float)):
                    first_values.append(float(first))
                if isinstance(second, (int, float)):
                    second_values.append(float(second))
                if isinstance(low, (int, float)):
                    minima.append(float(low))
                if isinstance(high, (int, float)):
                    maxima.append(float(high))
        if not minima or not maxima:
            return None
        return {
            "rsi_at_first_pivot": first_values[0] if first_values else None,
            "rsi_at_second_pivot": second_values[-1] if second_values else None,
            "rsi_min_during_window": min(minima),
            "rsi_max_during_window": max(maxima),
        }

    def _historical_rsi_stats(self, events: list[dict]) -> dict | None:
        """Compatibility wrapper used by point and historical claim branches."""
        return self._rsi_stats(events)

    def _set_rsi_threshold(
        self, claim: TechnicalClaim, canonical: str, rsi: float, scope: str, snapshot: dict
    ) -> None:
        threshold = self.rsi_overbought if canonical == "rsi_overbought" else self.rsi_oversold
        comparison = ">" if canonical == "rsi_overbought" else "<"
        supported = rsi > threshold if canonical == "rsi_overbought" else rsi < threshold
        self._set(
            claim,
            bool(supported),
            f"Recorded RSI at {scope} is {rsi:.2f}; claim threshold is {comparison}{threshold:.0f}.",
        )

    def _set_rsi_threshold_from_stats(
        self,
        claim: TechnicalClaim,
        canonical: str,
        stats: dict,
        scope_kind: str,
        snapshot: dict,
    ) -> None:
        if canonical in ("rsi_overbought", "historical_rsi_overbought"):
            value = stats.get("rsi_max_during_window")
            threshold = self.rsi_overbought
            supported = value is not None and float(value) >= threshold
            label = "maximum"
            comparison = ">="
        else:
            value = stats.get("rsi_min_during_window")
            threshold = self.rsi_oversold
            supported = value is not None and float(value) <= threshold
            label = "minimum"
            comparison = "<="
        if value is None:
            claim.verification_status = VerificationStatus.INSUFFICIENT_EVIDENCE
            claim.verification_reason = "RSI extreme values are not retained for this scope."
            return
        self._set(
            claim,
            bool(supported),
            f"RSI {label} in the {scope_kind} window is {float(value):.2f}; "
            f"threshold is {comparison}{threshold:.0f}. RSI at analyzed_at is not used "
            "to invalidate a historical condition.",
        )

    def _infer_month_range(self, text: str, snapshot: dict) -> tuple[datetime, datetime] | None:
        """Parse explicit month ranges such as '8-9/2025' without inventing dates."""
        as_of = self._parse_dt(snapshot.get("as_of"))
        if as_of is None:
            return None
        lowered = text.casefold()
        match = re.search(
            r"(?:tháng\s*)?(\d{1,2})\s*[-–]\s*(\d{1,2})\s*/\s*(\d{4})",
            lowered,
        )
        if not match:
            return None
        first, last, year = (int(part) for part in match.groups())
        if not (1 <= first <= 12 and 1 <= last <= 12) or first > last:
            return None
        try:
            start = datetime(year, first, 1, tzinfo=UTC)
            end = datetime(year, last, calendar.monthrange(year, last)[1], 23, 59, 59, tzinfo=UTC)
        except ValueError:
            return None
        if end > as_of:
            return None
        return start, end

    @staticmethod
    def _looks_historical(text: str) -> bool:
        lowered = text.casefold()
        return any(marker in lowered for marker in HISTORICAL_MARKERS)

    @staticmethod
    def _parse_dt(value: object) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None

    def _parameters_match(self, claim: TechnicalClaim, snapshot: dict, canonical: str) -> bool:
        """Verify default-matching parameters; custom settings require a new measured snapshot."""
        if not claim.extracted_parameters:
            return True
        expected: dict[str, object] = {"bar_offset": 0}
        if canonical.startswith(("rsi_", "macd_")) and canonical.endswith("_divergence"):
            expected.update(snapshot.get("divergence_parameters", {}))
        elif canonical in HISTORICAL_RSI_CLAIMS:
            expected.update(snapshot.get("divergence_parameters", {}))
            expected["threshold"] = (
                self.rsi_overbought
                if canonical == "historical_rsi_overbought"
                else self.rsi_oversold
            )
        elif canonical in POINT_RSI_CLAIMS:
            expected.update(snapshot.get("indicators", {}).get("rsi", {}).get("parameters", {}))
            expected["threshold"] = (
                self.rsi_overbought if canonical == "rsi_overbought" else self.rsi_oversold
            )
        elif canonical == "volume_increase":
            expected.update(snapshot.get("indicators", {}).get("rvol", {}).get("parameters", {}))
        elif canonical in ("breakout", "bearish_breakout"):
            expected.update(snapshot.get("structure_parameters", {}))
        return all(
            key in expected and expected[key] == value
            for key, value in claim.extracted_parameters.items()
        )

    @staticmethod
    def _set(claim: TechnicalClaim, supported: bool | None, reason: str) -> None:
        claim.verification_status = (
            VerificationStatus.INSUFFICIENT_EVIDENCE
            if supported is None
            else VerificationStatus.VERIFIED
            if supported
            else VerificationStatus.CONTRADICTED
        )
        claim.verification_reason = reason
