"""The monitors plugin."""

import asyncio
from collections import defaultdict
from copy import deepcopy
from typing import Any, cast

from ..common import CastBoolMixin, is_rotated, state
from ..types import MonitorInfo
from .interface import Plugin


def trim_offset(monitors) -> None:
    """Make the monitor set layout start at 0,0."""
    off_x = None
    off_y = None
    for mon in monitors:
        if off_x is None:
            off_x = mon["x"]

        if off_y is None:
            off_y = mon["y"]

        off_x = min(mon["x"], off_x)
        off_y = min(mon["y"], off_y)

    for mon in monitors:
        mon["x"] -= off_x
        mon["y"] -= off_y


def clean_pos(position):
    """Harmonize position format."""
    return position.lower().replace("_", "").replace("-", "")


def scale_and_rotate_mon(monitor):
    """Scale and rotate the monitor dimensions."""
    width = int(monitor["width"] / monitor["scale"])
    height = int(monitor["height"] / monitor["scale"])
    if is_rotated(monitor):
        width, height = height, width
    return width, height


def get_xy(place, main_mon, other_mon):
    """Get the XY position of a monitor according to another (after `place` is applied).

    Place syntax: "<top|left|bottom|right> [center|middle|end] of" (without spaces)
    """
    align_x = False  # if alignment is on X axis, else on Y axis
    scaled_m_w, scaled_m_h = scale_and_rotate_mon(main_mon)
    scaled_om_w, scaled_om_h = scale_and_rotate_mon(other_mon)

    if place[0] in ("t", "b"):  # top or bottom
        align_x = True
        x = other_mon["x"]
        y = other_mon["y"] - scaled_m_h if place[0] == "t" else other_mon["y"] + scaled_om_h
    else:  # left or right
        y = other_mon["y"]
        x = other_mon["x"] - scaled_m_w if place[0] == "l" else other_mon["x"] + scaled_om_w

    centered = "middle" in place or "center" in place

    if align_x:
        if centered:
            x += int((scaled_om_w - scaled_m_w) / 2)
        elif "end" in place:
            x += int(scaled_om_w - scaled_m_w)
    else:
        if centered:
            y += int((scaled_om_h - scaled_m_h) / 2)
        elif "end" in place:
            y += scaled_m_h - scaled_om_h
    return (x, y)


def build_graph(config):
    """Make a sorted graph based on the cleaned_config."""
    graph = defaultdict(list)
    for name1, positions in config.items():
        for pos, names in positions.items():
            tldr_direction = pos.startswith(("left", "top"))
            for name2 in names:
                if tldr_direction:
                    graph[name1].append(name2)
                else:
                    graph[name2].append(name1)
    return graph


class Extension(CastBoolMixin, Plugin):  # pylint: disable=missing-class-docstring
    """Control monitors layout."""

    _mon_by_pat_cache: dict[str, dict] = {}

    async def on_reload(self) -> None:
        """Reload the plugin."""
        self._clear_mon_by_pat_cache()
        monitors = await self.hyprctl_json("monitors")
        if self.cast_bool(self.config.get("startup_relayout"), True):
            await self.run_relayout(monitors)

        for mon in state.monitors:
            await self._hotplug_command(name=mon, monitors=monitors)

    # Command

    async def run_relayout(self, monitors: list[MonitorInfo] | None = None) -> None:
        """Recompute & apply every monitors's layout."""
        self._clear_mon_by_pat_cache()

        if monitors is None:
            monitors = cast(list[MonitorInfo], await self.hyprctl_json("monitors"))

        cleaned_config = self.resolve_names(monitors)
        if cleaned_config:
            self.log.debug("Using %s", cleaned_config)
        else:
            self.log.debug("No configuration item is applicable")
        graph = build_graph(cleaned_config)
        need_change = self._update_positions(monitors, graph, cleaned_config)
        every_monitor = {v["name"]: v for v in await self.hyprctl_json("monitors all")}
        if need_change:
            trim_offset(monitors)

            for monitor in sorted(monitors, key=lambda x: x["x"] + x["y"]):
                name = monitor["name"]
                this_mon = every_monitor[name]
                resolution = f"{this_mon['width']}x{this_mon['height']}@{this_mon['refreshRate']}"
                scale = this_mon["scale"]
                position = f"{monitor['x']}x{monitor['y']}"
                transform = this_mon["transform"]

                await self.hyprctl(
                    f"monitor {name},{resolution},{position},{scale},transform,{transform}",
                    "keyword",
                )

    # Event handlers

    async def event_monitoradded(self, name) -> None:
        """Triggers when a monitor is plugged."""
        await asyncio.sleep(self.config.get("new_monitor_delay", 1.0))
        monitors = await self.hyprctl_json("monitors")
        await self._hotplug_command(monitors, name)
        await self.run_relayout(monitors)

    # Utils

    async def _hotplug_command(self, monitors: list[MonitorInfo], name: str) -> None:
        """Run the hotplug command for the monitor."""
        monitors_by_descr = {m["description"]: m for m in monitors}
        monitors_by_name = {m["name"]: m for m in monitors}
        for descr, command in self.config.get("hotplug_commands", {}).items():
            mon = self._get_mon_by_pat(descr, monitors_by_descr, monitors_by_name)
            if mon and mon["name"] == name:
                await asyncio.create_subprocess_shell(command)
                break

    def _clear_mon_by_pat_cache(self) -> None:
        """Clear the cache."""
        self._mon_by_pat_cache = {}

    def _get_mon_by_pat(self, pat, description_db, name_db):
        """Return a (plugged) monitor object given its pattern or none if not found."""
        cached = self._mon_by_pat_cache.get(pat)
        if cached is None:
            cached = name_db.get(pat)
            if cached is None:
                for full_descr in description_db:
                    if pat in full_descr:
                        cached = description_db[full_descr]
                        break
            if cached:
                self._mon_by_pat_cache[pat] = cast(dict[str, dict], cached)
        return cached

    _flipped_positions = {
        "topof": "bottomof",
        "bottomof": "topof",
        "leftof": "rightof",
        "rightof": "leftof",
        "topmiddleof": "bottommiddleof",
        "bottommiddleof": "topmiddleof",
        "leftmiddleof": "rightmiddleof",
        "rightmiddleof": "leftmiddleof",
        "topcenterof": "bottomcenterof",
        "bottomcenterof": "topcenterof",
        "leftcenterof": "rightcenterof",
        "rightcenterof": "leftcenterof",
        "topendof": "bottomendof",
        "bottomendof": "topendof",
        "leftendof": "rightendof",
        "rightendof": "leftendof",
    }

    def _update_positions(self, monitors, graph, config):
        """Apply configuration to monitors_by_name using graph."""
        monitors_by_name = {m["name"]: m for m in monitors}
        requires_update = False
        for _ in range(len(monitors_by_name) ** 2):
            changed = False
            for name in reversed(graph):
                mon1 = monitors_by_name[name]
                for name2 in graph[name]:
                    mon2 = monitors_by_name[name2]
                    for pos, _ in self.get_matching_config(name, name2, config):
                        try:
                            x, y = get_xy(self._flipped_positions[pos.lower()], mon2, mon1)
                        except TypeError:
                            self.log.error("Invalid position %s", pos)
                            continue
                        if x != mon2["x"]:
                            changed = True
                            requires_update = True
                            mon2["x"] = x
                        if y != mon2["y"]:
                            changed = True
                            requires_update = True
                            mon2["y"] = y
            if not changed:
                break
        return requires_update

    def get_matching_config(self, name1, name2, config):
        """Return rules matching name1 or name2 (relative to name1), looking up config.

        Returns a list of tuples (position, name) where name is the other monitor's name.
        """
        results = []
        ref_set = {name1, name2}
        for name_a, positions in config.items():
            for pos, names in positions.items():
                lpos = clean_pos(pos)
                for name_b in names:
                    if {name_a, name_b} == ref_set:
                        if name_a == name1:
                            results.append((lpos, name_b))
                        else:
                            results.append((self._flipped_positions[lpos], name_a))
        return results

    def resolve_names(self, monitors) -> dict[str, Any]:
        """Change partial descriptions used in config for monitor names.

        Args:
            monitors: list of plugged monitors
        Returns:
            dict: cleaned config
        """
        placement_rules = deepcopy(self.config.get("placement", {}))
        monitors_by_descr = {m["description"]: m for m in monitors}
        cleaned_config: dict[str, dict[str, Any]] = {}
        plugged_monitors = {m["name"]: m for m in monitors}
        for descr1, placement in placement_rules.items():
            mon = self._get_mon_by_pat(descr1, monitors_by_descr, plugged_monitors)
            if not mon:
                continue
            name = mon["name"]
            if name not in plugged_monitors:
                continue
            cleaned_config[name] = {}
            for position, descr_list in placement.items():
                if isinstance(descr_list, str):
                    descr_list = [descr_list]
                resolved = []
                for p in descr_list:
                    r = self._get_mon_by_pat(p, monitors_by_descr, plugged_monitors)
                    if r:
                        resolved.append(r["name"])
                if resolved:
                    cleaned_config[name][clean_pos(position)] = [r for r in resolved if r in plugged_monitors]
        return cleaned_config
