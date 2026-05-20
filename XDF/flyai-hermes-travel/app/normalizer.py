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

TYPE_ALIASES = {
    "flightcard": "flight_card",
    "flight-card": "flight_card",
    "flight": "flight_card",
    "aircard": "flight_card",
    "airlinecard": "flight_card",
    "ticketcard": "flight_card",
    "hotelcard": "hotel_card",
    "hotel": "hotel_card",
    "poicard": "poi_card",
    "poi": "poi_card",
    "destinationcard": "destination_card",
    "destination": "destination_card",
    "traincard": "train_card",
    "train": "train_card",
    "guidesection": "guide_section",
    "guide-section": "guide_section",
    "guide": "guide_section",
    "comparisontable": "comparison_table",
    "comparison-table": "comparison_table",
    "table": "comparison_table",
    "bookinglink": "booking_link",
    "booking-link": "booking_link",
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

STRUCTURAL_SOURCE_KEYS = {
    "type",
    "title",
    "price",
    "number",
    "label",
    "depCity",
    "depStation",
    "depTime",
    "arrCity",
    "arrStation",
    "arrTime",
    "carrier",
    "seat",
    "duration",
    "segments",
    "columns",
    "rows",
    "bookingUrl",
    "booking_url",
    "jumpUrl",
    "detailUrl",
    "url",
    "imageUrl",
    "image_url",
    "picUrl",
    "mainPic",
}


def normalize_output(raw_output: str, user_query: str = "") -> List[Dict[str, Any]]:
    parsed = _parse_json(raw_output)
    display_text = _display_text(raw_output)
    if parsed is None and display_text != raw_output:
        parsed = _parse_json(display_text)
    if parsed is None:
        display_text = _loose_json_data(display_text) or display_text
        return _postprocess_blocks(
            _blocks_from_markdown(display_text, title="查询结果"),
            display_text,
            user_query,
        )

    if isinstance(parsed, dict) and isinstance(parsed.get("blocks"), list):
        blocks = [_normalize_block(block) for block in parsed["blocks"] if isinstance(block, dict)]
        summary = parsed.get("summary")
        if summary:
            blocks.insert(0, {"type": "notice", "title": "查询结论", "items": [str(summary)]})
        return _postprocess_blocks(blocks or [_empty_notice()], _source_text_from_parsed(parsed, display_text), user_query)

    if isinstance(parsed, dict) and isinstance(parsed.get("data"), dict):
        item_list = parsed["data"].get("itemList")
        if isinstance(item_list, list):
            return _postprocess_blocks(
                _blocks_from_item_list(item_list),
                _source_text_from_parsed(parsed, display_text),
                user_query,
            )

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
        return _postprocess_blocks(blocks, parsed["data"], user_query)

    fallback_text = _display_text(raw_output)
    return _postprocess_blocks([_markdown_fallback(fallback_text)], fallback_text, user_query)


def _postprocess_blocks(blocks: List[Dict[str, Any]], source_text: str, user_query: str) -> List[Dict[str, Any]]:
    if not _wants_round_trip_flight(user_query, source_text):
        return blocks
    if any(_complete_round_trip_flight_card(block, source_text) for block in blocks):
        return _label_complete_round_trip_cards(blocks, source_text)

    repaired = _repair_round_trip_flight_card(source_text, blocks, user_query)
    if repaired:
        return _replace_or_insert_round_trip_card(blocks, repaired, source_text)

    if _has_incomplete_flight_card(blocks) or _source_has_flight_number(source_text):
        return _insert_round_trip_warning(blocks)
    return blocks


def _source_text_from_parsed(value: Any, fallback: str) -> str:
    strings: List[str] = []

    def collect(item: Any) -> None:
        if isinstance(item, str):
            clean = item.strip()
            if clean:
                strings.append(clean)
            return
        if isinstance(item, dict):
            for key, child in item.items():
                if key in STRUCTURAL_SOURCE_KEYS:
                    continue
                collect(child)
            return
        if isinstance(item, list):
            for child in item:
                collect(child)

    collect(value)
    return "\n".join(strings) or fallback


def _wants_round_trip_flight(user_query: str, source_text: str) -> bool:
    query = user_query or ""
    combined = f"{query}\n{source_text or ''}"
    has_round_trip = bool(
        re.search(r"(往返|来回|回程|返程|往回|返回)", query)
        or re.search(r"(往返|回程|返程)", combined)
    )
    if not has_round_trip:
        return False
    has_flight_intent = bool(
        re.search(r"(机票|航班|飞机|直飞|起飞|航司|航空|机场|航段)", query)
        or re.search(r"(航班|机票|航司|航空|机场|航段|起降时间)", combined)
        or _source_has_flight_number(combined)
    )
    return has_flight_intent


def _complete_round_trip_flight_card(block: Dict[str, Any], source_text: str) -> bool:
    if block.get("type") != "flight_card":
        return False
    segments = [segment for segment in block.get("segments", []) if isinstance(segment, dict)]
    if len(segments) < 2 or not _looks_like_round_trip_segments(segments):
        return False
    if _source_has_flight_number(source_text):
        outbound = next((segment for segment in segments if segment.get("label") == "去程"), segments[0])
        inbound = next((segment for segment in segments if segment.get("label") == "返程"), None)
        if inbound is None:
            first_dep = _segment_dep_endpoint(outbound)
            first_arr = _segment_arr_endpoint(outbound)
            inbound = next(
                (
                    segment
                    for segment in segments[1:]
                    if _same_endpoint(_segment_dep_endpoint(segment), first_arr)
                    and _same_endpoint(_segment_arr_endpoint(segment), first_dep)
                ),
                None,
            )
        if not outbound.get("number") or not (inbound and inbound.get("number")):
            return False
    if _source_has_price(source_text) and not (block.get("price") or _total_price_from_segments(segments)):
        return False
    return True


def _has_incomplete_flight_card(blocks: List[Dict[str, Any]]) -> bool:
    return any(block.get("type") == "flight_card" for block in blocks)


def _label_complete_round_trip_cards(blocks: List[Dict[str, Any]], source_text: str) -> List[Dict[str, Any]]:
    labeled_blocks: List[Dict[str, Any]] = []
    for block in blocks:
        if not _complete_round_trip_flight_card(block, source_text):
            labeled_blocks.append(block)
            continue
        cloned = dict(block)
        segments = [dict(segment) for segment in cloned.get("segments", []) if isinstance(segment, dict)]
        selected = _select_round_trip_segments(segments)
        for label, selected_segment in (("去程", selected[0] if selected else None), ("返程", selected[1] if len(selected) > 1 else None)):
            if not selected_segment:
                continue
            for segment in segments:
                if _segment_signature(segment) == _segment_signature(selected_segment):
                    segment["label"] = label
                    break
        cloned["segments"] = segments
        labeled_blocks.append(cloned)
    return labeled_blocks


def _source_has_flight_number(text: str) -> bool:
    return bool(_extract_flight_number(text or ""))


def _source_has_price(text: str) -> bool:
    return bool(_extract_price(text or ""))


def _repair_round_trip_flight_card(
    source_text: str,
    blocks: List[Dict[str, Any]],
    user_query: str,
) -> Optional[Dict[str, Any]]:
    candidate_sources = [source_text, f"{user_query}\n{source_text}"]
    parsers = (
        _round_trip_flight_table_blocks,
        _round_trip_flight_schedule_blocks,
        _round_trip_flight_prose_blocks,
        _flight_table_combo_blocks,
        _flight_combo_blocks,
    )
    for candidate_source in candidate_sources:
        for parser in parsers:
            for candidate in parser(candidate_source):
                if _complete_round_trip_flight_card(candidate, candidate_source):
                    return candidate

    combined = _round_trip_card_from_existing_blocks(blocks, source_text, user_query)
    if combined and _complete_round_trip_flight_card(combined, f"{user_query}\n{source_text}"):
        return combined

    generic = _generic_round_trip_flight_card(source_text, user_query)
    if generic and _complete_round_trip_flight_card(generic, f"{user_query}\n{source_text}"):
        return generic
    return None


def _replace_or_insert_round_trip_card(
    blocks: List[Dict[str, Any]],
    repaired: Dict[str, Any],
    source_text: str,
) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    replaced = False
    for block in blocks:
        if block.get("type") == "flight_card" and not _complete_round_trip_flight_card(block, source_text):
            if not replaced:
                result.append(repaired)
                replaced = True
            continue
        result.append(block)

    if replaced:
        return result

    insert_at = 0
    if result and result[0].get("type") == "notice" and result[0].get("title") == "查询结论":
        insert_at = 1
    for index, block in enumerate(result):
        if block.get("type") == "guide_section":
            insert_at = index
            break
    result.insert(insert_at, repaired)
    return result


def _insert_round_trip_warning(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    warning = {
        "type": "notice",
        "title": "结果可能不完整",
        "severity": "warning",
        "items": [
            "检测到你查询的是往返机票，但本次 Hermes/fly.ai 输出中没有足够结构化信息生成完整往返卡片。",
            "下方仍保留原始查询内容；建议补充明确去程和返程日期后重新查询。",
        ],
    }
    if any(block.get("title") == warning["title"] for block in blocks):
        return blocks
    insert_at = 0
    if blocks and blocks[0].get("type") == "notice" and blocks[0].get("title") == "查询结论":
        insert_at = 1
    found_target = False
    for index, block in enumerate(blocks):
        if block.get("type") == "flight_card":
            insert_at = index
            found_target = True
            break
    if not found_target:
        for index, block in enumerate(blocks):
            if block.get("type") == "guide_section":
                insert_at = index
                break
    return blocks[:insert_at] + [warning] + blocks[insert_at:]


def _round_trip_card_from_existing_blocks(
    blocks: List[Dict[str, Any]],
    source_text: str,
    user_query: str,
) -> Optional[Dict[str, Any]]:
    segments: List[Dict[str, Any]] = []
    for block in blocks:
        if block.get("type") != "flight_card":
            continue
        for segment in block.get("segments", []):
            if isinstance(segment, dict):
                segments.append(dict(segment))
    selected = _select_round_trip_segments(segments)
    if len(selected) < 2:
        return None
    return _round_trip_card_from_segments(selected, source_text, user_query)


def _generic_round_trip_flight_card(source_text: str, user_query: str) -> Optional[Dict[str, Any]]:
    origin, destination = _extract_route_label(f"{user_query}\n{source_text}")
    segments: List[Dict[str, Any]] = []
    seen_lines = set()
    for line in [*_fold_markdown_table_rows(source_text), *_fold_soft_wrapped_lines(source_text)]:
        clean = _clean_display_line(line)
        if not clean or clean in seen_lines:
            continue
        seen_lines.add(clean)
        if not re.search(r"(去程|回程|返程)", clean):
            continue
        segment = _flight_segment_from_combo_line(clean, origin, destination)
        if segment:
            segments.append(segment)

    selected = _select_round_trip_segments(_dedupe_segments(segments))
    if len(selected) < 2:
        return None
    return _round_trip_card_from_segments(selected, source_text, user_query)


def _select_round_trip_segments(segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if len(segments) < 2:
        return []
    outbound = next((segment for segment in segments if segment.get("label") == "去程"), None)
    inbound = next((segment for segment in segments if segment.get("label") == "返程"), None)
    if outbound and inbound:
        return [outbound, inbound]

    for index, first in enumerate(segments):
        first_dep = _segment_dep_endpoint(first)
        first_arr = _segment_arr_endpoint(first)
        if not first_dep or not first_arr:
            continue
        for second in segments[index + 1 :]:
            if _same_endpoint(_segment_dep_endpoint(second), first_arr) and _same_endpoint(_segment_arr_endpoint(second), first_dep):
                selected = [dict(first), dict(second)]
                if not selected[0].get("label"):
                    selected[0]["label"] = "去程"
                if not selected[1].get("label"):
                    selected[1]["label"] = "返程"
                return selected
    return []


def _dedupe_segments(segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: List[Dict[str, Any]] = []
    seen = set()
    for segment in segments:
        signature = (
            segment.get("label"),
            segment.get("number"),
            segment.get("depTime"),
            segment.get("arrTime"),
        )
        if signature in seen:
            continue
        seen.add(signature)
        deduped.append(segment)
    return deduped


def _segment_signature(segment: Dict[str, Any]) -> Tuple[Any, Any, Any, Any]:
    return (
        segment.get("number"),
        segment.get("depTime"),
        segment.get("arrTime"),
        _segment_dep_endpoint(segment),
    )


def _round_trip_card_from_segments(
    segments: List[Dict[str, Any]],
    source_text: str,
    user_query: str,
) -> Dict[str, Any]:
    outbound = segments[0]
    origin = _segment_dep_endpoint(outbound)
    destination = _segment_arr_endpoint(outbound)
    price = _total_price_from_segments(segments) or _extract_labeled_total_price(source_text)
    if not price:
        price = "未返回完整票价" if any(_has_real_price(segment.get("price")) for segment in segments) else "未返回票价"
    return {
        "type": "flight_card",
        "title": _round_trip_table_title(f"{user_query}\n{source_text}", origin, destination),
        "subtitle": _round_trip_table_subtitle(source_text),
        "price": price,
        "number": " / ".join(segment["number"] for segment in segments if segment.get("number")),
        "segments": segments,
        "items": _round_trip_prose_items(source_text, has_price=price not in {"未返回票价", "未返回完整票价"}),
    }


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
        for variant in _json_parse_variants(candidate):
            try:
                return json.loads(variant)
            except json.JSONDecodeError:
                continue
    return None


def _json_parse_variants(candidate: str) -> List[str]:
    variants = [candidate]
    repaired = _repair_json_string_line_breaks(candidate)
    if repaired != candidate:
        variants.append(repaired)
    return variants


def _repair_json_string_line_breaks(candidate: str) -> str:
    repaired: List[str] = []
    in_string = False
    escaped = False
    for char in candidate:
        if in_string and char in {"\n", "\r", "\t"}:
            if repaired and repaired[-1] != " ":
                repaired.append(" ")
            escaped = False
            continue
        repaired.append(char)
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
    return "".join(repaired)


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
    block_type = _normalize_block_type(str(normalized.get("type", "notice")))
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
    if block_type in {"flight_card", "train_card"}:
        normalized["price"] = _normalize_price_value(normalized.get("price"))
        if isinstance(normalized.get("segments"), list):
            normalized["segments"] = [
                _normalize_segment(segment)
                for segment in normalized["segments"]
                if isinstance(segment, dict)
            ]

    return normalized


def _normalize_block_type(value: str) -> str:
    clean = re.sub(r"\s+", "", value.strip())
    if not clean:
        return "notice"
    if clean in ALLOWED_TYPES:
        return clean
    lowered = clean.lower()
    if lowered in TYPE_ALIASES:
        return TYPE_ALIASES[lowered]
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", clean).lower()
    return TYPE_ALIASES.get(snake, snake)


def _normalize_segment(segment: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(segment)
    for key, value in list(normalized.items()):
        if isinstance(value, str):
            normalized[key] = _strip_entity_tags(value).strip()
    if "price" in normalized:
        normalized["price"] = _normalize_price_value(normalized.get("price"))
    return normalized


def _normalize_price_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    clean = value.strip()
    if not clean:
        return clean
    if re.search(r"(?:¥|￥|元|免费|未返回)", clean):
        extracted = _extract_price(clean)
        return extracted or clean.replace("￥", "¥")
    if re.fullmatch(r"\d{3,7}(?:\.\d+)?", clean.replace(",", "")):
        amount = float(clean.replace(",", ""))
        if amount.is_integer():
            return f"¥{int(amount):,}"
        return f"¥{amount:,.2f}"
    return clean


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
    journeys = source.get("journeys", [])
    for journey_index, journey in enumerate(journeys):
        if not isinstance(journey, dict):
            continue
        for segment in journey.get("segments", []):
            if not isinstance(segment, dict):
                continue
            label = None
            if block_type == "flight_card" and len(journeys) > 1:
                label = "返程" if journey_index == 1 else "去程"
            segments.append(
                {
                    "label": label,
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

    round_trip_table_blocks = _round_trip_flight_table_blocks(clean)
    if round_trip_table_blocks:
        return round_trip_table_blocks

    round_trip_schedule_blocks = _round_trip_flight_schedule_blocks(clean)
    if round_trip_schedule_blocks:
        return round_trip_schedule_blocks

    round_trip_prose_blocks = _round_trip_flight_prose_blocks(clean)
    if round_trip_prose_blocks:
        return round_trip_prose_blocks

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
    if "组合" not in text or "航段" not in text or not re.search(r"航班(?:号)?", text):
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


def _round_trip_flight_table_blocks(text: str) -> List[Dict[str, Any]]:
    if not re.search(r"(往返|去程|返程)", text) or "航段" not in text or not re.search(r"航班(?:号)?", text):
        return []

    segments = _flight_segments_from_markdown_table(text)
    if len(segments) < 2 or not _looks_like_round_trip_segments(segments):
        return []

    first_segment = segments[0]
    origin = first_segment.get("depCity") or first_segment.get("depStation")
    destination = first_segment.get("arrCity") or first_segment.get("arrStation")
    numbers = " / ".join(segment["number"] for segment in segments if segment.get("number"))
    title = _round_trip_table_title(text, origin, destination)
    return [
        {
            "type": "flight_card",
            "title": title,
            "subtitle": _round_trip_table_subtitle(text),
            "price": _total_price_from_segments(segments) or _extract_labeled_total_price(text) or _extract_price(text),
            "number": numbers,
            "segments": segments,
            "items": _extract_short_lines(text),
        }
    ]


def _round_trip_flight_schedule_blocks(text: str) -> List[Dict[str, Any]]:
    if "往返" not in text or "航班号" not in text or "路线" not in text or "时间" not in text:
        return []

    segments = _directional_schedule_segments(text)
    if len(segments) < 2 or not _looks_like_round_trip_segments(segments):
        return []

    pair = _recommended_pair_segments(text, segments)
    if pair:
        selected = pair
    else:
        outbound = next((segment for segment in segments if segment.get("label") == "去程"), None)
        inbound = next((segment for segment in segments if segment.get("label") == "返程"), None)
        selected = [segment for segment in (outbound, inbound) if segment]

    if len(selected) < 2:
        return []

    origin = _segment_dep_endpoint(selected[0])
    destination = _segment_arr_endpoint(selected[0])
    price = _total_price_from_segments(selected) or _extract_labeled_total_price(text)
    return [
        {
            "type": "flight_card",
            "title": _round_trip_schedule_title(text, origin, destination),
            "subtitle": _round_trip_table_subtitle(text),
            "price": price or "未返回票价",
            "number": " / ".join(segment["number"] for segment in selected if segment.get("number")),
            "segments": selected,
            "items": _round_trip_prose_items(text, has_price=bool(price)),
        }
    ]


def _round_trip_flight_prose_blocks(text: str) -> List[Dict[str, Any]]:
    if not re.search(r"(去程航班|出发航班|去程\s*[：:].*?(?:→|->|-))", text) or not re.search(
        r"(回程航班|返程航班|返程\s*[：:].*?(?:→|->|-)|回程\s*[：:].*?(?:→|->|-))",
        text,
    ):
        return []

    details = _flight_detail_segments(text)
    selected_numbers = _recommended_round_trip_numbers(text)
    if selected_numbers:
        segments = [details[number] for number in selected_numbers if number in details]
    else:
        outbound = next((segment for segment in details.values() if segment.get("label") == "去程"), None)
        inbound = next((segment for segment in details.values() if segment.get("label") == "返程"), None)
        segments = [segment for segment in (outbound, inbound) if segment]

    if len(segments) < 2 or not _looks_like_round_trip_segments(segments):
        return []

    origin = _segment_dep_endpoint(segments[0])
    destination = _segment_arr_endpoint(segments[0])
    numbers = " / ".join(segment["number"] for segment in segments if segment.get("number"))
    price = _extract_labeled_total_price(text) or _total_price_from_segments(segments)
    return [
        {
            "type": "flight_card",
            "title": _round_trip_prose_title(text, origin, destination),
            "subtitle": _round_trip_prose_subtitle(text),
            "price": price or "未返回票价",
            "number": numbers,
            "segments": segments,
            "items": _round_trip_prose_items(text, has_price=bool(price)),
        }
    ]


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
    headers: List[str] = []
    route_hint = _extract_route_label(text)
    for line in _fold_markdown_table_rows(text):
        if not line.startswith("|"):
            continue
        cells = [re.sub(r"\s+", " ", cell).strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 5:
            continue
        if "航段" in cells[0] and any(re.search(r"航班(?:号)?", cell) for cell in cells):
            in_flight_table = True
            headers = cells
            continue
        if re.fullmatch(r"[-:\s]+", "".join(cells)):
            continue
        if not in_flight_table:
            continue
        segment = _flight_segment_from_table_cells(cells, headers, route_hint)
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
            if "航段" in clean and ("价格" in clean or "票价" in clean):
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


def _flight_segment_from_table_cells(
    cells: List[str],
    headers: Optional[List[str]] = None,
    route_hint: Tuple[Optional[str], Optional[str]] = (None, None),
) -> Optional[Dict[str, Any]]:
    if len(cells) < 6:
        return None
    headers = headers or []
    route_cell = _table_cell(cells, headers, "航段") or cells[0]
    date_cell = _table_cell(cells, headers, "日期") or cells[1]
    flight_cell = _table_cell(cells, headers, "航班号") or _table_cell(cells, headers, "航班") or cells[2]
    dep_cell = _table_cell(cells, headers, "出发") or cells[3]
    arr_cell = _table_cell(cells, headers, "到达") or cells[4]
    price_cell = _table_cell(cells, headers, "价格") or _table_cell(cells, headers, "票价") or cells[-1]
    duration_cell = _table_cell(cells, headers, "时长") or _table_cell(cells, headers, "耗时")
    origin, destination = _city_pair_from_route_cell(route_cell)
    number = _extract_flight_number(flight_cell)
    label = _flight_segment_label(route_cell)
    time_cell = _table_cell(cells, headers, "起降时间") or _table_cell(cells, headers, "时间")
    airport_cell = _table_cell(cells, headers, "机场")
    if time_cell and airport_cell:
        dep_time, arr_time = _extract_time_pair(time_cell)
        dep_station, arr_station = _station_pair_from_route_cell(airport_cell)
    else:
        dep_station, dep_time = _split_station_time(dep_cell)
        arr_station, arr_time = _split_station_time(arr_cell)
    if not number or not dep_time or not arr_time:
        return None
    if not origin and not destination and label and route_hint[0] and route_hint[1]:
        if label == "返程":
            origin, destination = route_hint[1], route_hint[0]
        else:
            origin, destination = route_hint[0], route_hint[1]
    dep_city, dep_station = _infer_city_and_clean_station(dep_station)
    arr_city, arr_station = _infer_city_and_clean_station(arr_station)
    if not origin:
        origin = dep_city
    if not destination:
        destination = arr_city
    return {
        "label": label,
        "depCity": origin,
        "depStation": dep_station,
        "depTime": _join_date_time(date_cell, dep_time),
        "arrCity": destination,
        "arrStation": arr_station,
        "arrTime": arr_time,
        "carrier": _extract_carrier_from_flight_cell(flight_cell, number),
        "number": number,
        "duration": _normalize_duration(duration_cell),
        "price": _extract_price(price_cell),
    }


def _table_cell(cells: List[str], headers: List[str], header_keyword: str) -> Optional[str]:
    for index, header in enumerate(headers):
        if header_keyword in header and index < len(cells):
            return cells[index]
    return None


def _city_pair_from_route_cell(route_cell: str) -> Tuple[Optional[str], Optional[str]]:
    if not re.search(r"(→|->|－|-|—)", route_cell):
        return None, None
    left, right = re.split(r"\s*(?:→|->|－|-|—)\s*", route_cell, maxsplit=1)
    return _compact_route_endpoint(left), _compact_route_endpoint(right)


def _flight_segment_label(route_cell: str) -> Optional[str]:
    if "返程" in route_cell or "回程" in route_cell:
        return "返程"
    if "去程" in route_cell:
        return "去程"
    return None


def _infer_city_and_clean_station(station: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if not station:
        return None, station
    for city in sorted(ROUTE_CITIES, key=len, reverse=True):
        if station.startswith(city):
            clean_station = station[len(city) :].strip(" -:：")
            return city, clean_station or station
    for city in sorted(ROUTE_CITIES, key=len, reverse=True):
        if city in station:
            return city, station
    return None, station


def _split_station_time(value: str) -> Tuple[Optional[str], Optional[str]]:
    match = re.search(r"(\d{1,2}:\d{2})", value)
    if not match:
        return value.strip() or None, None
    station = value[: match.start()].strip(" -:：")
    return station or None, match.group(1)


def _extract_carrier_from_flight_cell(value: str, number: str) -> Optional[str]:
    carrier = value.replace(number, "", 1).strip(" -:：")
    return carrier or None


def _directional_schedule_segments(text: str) -> List[Dict[str, Any]]:
    segments: List[Dict[str, Any]] = []
    current_origin: Optional[str] = None
    current_destination: Optional[str] = None
    current_label: Optional[str] = None
    first_origin: Optional[str] = None
    first_destination: Optional[str] = None
    headers: List[str] = []

    for line in _fold_markdown_table_rows(text):
        clean = _clean_display_line(line)
        if not clean:
            continue

        if not clean.startswith("|"):
            pair = _city_pair_from_route_cell(clean)
            if pair[0] and pair[1] and _looks_like_city_route(pair):
                current_origin, current_destination = pair
                if first_origin is None:
                    first_origin, first_destination = pair
                    current_label = "去程"
                elif _same_endpoint(current_origin, first_destination) and _same_endpoint(current_destination, first_origin):
                    current_label = "返程"
                else:
                    current_label = None
                headers = []
            continue

        if not current_label or not current_origin or not current_destination:
            continue
        cells = [re.sub(r"\s+", " ", cell).strip() for cell in clean.strip().strip("|").split("|")]
        if len(cells) < 5:
            continue
        if any("航班号" in cell for cell in cells) and any("时间" in cell for cell in cells):
            headers = cells
            continue
        if re.fullmatch(r"[-:\s]+", "".join(cells)):
            continue
        segment = _flight_segment_from_schedule_cells(cells, headers, current_label, current_origin, current_destination)
        if segment:
            segments.append(segment)

    return segments


def _looks_like_city_route(pair: Tuple[Optional[str], Optional[str]]) -> bool:
    return bool(pair[0] in ROUTE_CITIES and pair[1] in ROUTE_CITIES)


def _flight_segment_from_schedule_cells(
    cells: List[str],
    headers: List[str],
    label: str,
    origin: str,
    destination: str,
) -> Optional[Dict[str, Any]]:
    flight_cell = _table_cell(cells, headers, "航班号") or cells[0]
    carrier_cell = _table_cell(cells, headers, "航司") or _table_cell(cells, headers, "航空公司") or (cells[1] if len(cells) > 1 else "")
    route_cell = _table_cell(cells, headers, "路线") or _table_cell(cells, headers, "航线") or (cells[2] if len(cells) > 2 else "")
    time_cell = _table_cell(cells, headers, "时间") or (cells[3] if len(cells) > 3 else "")
    duration_cell = _table_cell(cells, headers, "时长") or _table_cell(cells, headers, "耗时")
    price_cell = _table_cell(cells, headers, "价格") or ""

    number = _extract_flight_number(flight_cell)
    dep_time, arr_time = _extract_time_pair(time_cell)
    dep_station, arr_station = _station_pair_from_route_cell(route_cell)
    if not number or not dep_time or not arr_time:
        return None
    return {
        "label": label,
        "depCity": origin,
        "depStation": dep_station,
        "depTime": dep_time,
        "arrCity": destination,
        "arrStation": arr_station,
        "arrTime": arr_time,
        "carrier": carrier_cell or _extract_carrier_from_flight_cell(flight_cell, number),
        "number": number,
        "duration": _normalize_duration(duration_cell),
        "price": _extract_price(price_cell),
    }


def _station_pair_from_route_cell(route_cell: str) -> Tuple[Optional[str], Optional[str]]:
    if not re.search(r"(→|->|－|-|—)", route_cell):
        return None, None
    left, right = re.split(r"\s*(?:→|->|－|-|—)\s*", route_cell, maxsplit=1)
    return _clean_route_endpoint(left, is_left=True), _clean_route_endpoint(right, is_left=False)


def _recommended_pair_segments(text: str, segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_number = {segment.get("number"): segment for segment in segments if segment.get("number")}
    for left, right in re.findall(r"\b([A-Z0-9]{2,6}\d{2,4})\s*/\s*([A-Z0-9]{2,6}\d{2,4})\b", text, flags=re.IGNORECASE):
        first = by_number.get(left.upper())
        second = by_number.get(right.upper())
        if not first or not second:
            continue
        if {first.get("label"), second.get("label")} == {"去程", "返程"}:
            return [first, second] if first.get("label") == "去程" else [second, first]
    return []


def _round_trip_schedule_title(text: str, origin: Optional[str], destination: Optional[str]) -> str:
    first = _first_meaningful_line(_strip_entity_tags(text))
    if first and re.search(r"(往返|航班|机票)", first) and len(first) <= 46:
        return first
    if origin and destination:
        return f"往返航班候选：{origin} ↔ {destination}"
    return "往返航班候选"


def _flight_detail_segments(text: str) -> Dict[str, Dict[str, Any]]:
    segments: Dict[str, Dict[str, Any]] = {}
    current_label: Optional[str] = None
    current_origin: Optional[str] = None
    current_destination: Optional[str] = None
    current_number: Optional[str] = None
    current_lines: List[str] = []
    dates = _round_trip_dates(text)

    def flush() -> None:
        nonlocal current_number, current_lines
        if not current_number:
            return
        segment = _flight_segment_from_detail_lines(
            current_number,
            current_lines,
            current_label,
            current_origin,
            current_destination,
            dates.get(current_label or ""),
        )
        if segment:
            segments[current_number] = segment
        current_number = None
        current_lines = []

    for raw_line in text.splitlines():
        clean = _clean_display_line(raw_line)
        if not clean:
            continue
        if re.search(r"(去程航班|出发航班|去程\s*[：:].*?(?:→|->|-))", clean) and not _extract_flight_number(clean):
            flush()
            current_label = "去程"
            current_origin, current_destination = _city_pair_from_route_cell(clean)
            continue
        if re.search(r"(回程航班|返程航班|返程\s*[：:].*?(?:→|->|-)|回程\s*[：:].*?(?:→|->|-))", clean) and not _extract_flight_number(clean):
            flush()
            current_label = "返程"
            current_origin, current_destination = _city_pair_from_route_cell(clean)
            continue

        number = _line_flight_number(clean)
        if number and current_label:
            flush()
            current_number = number
            remainder = clean.replace(number, "", 1).strip(" -:：")
            current_lines = [remainder] if remainder else []
            continue

        if current_number:
            current_lines.append(clean)

    flush()
    return segments


def _flight_segment_from_detail_lines(
    number: str,
    lines: List[str],
    label: Optional[str],
    origin: Optional[str],
    destination: Optional[str],
    date_text: Optional[str],
) -> Optional[Dict[str, Any]]:
    body = "\n".join(line for line in lines if line)
    dep_time, dep_station, arr_time, arr_station = _flight_detail_time(body)
    if not dep_time or not arr_time:
        return None
    dep_city, dep_station = _infer_city_and_clean_station(dep_station)
    arr_city, arr_station = _infer_city_and_clean_station(arr_station)
    return {
        "label": label,
        "depCity": dep_city or origin,
        "depStation": dep_station,
        "depTime": _join_date_time(date_text, dep_time),
        "arrCity": arr_city or destination,
        "arrStation": arr_station,
        "arrTime": arr_time,
        "carrier": _extract_labeled_value(body, ["航空公司", "航司", "承运方"]) or _extract_carrier_from_flight_cell(lines[0], number) if lines else None,
        "number": number,
        "duration": _extract_duration(body),
        "price": _extract_price(body),
    }


def _flight_detail_time(text: str) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    match = re.search(
        r"(\d{1,2}:\d{2})\s*[（(]([^）)]+)[）)]\s*(?:→|->|至)\s*(\d{1,2}:\d{2})\s*[（(]([^）)]+)[）)]",
        text,
    )
    if match:
        return match.group(1), match.group(2).strip(), match.group(3), match.group(4).strip()

    dep_time, arr_time = _extract_time_pair(text)
    route = _extract_route_pair(text, allow_time=True)
    return dep_time, route[0], arr_time, route[1]


def _line_flight_number(line: str) -> Optional[str]:
    number = _extract_flight_number(line)
    if not number:
        return None
    without_number = line.replace(number, "", 1).strip(" -:：")
    if len(without_number) > 24 or re.search(r"(时间|航空公司|价格|时长|特点|去程|回程|返程)", without_number):
        return None
    return number


def _recommended_round_trip_numbers(text: str) -> List[str]:
    selected: List[str] = []
    in_recommendation = False
    for line in _fold_soft_wrapped_lines(text):
        if re.search(r"推荐组合", line):
            in_recommendation = True
            continue
        if not in_recommendation:
            continue
        has_direction = bool(re.search(r"(去程|回程|返程)", line))
        if has_direction:
            number = _extract_flight_number(line)
            if number and number not in selected:
                selected.append(number)
            if len(selected) >= 2:
                break
            continue
        if re.search(r"(当前为体验模式|Session:|Resume this session|⚠️)", line):
            break
    return selected


def _round_trip_dates(text: str) -> Dict[str, Optional[str]]:
    dates: Dict[str, Optional[str]] = {"去程": None, "返程": None}
    match = re.search(
        r"去程\s*[：:]\s*(\d{1,2}\s*月\s*\d{1,2}\s*日(?:[（(][^）)]+[）)])?).{0,80}?"
        r"(?:回程|返程)\s*[：:]\s*(\d{1,2}\s*月\s*\d{1,2}\s*日(?:[（(][^）)]+[）)])?)",
        text,
        flags=re.DOTALL,
    )
    if match:
        dates["去程"] = match.group(1)
        dates["返程"] = match.group(2)
    return dates


def _round_trip_prose_title(text: str, origin: Optional[str], destination: Optional[str]) -> str:
    base = "推荐往返航班组合"
    first = _first_meaningful_line(_strip_entity_tags(text))
    if first and re.search(r"(往返|机票|航班)", first) and len(first) <= 42:
        base = re.sub(r"推荐$", "推荐", first).strip()
    if origin and destination and "↔" not in base:
        return f"{base}：{origin} ↔ {destination}"
    return base


def _round_trip_prose_subtitle(text: str) -> Optional[str]:
    for line in _fold_soft_wrapped_lines(text):
        if "停留" in line and len(line) <= 80:
            return line
    return None


def _round_trip_prose_items(text: str, has_price: bool) -> List[str]:
    items = _extract_short_lines(text)
    if not has_price and re.search(r"(暂未提供具体日期的实时票价|未提供.*票价|价格.*受限)", text):
        notice = "本次 fly.ai 未返回实时票价，无法确认最低价。"
        if notice not in items:
            items.insert(0, notice)
    return items


def _looks_like_round_trip_segments(segments: List[Dict[str, Any]]) -> bool:
    labels = {segment.get("label") for segment in segments}
    if {"去程", "返程"}.issubset(labels):
        return True
    if len(segments) < 2:
        return False
    first_dep = _segment_dep_endpoint(segments[0])
    first_arr = _segment_arr_endpoint(segments[0])
    if not first_dep or not first_arr:
        return False
    return any(
        _same_endpoint(_segment_dep_endpoint(segment), first_arr)
        and _same_endpoint(_segment_arr_endpoint(segment), first_dep)
        for segment in segments[1:]
    )


def _segment_dep_endpoint(segment: Dict[str, Any]) -> Optional[str]:
    return segment.get("depCity") or segment.get("depStation")


def _segment_arr_endpoint(segment: Dict[str, Any]) -> Optional[str]:
    return segment.get("arrCity") or segment.get("arrStation")


def _same_endpoint(left: Optional[str], right: Optional[str]) -> bool:
    if not left or not right:
        return False
    return _compact_route_endpoint(left) == _compact_route_endpoint(right)


def _round_trip_table_title(text: str, origin: Optional[str], destination: Optional[str]) -> str:
    base = "往返航班"
    for line in _fold_soft_wrapped_lines(text):
        if "推荐最低总价方案" in line:
            base = "推荐最低总价方案"
            break
        if "推荐最低票价往返组合" in line:
            base = "推荐最低票价往返组合"
            break
        if "最低票价往返组合" in line:
            base = "最低票价往返组合"
            break
    if origin and destination:
        return f"{base}：{origin} ↔ {destination}"
    return base


def _round_trip_table_subtitle(text: str) -> Optional[str]:
    for line in _fold_soft_wrapped_lines(text):
        if "停留" in line and len(line) <= 80:
            return line
    return None


def _total_price_from_segments(segments: List[Dict[str, Any]]) -> Optional[str]:
    amounts: List[int] = []
    for segment in segments:
        price = segment.get("price")
        if not _has_real_price(price):
            continue
        match = re.search(r"[\d,]+", str(price))
        if match:
            amounts.append(int(match.group(0).replace(",", "")))
    if len(amounts) < 2:
        return None
    return f"¥{sum(amounts):,}"


def _has_real_price(value: Any) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    if not text or re.search(r"(未返回|暂无|待查|查价|查询价格|无具体票价)", text):
        return False
    return bool(_extract_price(text))


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
    for origin in sorted(ROUTE_CITIES, key=len, reverse=True):
        for destination in sorted(ROUTE_CITIES, key=len, reverse=True):
            if origin == destination:
                continue
            if re.search(rf"{re.escape(origin)}\s*(?:到|至|飞|→|->|-)\s*{re.escape(destination)}", text):
                return origin, destination
            if re.search(rf"{re.escape(origin)}.{{0,8}}{re.escape(destination)}.{{0,8}}(?:往返|来回)", text):
                return origin, destination
    return None, None


def _compact_route_endpoint(value: str) -> str:
    for city in sorted(ROUTE_CITIES, key=len, reverse=True):
        if city in value:
            return city
    clean = re.sub(r"(往返|来回|前后|附近|端午节|假期|出发|返回)", "", value)
    return clean[-6:] if len(clean) > 6 else clean


def _flight_segment_from_combo_line(line: str, origin: Optional[str], destination: Optional[str]) -> Optional[Dict[str, Any]]:
    clean = _clean_markdown_text(line)
    if not re.search(r"(去程|回程|返程)", clean):
        return None
    number = _extract_flight_number(clean)
    dep_time, arr_time = _extract_time_pair(clean)
    price = _extract_price(clean)
    if not number or not dep_time or not arr_time:
        return None

    direction = "return" if re.search(r"(回程|返程)", clean) else "outbound"
    label = "返程" if direction == "return" else "去程"
    date_match = re.search(r"(\d{1,2}\s*月\s*\d{1,2}\s*日|\d{1,2}/\d{1,2})", clean)
    carrier = _extract_carrier_after_number(clean, number)
    dep_station, arr_station = _direction_stations(direction, origin, destination)
    return {
        "label": label,
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
        return _normalize_duration(labeled)
    match = re.search(r"(?:约\s*)?\d+\s*小时(?:\s*\d+\s*分钟)?|(?:约\s*)?\d+\s*分钟", text)
    return re.sub(r"\s+", "", match.group(0)) if match else None


def _normalize_duration(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    clean = re.sub(r"\s+", "", str(value))
    match = re.fullmatch(r"(\d+(?:\.\d+)?)h(?:(\d+)m?)?", clean, flags=re.IGNORECASE)
    if match:
        hours = match.group(1)
        minutes = match.group(2)
        return f"{hours}小时{minutes}分钟" if minutes else f"{hours}小时"
    match = re.fullmatch(r"(\d+)m", clean, flags=re.IGNORECASE)
    if match:
        return f"{match.group(1)}分钟"
    return clean


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
        text = re.sub(r"^.*(?:出发\s*/\s*到达|出发到达)\s*[:：]?\s*", "", text)
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
