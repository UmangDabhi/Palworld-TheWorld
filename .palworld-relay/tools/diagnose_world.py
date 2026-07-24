#!/usr/bin/env python3
"""Produce a read-only identity and ownership report for a relay world."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from swap_coop_host import (
    GUID_RE,
    HOST_GUID,
    SwapError,
    dps_summary,
    entry_instance,
    entry_player_guid,
    expedition_lock_status,
    is_player_entry,
    keyed_entries,
    load_sav,
    normalize_guid,
    player_instance,
    player_struct,
    save_parameter,
    validate_player_links,
    validate_world,
)


def field_value(properties: dict, name: str, default: object = "<unknown>") -> object:
    value = properties.get(name, {}).get("value", default)
    while isinstance(value, dict) and "value" in value:
        value = value["value"]
    return value


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def diagnose(world: Path, host_client_guid: str, client_guid: str) -> None:
    world = world.resolve()
    host_client_guid = normalize_guid(host_client_guid)
    client_guid = normalize_guid(client_guid)
    level_document, _, _ = load_sav(world / "Level.sav")
    character_entries = [
        entry
        for entry in keyed_entries(level_document, "CharacterSaveParameterMap")
        if is_player_entry(entry)
    ]
    players = world / "Players"
    normal_paths = sorted(
        (
            path
            for path in players.glob("*.sav")
            if GUID_RE.fullmatch(path.stem)
        ),
        key=lambda path: path.name.upper(),
    )
    dps_paths = sorted(
        (
            path
            for path in players.glob("*_dps.sav")
            if GUID_RE.fullmatch(path.stem[:-4])
        ),
        key=lambda path: path.name.upper(),
    )

    print(
        f"EXPECTED_LAYOUT host_file={HOST_GUID}.sav "
        f"host_character_guid={host_client_guid} client_file={client_guid}.sav"
    )
    print(
        f"FILE_COUNTS ordinary={len(normal_paths)} dps_sidecars={len(dps_paths)}"
    )
    active_expedition_locks, orphaned_expedition_locks = expedition_lock_status(
        level_document
    )
    active_stations = sorted(
        {station_id for _, _, station_id, _ in active_expedition_locks}
    )
    orphaned_stations = sorted(
        {station_id for _, _, station_id, _ in orphaned_expedition_locks}
    )
    print(
        f"EXPEDITION_LOCKS active={len(active_expedition_locks)} "
        f"orphaned={len(orphaned_expedition_locks)} "
        f"active_stations={','.join(active_stations) or 'none'} "
        f"orphaned_stations={','.join(orphaned_stations) or 'none'}"
    )

    guild_memberships: dict[str, list[str]] = {}
    for group in keyed_entries(level_document, "GroupSaveDataMap"):
        try:
            if group["value"]["GroupType"]["value"]["value"] != "EPalGroupType::Guild":
                continue
            raw = group["value"]["RawData"]["value"]
            group_id = normalize_guid(str(raw["group_id"]))
            members = [
                normalize_guid(str(player["player_uid"]))
                for player in raw.get("players", [])
            ]
            for member in members:
                guild_memberships.setdefault(member, []).append(group_id)
            marker_owners = sorted(
                {
                    normalize_guid(str(marker["owner_player_uid"]))
                    for marker in raw.get("guild_markers", [])
                }
            )
            has_assets = any(
                raw.get(name)
                for name in (
                    "individual_character_handle_ids",
                    "base_ids",
                    "map_object_instance_ids_base_camp_points",
                    "guild_markers",
                )
            )
            dormant_alias = (
                host_client_guid in members
                and not has_assets
                and all(member == host_client_guid for member in members)
            )
            print(
                f"GUILD id={group_id} name={raw.get('guild_name')!r} "
                f"admin={normalize_guid(str(raw.get('admin_player_uid')))} "
                f"members={','.join(members) or 'none'} "
                f"handles={len(raw.get('individual_character_handle_ids', []))} "
                f"bases={len(raw.get('base_ids', []))} "
                f"markers={len(raw.get('guild_markers', []))} "
                f"marker_owners={','.join(marker_owners) or 'none'} "
                f"dormant_host_alias={str(dormant_alias).lower()}"
            )
        except Exception as exc:
            print(f"GUILD_ERROR key={group.get('key')} error={exc}")

    for player_guid, group_ids in sorted(guild_memberships.items()):
        if len(group_ids) > 1:
            print(
                f"DUPLICATE_GUILD_MEMBERSHIP player={player_guid} "
                f"guilds={','.join(group_ids)}"
            )

    documents: dict[str, dict] = {}
    instances: dict[str, list[str]] = {}
    for path in normal_paths:
        file_guid = normalize_guid(path.stem)
        try:
            document, _, _ = load_sav(path)
            documents[file_guid] = document
            data = player_struct(document)
            uid = normalize_guid(str(data["PlayerUId"]["value"]))
            individual_uid = normalize_guid(
                str(data["IndividualId"]["value"]["PlayerUId"]["value"])
            )
            instance = player_instance(document)
            instances.setdefault(instance, []).append(path.name)
            records = [
                entry
                for entry in character_entries
                if entry_instance(entry) == instance
            ]
            matching_records = [
                entry
                for entry in records
                if entry_player_guid(entry) == uid
            ]
            record = matching_records[0] if matching_records else (records[0] if records else None)
            parameters = save_parameter(record) if record else {}
            character_level = field_value(parameters, "Level")
            nickname = field_value(parameters, "NickName")
            level_guids = sorted({entry_player_guid(entry) for entry in records})

            if file_guid == HOST_GUID:
                relation = "active-host"
                other_guid = client_guid
            elif file_guid == client_guid:
                relation = "active-client"
                other_guid = HOST_GUID
            elif file_guid == host_client_guid:
                relation = "possible-stale-host-alias"
                other_guid = client_guid
            else:
                relation = "unexpected-file"
                other_guid = HOST_GUID

            link_status = "not-validated"
            items: int | str = "<unknown>"
            pals: int | str = "<unknown>"
            dps_pals: int | str = 0
            try:
                summary = validate_player_links(level=level_document, document=document, expected_guid=uid, label=path.name)
                link_status = "valid"
                items = summary.item_slots
                pals = summary.pals
            except SwapError as exc:
                link_status = f"invalid:{exc}"

            dps_path = players / f"{file_guid}_dps.sav"
            if dps_path.is_file():
                try:
                    dps = dps_summary(dps_path, uid, other_guid)
                    dps_pals = dps[0] if dps else 0
                except SwapError as exc:
                    dps_pals = f"invalid:{exc}"

            print(
                f"PLAYER file={path.name} relation={relation} bytes={path.stat().st_size} "
                f"sha256={sha256(path)} uid={uid} individual_uid={individual_uid} "
                f"instance={instance} level={character_level!r} nickname={nickname!r} "
                f"level_guids={','.join(level_guids) or 'none'} items={items} pals={pals} "
                f"dps_pals={dps_pals} links={link_status}"
            )
        except Exception as exc:
            print(f"PLAYER_ERROR file={path.name} error={exc}")

    for instance, names in sorted(instances.items()):
        if len(names) > 1:
            print(
                f"DUPLICATE_INSTANCE instance={instance} files={','.join(sorted(names))}"
            )

    host_document = documents.get(HOST_GUID)
    alias_document = documents.get(host_client_guid)
    if host_document is not None and alias_document is not None:
        host_instance = player_instance(host_document)
        alias_instance = player_instance(alias_document)
        host_uid = normalize_guid(str(player_struct(host_document)["PlayerUId"]["value"]))
        alias_uid = normalize_guid(str(player_struct(alias_document)["PlayerUId"]["value"]))
        if (
            host_uid == HOST_GUID
            and alias_uid == host_client_guid
            and host_instance == alias_instance
        ):
            print(
                "STALE_HOST_ALIAS=PROVEN "
                f"file={host_client_guid}.sav instance={host_instance} "
                "reason=same-character-instance-as-host"
            )
        else:
            print(
                "STALE_HOST_ALIAS=REJECTED "
                f"file={host_client_guid}.sav host_uid={host_uid} alias_uid={alias_uid} "
                f"host_instance={host_instance} alias_instance={alias_instance}"
            )

    normal_guids = set(documents)
    for dps_path in dps_paths:
        owner_guid = normalize_guid(dps_path.stem[:-4])
        if owner_guid not in normal_guids:
            print(f"ORPHAN_DPS file={dps_path.name} owner_file_missing=true")

    expected_normal = {HOST_GUID, client_guid}
    actual_normal = normal_guids
    print(
        "LAYOUT_DIFF "
        f"missing={','.join(sorted(expected_normal - actual_normal)) or 'none'} "
        f"extra={','.join(sorted(actual_normal - expected_normal)) or 'none'}"
    )

    try:
        host, client = validate_world(world, host_client_guid, client_guid)
        print(
            "WORLD_VALIDATION=OK "
            f"host_items={host.item_slots} host_pals={host.pals} "
            f"client_items={client.item_slots} client_pals={client.pals}"
        )
    except SwapError as exc:
        print(f"WORLD_VALIDATION=WARNING {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("world", type=Path)
    parser.add_argument("host_client_guid")
    parser.add_argument("client_guid")
    args = parser.parse_args()
    try:
        diagnose(args.world, args.host_client_guid, args.client_guid)
    except Exception as exc:
        print(f"DIAGNOSE_ERROR: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
