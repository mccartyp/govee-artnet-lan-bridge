"""Entrypoint for the Govee Artnet LAN bridge."""

from __future__ import annotations

import asyncio
import contextlib
import signal
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, List, Mapping, Optional

from .capabilities import CapabilityCatalog
from .config import Config, load_config
from .db import apply_migrations
from .devices import DeviceStore
from .api import ApiService
from .artnet import ArtNetService
from .discovery import DiscoveryService
from .health import BackoffPolicy, HealthMonitor
from .poller import DevicePollerService
from .protocol import GoveeProtocolService
from .sender import DeviceSenderService
from .logging import configure_logging, get_logger


@dataclass
class RunningServices:
    """Track running service instances for state capture."""

    protocol: Optional[GoveeProtocolService] = None
    discovery: Optional[DiscoveryService] = None
    artnet: Optional[ArtNetService] = None
    sender: Optional[DeviceSenderService] = None
    poller: Optional[DevicePollerService] = None
    api: Optional[ApiService] = None


async def _protocol_loop(
    stop_event: asyncio.Event,
    config: Config,
    services: Optional[RunningServices] = None,
) -> None:
    logger = get_logger("artnet.protocol")
    service = GoveeProtocolService(config)
    if services is not None:
        services.protocol = service
    await service.start()
    logger.info("Protocol service started")
    try:
        await stop_event.wait()
    finally:
        await service.stop()
        if services is not None:
            services.protocol = None
        logger.info("Protocol service stopped")


async def _discovery_loop(
    stop_event: asyncio.Event,
    config: Config,
    store: DeviceStore,
    health: HealthMonitor,
    protocol: Optional[GoveeProtocolService] = None,
    services: Optional[RunningServices] = None,
) -> None:
    logger = get_logger("artnet.discovery")
    backoff = BackoffPolicy(
        base=config.device_backoff_base,
        factor=config.device_backoff_factor,
        maximum=config.device_backoff_max,
    )
    proto_inst = protocol.protocol if protocol else None
    service = DiscoveryService(config, store, protocol=proto_inst)
    if services is not None:
        services.discovery = service
    start_failures = 0
    while not stop_event.is_set():
        allowed, remaining = await health.allow_attempt("discovery")
        if not allowed:
            logger.warning(
                "Discovery temporarily suppressed after repeated failures",
                extra={"cooldown_seconds": round(remaining, 2)},
            )
            await _wait_or_stop(stop_event, remaining)
            continue
        try:
            await service.start()
            await health.record_success("discovery")
            start_failures = 0
            break
        except Exception as exc:
            start_failures += 1
            logger.exception("Discovery service failed to start")
            await health.record_failure("discovery", exc)
            await _wait_or_stop(stop_event, backoff.delay(start_failures))
    else:
        return

    logger.info(
        "Discovery loop starting",
        extra={"interval": config.discovery_interval},
    )
    failures = 0
    try:
        while not stop_event.is_set():
            logger.debug("Running discovery cycle")
            try:
                await service.run_cycle()
                await health.record_success("discovery")
                failures = 0
            except Exception as exc:
                logger.exception("Discovery cycle failed")
                failures += 1
                await health.record_failure("discovery", exc)
                await _wait_or_stop(stop_event, backoff.delay(failures))
                continue
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=config.discovery_interval
                )
            except asyncio.TimeoutError:
                continue
    except asyncio.CancelledError:
        logger.info("Discovery loop cancelled")
        raise
    finally:
        await service.stop()
        if services is not None:
            services.discovery = None
        logger.info("Discovery loop stopped")


async def _rate_limit_monitor(stop_event: asyncio.Event, config: Config) -> None:
    logger = get_logger("artnet.rate_limit")
    logger.info(
        "Rate limit monitor starting",
        extra={
            "per_second": config.rate_limit_per_second,
            "burst": config.rate_limit_burst,
        },
    )
    try:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=5)
            except asyncio.TimeoutError:
                logger.debug("Rate limiter heartbeat")
                continue
    except asyncio.CancelledError:
        logger.info("Rate limit monitor cancelled")
        raise
    finally:
        logger.info("Rate limit monitor stopped")


async def _artnet_loop(
    stop_event: asyncio.Event,
    config: Config,
    store: DeviceStore,
    health: HealthMonitor,
    services: Optional[RunningServices] = None,
    artnet_state: Optional[Mapping[str, Mapping[str, Any]]] = None,
    event_bus: Optional[Any] = None,
) -> None:
    logger = get_logger("artnet.artnet")
    service = ArtNetService(config, store, initial_last_payloads=artnet_state, event_bus=event_bus)
    if services is not None:
        services.artnet = service
    backoff = BackoffPolicy(
        base=config.device_backoff_base,
        factor=config.device_backoff_factor,
        maximum=config.device_backoff_max,
    )
    failures = 0
    while not stop_event.is_set():
        allowed, remaining = await health.allow_attempt("artnet")
        if not allowed:
            logger.warning(
                "ArtNet listener suppressed after repeated failures",
                extra={"cooldown_seconds": round(remaining, 2)},
            )
            await _wait_or_stop(stop_event, remaining)
            continue
        try:
            await service.start()
            await health.record_success("artnet")
        except Exception as exc:
            failures += 1
            logger.exception("ArtNet service failed to start; will retry")
            await health.record_failure("artnet", exc)
            await _wait_or_stop(stop_event, backoff.delay(failures))
            continue
        failures = 0
        wait_tasks = [
            asyncio.create_task(stop_event.wait()),
            asyncio.create_task(service.error_event.wait()),
        ]
        done, pending = await asyncio.wait(wait_tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        if stop_event.is_set():
            break
        logger.warning("ArtNet listener restarting after error")
        await health.record_failure("artnet")
        failures += 1
        await _wait_or_stop(stop_event, backoff.delay(failures))
    await service.stop()
    if services is not None:
        services.artnet = None
    logger.info("ArtNet loop stopped")


async def _sender_loop(
    stop_event: asyncio.Event,
    config: Config,
    store: DeviceStore,
    health: HealthMonitor,
    services: Optional[RunningServices] = None,
) -> None:
    logger = get_logger("artnet.sender")
    service = DeviceSenderService(config, store, health=health)
    if services is not None:
        services.sender = service
    await service.start()
    try:
        await stop_event.wait()
    finally:
        await service.stop()
        logger.info("Sender loop stopped")
        if services is not None:
            services.sender = None


async def _api_loop(
    stop_event: asyncio.Event,
    config: Config,
    store: DeviceStore,
    health: HealthMonitor,
    services: Optional[RunningServices] = None,
    reload_callback: Optional[Callable[[], Awaitable[None]]] = None,
    log_buffer: Optional[Any] = None,
    event_bus: Optional[Any] = None,
) -> None:
    logger = get_logger("artnet.api")
    service = ApiService(
        config,
        store,
        health=health,
        reload_callback=reload_callback,
        log_buffer=log_buffer,
        event_bus=event_bus,
    )
    if services is not None:
        services.api = service
    await service.start()
    try:
        await stop_event.wait()
    finally:
        await service.stop()
        logger.info("API loop stopped")
        if services is not None:
            services.api = None


async def _poller_loop(
    stop_event: asyncio.Event,
    config: Config,
    store: DeviceStore,
    health: HealthMonitor,
    protocol: Optional[GoveeProtocolService] = None,
    services: Optional[RunningServices] = None,
) -> None:
    logger = get_logger("artnet.poller")
    service = DevicePollerService(config, store, health=health)
    if services is not None:
        services.poller = service
    await service.start()
    try:
        await stop_event.wait()
    finally:
        await service.stop()
        if services is not None:
            services.poller = None
        logger.info("Poller loop stopped")


async def _stop_services(
    stop_event: asyncio.Event, tasks: Iterable[asyncio.Task[None]], logger: logging.Logger
) -> None:
    stop_event.set()
    done, pending = await asyncio.wait(set(tasks), timeout=5)
    for task in pending:
        task.cancel()
    if pending:
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.gather(*pending)
    for task in done:
        with contextlib.suppress(asyncio.CancelledError):
            await task


def _load_reloaded_config(
    cli_args: Optional[Iterable[str]],
    logger: logging.Logger,
    current_config: Config,
) -> Optional[Config]:
    try:
        new_config = load_config(cli_args)
    except Exception:
        logger.exception("Failed to reload configuration; keeping existing settings")
        return None

    if new_config.db_path != current_config.db_path:
        logger.error(
            "Config reload rejected because db_path changed; restart required",
            extra={"current_db_path": str(current_config.db_path), "new_db_path": str(new_config.db_path)},
        )
        return None
    if new_config.capability_catalog_path != current_config.capability_catalog_path:
        logger.error(
            "Config reload rejected because capability_catalog_path changed; restart required",
            extra={
                "current_catalog_path": str(current_config.capability_catalog_path),
                "new_catalog_path": str(new_config.capability_catalog_path),
            },
        )
        return None
    return new_config


def _load_capability_catalog(path: Path, logger: logging.Logger) -> CapabilityCatalog:
    try:
        return CapabilityCatalog.from_path(path)
    except Exception:
        logger.exception("Failed to load capability catalog", extra={"path": str(path)})
        raise


async def _run_async(config: Config, cli_args: Optional[Iterable[str]] = None) -> None:
    logger = get_logger("govee")
    shutdown_event = asyncio.Event()
    reload_event = asyncio.Event()

    # Initialize log buffer and event_bus BEFORE creating store
    log_buffer = None
    event_bus = None
    if config.log_buffer_enabled:
        from .log_buffer import LogBuffer
        log_buffer = LogBuffer(max_size=config.log_buffer_size)
        logger.info("Log buffer enabled", extra={"size": config.log_buffer_size})

    if config.event_bus_enabled:
        from .events import EventBus
        event_bus = EventBus()
        logger.info("Event bus enabled")

    catalog = _load_capability_catalog(config.capability_catalog_path, logger)
    store = DeviceStore(config.db_path, capability_catalog=catalog, event_bus=event_bus)
    await store.start()
    await store.refresh_metrics()

    # Reconfigure logging with log buffer
    if log_buffer is not None:
        configure_logging(config, log_buffer)
        logger = get_logger("govee")  # Get logger again after reconfiguration

    def _request_shutdown(sig: Optional[int] = None) -> None:
        if not shutdown_event.is_set():
            logger.warning("Shutdown requested", extra={"signal": sig})
            shutdown_event.set()

    def _request_reload(sig: Optional[int] = None) -> None:
        logger.warning("Config reload requested", extra={"signal": sig})
        reload_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _request_shutdown, sig.name)
    with contextlib.suppress(NotImplementedError):
        loop.add_signal_handler(signal.SIGHUP, _request_reload, "SIGHUP")

    artnet_state: Optional[Mapping[str, Mapping[str, Any]]] = None
    current_config = config

    while not shutdown_event.is_set():
        await store.sync_manual_devices(current_config.manual_devices)
        health = HealthMonitor(
            ("discovery", "sender", "artnet", "api", "poller"),
            failure_threshold=current_config.subsystem_failure_threshold,
            cooldown_seconds=current_config.subsystem_failure_cooldown,
            event_bus=event_bus,
        )
        services = RunningServices()
        stop_event = asyncio.Event()

        # Start protocol service first (provides shared UDP listener)
        protocol_task = asyncio.create_task(_protocol_loop(stop_event, current_config, services))
        # Wait for protocol to be ready
        await asyncio.sleep(0.1)
        protocol_service = services.protocol

        tasks: List[asyncio.Task[None]] = [
            protocol_task,
            asyncio.create_task(_discovery_loop(stop_event, current_config, store, health, protocol_service, services)),
            asyncio.create_task(_rate_limit_monitor(stop_event, current_config)),
            asyncio.create_task(_artnet_loop(stop_event, current_config, store, health, services, artnet_state, event_bus)),
            asyncio.create_task(_sender_loop(stop_event, current_config, store, health, services)),
            asyncio.create_task(_poller_loop(stop_event, current_config, store, health, protocol_service, services)),
            asyncio.create_task(_api_loop(stop_event, current_config, store, health, services, _request_reload, log_buffer, event_bus)),
        ]
        logger.info(
            "Bridge services started",
            extra={
                "artnet_port": current_config.artnet_port,
                "api_port": current_config.api_port,
                "db_path": str(current_config.db_path),
                "dry_run": current_config.dry_run,
            },
        )

        while True:
            wait_tasks = [
                asyncio.create_task(shutdown_event.wait()),
                asyncio.create_task(reload_event.wait()),
            ]
            done, pending = await asyncio.wait(wait_tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            for task in done:
                with contextlib.suppress(asyncio.CancelledError):
                    await task

            if shutdown_event.is_set():
                await _stop_services(stop_event, tasks, logger)
                break

            reload_event.clear()
            new_config = _load_reloaded_config(cli_args, logger, current_config)
            if new_config is None:
                logger.warning("Continuing with existing configuration after failed reload")
                continue

            artnet_service = services.artnet
            await _stop_services(stop_event, tasks, logger)
            if artnet_service is not None:
                artnet_state = artnet_service.snapshot_last_payloads()
            current_config = new_config
            configure_logging(current_config, log_buffer)
            logger = get_logger("govee")  # Get logger again after reconfiguration
            logger.info("Configuration reloaded", extra={"config": current_config.logging_dict()})
            break

        if shutdown_event.is_set():
            break

    await store.stop()
    logger.info("Bridge shutdown complete")


async def _wait_or_stop(stop_event: asyncio.Event, delay: float) -> None:
    if delay <= 0:
        return
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=delay)
    except asyncio.TimeoutError:
        return


def run(cli_args: Optional[Iterable[str]] = None) -> None:
    """CLI entrypoint used by setuptools."""

    config = load_config(cli_args)
    configure_logging(config)
    logger = get_logger("govee")
    logger.info("Loaded configuration", extra={"config": config.logging_dict()})

    apply_migrations(config.db_path)
    if config.migrate_only:
        logger.info("Migrations complete; exiting per configuration.")
        return
    try:
        asyncio.run(_run_async(config, cli_args))
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")


if __name__ == "__main__":
    run()
