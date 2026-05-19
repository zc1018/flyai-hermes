from __future__ import annotations

import json
import re
import textwrap
from typing import Any, Dict, Iterable, List, Optional, Tuple


ALLOWED_TYPES = {
    "flight_card",
    "hotel_card",
    "poi_card",
    "destination_card",
    "train_card",
    "guide_section",
    "comparison_table",
    "notice",
    "booking_link",
}

ROUTE_CITIES = [
    "北京",
    "上海",
    "广州",
    "深圳",
    "杭州",
    "南京",
    "成都",
    "重庆",
    "武汉",
    "西安",
    "东京",
    "大阪",
    "首尔",
    "新加坡",
    "曼谷",
    "巴黎",
    "伦敦",
    "纽约",
    "洛杉矶",
]


def normalize_output(raw_output: str) -> List[Dict[str, Any]]:
    parsed = _parse_json(raw_output)
    display_text = _display_text(raw_output)
    if parsed is None and display_text != raw_output:
        parsed = _parse_json(display_text)
    if parsed is None:
        display_text = _loose_json_data(display_text) or display_text
        return _blocks_from_markdown(display_text, title="查询结果")

    if isinstance(parsed, dict) and isinstance(parsed.get("blocks"), list):
        blocks = [_normalize_block(block) for block in parsed["blocks"] if isinstance(block, dict)]
        summary = parsed.get("summary")
        if summary:
            blocks.insert(0, {"type": "notice", "title": "查询结论", "items": [str(summary)]})
        return blocks or [_empty_notice()]

    if isinstance(parsed, dict) and isinstance(parsed.get("data"), dict):
        item_list = parsed["data"].get("itemList")
        if isinstance(item_list, list):
            return _blocks_from_item_list(item_list)

    if isinstance(parsed, dict) and isinstance(parsed.get("data"), str):
        blocks = _blocks_from_markdown(parsed["data"], title=parsed.get("summary") or "FlyAI 查询结果")
        if parsed.get("systemMessage"):
            blocks.append(
                {
                    "type": "notice",
                    "title": "查询提示",
                    "items": [str(parsed["systemMessage"])],
                }
            )
        return blocks

    return [_markdown_fallback(_display_text(raw_output))]


def error_blocks(message: str, stderr: str = "") -> List[Dict[str, Any]]:
    items = [message]
    if stderr:
        items.append(_clean_error_detail(stderr))
    return [{"type": "notice", "title": "查询失败", "severity": "error", "items": items}]


def _parse_json(raw_output: str) -> Optional[Any]:
    text = raw_output.strip()
    if not text:
        return None

    candidates = _json_candidates(text)

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def _json_candidates(text: str) -> List[str]:
    candidates = [text]
    fenced = re.findall(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    candidates.extend(chunk.strip() for chunk in fenced if chunk.strip())

    for line in reversed(text.splitlines()):
        clean = line.strip()
        if clean.startswith(("{", "[")):
            candidates.append(clean)

    decoder = json.JSONDecoder()
    spans: List[Tuple[int, str]] = []
    for match in re.finditer(r"[\{\[]", text):
        start = match.start()
        try:
            _, end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        spans.append((start, text[start : start + end]))

    candidates.extend(candidate for _, candidate in sorted(spans, key=lambda item: item[0], reverse=True))

    deduped: List[str] = []
    seen = set()
    for candidate in candidates:
        if candidate and candidate not in seen:
            deduped.append(candidate)
            seen.add(candidate)
    return deduped


def _display_text(raw_output: str) -> str:
    final_answer = _last_hermes_answer(raw_output)
    return final_answer or raw_output


def _last_hermes_answer(text: str) -> Optional[str]:
    lines = text.splitlines()
    starts = [index for index, line in enumerate(lines) if "⚕ Hermes" in line]
    if not starts:
        return None

    start = starts[-1] + 1
    end = len(lines)
    for index in range(start, len(lines)):
        stripped = lines[index].strip()
        if stripped.startswith("╰"):
            end = index
            break
        if stripped.startswith("Resume this session") or stripped.startswith("Session:"):
            end = index
            break
        if stripped.startswith("⚠ Iteration budget"):
            end = index
            break
        if index > start and stripped.startswith("─") and "──" in stripped:
            end = index
            break

    content_lines = []
    for line in lines[start:end]:
        clean = line.rstrip()
        if clean.startswith("│"):
            clean = clean.strip("│")
        content_lines.append(clean)

    content = textwrap.dedent("\n".join(content_lines)).strip()
    return content or None


def _loose_json_data(text: str) -> Optional[str]:
    match = re.search(r'^\s*\{\s*"data"\s*:\s*"(.*)"\s*,\s*"message"\s*:', text, flags=re.DOTALL)
    if not match:
        return None
    data = match.group(1)
    data = data.replace(r"\"", '"').replace(r"\\", "\\")
    return data.strip() or None


def _normalize_block(block: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(block)
    block_type = str(normalized.get("type", "notice"))
    if block_type not in ALLOWED_TYPES:
        block_type = "notice"
    normalized["type"] = block_type

    _alias(normalized, "bookingUrl", ["booking_url", "jumpUrl", "detailUrl", "url"])
    _alias(normalized, "imageUrl", ["image_url", "picUrl", "mainPic", "image"])
    _alias(normalized, "title", ["name"])

    for list_key in ("items", "segments", "columns", "rows"):
        if list_key in normalized and not isinstance(normalized[list_key], list):
            normalized[list_key] = [normalized[list_key]]

    for key, value in list(normalized.items()):
        if isinstance(value, str):
            normalized[key] = _strip_entity_tags(value)
        elif key == "items" and isinstance(value, list):
            normalized[key] = [
                _clean_display_line(item) if isinstance(item, str) else item
                for item in value
                if not isinstance(item, str) or _clean_display_line(item)
            ]

    if "meta" in normalized and not isinstance(normalized["meta"], dict):
        normalized["meta"] = {"value": normalized["meta"]}
    if isinstance(normalized.get("meta"), dict):
        for key in ("address", "score", "scoreDesc", "star", "ticketName"):
            if not normalized.get(key) and normalized["meta"].get(key):
                normalized[key] = normalized["meta"][key]

    if block_type == "comparison_table":
        normalized["rows"] = _normalize_table_rows(
            normalized.get("columns", []),
            normalized.get("rows", []),
        )

    return normalized


def _alias(target: Dict[str, Any], preferred: str, aliases: Iterable[str]) -> None:
    if target.get(preferred):
        return
    for alias in aliases:
        if target.get(alias):
            target[preferred] = target[alias]
            return


def _normalize_table_rows(columns: List[Any], rows: List[Any]) -> List[Dict[str, Any]]:
    normalized_rows: List[Dict[str, Any]] = []
    column_names = [str(column) for column in columns]
    for row in rows:
        if isinstance(row, dict):
            normalized_rows.append(row)
        elif isinstance(row, list):
            normalized_rows.append(
                {
                    column_names[index]: value
                    for index, value in enumerate(row)
                    if index < len(column_names)
                }
            )
    return normalized_rows


def _blocks_from_item_list(item_list: List[Any]) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = []
    for item in item_list:
        if not isinstance(item, dict):
            continue
        source = item.get("info") if isinstance(item.get("info"), dict) else item
        if _looks_like_transport(source, "飞机"):
            blocks.append(_transport_block(source, "flight_card"))
        elif _looks_like_transport(source, "火车"):
            blocks.append(_transport_block(source, "train_card"))
        elif source.get("detailUrl") or source.get("mainPic") or source.get("star"):
            blocks.append(_hotel_block(source))
        elif source.get("ticketInfo") or source.get("jumpUrl") or source.get("picUrl"):
            blocks.append(_poi_block(source))

    return blocks or [_empty_notice()]


def _looks_like_transport(source: Dict[str, Any], transport_type: str) -> bool:
    journeys = source.get("journeys")
    if not isinstance(journeys, list):
        return False
    for journey in journeys:
        for segment in journey.get("segments", []) if isinstance(journey, dict) else []:
            if isinstance(segment, dict) and segment.get("transportType") == transport_type:
                return True
    return False


def _transport_block(source: Dict[str, Any], block_type: str) -> Dict[str, Any]:
    segments: List[Dict[str, Any]] = []
    for journey in source.get("journeys", []):
        if not isinstance(journey, dict):
            continue
        for segment in journey.get("segments", []):
            if not isinstance(segment, dict):
                continue
            segments.append(
                {
                    "depCity": segment.get("depCityName"),
                    "depStation": segment.get("depStationName"),
                    "depTime": segment.get("depDateTime"),
                    "arrCity": segment.get("arrCityName"),
                    "arrStation": segment.get("arrStationName"),
                    "arrTime": segment.get("arrDateTime"),
                    "carrier": segment.get("marketingTransportName"),
                    "number": segment.get("marketingTransportNo"),
                    "seat": segment.get("seatClassName"),
                    "duration": _format_duration(segment.get("duration")),
                }
            )

    first_segment = segments[0] if segments else {}
    title = "航班方案" if block_type == "flight_card" else "火车方案"
    return {
        "type": block_type,
        "title": title,
        "price": source.get("adultPrice"),
        "duration": _format_duration(source.get("totalDuration")),
        "carrier": first_segment.get("carrier"),
        "number": first_segment.get("number"),
        "seat": first_segment.get("seat"),
        "bookingUrl": source.get("jumpUrl"),
        "segments": segments,
    }


def _blocks_from_markdown(markdown: str, title: str = "FlyAI 查询结果") -> List[Dict[str, Any]]:
    clean = markdown.strip()
    if not clean:
        return [_empty_notice()]

    flight_table_combo_blocks = _flight_table_combo_blocks(clean)
    if flight_table_combo_blocks:
        return flight_table_combo_blocks

    flight_combo_blocks = _flight_combo_blocks(clean)
    if flight_combo_blocks:
        return flight_combo_blocks

    spot_blocks = _spot_entity_blocks(clean)
    if spot_blocks:
        return spot_blocks

    destination_blocks = _plain_destination_blocks(clean)
    if destination_blocks:
        return destination_blocks

    blocks: List[Dict[str, Any]] = []
    seen = set()
    for section in _split_markdown_sections(clean):
        block = _card_from_markdown_section(section)
        if not block:
            continue
        signature = (block.get("type"), block.get("title"), block.get("bookingUrl"), block.get("number"))
        if signature in seen:
            continue
        seen.add(signature)
        blocks.append(block)

    guide = _guide_block(clean, title)
    if not blocks:
        return [guide]
    blocks.append(guide)
    return blocks


def _card_from_markdown_section(section: str) -> Optional[Dict[str, Any]]:
    plain = _markdown_to_plain(section)
    title, booking_url = _primary_markdown_title_url(section)
    image_url = _markdown_image_url(section)

    train_no = _extract_train_number(plain)
    if train_no and _looks_like_train_markdown(plain, title, train_no):
        return _markdown_transport_block(
            block_type="train_card",
            title=title or train_no,
            number=train_no,
            plain=plain,
            booking_url=booking_url,
        )

    flight_no = _extract_flight_number(plain)
    if flight_no:
        return _markdown_transport_block(
            block_type="flight_card",
            title=title or flight_no,
            number=flight_no,
            plain=plain,
            booking_url=booking_url,
        )

    if _looks_like_hotel_markdown(plain, title):
        return {
            "type": "hotel_card",
            "title": title or _first_meaningful_line(plain) or "酒店",
            "subtitle": _extract_labeled_value(plain, ["点评", "推荐理由", "亮点"]),
            "price": _extract_price(plain),
            "address": _extract_labeled_value(plain, ["地址", "位置", "商圈"]),
            "score": _extract_labeled_value(plain, ["评分", "用户评分", "评价"]),
            "scoreDesc": _extract_labeled_value(plain, ["评分说明", "口碑"]),
            "star": _extract_labeled_value(plain, ["星级", "档次", "酒店类型", "等级"]),
            "imageUrl": image_url,
            "bookingUrl": booking_url,
            "items": _extract_short_lines(plain),
        }

    if _looks_like_poi_markdown(plain, title):
        return {
            "type": "poi_card",
            "title": _compact_place_title(title, plain, "旅行项目"),
            "subtitle": _extract_labeled_value(plain, ["推荐理由", "亮点", "开放时间"]),
            "price": _extract_price(plain),
            "address": _extract_labeled_value(plain, ["地址", "位置"]),
            "ticketName": _extract_labeled_value(plain, ["票种", "门票", "产品", "套餐", "项目"]),
            "imageUrl": image_url,
            "bookingUrl": booking_url,
            "items": _extract_short_lines(plain),
        }

    return None


def _markdown_transport_block(
    block_type: str,
    title: str,
    number: str,
    plain: str,
    booking_url: Optional[str],
) -> Dict[str, Any]:
    dep, arr = _extract_route_pair(plain, allow_time=block_type == "flight_card")
    dep_time, arr_time = _extract_time_pair(plain)
    duration = _extract_duration(plain)
    carrier = _extract_labeled_value(plain, ["航司", "航空公司", "承运方", "车次类型"])
    seat = _extract_labeled_value(plain, ["舱位", "座席", "席别", "座位", "座席类型"])
    segment = {
        "depStation": dep,
        "depTime": dep_time,
        "arrStation": arr,
        "arrTime": arr_time,
        "carrier": carrier,
        "number": number,
        "seat": seat,
        "duration": duration,
    }
    return {
        "type": block_type,
        "title": title,
        "price": _extract_price(plain),
        "duration": duration,
        "carrier": carrier,
        "number": number,
        "seat": seat,
        "bookingUrl": booking_url,
        "segments": [segment],
        "items": _extract_short_lines(plain),
    }


def _flight_combo_blocks(text: str) -> List[Dict[str, Any]]:
    if "推荐组合" not in text or not re.search(r"(去程|回程)", text):
        return []

    origin, destination = _extract_route_label(text)
    blocks: List[Dict[str, Any]] = []
    combo_pattern = re.compile(
        r"推荐组合\s*([A-Za-z0-9一二三四五六七八九]+)\s*[：:]\s*(.*?)(?=\n\s*推荐组合\s*[A-Za-z0-9一二三四五六七八九]+\s*[：:]|\n\s*结论|\Z)",
        flags=re.DOTALL,
    )
    for combo in combo_pattern.finditer(text):
        label = combo.group(1).strip()
        body = combo.group(2).strip()
        segments = []
        for line in body.splitlines():
            segment = _flight_segment_from_combo_line(line, origin, destination)
            if segment:
                segments.append(segment)
        if not segments:
            continue

        total_price = _extract_labeled_total_price(body) or _extract_price(combo.group(0))
        numbers = " / ".join(segment["number"] for segment in segments if segment.get("number"))
        title = f"推荐组合 {label}"
        if origin and destination:
            title = f"{title}：{origin} ↔ {destination}"
        blocks.append(
            {
                "type": "flight_card",
                "title": title,
                "subtitle": _combo_subtitle(body),
                "price": total_price,
                "number": numbers,
                "segments": segments,
                "items": _extract_short_lines(body),
            }
        )

    if blocks:
        return blocks

    return _single_flight_summary_block(text, origin, destination)


def _flight_table_combo_blocks(text: str) -> List[Dict[str, Any]]:
    if "组合" not in text or "航段" not in text or "航班号" not in text:
        return []

    blocks: List[Dict[str, Any]] = []
    combo_pattern = re.compile(
        r"组合\s*([A-Za-z0-9一二三四五六七八九十]+)\s*[：:]\s*(.*?)(?=\n\s*组合\s*[A-Za-z0-9一二三四五六七八九十]+\s*[：:]|\n\s*(?:其他可选|去程上午|结论)\b|\Z)",
        flags=re.DOTALL,
    )
    for combo in combo_pattern.finditer(text):
        label = combo.group(1).strip()
        body = combo.group(2).strip()
        segments = _flight_segments_from_markdown_table(body)
        if not segments:
            continue

        first_segment = segments[0]
        origin = first_segment.get("depCity") or first_segment.get("depStation")
        destination = first_segment.get("arrCity") or first_segment.get("arrStation")
        numbers = " / ".join(segment["number"] for segment in segments if segment.get("number"))
        title = f"组合{label}"
        if origin and destination:
            title = f"{title}：{origin} ↔ {destination}"

        blocks.append(
            {
                "type": "flight_card",
                "title": title,
                "subtitle": _combo_subtitle(body),
                "price": _extract_labeled_total_price(body) or _extract_price(combo.group(0)),
                "number": numbers,
                "segments": segments,
            }
        )

    return blocks


def _spot_entity_blocks(text: str) -> List[Dict[str, Any]]:
    matches = list(re.finditer(r"<spot_entity>\s*(.*?)\s*</spot_entity>", text, flags=re.DOTALL | re.IGNORECASE))
    if not matches:
        return []

    blocks: List[Dict[str, Any]] = []
    for index, match in enumerate(matches):
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        section = text[match.end() : next_start]
        summary_match = re.search(r"\n\s*总结\s*\n", section)
        if summary_match:
            section = section[: summary_match.start()]

        title = _spot_entity_name(match.group(1)) or "目的地"
        subtitle: Optional[str] = None
        items: List[str] = []
        for line in section.splitlines():
            clean = _clean_display_line(line)
            if not clean or _is_runtime_notice(clean):
                continue
            if clean.startswith("亮点："):
                subtitle = clean.removeprefix("亮点：").strip()
                continue
            items.append(clean)

        blocks.append(
            {
                "type": "destination_card",
                "title": title,
                "subtitle": subtitle,
                "items": items[:4],
            }
        )

    summary = _spot_entity_summary(text, matches[-1].end())
    if summary:
        blocks.append(summary)
    return blocks


def _plain_destination_blocks(text: str) -> List[Dict[str, Any]]:
    if not re.search(r"亮点\s*[：:]", text) or not re.search(r"推荐理由\s*[：:]", text):
        return []

    raw_lines = text.splitlines()
    clean_lines = [_clean_display_line(line) for line in raw_lines]
    blocks: List[Dict[str, Any]] = []
    index = 0
    while index < len(clean_lines):
        line = clean_lines[index]
        if not line or not _looks_like_destination_heading(line):
            index += 1
            continue

        next_index = _next_nonempty_index(clean_lines, index + 1)
        if next_index is None or not re.match(r"亮点\s*[：:]", clean_lines[next_index]):
            index += 1
            continue

        section: List[str] = []
        cursor = next_index
        while cursor < len(clean_lines):
            current = clean_lines[cursor]
            if cursor != next_index and _looks_like_destination_heading(current):
                lookahead = _next_nonempty_index(clean_lines, cursor + 1)
                if lookahead is not None and re.match(r"亮点\s*[：:]", clean_lines[lookahead]):
                    break
            if current.startswith("总结"):
                break
            if current and not _is_runtime_notice(current):
                section.append(current)
            cursor += 1

        subtitle: Optional[str] = None
        items: List[str] = []
        for item in section:
            if re.match(r"亮点\s*[：:]", item):
                subtitle = re.sub(r"^亮点\s*[：:]\s*", "", item)
            elif item:
                items.append(item)

        blocks.append(
            {
                "type": "destination_card",
                "title": line,
                "subtitle": subtitle,
                "items": items[:4],
            }
        )
        index = cursor

    summary = _plain_destination_summary(clean_lines)
    if summary:
        blocks.append(summary)
    return blocks if len(blocks) >= 2 else []


def _looks_like_destination_heading(line: str) -> bool:
    if not line or len(line) > 32:
        return False
    if re.search(r"[：:。；;，,]|推荐|精选|基于|以下|总结|亮点|理由|权衡|当前", line):
        return False
    if re.search(r"[\u4e00-\u9fffA-Za-z]+[（(][\u4e00-\u9fffA-Za-z· ]+[）)]", line):
        return True
    return bool(re.search(r"(日本|韩国|新加坡|泰国|马来西亚|阿联酋|迪拜|东京|大阪|首尔|曼谷|吉隆坡)", line))


def _next_nonempty_index(lines: List[str], start: int) -> Optional[int]:
    for index in range(start, len(lines)):
        if lines[index]:
            return index
    return None


def _plain_destination_summary(lines: List[str]) -> Optional[Dict[str, Any]]:
    items: List[str] = []
    for index, line in enumerate(lines):
        if not line.startswith("总结"):
            continue
        summary = re.sub(r"^总结\s*[：:]?\s*", "", line)
        if summary:
            items.append(summary)
        for extra in lines[index + 1 :]:
            if not extra:
                continue
            if _is_runtime_notice(extra):
                continue
            items.append(extra)
            if len(items) >= 3:
                break
        break
    if not items:
        return None
    return {
        "type": "guide_section",
        "title": "总结",
        "items": items,
    }


def _spot_entity_summary(text: str, start: int) -> Optional[Dict[str, Any]]:
    tail = text[start:]
    summary_match = re.search(r"\n\s*总结\s*\n(?P<body>.*)$", tail, flags=re.DOTALL)
    if not summary_match:
        return None
    items: List[str] = []
    for line in summary_match.group("body").splitlines():
        clean = _clean_display_line(line)
        if not clean or _is_runtime_notice(clean):
            continue
        items.append(clean)
        if len(items) >= 3:
            break
    if not items:
        return None
    return {
        "type": "guide_section",
        "title": "总结",
        "items": items,
    }


def _spot_entity_name(payload: str) -> Optional[str]:
    clean = re.sub(r"\s+", " ", payload).strip()
    match = re.search(r"(?:^|;)\s*name\s*:\s*([^;<>]+)", clean)
    if match:
        return match.group(1).strip() or None
    return clean or None


def _flight_segments_from_markdown_table(text: str) -> List[Dict[str, Any]]:
    segments: List[Dict[str, Any]] = []
    in_flight_table = False
    for line in _fold_markdown_table_rows(text):
        if not line.startswith("|"):
            continue
        cells = [re.sub(r"\s+", " ", cell).strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 5:
            continue
        if "航段" in cells[0] and any("航班号" in cell for cell in cells):
            in_flight_table = True
            continue
        if re.fullmatch(r"[-:\s]+", "".join(cells)):
            continue
        if not in_flight_table:
            continue
        segment = _flight_segment_from_table_cells(cells)
        if segment:
            segments.append(segment)
    return segments


def _fold_markdown_table_rows(text: str) -> List[str]:
    rows: List[str] = []
    pending: Optional[str] = None
    expected_pipes = 0
    for raw_line in text.splitlines():
        clean = raw_line.strip()
        if not clean:
            if pending:
                rows.append(pending)
                pending = None
            continue
        if clean.startswith("|"):
            if pending:
                rows.append(pending)
            pending = clean
            if "航段" in clean and "价格" in clean:
                expected_pipes = clean.count("|")
            if expected_pipes and pending.count("|") >= expected_pipes:
                rows.append(pending)
                pending = None
            continue
        if pending and expected_pipes and pending.count("|") < expected_pipes and "|" in clean:
            pending = f"{pending} {clean}"
            if pending.count("|") >= expected_pipes:
                rows.append(pending)
                pending = None
            continue
        if pending:
            rows.append(pending)
            pending = None
        rows.append(clean)
    if pending:
        rows.append(pending)
    return rows


def _flight_segment_from_table_cells(cells: List[str]) -> Optional[Dict[str, Any]]:
    if len(cells) < 6:
        return None
    route_cell, date_cell, flight_cell, dep_cell, arr_cell, price_cell = cells[:6]
    origin, destination = _city_pair_from_route_cell(route_cell)
    number = _extract_flight_number(flight_cell)
    dep_station, dep_time = _split_station_time(dep_cell)
    arr_station, arr_time = _split_station_time(arr_cell)
    if not number or not dep_time or not arr_time:
        return None
    return {
        "depCity": origin,
        "depStation": dep_station,
        "depTime": _join_date_time(date_cell, dep_time),
        "arrCity": destination,
        "arrStation": arr_station,
        "arrTime": arr_time,
        "carrier": _extract_carrier_from_flight_cell(flight_cell, number),
        "number": number,
        "price": _extract_price(price_cell),
    }


def _city_pair_from_route_cell(route_cell: str) -> Tuple[Optional[str], Optional[str]]:
    if not re.search(r"(→|->|－|-|—)", route_cell):
        return None, None
    left, right = re.split(r"\s*(?:→|->|－|-|—)\s*", route_cell, maxsplit=1)
    return _compact_route_endpoint(left), _compact_route_endpoint(right)


def _split_station_time(value: str) -> Tuple[Optional[str], Optional[str]]:
    match = re.search(r"(\d{1,2}:\d{2})", value)
    if not match:
        return value.strip() or None, None
    station = value[: match.start()].strip(" -:：")
    return station or None, match.group(1)


def _extract_carrier_from_flight_cell(value: str, number: str) -> Optional[str]:
    carrier = value.replace(number, "", 1).strip(" -:：")
    return carrier or None


def _combo_subtitle(body: str) -> Optional[str]:
    first_line = _first_meaningful_line(body)
    if not first_line:
        return None
    note = re.sub(r"^(?:总价|合计)\s*[：:]?\s*¥\s*[\d,]+(?:\.\d+)?", "", first_line)
    note = note.strip(" -:：()（）")
    return note or first_line


def _flight_combo_notes(text: str) -> Optional[Dict[str, Any]]:
    items: List[str] = []
    for clean in _fold_soft_wrapped_lines(text):
        if not clean:
            continue
        if clean.startswith("|") or re.fullmatch(r"[-|\s]+", clean):
            continue
        if re.search(r"^(推荐组合|组合|去程|回程|合计|总价|其他可选|预订)", clean):
            continue
        if _extract_flight_number(clean) or re.search(r"\d{1,2}:\d{2}", clean):
            continue
        if clean in items:
            continue
        items.append(clean)
        if len(items) >= 4:
            break
    if not items:
        return None
    return {
        "type": "guide_section",
        "title": "补充说明",
        "items": items,
    }


def _fold_soft_wrapped_lines(text: str) -> List[str]:
    folded: List[str] = []
    for line in text.splitlines():
        clean = _clean_markdown_text(line)
        if not clean:
            continue
        starts_structural = bool(
            re.search(r"^(推荐组合|去程|回程|合计|总价|结论|最低票价|端午节日期说明|价格说明|注意)", clean)
        )
        previous_is_heading = bool(
            folded and len(folded[-1]) <= 20 and re.search(r"(说明|组合|结论|提示|注意)$", folded[-1])
        )
        if folded and not starts_structural and not previous_is_heading and not re.search(r"[。！？；;：:]$", folded[-1]):
            folded[-1] = f"{folded[-1]}{clean}"
        else:
            folded.append(clean)
    return folded


def _extract_route_label(text: str) -> Tuple[Optional[str], Optional[str]]:
    match = re.search(r"([\u4e00-\u9fffA-Za-z]{2,14})\s*[↔⇄]\s*([\u4e00-\u9fffA-Za-z]{2,14})", text)
    if match:
        return _compact_route_endpoint(match.group(1)), _compact_route_endpoint(match.group(2))
    return None, None


def _compact_route_endpoint(value: str) -> str:
    for city in sorted(ROUTE_CITIES, key=len, reverse=True):
        if city in value:
            return city
    clean = re.sub(r"(往返|来回|前后|附近|端午节|假期|出发|返回)", "", value)
    return clean[-6:] if len(clean) > 6 else clean


def _flight_segment_from_combo_line(line: str, origin: Optional[str], destination: Optional[str]) -> Optional[Dict[str, Any]]:
    clean = _clean_markdown_text(line)
    if not re.search(r"(去程|回程)", clean):
        return None
    number = _extract_flight_number(clean)
    dep_time, arr_time = _extract_time_pair(clean)
    price = _extract_price(clean)
    if not number or not dep_time or not arr_time:
        return None

    direction = "return" if "回程" in clean else "outbound"
    date_match = re.search(r"(\d{1,2}\s*月\s*\d{1,2}\s*日|\d{1,2}/\d{1,2})", clean)
    carrier = _extract_carrier_after_number(clean, number)
    dep_station, arr_station = _direction_stations(direction, origin, destination)
    return {
        "depStation": dep_station,
        "depTime": _join_date_time(date_match.group(1) if date_match else None, dep_time),
        "arrStation": arr_station,
        "arrTime": arr_time,
        "carrier": carrier,
        "number": number,
        "price": price,
    }


def _extract_carrier_after_number(text: str, number: str) -> Optional[str]:
    after = text.split(number, 1)[1] if number in text else ""
    match = re.match(r"\s*([\u4e00-\u9fffA-Za-z·]{1,18}?)(?=\s*\d{1,2}:\d{2})", after)
    if match:
        return match.group(1).strip() or None
    return None


def _direction_stations(direction: str, origin: Optional[str], destination: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if not origin or not destination:
        return None, None
    if direction == "return":
        return destination, origin
    return origin, destination


def _join_date_time(date_text: Optional[str], time_text: Optional[str]) -> Optional[str]:
    if not time_text:
        return None
    if not date_text:
        return time_text
    clean_date = re.sub(r"\s+", "", date_text)
    return f"{clean_date} {time_text}"


def _extract_labeled_total_price(text: str) -> Optional[str]:
    match = re.search(r"(?:总价|合计)\s*[：:]?\s*(¥\s*[\d,]+)", text)
    return re.sub(r"\s+", "", match.group(1)) if match else None


def _single_flight_summary_block(text: str, origin: Optional[str], destination: Optional[str]) -> List[Dict[str, Any]]:
    number = _extract_flight_number(text)
    dep_time, arr_time = _extract_time_pair(text)
    price = _extract_price(text)
    if not number or not price:
        return []
    title = "航班方案"
    if origin and destination:
        title = f"{origin} ↔ {destination}"
    return [
        {
            "type": "flight_card",
            "title": title,
            "price": price,
            "number": number,
            "segments": [
                {
                    "depStation": origin,
                    "depTime": dep_time,
                    "arrStation": destination,
                    "arrTime": arr_time,
                    "number": number,
                }
            ],
            "items": _extract_short_lines(text),
        }
    ]


def _split_markdown_sections(markdown: str) -> List[str]:
    sections: List[List[str]] = []
    current: List[str] = []
    for line in markdown.splitlines():
        if current and _is_markdown_section_start(line):
            sections.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append(current)
    return ["\n".join(section).strip() for section in sections if "\n".join(section).strip()]


def _is_markdown_section_start(line: str) -> bool:
    clean = line.strip()
    if not clean:
        return False
    if re.match(r"^#{1,4}\s+\S", clean):
        return True
    if re.search(r"(?<!!)\[[^\]]+\]\(https?://[^)]+\)", clean):
        return True
    if re.match(r"^\*\*[^*]{2,80}\*\*$", clean) and not re.search(r"(价格|票价|地址|时间|评分|出发|到达|门票)\s*[:：]", clean):
        return True
    return False


def _primary_markdown_title_url(section: str) -> Tuple[Optional[str], Optional[str]]:
    link = re.search(r"(?<!!)\[([^\]]+)\]\((https?://[^)]+)\)", section)
    if link:
        return _clean_markdown_text(link.group(1)), link.group(2).strip()

    bold = re.search(r"\*\*([^*\n]{2,100})\*\*", section)
    if bold:
        return _clean_markdown_text(bold.group(1)), None

    heading = re.search(r"^\s*#{1,4}\s+(.+)$", section, flags=re.MULTILINE)
    if heading:
        return _clean_markdown_text(heading.group(1)), None

    first_line = _first_meaningful_line(_markdown_to_plain(section))
    return first_line, None


def _markdown_image_url(section: str) -> Optional[str]:
    match = re.search(r"!\[[^\]]*\]\((https?://[^)]+)\)", section)
    return match.group(1).strip() if match else None


def _markdown_to_plain(markdown: str) -> str:
    text = re.sub(r"!\[([^\]]*)\]\(https?://[^)]+\)", r"\1", markdown)
    text = re.sub(r"(?<!!)\[([^\]]+)\]\(https?://[^)]+\)", r"\1", text)
    text = re.sub(r"[*_`]", "", text)
    lines = []
    for line in text.splitlines():
        clean = re.sub(r"^\s*(?:#{1,6}\s*|[-*]\s+|\d+[.)]\s+)", "", line).strip()
        if clean:
            lines.append(clean)
    return "\n".join(lines)


def _clean_markdown_text(value: str) -> str:
    value = _strip_entity_tags(value)
    text = re.sub(r"!\[[^\]]*\]\(https?://[^)]+\)", "", value)
    text = re.sub(r"(?<!!)\[([^\]]+)\]\(https?://[^)]+\)", r"\1", text)
    text = re.sub(r"[*_`#]", "", text)
    text = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", text)
    return re.sub(r"\s+", " ", text).strip(" -:：")


def _clean_display_line(value: str) -> str:
    return _clean_markdown_text(value).strip()


def _strip_entity_tags(value: str) -> str:
    text = re.sub(
        r"<spot_entity>\s*(.*?)\s*</spot_entity>",
        lambda match: _spot_entity_name(match.group(1)) or "",
        value,
        flags=re.DOTALL | re.IGNORECASE,
    )
    text = re.sub(r"</?[a-zA-Z][a-zA-Z0-9_-]*[^>]*>", "", text)
    return text


def _is_runtime_notice(value: str) -> bool:
    return bool(re.search(r"(当前为体验模式|飞猪AI开放平台|正式API Key|Resume this session|Session:)", value))


def _extract_train_number(text: str) -> Optional[str]:
    match = re.search(r"(?<![A-Z0-9])([GDCZTKY]\d{1,5})(?![A-Z0-9])", text, flags=re.IGNORECASE)
    return match.group(1).upper() if match else None


def _extract_flight_number(text: str) -> Optional[str]:
    match = re.search(r"(?<![A-Z0-9])((?:[A-Z]{2}|[A-Z]\d|\d[A-Z])\d{3,4})(?![A-Z0-9])", text, flags=re.IGNORECASE)
    return match.group(1).upper() if match else None


def _extract_price(text: str) -> Optional[str]:
    if re.search(r"(免费|免门票|无需门票|无(?:需)?大门票)", text):
        return "免费"
    match = re.search(r"(?:¥|￥)\s*[\d,]+(?:\.\d+)?(?:\s*(?:起|/晚|每晚|元起)?)?", text)
    if match:
        return re.sub(r"\s+", "", match.group(0)).replace("￥", "¥")
    match = re.search(r"(?<!\d)(\d+(?:\.\d+)?)\s*元(?:起|/晚|每晚)?", text)
    return f"¥{match.group(1)}" if match else None


def _extract_duration(text: str) -> Optional[str]:
    labeled = _extract_labeled_value(text, ["历时", "耗时", "用时", "时长", "飞行时间", "行程时间"])
    if labeled:
        return labeled
    match = re.search(r"(?:约\s*)?\d+\s*小时(?:\s*\d+\s*分钟)?|(?:约\s*)?\d+\s*分钟", text)
    return re.sub(r"\s+", "", match.group(0)) if match else None


def _extract_labeled_value(text: str, labels: List[str]) -> Optional[str]:
    label_pattern = "|".join(re.escape(label) for label in labels)
    for line in text.splitlines():
        match = re.search(rf"(?:{label_pattern})\s*[:：]\s*(.+)$", line, flags=re.IGNORECASE)
        if not match:
            continue
        value = match.group(1).strip()
        value = re.split(r"\s{2,}|[；;]", value)[0].strip()
        return value or None
    return None


def _extract_route_pair(text: str, allow_time: bool) -> Tuple[Optional[str], Optional[str]]:
    for line in text.splitlines():
        if not allow_time and re.search(r"\d{1,2}:\d{2}", line):
            continue
        pair = _split_route_line(line)
        if pair:
            return pair
    return None, None


def _split_route_line(line: str) -> Optional[Tuple[str, str]]:
    if not re.search(r"(→|->)", line):
        return None
    left, right = re.split(r"\s*(?:→|->)\s*", line, maxsplit=1)
    left = _clean_route_endpoint(left, is_left=True)
    right = _clean_route_endpoint(right, is_left=False)
    if not left or not right:
        return None
    return left, right


def _clean_route_endpoint(value: str, is_left: bool) -> str:
    text = re.sub(r"\d{1,2}:\d{2}", "", value)
    if is_left:
        text = re.sub(r"^.*(?:出发站|始发站|出发|始发|起飞|起点)\s*[:：]?\s*", "", text)
    else:
        text = re.sub(r"^\s*(?:到达站|终到站|到达|抵达|降落|终点)\s*[:：]?\s*", "", text)
    text = re.sub(r"\([^)]*\)", "", text)
    text = re.sub(r"（[^）]*）", "", text)
    return re.sub(r"\s+", " ", text).strip(" -:：，,")


def _extract_time_pair(text: str) -> Tuple[Optional[str], Optional[str]]:
    for line in text.splitlines():
        match = re.search(r"(\d{1,2}:\d{2}).{0,24}?(?:→|->|至).{0,24}?(\d{1,2}:\d{2})", line)
        if match:
            return match.group(1), match.group(2)
    times = re.findall(r"\b\d{1,2}:\d{2}\b", text)
    if len(times) >= 2:
        return times[0], times[1]
    return None, None


def _looks_like_hotel_markdown(plain: str, title: Optional[str]) -> bool:
    haystack = f"{title or ''}\n{plain}"
    if not re.search(r"(酒店|宾馆|民宿|客栈|Hotel|Marriott|Hilton|Hyatt|万豪|希尔顿|凯悦|亚朵|全季|如家|汉庭|洲际|喜来登|入住|每晚)", haystack, re.IGNORECASE):
        return False
    return bool(_extract_price(plain) or _extract_labeled_value(plain, ["地址", "位置", "评分", "星级"]) or re.search(r"https?://", haystack))


def _looks_like_train_markdown(plain: str, title: Optional[str], train_no: str) -> bool:
    haystack = f"{title or ''}\n{plain}"
    if re.search(r"(火车|高铁|动车|列车|车次|车票|出发站|到达站|发车|到站|候车|席别)", haystack):
        return True
    dep, arr = _extract_route_pair(plain, allow_time=False)
    return bool(title and train_no in title and dep and arr)


def _looks_like_poi_markdown(plain: str, title: Optional[str]) -> bool:
    haystack = f"{title or ''}\n{plain}"
    if not re.search(r"(景区|景点|门票|票种|游船|乐园|公园|博物馆|展馆|演出|一日游|观光|项目)", haystack):
        return False
    return bool(_extract_price(plain) or _extract_labeled_value(plain, ["地址", "位置", "门票", "票种", "项目"]) or re.search(r"https?://", haystack))


def _first_meaningful_line(text: str) -> Optional[str]:
    for line in text.splitlines():
        clean = _clean_markdown_text(line)
        if clean:
            return clean
    return None


def _compact_place_title(title: Optional[str], plain: str, fallback: str) -> str:
    candidate = title or _first_meaningful_line(plain)
    if (
        candidate
        and len(candidate) <= 42
        and not candidate.startswith(("{", "["))
        and not re.search(r"(?:是|为|。|，|,|关于|如下|门票信息)", candidate)
    ):
        return candidate

    for line in plain.splitlines():
        clean = _clean_markdown_text(line)
        about = re.search(r"关于\s*([\u4e00-\u9fffA-Za-z0-9·（）()]{2,36}?)(?:的?(?:门票|票价|旅行|游玩|信息)|如下)", clean)
        if about:
            return about.group(1)
        match = re.match(r"([\u4e00-\u9fffA-Za-z0-9·（）()]{2,36}?)(?:是|为|，|,|：|:)", clean)
        if match:
            return match.group(1)

    if candidate:
        return candidate[:42].rstrip() + "..."
    return fallback


def _guide_block(markdown: str, title: str) -> Dict[str, Any]:
    clean = _strip_entity_tags(markdown).strip()
    return {
        "type": "guide_section",
        "title": title,
        "markdown": clean,
        "items": _extract_short_lines(clean),
    }


def _hotel_block(source: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "hotel_card",
        "title": source.get("name") or source.get("title") or "酒店",
        "subtitle": source.get("review") or source.get("interestsPoi"),
        "price": source.get("price"),
        "address": source.get("address"),
        "score": source.get("score"),
        "scoreDesc": source.get("scoreDesc"),
        "star": source.get("star"),
        "imageUrl": source.get("mainPic"),
        "bookingUrl": source.get("detailUrl"),
    }


def _poi_block(source: Dict[str, Any]) -> Dict[str, Any]:
    ticket_info = source.get("ticketInfo") if isinstance(source.get("ticketInfo"), dict) else {}
    return {
        "type": "poi_card",
        "title": source.get("name") or source.get("title") or "旅行项目",
        "subtitle": source.get("freePoiStatus") or source.get("scoreDesc"),
        "price": ticket_info.get("price") or source.get("price"),
        "address": source.get("address"),
        "ticketName": ticket_info.get("ticketName"),
        "imageUrl": source.get("picUrl") or source.get("mainPic"),
        "bookingUrl": source.get("jumpUrl"),
        "items": source.get("tags") if isinstance(source.get("tags"), list) else [],
    }


def _markdown_fallback(raw_output: str) -> Dict[str, Any]:
    text = _strip_entity_tags(raw_output).strip()
    if not text:
        return _empty_notice()
    return {
        "type": "guide_section",
        "title": "查询结果",
        "markdown": text,
        "items": _extract_short_lines(text),
    }


def _clean_error_detail(stderr: str) -> str:
    text = re.sub(r"<[^>]+>", " ", stderr)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > 800:
        text = text[:800] + "..."
    return text


def _format_duration(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if not re.fullmatch(r"\d+", text):
        return text

    minutes = int(text)
    hours, remainder = divmod(minutes, 60)
    if hours and remainder:
        return f"{hours}小时{remainder}分钟"
    if hours:
        return f"{hours}小时"
    return f"{remainder}分钟"


def _extract_short_lines(text: str) -> List[str]:
    lines = []
    for line in text.splitlines():
        clean = _clean_display_line(line)
        if clean and not _is_runtime_notice(clean):
            lines.append(clean)
        if len(lines) >= 8:
            break
    return lines


def _empty_notice() -> Dict[str, Any]:
    return {
        "type": "notice",
        "title": "没有可展示的结果",
        "items": ["没有从 Hermes 输出中解析到旅行结果，请换一个更具体的查询再试。"],
    }
