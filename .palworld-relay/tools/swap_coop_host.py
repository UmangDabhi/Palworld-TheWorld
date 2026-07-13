#!/usr/bin/env python3
"""Atomically rotate two Palworld co-op characters when the host changes.

The current host always occupies GUID 000...001. The incoming host currently
occupies their normal client GUID. This tool swaps those two identities in both
player saves and Level.sav in one parse/write cycle, avoiding the destructive
two-pass overwrite used by older host-fix recipes.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from palworld_save_tools.archive import UUID
from palworld_save_tools.gvas import GvasFile
from palworld_save_tools.palsav import compress_gvas_to_sav, decompress_sav_to_gvas
from palworld_save_tools.paltypes import PALWORLD_CUSTOM_PROPERTIES, PALWORLD_TYPE_HINTS

HOST_GUID = "00000000000000000000000000000001"
GUID_RE = re.compile(r"^[0-9a-fA-F]{32}$")


class SwapError(RuntimeError):
    pass


def normalize_guid(value: str) -> str:
    guid = value.replace("-", "").upper()
    if not GUID_RE.fullmatch(guid):
        raise SwapError(f"Expected a 32-character hexadecimal GUID, got {value!r}")
    return guid


def dashed(guid: str) -> str:
    value = normalize_guid(guid).lower()
    return f"{value[:8]}-{value[8:12]}-{value[12:16]}-{value[16:20]}-{value[20:]}"


def uuid(guid: str) -> UUID:
    return UUID.from_str(dashed(guid))


def load_sav(path: Path) -> tuple[dict, int, bool]:
    data = path.read_bytes()
    if len(data) < 12:
        raise SwapError(f"Save is too short: {path}")
    use_zlib = data[8:11] == b"PlZ"
    raw_gvas, save_type = decompress_sav_to_gvas(data)
    gvas = GvasFile.read(
        raw_gvas,
        PALWORLD_TYPE_HINTS,
        PALWORLD_CUSTOM_PROPERTIES,
        allow_nan=True,
    )
    return gvas.dump(), save_type, use_zlib


def encode_sav(document: dict, save_type: int, use_zlib: bool) -> bytes:
    gvas = GvasFile.load(document)
    raw = gvas.write(PALWORLD_CUSTOM_PROPERTIES)
    return compress_gvas_to_sav(raw, save_type, zlib=use_zlib)


def player_struct(document: dict) -> dict:
    try:
        return document["properties"]["SaveData"]["value"]
    except KeyError as exc:
        raise SwapError(f"Unexpected player-save structure; missing {exc}") from exc


def rewrite_player(document: dict, destination_guid: str) -> str:
    data = player_struct(document)
    destination = uuid(destination_guid)
    data["PlayerUId"]["value"] = destination
    individual = data["IndividualId"]["value"]
    individual["PlayerUId"]["value"] = destination
    return str(individual["InstanceId"]["value"]).lower()


def player_instance(document: dict) -> str:
    return str(player_struct(document)["IndividualId"]["value"]["InstanceId"]["value"]).lower()


def character_entries(level: dict) -> list:
    try:
        return level["properties"]["worldSaveData"]["value"]["CharacterSaveParameterMap"]["value"]
    except KeyError as exc:
        raise SwapError(f"Unexpected Level.sav structure; missing {exc}") from exc


def rewrite_character_map(level: dict, instance_to_guid: dict[str, str]) -> dict[str, int]:
    counts = {instance: 0 for instance in instance_to_guid}
    for entry in character_entries(level):
        try:
            instance = str(entry["key"]["InstanceId"]["value"]).lower()
        except KeyError:
            continue
        if instance in instance_to_guid:
            entry["key"]["PlayerUId"]["value"] = uuid(instance_to_guid[instance])
            counts[instance] += 1
    return counts


def remove_placeholder_character(level: dict, placeholder_instance: str | None) -> int:
    if not placeholder_instance:
        return 0
    entries = character_entries(level)
    before = len(entries)
    entries[:] = [
        entry
        for entry in entries
        if str(entry.get("key", {}).get("InstanceId", {}).get("value", "")).lower()
        != placeholder_instance.lower()
    ]
    return before - len(entries)


def replace_guid_value(value, mapping: dict[str, str]):
    try:
        normalized = normalize_guid(str(value))
    except SwapError:
        return value, False
    destination = mapping.get(normalized)
    return (uuid(destination), True) if destination else (value, False)


def rewrite_guilds(level: dict, mapping: dict[str, str]) -> int:
    try:
        groups = level["properties"]["worldSaveData"]["value"]["GroupSaveDataMap"]["value"]
    except KeyError:
        return 0
    rewrites = 0
    for group in groups:
        try:
            if group["value"]["GroupType"]["value"]["value"] != "EPalGroupType::Guild":
                continue
            raw = group["value"]["RawData"]["value"]
        except KeyError:
            continue

        for handle in raw.get("individual_character_handle_ids", []):
            value, changed = replace_guid_value(handle.get("guid"), mapping)
            if changed:
                handle["guid"] = value
                rewrites += 1

        if "admin_player_uid" in raw:
            value, changed = replace_guid_value(raw["admin_player_uid"], mapping)
            if changed:
                raw["admin_player_uid"] = value
                rewrites += 1

        for player in raw.get("players", []):
            value, changed = replace_guid_value(player.get("player_uid"), mapping)
            if changed:
                player["player_uid"] = value
                rewrites += 1
    return rewrites


def validate_player_bytes(data: bytes, expected_guid: str, temp_path: Path) -> None:
    temp_path.write_bytes(data)
    try:
        document, _, _ = load_sav(temp_path)
        actual = normalize_guid(str(player_struct(document)["PlayerUId"]["value"]))
        if actual != normalize_guid(expected_guid):
            raise SwapError(f"Round-trip validation failed: expected {expected_guid}, found {actual}")
    finally:
        temp_path.unlink(missing_ok=True)


def swap(world: Path, current_client_guid: str, incoming_client_guid: str) -> None:
    current_client_guid = normalize_guid(current_client_guid)
    incoming_client_guid = normalize_guid(incoming_client_guid)
    if HOST_GUID in (current_client_guid, incoming_client_guid):
        raise SwapError("Client GUIDs cannot be the co-op host GUID.")
    if current_client_guid == incoming_client_guid:
        raise SwapError("The two players must have different client GUIDs.")

    players = world / "Players"
    level_path = world / "Level.sav"
    host_path = players / f"{HOST_GUID}.sav"
    incoming_path = players / f"{incoming_client_guid}.sav"
    current_client_path = players / f"{current_client_guid}.sav"
    for path in (level_path, host_path, incoming_path):
        if not path.is_file():
            raise SwapError(f"Required save does not exist: {path}")

    level, level_type, level_zlib = load_sav(level_path)
    host_doc, host_type, host_zlib = load_sav(host_path)
    incoming_doc, incoming_type, incoming_zlib = load_sav(incoming_path)

    placeholder_instance = None
    if current_client_path.is_file() and current_client_path not in (host_path, incoming_path):
        placeholder_doc, _, _ = load_sav(current_client_path)
        placeholder_instance = player_instance(placeholder_doc)

    host_instance = rewrite_player(host_doc, current_client_guid)
    incoming_instance = rewrite_player(incoming_doc, HOST_GUID)
    counts = rewrite_character_map(
        level,
        {host_instance: current_client_guid, incoming_instance: HOST_GUID},
    )
    if counts[host_instance] < 1 or counts[incoming_instance] < 1:
        raise SwapError(f"Level.sav did not contain both character instances: {counts}")

    removed = remove_placeholder_character(level, placeholder_instance)
    guild_rewrites = rewrite_guilds(
        level,
        {HOST_GUID: current_client_guid, incoming_client_guid: HOST_GUID},
    )

    level_bytes = encode_sav(level, level_type, level_zlib)
    old_host_bytes = encode_sav(host_doc, host_type, host_zlib)
    new_host_bytes = encode_sav(incoming_doc, incoming_type, incoming_zlib)

    validation = world / ".player-swap-validation.tmp"
    validate_player_bytes(old_host_bytes, current_client_guid, validation)
    validate_player_bytes(new_host_bytes, HOST_GUID, validation)

    level_tmp = world / ".Level.sav.swap.tmp"
    old_host_tmp = players / ".old-host.swap.tmp"
    new_host_tmp = players / ".new-host.swap.tmp"
    level_tmp.write_bytes(level_bytes)
    # Parse the staged Level.sav before replacing the working copy.
    load_sav(level_tmp)
    old_host_tmp.write_bytes(old_host_bytes)
    new_host_tmp.write_bytes(new_host_bytes)

    os.replace(level_tmp, level_path)
    for path in {host_path, incoming_path, current_client_path}:
        path.unlink(missing_ok=True)
    os.replace(old_host_tmp, current_client_path)
    os.replace(new_host_tmp, host_path)

    print(
        f"SWAP_OK current-host -> {current_client_guid}; "
        f"incoming-host {incoming_client_guid} -> {HOST_GUID}; "
        f"guild_rewrites={guild_rewrites}; placeholder_entries_removed={removed}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("world", type=Path)
    parser.add_argument("current_client_guid")
    parser.add_argument("incoming_client_guid")
    args = parser.parse_args()
    try:
        swap(args.world.resolve(), args.current_client_guid, args.incoming_client_guid)
    except Exception as exc:
        print(f"SWAP_ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
