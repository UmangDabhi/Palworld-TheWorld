#!/usr/bin/env python3
"""Validate and atomically rotate two Palworld local co-op characters."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
from contextlib import redirect_stdout
from dataclasses import dataclass, replace
from pathlib import Path

from palworld_save_tools.archive import UUID
from palworld_save_tools.gvas import GvasFile
from palworld_save_tools.palsav import compress_gvas_to_sav, decompress_sav_to_gvas
from palworld_save_tools.paltypes import PALWORLD_CUSTOM_PROPERTIES, PALWORLD_TYPE_HINTS

HOST_GUID = "00000000000000000000000000000001"
ZERO_GUID = "00000000000000000000000000000000"
GUID_RE = re.compile(r"^[0-9a-fA-F]{32}$")
REQUIRED_WORLD_PATHS = ("Level.sav", "LevelMeta.sav", "WorldOption.sav", "LocalData.sav", "Players")
EXPEDITION_ASSIGNMENT_FIELD = "MapObjectConcreteInstanceIdAssignedToExpedition"
EXPEDITION_MODEL_TYPE = "PalMapObjectCharacterTeamMissionModel"


class SwapError(RuntimeError):
    pass


@dataclass(frozen=True)
class PlayerSummary:
    guid: str
    instance: str
    item_slots: int
    pals: int
    item_fingerprint: str
    pal_instances: frozenset[str]
    dps_pals: int = 0
    dps_fingerprint: str | None = None


def normalize_guid(value: str) -> str:
    guid = value.replace("-", "").upper()
    if not GUID_RE.fullmatch(guid):
        raise SwapError(f"Expected a 32-character hexadecimal GUID, got {value!r}")
    return guid


def normalize_identity_for_fingerprint(value, identity_roles: dict[str, str]):
    if isinstance(value, dict):
        return {
            key: normalize_identity_for_fingerprint(child, identity_roles)
            for key, child in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [normalize_identity_for_fingerprint(child, identity_roles) for child in value]
    if isinstance(value, (str, UUID)):
        try:
            guid = normalize_guid(str(value))
            if guid in identity_roles:
                return f"<{identity_roles[guid]}>"
        except SwapError:
            pass
        return str(value) if isinstance(value, UUID) else value
    return value


def document_fingerprint(document: dict, identity_roles: dict[str, str]) -> str:
    canonical = json.dumps(
        normalize_identity_for_fingerprint(document, identity_roles),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def dps_summary(path: Path, player_guid: str, other_guid: str) -> tuple[int, str] | None:
    if not path.is_file():
        return None
    document, _, _ = load_sav(path)
    try:
        entries = document["properties"]["SaveParameterArray"]["value"]["values"]
    except KeyError as exc:
        raise SwapError(f"Unexpected dimensional Pal storage structure in {path.name}: {exc}") from exc
    if not isinstance(entries, list):
        raise SwapError(f"Dimensional Pal storage in {path.name} is not an array")

    active_instances: set[str] = set()
    zero = dashed(ZERO_GUID)
    for entry in entries:
        try:
            instance = str(entry["InstanceId"]["value"]["InstanceId"]["value"]).lower()
        except KeyError as exc:
            raise SwapError(f"Malformed dimensional Pal entry in {path.name}: {exc}") from exc
        if instance == zero:
            continue
        if instance in active_instances:
            raise SwapError(f"Duplicate dimensional Pal instance {instance} in {path.name}")
        active_instances.add(instance)
    return len(active_instances), document_fingerprint(
        document,
        {player_guid: "SELF", other_guid: "OTHER"},
    )


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
            "armor-equipment": container_id(inventory["PlayerEquipArmorContainerId"]),
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


def map_object_entries(level: dict) -> list:
    try:
        value = world_data(level)["MapObjectSaveData"]["value"]
    except KeyError as exc:
        raise SwapError(f"Level.sav is missing MapObjectSaveData: {exc}") from exc
    entries = value.get("values") if isinstance(value, dict) else value
    if not isinstance(entries, list):
        raise SwapError("Level.sav MapObjectSaveData is not a list")
    return entries


def expedition_lock_status(level: dict) -> tuple[list[tuple], list[tuple]]:
    station_payloads: dict[str, bytes] = {}
    known_stations: set[str] = set()
    for entry in map_object_entries(level):
        raw = (
            entry.get("ConcreteModel", {})
            .get("value", {})
            .get("RawData", {})
            .get("value", {})
        )
        if raw.get("concrete_model_type") != EXPEDITION_MODEL_TYPE:
            continue
        station_id = str(raw.get("instance_id", "")).lower()
        if not station_id:
            continue
        known_stations.add(station_id)
        unknown_bytes = raw.get("unknown_bytes")
        if isinstance(unknown_bytes, (list, tuple)):
            station_payloads[station_id] = bytes(unknown_bytes)

    assigned: list[tuple] = []
    orphaned: list[tuple] = []
    for entry in keyed_entries(level, "CharacterSaveParameterMap"):
        parameter = save_parameter(entry)
        assignment = parameter.get(EXPEDITION_ASSIGNMENT_FIELD)
        if not assignment:
            continue
        if is_player_entry(entry):
            raise SwapError("A player character unexpectedly has an expedition assignment")
        station_id = str(assignment.get("value", "")).lower()
        instance_id = entry_instance(entry)
        if station_id in known_stations and station_id not in station_payloads:
            raise SwapError(
                f"Expedition station {station_id} uses an unsupported save layout; "
                "no automatic lock repair was attempted"
            )
        payload = station_payloads.get(station_id)
        lock = (entry, parameter, station_id, instance_id)
        if payload is not None and UUID.from_str(instance_id).raw_bytes in payload:
            assigned.append(lock)
        else:
            orphaned.append(lock)
    return assigned, orphaned


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


def occupied_item_slots(container: dict, label: str) -> list[dict]:
    result = []
    for slot in occupied_slots(container):
        raw = slot.get("RawData", {}).get("value")
        if not raw:
            continue
        item = raw.get("item", {})
        static_id = item.get("static_id")
        count = raw.get("count")
        if not static_id or not isinstance(count, int) or count <= 0:
            raise SwapError(
                f"{label} has a malformed occupied item slot "
                f"(static_id={static_id!r}, count={count!r})"
            )
        result.append(raw)
    return result


def dynamic_item_entries(level: dict) -> list:
    try:
        entries = world_data(level)["DynamicItemSaveData"]["value"]["values"]
    except KeyError as exc:
        raise SwapError(f"Level.sav is missing DynamicItemSaveData: {exc}") from exc
    if not isinstance(entries, list):
        raise SwapError("Level.sav DynamicItemSaveData is not a list")
    return entries


def dynamic_item_id(entry: dict) -> str:
    try:
        return str(entry["RawData"]["value"]["id"]["local_id_in_created_world"]).lower()
    except KeyError as exc:
        raise SwapError(f"Malformed dynamic item record; missing {exc}") from exc


def dynamic_item_static_id(entry: dict) -> str:
    try:
        return str(entry["RawData"]["value"]["id"]["static_id"])
    except KeyError as exc:
        raise SwapError(f"Malformed dynamic item record; missing {exc}") from exc


def item_fingerprint(
    item_ids: dict[str, str],
    item_containers: dict[str, dict],
    dynamic_items: dict[str, dict],
    referenced_dynamic_ids: set[str],
) -> str:
    payload = {
        "containers": {
            name: item_containers[container_id]
            for name, container_id in sorted(item_ids.items())
        },
        "dynamic_items": {
            item_id: dynamic_items[item_id]
            for item_id in sorted(referenced_dynamic_ids)
        },
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


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
    dynamic_items = index_unique(
        dynamic_item_entries(level), dynamic_item_id, "dynamic item"
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

    item_slots = 0
    zero_id = "00000000-0000-0000-0000-000000000000"
    referenced_dynamic_ids: set[str] = set()
    for container_name, container_value in item_ids.items():
        slots = occupied_item_slots(
            item_containers[container_value], f"{label} {container_name} container"
        )
        item_slots += len(slots)
        for slot in slots:
            item = slot["item"]
            dynamic_id = str(item["dynamic_id"]["local_id_in_created_world"]).lower()
            if dynamic_id == zero_id:
                continue
            referenced_dynamic_ids.add(dynamic_id)
            dynamic = dynamic_items.get(dynamic_id)
            if dynamic is None:
                raise SwapError(
                    f"{label} {container_name} item {item['static_id']} points to missing "
                    f"dynamic item {dynamic_id}. Opening this world could reset the inventory."
                )
            dynamic_static_id = dynamic_item_static_id(dynamic)
            if dynamic_static_id != item["static_id"]:
                raise SwapError(
                    f"{label} {container_name} item {dynamic_id} has conflicting static IDs "
                    f"({item['static_id']} versus {dynamic_static_id})"
                )
    pal_instances: set[str] = set()
    for container_name, container_value in character_ids.items():
        for slot in occupied_slots(character_containers[container_value]):
            try:
                raw = slot["RawData"]["value"]
                owner_guid = normalize_guid(str(raw["player_uid"]))
                pal_instance = str(raw["instance_id"]).lower()
            except KeyError as exc:
                raise SwapError(f"{label} {container_name} has a malformed slot: {exc}") from exc
            pal_entries = character_by_instance.get(pal_instance, [])
            if len(pal_entries) != 1:
                raise SwapError(
                    f"{label} {container_name} Pal {pal_instance} does not have exactly one "
                    "Level.sav character record"
                )
            provenance_guid = entry_player_guid(pal_entries[0])
            if owner_guid != provenance_guid:
                raise SwapError(
                    f"{label} {container_name} Pal {pal_instance} has conflicting provenance "
                    f"({owner_guid} in its slot versus {provenance_guid} in its character key)"
                )
            owner = save_parameter(pal_entries[0]).get("OwnerPlayerUId", {}).get("value")
            current_owner = normalize_guid(str(owner)) if owner is not None else owner_guid
            if current_owner != expected_guid:
                raise SwapError(
                    f"{label} {container_name} Pal {pal_instance} is currently owned by "
                    f"{current_owner}, expected {expected_guid}"
                )
            if pal_instance in pal_instances:
                raise SwapError(f"{label} Pal {pal_instance} appears in more than one player container")
            pal_instances.add(pal_instance)

    validate_guild(level, expected_guid, instance, label)
    return PlayerSummary(
        expected_guid,
        instance,
        item_slots,
        len(pal_instances),
        item_fingerprint(item_ids, item_containers, dynamic_items, referenced_dynamic_ids),
        frozenset(pal_instances),
    )


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
    normal_files: set[str] = set()
    dps_files: set[str] = set()
    unexpected_files: list[str] = []
    for path in players.glob("*.sav"):
        stem = path.stem.upper()
        if GUID_RE.fullmatch(stem):
            normal_files.add(stem)
        elif stem.endswith("_DPS") and GUID_RE.fullmatch(stem[:-4]):
            dps_files.add(stem[:-4])
        else:
            unexpected_files.append(path.name)
    expected_files = {HOST_GUID, client_guid}
    unexpected_dps = sorted(dps_files - expected_files)
    if normal_files != expected_files or unexpected_dps or unexpected_files:
        missing = sorted(expected_files - normal_files)
        unexpected = sorted(normal_files - expected_files)
        raise SwapError(
            "Player-file layout does not match the selected host. "
            f"Missing normal saves={missing or 'none'}; "
            f"unexpected normal saves={unexpected or 'none'}; "
            f"unexpected DPS sidecars={unexpected_dps or 'none'}; "
            f"unknown saves={unexpected_files or 'none'}. "
            "No file was deleted; use the latest relay backup to investigate."
        )

    level, _, _ = load_sav(world / "Level.sav")
    host_doc, _, _ = load_sav(players / f"{HOST_GUID}.sav")
    client_doc, _, _ = load_sav(players / f"{client_guid}.sav")
    host = validate_player_links(level, host_doc, HOST_GUID, "Host")
    client = validate_player_links(level, client_doc, client_guid, "Client")
    host_dps = dps_summary(
        players / f"{HOST_GUID}_dps.sav", HOST_GUID, client_guid
    )
    client_dps = dps_summary(
        players / f"{client_guid}_dps.sav", client_guid, HOST_GUID
    )
    if host_dps is not None:
        host = replace(host, dps_pals=host_dps[0], dps_fingerprint=host_dps[1])
    if client_dps is not None:
        client = replace(client, dps_pals=client_dps[0], dps_fingerprint=client_dps[1])

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


def normalize_stale_host_alias(
    world: Path,
    host_client_guid: str,
    client_guid: str,
    backup: Path,
) -> None:
    world = world.resolve()
    host_client_guid = normalize_guid(host_client_guid)
    client_guid = normalize_guid(client_guid)
    players = world / "Players"
    normal_files = {
        path.stem.upper(): path
        for path in players.glob("*.sav")
        if GUID_RE.fullmatch(path.stem)
    }
    expected = {HOST_GUID, client_guid}
    extras = set(normal_files) - expected
    if not extras:
        print("LAYOUT_NORMALIZATION_OK stale_alias=none")
        return
    if extras != {host_client_guid}:
        raise SwapError(
            f"Cannot safely normalize player layout; unexpected ordinary saves: {sorted(extras)}"
        )

    host_path = normal_files.get(HOST_GUID)
    alias_path = normal_files[host_client_guid]
    if host_path is None or client_guid not in normal_files:
        raise SwapError("Cannot normalize a stale alias because the host or client save is missing")
    alias_dps = players / f"{host_client_guid}_dps.sav"
    if alias_dps.is_file():
        raise SwapError(
            f"Refusing to move stale alias {alias_path.name}: matching DPS sidecar "
            f"{alias_dps.name} needs separate ownership inspection"
        )

    host_doc, _, _ = load_sav(host_path)
    alias_doc, _, _ = load_sav(alias_path)
    if normalize_guid(str(player_struct(host_doc)["PlayerUId"]["value"])) != HOST_GUID:
        raise SwapError(f"Host file {host_path.name} does not contain the host UID")
    if normalize_guid(str(player_struct(alias_doc)["PlayerUId"]["value"])) != host_client_guid:
        raise SwapError(f"Alias file {alias_path.name} does not contain {host_client_guid}")
    host_instance = player_instance(host_doc)
    alias_instance = player_instance(alias_doc)
    if host_instance != alias_instance:
        raise SwapError(
            f"Refusing to move {alias_path.name}: its character instance {alias_instance} "
            f"does not match host instance {host_instance}"
        )

    backup = backup.resolve()
    backup_root = (world / ".palworld-relay" / "backups").resolve()
    try:
        backup.relative_to(backup_root)
    except ValueError as exc:
        raise SwapError(f"Quarantine path is outside relay backups: {backup}") from exc
    destination_dir = backup / "stale-player-aliases"
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / alias_path.name
    if destination.exists():
        raise SwapError(f"Stale-alias backup already exists: {destination}")
    os.replace(alias_path, destination)
    print(
        "LAYOUT_NORMALIZATION_OK "
        f"stale_alias={alias_path.name} instance={host_instance} moved_to={destination} "
        f"restore_to={alias_path}"
    )


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


def neutralize_dormant_host_guild_aliases(level: dict, stale_guid: str) -> int:
    stale_guid = normalize_guid(stale_guid)
    neutralized = 0
    for group in keyed_entries(level, "GroupSaveDataMap"):
        try:
            if group["value"]["GroupType"]["value"]["value"] != "EPalGroupType::Guild":
                continue
            raw = group["value"]["RawData"]["value"]
        except KeyError:
            continue

        stale_players = [
            player
            for player in raw.get("players", [])
            if normalize_guid(str(player.get("player_uid"))) == stale_guid
        ]
        if not stale_players:
            continue

        players = raw.get("players", [])
        admin = normalize_guid(str(raw.get("admin_player_uid", ZERO_GUID)))
        has_assets = any(
            raw.get(name)
            for name in (
                "individual_character_handle_ids",
                "base_ids",
                "map_object_instance_ids_base_camp_points",
                "guild_markers",
            )
        )
        if has_assets or len(stale_players) != len(players) or admin != stale_guid:
            raise SwapError(
                f"Dormant host alias {stale_guid} also appears in a non-empty or shared guild. "
                "Refusing to guess which guild data belongs to a real player."
            )

        raw["players"] = []
        raw["admin_player_uid"] = uuid(ZERO_GUID)
        raw["group_name"] = normalize_guid(str(raw["group_id"]))
        neutralized += 1
    return neutralized


def write_validated(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    load_sav(path)


def repair_expedition_locks(
    world: Path,
    host_client_guid: str,
    client_guid: str,
    require_inactive: bool = False,
) -> None:
    world = world.resolve()
    host_client_guid = normalize_guid(host_client_guid)
    client_guid = normalize_guid(client_guid)
    before_host, before_client = validate_world(world, host_client_guid, client_guid)
    level_path = world / "Level.sav"
    original_bytes = level_path.read_bytes()
    level, save_type, use_zlib = load_sav(level_path)
    assigned, orphaned = expedition_lock_status(level)
    if require_inactive and assigned:
        station_ids = sorted({station_id for _, _, station_id, _ in assigned})
        raise SwapError(
            f"Active Pal expedition detected: {len(assigned)} Pals at station(s) "
            f"{','.join(station_ids)}. Finish and claim every expedition on the "
            "current host, close Palworld, then retry. Active expeditions cannot "
            "be transferred safely between co-op hosts. No save was changed."
        )
    if not orphaned:
        print(
            f"EXPEDITION_REPAIR_OK unlocked=0 active={len(assigned)} "
            "reason=no-orphaned-locks"
        )
        return

    station_ids = sorted({station_id for _, _, station_id, _ in orphaned})
    for _, parameter, _, _ in orphaned:
        del parameter[EXPEDITION_ASSIGNMENT_FIELD]
    expected_fingerprint = document_fingerprint(level, {})
    level_tmp = world / ".Level.sav.expedition-repair.tmp"
    installed = False
    try:
        write_validated(level_tmp, encode_sav(level, save_type, use_zlib))
        staged_level, _, _ = load_sav(level_tmp)
        if document_fingerprint(staged_level, {}) != expected_fingerprint:
            raise SwapError("Expedition repair did not round-trip exactly")
        staged_assigned, staged_orphaned = expedition_lock_status(staged_level)
        if staged_orphaned or len(staged_assigned) != len(assigned):
            raise SwapError("Expedition lock counts changed unexpectedly while staging repair")

        os.replace(level_tmp, level_path)
        installed = True
        after_host, after_client = validate_world(world, host_client_guid, client_guid)
        if before_host != after_host or before_client != after_client:
            raise SwapError("Player inventory, equipment, or Pal ownership changed during repair")
    except Exception:
        if installed:
            level_path.write_bytes(original_bytes)
            validate_world(world, host_client_guid, client_guid)
        raise
    finally:
        level_tmp.unlink(missing_ok=True)

    print(
        f"EXPEDITION_REPAIR_OK unlocked={len(orphaned)} active={len(assigned)} "
        f"stations={','.join(station_ids)} player_data_exact=true"
    )


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
    host_dps_path = players / f"{HOST_GUID}_dps.sav"
    incoming_dps_path = players / f"{incoming_client_guid}_dps.sav"
    outgoing_dps_path = players / f"{current_client_guid}_dps.sav"

    level, level_type, level_zlib = load_sav(level_path)
    host_doc, host_type, host_zlib = load_sav(host_path)
    incoming_doc, incoming_type, incoming_zlib = load_sav(incoming_path)
    host_dps = load_sav(host_dps_path) if host_dps_path.is_file() else None
    incoming_dps = load_sav(incoming_dps_path) if incoming_dps_path.is_file() else None
    neutralized_guilds = neutralize_dormant_host_guild_aliases(
        level, current_client_guid
    )
    mapping = {HOST_GUID: current_client_guid, incoming_client_guid: HOST_GUID}
    rewrites = rewrite_guid_references(level, mapping)
    rewrites += rewrite_guid_references(host_doc, mapping)
    rewrites += rewrite_guid_references(incoming_doc, mapping)
    if host_dps is not None:
        rewrites += rewrite_guid_references(host_dps[0], mapping)
    if incoming_dps is not None:
        rewrites += rewrite_guid_references(incoming_dps[0], mapping)

    validate_player_links(level, host_doc, current_client_guid, "Outgoing host")
    validate_player_links(level, incoming_doc, HOST_GUID, "Incoming host")

    level_tmp = world / ".Level.sav.swap.tmp"
    outgoing_tmp = players / ".outgoing-host.swap.tmp"
    incoming_tmp = players / ".incoming-host.swap.tmp"
    outgoing_dps_tmp = players / ".outgoing-host-dps.swap.tmp"
    incoming_dps_tmp = players / ".incoming-host-dps.swap.tmp"
    try:
        write_validated(level_tmp, encode_sav(level, level_type, level_zlib))
        write_validated(outgoing_tmp, encode_sav(host_doc, host_type, host_zlib))
        write_validated(incoming_tmp, encode_sav(incoming_doc, incoming_type, incoming_zlib))
        if host_dps is not None:
            write_validated(
                outgoing_dps_tmp,
                encode_sav(host_dps[0], host_dps[1], host_dps[2]),
            )
        if incoming_dps is not None:
            write_validated(
                incoming_dps_tmp,
                encode_sav(incoming_dps[0], incoming_dps[1], incoming_dps[2]),
            )
        os.replace(level_tmp, level_path)
        os.replace(outgoing_tmp, outgoing_path)
        os.replace(incoming_tmp, host_path)
        incoming_path.unlink()
        if host_dps is not None:
            os.replace(outgoing_dps_tmp, outgoing_dps_path)
        if incoming_dps is not None:
            os.replace(incoming_dps_tmp, host_dps_path)
        elif host_dps_path.is_file():
            host_dps_path.unlink()
        if incoming_dps_path.is_file():
            incoming_dps_path.unlink()
        after_host, after_client = validate_world(world, incoming_client_guid, current_client_guid)
    finally:
        level_tmp.unlink(missing_ok=True)
        outgoing_tmp.unlink(missing_ok=True)
        incoming_tmp.unlink(missing_ok=True)
        outgoing_dps_tmp.unlink(missing_ok=True)
        incoming_dps_tmp.unlink(missing_ok=True)

    if before_host.item_slots != after_client.item_slots or before_host.pals != after_client.pals:
        raise SwapError("Outgoing host inventory/Pal counts changed during the swap")
    if before_client.item_slots != after_host.item_slots or before_client.pals != after_host.pals:
        raise SwapError("Incoming host inventory/Pal counts changed during the swap")
    if before_host.item_fingerprint != after_client.item_fingerprint:
        raise SwapError("Outgoing host inventory/equipment contents changed during the swap")
    if before_client.item_fingerprint != after_host.item_fingerprint:
        raise SwapError("Incoming host inventory/equipment contents changed during the swap")
    if before_host.pal_instances != after_client.pal_instances:
        raise SwapError("Outgoing host Pal identities changed during the swap")
    if before_client.pal_instances != after_host.pal_instances:
        raise SwapError("Incoming host Pal identities changed during the swap")
    if (
        before_host.dps_pals != after_client.dps_pals
        or before_host.dps_fingerprint != after_client.dps_fingerprint
    ):
        raise SwapError("Outgoing host dimensional Pal storage changed during the swap")
    if (
        before_client.dps_pals != after_host.dps_pals
        or before_client.dps_fingerprint != after_host.dps_fingerprint
    ):
        raise SwapError("Incoming host dimensional Pal storage changed during the swap")
    print(
        f"SWAP_OK old_host={current_client_guid} new_host={incoming_client_guid} "
        f"guid_references={rewrites} "
        f"dormant_guild_aliases_neutralized={neutralized_guilds} "
        f"old_host_items={after_client.item_slots} old_host_pals={after_client.pals} "
        f"new_host_items={after_host.item_slots} new_host_pals={after_host.pals} "
        f"old_host_dps_pals={after_client.dps_pals} new_host_dps_pals={after_host.dps_pals} "
        "items_exact=true pals_exact=true dps_exact=true"
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
    normalize_parser = commands.add_parser(
        "normalize-layout", help="quarantine a provably stale host client alias"
    )
    normalize_parser.add_argument("world", type=Path)
    normalize_parser.add_argument("host_client_guid")
    normalize_parser.add_argument("client_guid")
    normalize_parser.add_argument("backup", type=Path)
    expedition_parser = commands.add_parser(
        "repair-expedition-locks",
        help="clear only Pal expedition locks absent from the station's active team",
    )
    expedition_parser.add_argument("world", type=Path)
    expedition_parser.add_argument("host_client_guid")
    expedition_parser.add_argument("client_guid")
    expedition_parser.add_argument("--require-inactive", action="store_true")
    arguments = sys.argv[1:]
    # Older Pull-And-Swap.ps1 versions call the tool without the "swap" verb.
    if len(arguments) == 3 and arguments[0] not in {
        "validate",
        "swap",
        "normalize-layout",
        "repair-expedition-locks",
    }:
        arguments.insert(0, "swap")
    args = parser.parse_args(arguments)
    try:
        if args.command == "validate":
            host, client = validate_world(args.world, args.host_client_guid, args.client_guid)
            print(
                f"VALIDATION_OK host_items={host.item_slots} host_pals={host.pals} "
                f"host_dps_pals={host.dps_pals} "
                f"client_items={client.item_slots} client_pals={client.pals} "
                f"client_dps_pals={client.dps_pals}"
            )
        elif args.command == "swap":
            swap(args.world, args.current_client_guid, args.incoming_client_guid)
        elif args.command == "normalize-layout":
            normalize_stale_host_alias(
                args.world,
                args.host_client_guid,
                args.client_guid,
                args.backup,
            )
        else:
            repair_expedition_locks(
                args.world,
                args.host_client_guid,
                args.client_guid,
                args.require_inactive,
            )
    except Exception as exc:
        print(f"{args.command.upper()}_ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
