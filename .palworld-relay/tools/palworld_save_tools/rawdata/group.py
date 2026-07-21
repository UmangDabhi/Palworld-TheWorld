from typing import Sequence

from palworld_save_tools.archive import *


def player_info_reader(reader: FArchiveReader) -> dict[str, Any]:
    return {
        "player_uid": reader.guid(),
        "player_info": {
            "last_online_real_time": reader.i64(),
            "player_name": reader.fstring(),
        },
    }


def player_info_writer(writer: FArchiveWriter, p: dict[str, Any]) -> None:
    writer.guid(p["player_uid"])
    writer.i64(p["player_info"]["last_online_real_time"])
    writer.fstring(p["player_info"]["player_name"])


def guild_player_info_reader(reader: FArchiveReader) -> dict[str, Any]:
    player = player_info_reader(reader)
    player["role"] = reader.byte()
    return player


def guild_player_info_writer(writer: FArchiveWriter, p: dict[str, Any]) -> None:
    player_info_writer(writer, p)
    writer.byte(p["role"])


def guild_marker_reader(reader: FArchiveReader) -> dict[str, Any]:
    return {
        "marker_id": reader.guid(),
        "icon_location": reader.vector_dict(),
        "icon_type": reader.i32(),
        "owner_player_uid": reader.guid(),
    }


def guild_marker_writer(writer: FArchiveWriter, p: dict[str, Any]) -> None:
    writer.guid(p["marker_id"])
    writer.vector_dict(p["icon_location"])
    writer.i32(p["icon_type"])
    writer.guid(p["owner_player_uid"])


def role_permission_reader(reader: FArchiveReader) -> dict[str, Any]:
    return {
        "role": reader.byte(),
        "permissions": reader.tarray(lambda r: r.byte()),
    }


def role_permission_writer(writer: FArchiveWriter, p: dict[str, Any]) -> None:
    writer.byte(p["role"])
    writer.tarray(lambda w, value: w.byte(value), p["permissions"])


def read_guild_tail_v2(reader: FArchiveReader) -> dict[str, Any]:
    return {
        "guild_chest_allowed_roles": reader.tarray(lambda r: r.byte()),
        "unknown_i32": reader.i32(),
        "admin_player_uid": reader.guid(),
        "players": reader.tarray(guild_player_info_reader),
        "role_permissions": reader.tarray(role_permission_reader),
        "trailing_bytes": reader.byte_list(4),
    }


def read_guild_tail_v1(reader: FArchiveReader) -> dict[str, Any]:
    return {
        "admin_player_uid": reader.guid(),
        "players": reader.tarray(player_info_reader),
        "trailing_bytes": reader.byte_list(4),
    }


def read_guild_tail(reader: FArchiveReader) -> dict[str, Any]:
    start = reader.data.tell()
    try:
        tail = read_guild_tail_v2(reader)
        if reader.eof():
            return tail
    except Exception:
        pass
    reader.data.seek(start)
    return read_guild_tail_v1(reader)


def decode(
    reader: FArchiveReader, type_name: str, size: int, path: str
) -> dict[str, Any]:
    if type_name != "MapProperty":
        raise Exception(f"Expected MapProperty, got {type_name}")
    value = reader.property(type_name, size, path, nested_caller_path=path)
    # Decode the raw bytes and replace the raw data
    group_map = value["value"]
    for group in group_map:
        group_type = group["value"]["GroupType"]["value"]["value"]
        group_bytes = group["value"]["RawData"]["value"]["values"]
        group["value"]["RawData"]["value"] = decode_bytes(
            reader, group_bytes, group_type
        )
    return value


def decode_bytes(
    parent_reader: FArchiveReader, group_bytes: Sequence[int], group_type: str
) -> dict[str, Any]:
    reader = parent_reader.internal_copy(bytes(group_bytes), debug=False)
    group_data = {
        "group_type": group_type,
        "group_id": reader.guid(),
        "group_name": reader.fstring(),
        "individual_character_handle_ids": reader.tarray(instance_id_reader),
    }
    if group_type in [
        "EPalGroupType::Guild",
        "EPalGroupType::IndependentGuild",
        "EPalGroupType::Organization",
    ]:
        group_data |= {"org_type": reader.byte()}
    if group_type == "EPalGroupType::Organization":
        group_data |= {"trailing_bytes": reader.byte_list(12)}

    if group_type == "EPalGroupType::Guild":
        guild: dict[str, Any] = {
            "leading_bytes": reader.byte_list(4),
            "base_ids": reader.tarray(uuid_reader),
            "unknown_1": reader.i32(),
            "base_camp_level": reader.i32(),
            "map_object_instance_ids_base_camp_points": reader.tarray(uuid_reader),
            "guild_name": reader.fstring(),
            "last_guild_name_modifier_player_uid": reader.guid(),
            "guild_markers": reader.tarray(guild_marker_reader),
        }
        group_data |= guild
        group_data |= read_guild_tail(reader)
    if group_type == "EPalGroupType::IndependentGuild":
        guild: dict[str, Any] = {
            "base_camp_level": reader.i32(),
            "map_object_instance_ids_base_camp_points": reader.tarray(uuid_reader),
            "guild_name": reader.fstring(),
        }
        group_data |= guild
        indie = {
            "player_uid": reader.guid(),
            "guild_name_2": reader.fstring(),
            "player_info": {
                "last_online_real_time": reader.i64(),
                "player_name": reader.fstring(),
            },
        }
        group_data |= indie
    if not reader.eof():
        group_data["extra_trailing_bytes"] = reader.byte_list(reader.size - reader.data.tell())
    return group_data


def encode(
    writer: FArchiveWriter, property_type: str, properties: dict[str, Any]
) -> int:
    if property_type != "MapProperty":
        raise Exception(f"Expected MapProperty, got {property_type}")
    del properties["custom_type"]
    group_map = properties["value"]
    for group in group_map:
        if "values" in group["value"]["RawData"]["value"]:
            continue
        p = group["value"]["RawData"]["value"]
        encoded_bytes = encode_bytes(p)
        group["value"]["RawData"]["value"] = {"values": [b for b in encoded_bytes]}
    return writer.property_inner(property_type, properties)


def encode_bytes(p: dict[str, Any]) -> bytes:
    writer = FArchiveWriter()
    writer.guid(p["group_id"])
    writer.fstring(p["group_name"])
    writer.tarray(instance_id_writer, p["individual_character_handle_ids"])
    if p["group_type"] in [
        "EPalGroupType::Guild",
        "EPalGroupType::IndependentGuild",
        "EPalGroupType::Organization",
    ]:
        writer.byte(p["org_type"])
    if p["group_type"] == "EPalGroupType::Organization":
        writer.write(bytes(p["trailing_bytes"]))
    if p["group_type"] == "EPalGroupType::IndependentGuild":
        writer.guid(p["player_uid"])
        writer.fstring(p["guild_name_2"])
        writer.i64(p["player_info"]["last_online_real_time"])
        writer.fstring(p["player_info"]["player_name"])
    if p["group_type"] == "EPalGroupType::Guild":
        writer.write(bytes(p["leading_bytes"]))
        writer.tarray(uuid_writer, p["base_ids"])
        writer.i32(p["unknown_1"])
        writer.i32(p["base_camp_level"])
        writer.tarray(uuid_writer, p["map_object_instance_ids_base_camp_points"])
        writer.fstring(p["guild_name"])
        writer.guid(p["last_guild_name_modifier_player_uid"])
        writer.tarray(guild_marker_writer, p["guild_markers"])
        if "role_permissions" in p:
            writer.tarray(lambda w, value: w.byte(value), p["guild_chest_allowed_roles"])
            writer.i32(p["unknown_i32"])
            writer.guid(p["admin_player_uid"])
            writer.tarray(guild_player_info_writer, p["players"])
            writer.tarray(role_permission_writer, p["role_permissions"])
        else:
            writer.guid(p["admin_player_uid"])
            writer.tarray(player_info_writer, p["players"])
        writer.write(bytes(p["trailing_bytes"]))
    if "extra_trailing_bytes" in p:
        writer.write(bytes(p["extra_trailing_bytes"]))
    encoded_bytes = writer.bytes()
    return encoded_bytes
