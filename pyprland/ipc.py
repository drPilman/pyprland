#!/bin/env python
""" Interact with hyprland using sockets """
import asyncio
from logging import Logger
from typing import Any
import json
import os

from .common import get_logger, PyprError

log: Logger | None = None

HYPRCTL = f'/tmp/hypr/{ os.environ["HYPRLAND_INSTANCE_SIGNATURE"] }/.socket.sock'
EVENTS = f'/tmp/hypr/{ os.environ["HYPRLAND_INSTANCE_SIGNATURE"] }/.socket2.sock'


async def get_event_stream():
    "Returns a new event socket connection"
    return await asyncio.open_unix_connection(EVENTS)


async def hyprctlJSON(command) -> list[dict[str, Any]] | dict[str, Any]:
    """Run an IPC command and return the JSON output."""
    assert log
    log.debug(command)
    try:
        ctl_reader, ctl_writer = await asyncio.open_unix_connection(HYPRCTL)
    except FileNotFoundError as e:
        log.critical("hyprctl socket not found! is it running ?")
        raise PyprError() from e
    ctl_writer.write(f"-j/{command}".encode())
    await ctl_writer.drain()
    resp = await ctl_reader.read()
    ctl_writer.close()
    await ctl_writer.wait_closed()
    ret = json.loads(resp)
    assert isinstance(ret, (list, dict))
    return ret


def _format_command(command_list, default_base_command):
    "helper function to format BATCH commands"
    for command in command_list:
        if isinstance(command, str):
            yield f"{default_base_command} {command}"
        else:
            yield f"{command[1]} {command[0]}"


async def hyprctl(command, base_command="dispatch") -> bool:
    """Run an IPC command. Returns success value."""
    assert log
    log.debug(command)
    try:
        ctl_reader, ctl_writer = await asyncio.open_unix_connection(HYPRCTL)
    except FileNotFoundError as e:
        log.critical("hyprctl socket not found! is it running ?")
        raise PyprError() from e

    if isinstance(command, list):
        ctl_writer.write(
            f"[[BATCH]] {' ; '.join(_format_command(command, base_command))}".encode()
        )
    else:
        ctl_writer.write(f"/{base_command} {command}".encode())
    await ctl_writer.drain()
    resp = await ctl_reader.read(100)
    ctl_writer.close()
    await ctl_writer.wait_closed()
    r: bool = resp == b"ok" * (len(resp) // 2)
    if not r:
        log.error("FAILED %s", resp)
    return r


async def get_focused_monitor_props() -> dict[str, Any]:
    "Returns focused monitor data"
    for monitor in await hyprctlJSON("monitors"):
        assert isinstance(monitor, dict)
        if monitor.get("focused"):
            return monitor
    raise RuntimeError("no focused monitor")


def init():
    "initialize logging"
    global log
    log = get_logger("ipc")
