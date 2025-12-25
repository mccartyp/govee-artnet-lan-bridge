"""Entrypoint for the Govee Artnet LAN bridge."""

from __future__ import annotations

import asyncio
import contextlib
import signal
import logging
from typing import Iterable, List, Optional

from .config import Config, load_config
from .db import apply_migrations
from .devices import DeviceStore
from .api import ApiService
from .artnet import ArtNetService
from .discovery import DiscoveryService
from .health import BackoffPolicy, HealthMonitor
from .sender import DeviceSenderService
from .logging import configure_logging, get_logger


async def _discovery_loop(
    stop_event: asyncio.Event,
    config: Config,
    store: DeviceStore,
    health: HealthMonitor,
) -> None:
    logger = get_logger("govee.discovery")
    backoff = BackoffPolicy(
        base=config.device_backoff_base,
        factor=config.device_backoff_factor,
        maximum=config.device_backoff_max,
    )
    service = DiscoveryService(config, store)
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
        logger.info("Discovery loop stopped")


async def _rate_limit_monitor(stop_event: asyncio.Event, config: Config) -> None:
    logger = get_logger("govee.rate_limit")
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
    stop_event: asyncio.Event, config: Config, store: DeviceStore, health: HealthMonitor
) -> None:
    logger = get_logger("govee.artnet")
    service = ArtNetService(config, store)
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
    logger.info("ArtNet loop stopped")


async def _sender_loop(
    stop_event: asyncio.Event, config: Config, store: DeviceStore, health: HealthMonitor
) -> None:
    logger = get_logger("govee.sender")
    service = DeviceSenderService(config, store, health=health)
    await service.start()
    try:
        await stop_event.wait()
    finally:
        await service.stop()
        logger.info("Sender loop stopped")


async def _api_loop(
    stop_event: asyncio.Event, config: Config, store: DeviceStore, health: HealthMonitor
) -> None:
    logger = get_logger("govee.api")
    service = ApiService(config, store, health=health)
    await service.start()
    try:
        await stop_event.wait()
    finally:
        await service.stop()
        logger.info("API loop stopped")


async def _run_async(config: Config) -> None:
    logger = get_logger("govee")
    stop_event = asyncio.Event()
    store = DeviceStore(config.db_path)
    await store.start()
    health = HealthMonitor(
        ("discovery", "sender", "artnet", "api"),
        failure_threshold=config.subsystem_failure_threshold,
        cooldown_seconds=config.subsystem_failure_cooldown,
    )

    def _request_shutdown(sig: Optional[int] = None) -> None:
        if not stop_event.is_set():
            logger.warning("Shutdown requested", extra={"signal": sig})
            stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _request_shutdown, sig.name)

    await store.sync_manual_devices(config.manual_devices)
    tasks: List[asyncio.Task[None]] = [
        asyncio.create_task(_discovery_loop(stop_event, config, store, health)),
        asyncio.create_task(_rate_limit_monitor(stop_event, config)),
        asyncio.create_task(_artnet_loop(stop_event, config, store, health)),
        asyncio.create_task(_sender_loop(stop_event, config, store, health)),
        asyncio.create_task(_api_loop(stop_event, config, store, health)),
    ]
    logger.info(
        "Bridge services started",
        extra={
            "artnet_port": config.artnet_port,
            "api_port": config.api_port,
            "db_path": str(config.db_path),
            "dry_run": config.dry_run,
        },
    )

    try:
        await stop_event.wait()
    finally:
        await _shutdown_tasks(tasks, logger)
        await store.stop()
        logger.info("Bridge shutdown complete")


async def _shutdown_tasks(
    tasks: Iterable[asyncio.Task[None]], logger: logging.Logger
) -> None:
    for task in tasks:
        task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.gather(*tasks)


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
        asyncio.run(_run_async(config))
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")


if __name__ == "__main__":
    run()
