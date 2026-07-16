#!/usr/bin/env python3
"""Validate and atomically rotate two Palworld local co-op characters."""

from __future__ import annotations

import argparse
import io
import os
import re
import sys
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path

from palworld_save_tools.archive import UUID
from palworld_save_tools.gvas import GvasFile
from palworld_save_tools.palsav import compress_gvas_to_sav, decompress_sav_to_gvas
from palworld_save_tools.paltypes import PALWORLD_CUSTOM_PROPERTIES, PALWORLD_TYPE_HINTS

HOST_GUID = "00000000000000000000000000000001"
ZERO_GUID = "00000000000000000000000000000000"
GUID_RE = re.compile(r"^[0-9a-fA-F]{32}$")
REQUIRED_WORLD_PATHS = ("Level.sav", "LevelMeta.sav", "WorldOption.sav", "LocalData.sav", "Players")


class SwapError(RuntimeError):
    pass


@dataclass(frozen=True)
class PlayerSummary:
    guid: str
    instance: str
    item_slots: int
    pals: int


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
    if not path.is_file():
        raise SwapError(f"Required save does not exist: {path}")
    data = path.read_bytes()
    if len(data) < 12:
        raise SwapError(f"Save is too short or corrupt: {path}")
    use_zlib = data[8:11] == b"PlZ"
    parser_output = io.StringIO()
    try:
        with redirect_stdout(parser_output):
            raw_gvas, save_type = decompress_sav_to_gvas(data)
            gvas = GvasFile.read(
                raw_gvas,
                PALWORLD_TYPE_HINTS,
                PALWORLD_CUSTOM_PROPERTIES,
                allow_nan=True,
            )
    except Exception as exc:
        raise SwapError(f"Could not parse {path}: {exc}") from exc
    return gvas.dump(), save_type, use_zlib


def encode_sav(document: dict, save_type: int, use_zlib: bool) -> bytes:
    parser_output = io.StringIO()
    try:
        with redirect_stdout(parser_output):
            raw = GvasFile.load(document).write(PALWORLD_CUSTOM_PROPERTIES)
            return compress_gvas_to_sav(raw, save_type, zlib=use_zlib)
    except Exception as exc:
        raise SwapError(f"Could not encode staged save: {exc}") from exc


def world_data(level: dict) -> dict:
    try:
        return level["properties"]["worldSaveData"]["value"]
    except KeyError as exc:
        raise SwapError(f"Unexpected Level.sav structure; missing {exc}") from exc


def player_struct(document: dict) -> dict:
    try:
        return document["properties"]["SaveData"]["value"]
    except KeyError as exc:
        raise SwapError(f"Unexpected player-save structure; missing {exc}") from exc


def player_instance(document: dict) -> str:
    try:
        return str(player_struct(document)["IndividualId"]["value"]["InstanceId"]["value"]).lower()
    except KeyError as exc:
        raise SwapError(f"Player save is missing its character instance: {exc}") from exc


def container_id(container: dict) -> str:
    try:
        return str(container["value"]["ID"]["value"]).lower()
    except KeyError as exc:
        raise SwapError(f"Unexpected player-save container structure; missing {exc}") from exc


def player_container_ids(document: dict) -> tuple[dict[str, str], dict[str, str]]:
    data = player_struct(document)
    try:
        inventory = data["InventoryInfo"]["value"]
        item_ids = {
            "inventory": container_id(inventory["CommonContainerId"]),
            "drop-slot": container_id(inventory["DropSlotContainerId"]),
            "key-items": container_id(inventory["EssentialContainerId"]),
            "food-equipment": container_id(inventory["FoodEquipContainerId"]),
            "weapon-loadout": container_id(inventory["WeaponLoadOutContainerId"]),
        }
        character_ids = {
            "party": container_id(data["OtomoCharacterContainerId"]),
            "palbox": container_id(data["PalStorageContainerId"]),
        }
    except KeyError as exc:
        raise SwapError(f"Player save is missing inventory or Pal-container data: {exc}") from exc
    return item_ids, character_ids


def keyed_entries(level: dict, name: str) -> list:
    try:
        entries = world_data(level)[name]["value"]
    except KeyError as exc:
        raise SwapError(f"Level.sav is missing {name}: {exc}") from exc
    if not isinstance(entries, list):
        raise SwapError(f"Level.sav {name} is not a list")
    return entries


def entry_id(entry: dict) -> str:
    return str(entry["key"]["ID"]["value"]).lower()


def entry_instance(entry: dict) -> str:
    return str(entry["key"]["InstanceId"]["value"]).lower()


def entry_player_guid(entry: dict) -> str:
    return normalize_guid(str(entry["key"]["PlayerUId"]["value"]))


def save_parameter(entry: dict) -> dict:
    try:
        return entry["value"]["RawData"]["value"]["object"]["SaveParameter"]["value"]
    except KeyError:
        return {}


def is_player_entry(entry: dict) -> bool:
    return bool(save_parameter(entry).get("IsPlayer", {}).get("value", False))


def index_unique(entries: list, key_func, label: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for entry in entries:
        try:
            key = key_func(entry)
        except KeyError as exc:
            raise SwapError(f"Malformed {label} entry; missing {exc}") from exc
        if key in result:
            raise SwapError(f"Duplicate {label} ID in Level.sav: {key}")
        result[key] = entry
    return result


def occupied_slots(container: dict) -> list:
    try:
        return container["value"]["Slots"]["value"]["values"]
    except KeyError as exc:
        raise SwapError(f"Malformed container in Level.sav; missing {exc}") from exc


def validate_guild(level: dict, expected_guid: str, instance: str, label: str) -> None:
    member_matches = 0
    handle_matches = 0
    for group in keyed_entries(level, "GroupSaveDataMap"):
        try:
            if group["value"]["GroupType"]["value"]["value"] != "EPalGroupType::Guild":
                continue
            raw = group["value"]["RawData"]["value"]
        except KeyError:
            continue
        member_matches += sum(
            normalize_guid(str(player.get("player_uid"))) == expected_guid
            for player in raw.get("players", [])
            if player.get("player_uid") is not None
        )
        for handle in raw.get("individual_character_handle_ids", []):
            if str(handle.get("instance_id", "")).lower() != instance:
                continue
            actual = normalize_guid(str(handle.get("guid")))
            if actual != expected_guid:
                raise SwapError(
                    f"{label} guild handle points to {actual}, expected {expected_guid}"
                )
            handle_matches += 1
    if member_matches > 1 or handle_matches != 1:
        raise SwapError(
            f"{label} guild link is incomplete "
            f"(member records={member_matches}, character handles={handle_matches}; "
            "expected at most 1 member record and exactly 1 character handle)"
        )


def validate_player_links(level: dict, document: dict, expected_guid: str, label: str) -> PlayerSummary:
    expected_guid = normalize_guid(expected_guid)
    data = player_struct(document)
    try:
        actual_guid = normalize_guid(str(data["PlayerUId"]["value"]))
        individual_guid = normalize_guid(str(data["IndividualId"]["value"]["PlayerUId"]["value"]))
    except KeyError as exc:
        raise SwapError(f"{label} player save is missing identity data: {exc}") from exc
    if actual_guid != expected_guid or individual_guid != expected_guid:
        raise SwapError(
            f"{label} file identity mismatch: PlayerUId={actual_guid}, "
            f"IndividualId.PlayerUId={individual_guid}, expected={expected_guid}"
        )

    instance = player_instance(document)
    character_entries = keyed_entries(level, "CharacterSaveParameterMap")
    character_by_instance: dict[str, list[dict]] = {}
    for entry in character_entries:
        character_by_instance.setdefault(entry_instance(entry), []).append(entry)

    matches = [
        entry
        for entry in character_by_instance.get(instance, [])
        if entry_player_guid(entry) == expected_guid and is_player_entry(entry)
    ]
    if len(matches) != 1:
        raise SwapError(
            f"{label} character does not match Level.sav exactly once "
            f"(guid={expected_guid}, instance={instance}, matches={len(matches)})"
        )

    item_ids, character_ids = player_container_ids(document)
    item_containers = index_unique(
        keyed_entries(level, "ItemContainerSaveData"), entry_id, "item container"
    )
    character_containers = index_unique(
        keyed_entries(level, "CharacterContainerSaveData"), entry_id, "character container"
    )
    missing_items = [name for name, value in item_ids.items() if value not in item_containers]
    missing_characters = [name for name, value in character_ids.items() if value not in character_containers]
    if missing_items or missing_characters:
        missing = ", ".join(missing_items + missing_characters)
        raise SwapError(
            f"{label} points to missing Level.sav containers: {missing}. "
            "Opening this world could show an empty inventory or Palbox."
        )

    item_slots = sum(len(occupied_slots(item_containers[value])) for value in item_ids.values())
    pal_count = 0
    for container_name, container_value in character_ids.items():
        for slot in occupied_slots(character_containers[container_value]):
            try:
                raw = slot["RawData"]["value"]
                owner_guid = normalize_guid(str(raw["player_uid"]))
                pal_instance = str(raw["instance_id"]).lower()
            except KeyError as exc:
                raise SwapError(f"{label} {container_name} has a malformed slot: {exc}") from exc
            if owner_guid != expected_guid:
                raise SwapError(
                    f"{label} {container_name} slot {pal_instance} belongs to {owner_guid}, "
                    f"expected {expected_guid}"
                )
            pal_entries = character_by_instance.get(pal_instance, [])
            if len(pal_entries) != 1 or entry_player_guid(pal_entries[0]) != expected_guid:
                raise SwapError(
                    f"{label} {container_name} Pal {pal_instance} is missing or linked to another player"
                )
            owner = save_parameter(pal_entries[0]).get("OwnerPlayerUId", {}).get("value")
            if owner is not None and normalize_guid(str(owner)) != expected_guid:
                raise SwapError(
                    f"{label} {container_name} Pal {pal_instance} has the wrong OwnerPlayerUId"
                )
            pal_count += 1

    validate_guild(level, expected_guid, instance, label)
    return PlayerSummary(expected_guid, instance, item_slots, pal_count)


def validate_world(world: Path, host_client_guid: str, client_guid: str) -> tuple[PlayerSummary, PlayerSummary]:
    world = world.resolve()
    host_client_guid = normalize_guid(host_client_guid)
    client_guid = normalize_guid(client_guid)
    if HOST_GUID in (host_client_guid, client_guid) or host_client_guid == client_guid:
        raise SwapError("Host/client GUID mapping is invalid")
    for relative in REQUIRED_WORLD_PATHS:
        if not (world / relative).exists():
            raise SwapError(f"This is not the complete local co-op world; missing {relative}")

    players = world / "Players"
    actual_files = {path.stem.upper() for path in players.glob("*.sav")}
    expected_files = {HOST_GUID, client_guid}
    if actual_files != expected_files:
        missing = sorted(expected_files - actual_files)
        unexpected = sorted(actual_files - expected_files)
        raise SwapError(
            "Player-file layout does not match the selected host. "
            f"Missing={missing or 'none'}; unexpected={unexpected or 'none'}. "
            "No file was deleted; use the latest relay backup to investigate."
        )

    level, _, _ = load_sav(world / "Level.sav")
    host_doc, _, _ = load_sav(players / f"{HOST_GUID}.sav")
    client_doc, _, _ = load_sav(players / f"{client_guid}.sav")
    host = validate_player_links(level, host_doc, HOST_GUID, "Host")
    client = validate_player_links(level, client_doc, client_guid, "Client")

    stale_host_entries = [
        entry
        for entry in keyed_entries(level, "CharacterSaveParameterMap")
        if is_player_entry(entry) and entry_player_guid(entry) == host_client_guid
    ]
    if stale_host_entries:
        raise SwapError(
            f"Found {len(stale_host_entries)} stale character record(s) for the host client GUID "
            f"{host_client_guid}. Refusing to guess which data to remove."
        )
    return host, client


def replacement_for(value, destination_guid: str):
    if isinstance(value, UUID):
        return uuid(destination_guid)
    if isinstance(value, str):
        replacement = dashed(destination_guid) if "-" in value else normalize_guid(destination_guid)
        if value.islower():
            return replacement.lower()
        return replacement
    return value


def rewrite_guid_references(value, mapping: dict[str, str]) -> int:
    rewrites = 0
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(child, (dict, list, tuple)):
                rewrites += rewrite_guid_references(child, mapping)
                continue
            try:
                source = normalize_guid(str(child))
            except SwapError:
                continue
            if source in mapping:
                value[key] = replacement_for(child, mapping[source])
                rewrites += 1
    elif isinstance(value, list):
        for index, child in enumerate(value):
            if isinstance(child, (dict, list, tuple)):
                rewrites += rewrite_guid_references(child, mapping)
                continue
            try:
                source = normalize_guid(str(child))
            except SwapError:
                continue
            if source in mapping:
                value[index] = replacement_for(child, mapping[source])
                rewrites += 1
    elif isinstance(value, tuple):
        # Tuples in these saves are binary/custom-version data, not mutable GUID fields.
        return 0
    return rewrites


def write_validated(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    load_sav(path)


def swap(world: Path, current_client_guid: str, incoming_client_guid: str) -> None:
    world = world.resolve()
    current_client_guid = normalize_guid(current_client_guid)
    incoming_client_guid = normalize_guid(incoming_client_guid)
    before_host, before_client = validate_world(world, current_client_guid, incoming_client_guid)

    players = world / "Players"
    level_path = world / "Level.sav"
    host_path = players / f"{HOST_GUID}.sav"
    incoming_path = players / f"{incoming_client_guid}.sav"
    outgoing_path = players / f"{current_client_guid}.sav"

    level, level_type, level_zlib = load_sav(level_path)
    host_doc, host_type, host_zlib = load_sav(host_path)
    incoming_doc, incoming_type, incoming_zlib = load_sav(incoming_path)
    mapping = {HOST_GUID: current_client_guid, incoming_client_guid: HOST_GUID}
    rewrites = rewrite_guid_references(level, mapping)
    rewrites += rewrite_guid_references(host_doc, mapping)
    rewrites += rewrite_guid_references(incoming_doc, mapping)

    validate_player_links(level, host_doc, current_client_guid, "Outgoing host")
    validate_player_links(level, incoming_doc, HOST_GUID, "Incoming host")

    level_tmp = world / ".Level.sav.swap.tmp"
    outgoing_tmp = players / ".outgoing-host.swap.tmp"
    incoming_tmp = players / ".incoming-host.swap.tmp"
    try:
        write_validated(level_tmp, encode_sav(level, level_type, level_zlib))
        write_validated(outgoing_tmp, encode_sav(host_doc, host_type, host_zlib))
        write_validated(incoming_tmp, encode_sav(incoming_doc, incoming_type, incoming_zlib))
        os.replace(level_tmp, level_path)
        os.replace(outgoing_tmp, outgoing_path)
        os.replace(incoming_tmp, host_path)
        incoming_path.unlink()
        after_host, after_client = validate_world(world, incoming_client_guid, current_client_guid)
    finally:
        level_tmp.unlink(missing_ok=True)
        outgoing_tmp.unlink(missing_ok=True)
        incoming_tmp.unlink(missing_ok=True)

    if before_host.item_slots != after_client.item_slots or before_host.pals != after_client.pals:
        raise SwapError("Outgoing host inventory/Pal counts changed during the swap")
    if before_client.item_slots != after_host.item_slots or before_client.pals != after_host.pals:
        raise SwapError("Incoming host inventory/Pal counts changed during the swap")
    print(
        f"SWAP_OK old_host={current_client_guid} new_host={incoming_client_guid} "
        f"guid_references={rewrites} "
        f"old_host_items={after_client.item_slots} old_host_pals={after_client.pals} "
        f"new_host_items={after_host.item_slots} new_host_pals={after_host.pals}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate_parser = commands.add_parser("validate", help="validate a prepared two-player world")
    validate_parser.add_argument("world", type=Path)
    validate_parser.add_argument("host_client_guid")
    validate_parser.add_argument("client_guid")
    swap_parser = commands.add_parser("swap", help="rotate current host and incoming client")
    swap_parser.add_argument("world", type=Path)
    swap_parser.add_argument("current_client_guid")
    swap_parser.add_argument("incoming_client_guid")
    arguments = sys.argv[1:]
    # Older Pull-And-Swap.ps1 versions call the tool without the "swap" verb.
    if len(arguments) == 3 and arguments[0] not in {"validate", "swap"}:
        arguments.insert(0, "swap")
    args = parser.parse_args(arguments)
    try:
        if args.command == "validate":
            host, client = validate_world(args.world, args.host_client_guid, args.client_guid)
            print(
                f"VALIDATION_OK host_items={host.item_slots} host_pals={host.pals} "
                f"client_items={client.item_slots} client_pals={client.pals}"
            )
        else:
            swap(args.world, args.current_client_guid, args.incoming_client_guid)
    except Exception as exc:
        print(f"{args.command.upper()}_ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
