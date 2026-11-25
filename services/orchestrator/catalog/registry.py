"""Versioned catalog snapshots, quarantine of new models, retirement alerts, and stale fallback."""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from services.orchestrator.contracts import HostCapabilityReport, ModelRecord

from .sources import HostDiscoveryCatalogMapper, OfficialCatalogAdapter, SourceFetchError


class CatalogStaleError(RuntimeError):
    """Raised when automatic dispatch is attempted against a stale catalog snapshot."""


@dataclass(frozen=True)
class RetirementAlert:
    provider: str
    model_id: str
    auth_mode: str
    reason: str
    detected_at: str


@dataclass(frozen=True)
class CatalogSnapshot:
    snapshot_id: str
    version: int
    source_urls: list[str]
    models: list[ModelRecord]
    fetched_at: str
    expires_at: str
    is_stale: bool
    checksum: str
    alerts: list[RetirementAlert] = field(default_factory=list)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _compute_checksum(models: list[ModelRecord]) -> str:
    payload = json.dumps(
        [m.model_dump() for m in sorted(models, key=lambda x: (x.provider, x.model_id, x.auth_mode))],
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class CatalogService:
    """Manages immutable catalog snapshots, daily/on-demand refresh, and lifecycle states."""

    def __init__(
        self,
        adapters: list[OfficialCatalogAdapter] | None = None,
        ttl_hours: int = 24,
    ) -> None:
        self._adapters = adapters or []
        self._ttl_hours = ttl_hours
        self._snapshots: list[CatalogSnapshot] = []
        self._host_mapper = HostDiscoveryCatalogMapper()

    @property
    def latest_snapshot(self) -> CatalogSnapshot | None:
        return self._snapshots[-1] if self._snapshots else None

    def get_snapshot(self, snapshot_id: str) -> CatalogSnapshot | None:
        for snap in self._snapshots:
            if snap.snapshot_id == snapshot_id:
                return snap
        return None

    def refresh(
        self,
        *,
        host_report: HostCapabilityReport | None = None,
        now: datetime | None = None,
    ) -> CatalogSnapshot:
        """
        Fetch official catalogs + optional verified host CLI capabilities.
        - New models not previously verified enter `candidate` quarantine.
        - Previously known models missing from refresh or marked deprecated emit RetirementAlert.
        - On upstream failure, returns last known snapshot marked `is_stale=True`.
        """
        current_dt = now or _utc_now()
        now_iso = _iso(current_dt)
        expires_iso = _iso(current_dt + timedelta(hours=self._ttl_hours))

        prev = self.latest_snapshot
        prev_by_key: dict[tuple[str, str, str], ModelRecord] = {}
        if prev is not None:
            for m in prev.models:
                prev_by_key[(m.provider, m.model_id, m.auth_mode)] = m

        collected: dict[tuple[str, str, str], ModelRecord] = {}
        source_urls: list[str] = []
        fetch_failures: list[str] = []

        for adapter in self._adapters:
            source_urls.append(adapter.source_url)
            try:
                models = adapter.fetch_models(now_iso=now_iso)
                for m in models:
                    key = (m.provider, m.model_id, m.auth_mode)
                    if key not in prev_by_key and m.status == "verified":
                        # New models must enter candidate quarantine until compatibility/benchmark verified
                        m = m.model_copy(update={"status": "candidate"})
                    collected[key] = m
            except SourceFetchError as exc:
                fetch_failures.append(f"{adapter.provider}: {exc}")

        if host_report is not None:
            source_urls.append(f"host-discovery://{host_report.host_os}")
            for hm in self._host_mapper.build_records(host_report):
                key = (hm.provider, hm.model_id, hm.auth_mode)
                # Host-verified CLI selectors are directly eligible (`verified`)
                collected[key] = hm

        if fetch_failures and not collected and prev is not None:
            stale_snap = CatalogSnapshot(
                snapshot_id=prev.snapshot_id,
                version=prev.version,
                source_urls=prev.source_urls,
                models=prev.models,
                fetched_at=prev.fetched_at,
                expires_at=prev.expires_at,
                is_stale=True,
                checksum=prev.checksum,
                alerts=prev.alerts,
            )
            self._snapshots[-1] = stale_snap
            return stale_snap

        alerts: list[RetirementAlert] = []
        for key, old_rec in prev_by_key.items():
            if key not in collected and old_rec.status != "deprecated":
                retired = old_rec.model_copy(update={"status": "deprecated"})
                collected[key] = retired
                alerts.append(
                    RetirementAlert(
                        provider=old_rec.provider,
                        model_id=old_rec.model_id,
                        auth_mode=old_rec.auth_mode,
                        reason="Model missing from latest refresh; marked deprecated without silent switch",
                        detected_at=now_iso,
                    )
                )
            elif key in collected and collected[key].status == "deprecated" and old_rec.status != "deprecated":
                alerts.append(
                    RetirementAlert(
                        provider=old_rec.provider,
                        model_id=old_rec.model_id,
                        auth_mode=old_rec.auth_mode,
                        reason="Model marked deprecated in official source",
                        detected_at=now_iso,
                    )
                )

        models_list = list(collected.values())
        snap = CatalogSnapshot(
            snapshot_id=str(uuid.uuid4()),
            version=(prev.version + 1) if prev else 1,
            source_urls=sorted(set(source_urls)),
            models=models_list,
            fetched_at=now_iso,
            expires_at=expires_iso,
            is_stale=bool(fetch_failures),
            checksum=_compute_checksum(models_list),
            alerts=alerts,
        )
        self._snapshots.append(snap)
        return snap

    def is_expired(self, snapshot: CatalogSnapshot, now: datetime | None = None) -> bool:
        if snapshot.is_stale:
            return True
        current_dt = now or _utc_now()
        exp_dt = datetime.strptime(snapshot.expires_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return current_dt >= exp_dt
