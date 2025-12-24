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
from .sender import DeviceSenderService
from .logging import configure_logging, get_logger


async def _discovery_loop(
    stop_event: asyncio.Event, config: Config, store: DeviceStore
) -> None:
    logger = get_logger("govee.discovery")
    service = DiscoveryService(config, store)
    await service.start()
    logger.info(
        "Discovery loop starting",
        extra={"interval": config.discovery_interval},
    )
    try:
        while not stop_event.is_set():
            logger.debug("Running discovery cycle")
            try:
                await service.run_cycle()
            except Exception:
                logger.exception("Discovery cycle failed")
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
    stop_event: asyncio.Event, config: Config, store: DeviceStore
) -> None:
    logger = get_logger("govee.artnet")
    service = ArtNetService(config, store)
    await service.start()
    try:
        await stop_event.wait()
    finally:
        await service.stop()
        logger.info("ArtNet loop stopped")


async def _sender_loop(
    stop_event: asyncio.Event, config: Config, store: DeviceStore
) -> None:
    logger = get_logger("govee.sender")
    service = DeviceSenderService(config, store)
    await service.start()
    try:
        await stop_event.wait()
    finally:
        await service.stop()
        logger.info("Sender loop stopped")


async def _api_loop(stop_event: asyncio.Event, config: Config, store: DeviceStore) -> None:
    logger = get_logger("govee.api")
    service = ApiService(config, store)
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
        asyncio.create_task(_discovery_loop(stop_event, config, store)),
        asyncio.create_task(_rate_limit_monitor(stop_event, config)),
        asyncio.create_task(_artnet_loop(stop_event, config, store)),
        asyncio.create_task(_sender_loop(stop_event, config, store)),
        asyncio.create_task(_api_loop(stop_event, config, store)),
    ]
    logger.info(
        "Bridge services started",
        extra={
            "artnet_port": config.artnet_port,
            "api_port": config.api_port,
            "db_path": str(config.db_path),
        },
    )

    try:
        await stop_event.wait()
    finally:
        await _shutdown_tasks(tasks, logger)
        logger.info("Bridge shutdown complete")


async def _shutdown_tasks(
    tasks: Iterable[asyncio.Task[None]], logger: logging.Logger
) -> None:
    for task in tasks:
        task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.gather(*tasks)


def run(cli_args: Optional[Iterable[str]] = None) -> None:
    """CLI entrypoint used by setuptools."""

    config = load_config(cli_args)
    configure_logging(config)
    logger = get_logger("govee")
    logger.debug("Loaded configuration", extra={"config": config})

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
