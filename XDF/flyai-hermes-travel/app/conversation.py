from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Dict, List


CITY_WORDS = [
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
    "天津",
    "青岛",
    "厦门",
    "长沙",
    "昆明",
    "三亚",
    "香港",
    "澳门",
    "台北",
    "东京",
    "大阪",
    "京都",
    "札幌",
    "福冈",
    "首尔",
    "釜山",
    "新加坡",
    "曼谷",
    "清迈",
    "普吉",
    "吉隆坡",
    "巴厘岛",
    "巴黎",
    "伦敦",
    "罗马",
    "米兰",
    "马德里",
    "巴塞罗那",
    "洛杉矶",
    "纽约",
    "旧金山",
    "温哥华",
    "悉尼",
    "墨尔本",
]

CONFIRM_WORDS = ("确认", "可以", "开始查", "查吧", "就这样", "没问题", "执行", "开始查询")


def build_conversation_turn(profile: Dict[str, Any], message: str) -> Dict[str, Any]:
    updated = deepcopy(profile or {})
    clean = _compact(message)
    updated.setdefault("preferences", [])
    updated.setdefault("avoid", [])
    updated.setdefault("source_messages", [])
    updated["source_messages"] = (updated["source_messages"] + [clean])[-8:]
    updated["last_user_message"] = clean

    if _is_confirmation(clean) and updated.get("ready_to_search") and updated.get("search_query"):
        return {
            "action": "search_requested",
            "profile": updated,
            "assistant_message": "我会按刚才确认的条件开始查实时结果。",
            "missing_fields": [],
            "confirmation": _confirmation_payload(updated),
            "search_query": updated["search_query"],
        }

    _merge_profile(updated, clean)
    missing = _missing_fields(updated)
    ready = not missing
    updated["missing_fields"] = missing
    updated["ready_to_search"] = ready
    updated["summary"] = _profile_summary(updated)
    updated["search_query"] = _search_query(updated) if ready else ""

    if ready:
        confirmation = _confirmation_payload(updated)
        assistant_message = "我先按这些条件整理好了，确认后再调用 fly.ai 和小红书数据源。"
        action = "confirm"
    else:
        confirmation = None
        assistant_message = _clarifying_message(updated, missing)
        action = "clarify"

    return {
        "action": action,
        "profile": updated,
        "assistant_message": assistant_message,
        "missing_fields": missing,
        "confirmation": confirmation,
        "search_query": updated.get("search_query", ""),
    }


def conversation_title(profile: Dict[str, Any], fallback: str = "新的旅行计划") -> str:
    origin = profile.get("origin")
    destination = profile.get("destination")
    intent = profile.get("intent_label") or "旅行"
    if origin and destination:
        return f"{origin}到{destination}{intent}"
    if destination:
        return f"{destination}{intent}"
    if origin and profile.get("destination_mode") == "discovery":
        return f"{origin}出发目的地推荐"
    first = (profile.get("source_messages") or [fallback])[0]
    return _compact(first)[:32] or fallback


def _merge_profile(profile: Dict[str, Any], text: str) -> None:
    cities = _cities_in(text)
    explicit_origin = _match_first(text, [r"从\s*([\u4e00-\u9fa5A-Za-z]{2,12})\s*出发", r"([\u4e00-\u9fa5A-Za-z]{2,12})\s*出发"])
    route = _route_from_text(text, cities)
    if explicit_origin:
        profile["origin"] = _normalize_city(explicit_origin, cities)
    if route.get("origin"):
        profile["origin"] = route["origin"]
    if route.get("destination"):
        profile["destination"] = route["destination"]
    elif cities:
        if ("去" in text or "到" in text) and not explicit_origin:
            profile["destination"] = cities[-1]
        elif not profile.get("origin"):
            profile["origin"] = cities[0]

    if re.search(r"目的地|有哪些选择|去哪|去哪里|推荐.*国家|推荐.*城市|出国游", text):
        profile["destination_mode"] = "discovery"
    if re.search(r"往返|来回|返程|回程|返回|↔|⇄", text):
        profile["trip_type"] = "round_trip"
    elif re.search(r"单程", text):
        profile["trip_type"] = "one_way"

    date_text = _extract_date_text(text)
    if date_text:
        profile["date_text"] = date_text
    duration = _match_first(text, [r"(停留\s*\d+\s*晚)", r"(\d+\s*晚)", r"(\d+\s*天(?:左右)?)", r"(一周|两周)"])
    if duration:
        profile["duration_text"] = duration.replace(" ", "")
    travelers = _match_first(text, [r"(\d+\s*个?人)", r"(夫妻(?:两个?人)?)", r"(情侣)", r"(亲子)", r"(一家[三四五六]口?)"])
    if travelers:
        profile["travelers_text"] = travelers.replace(" ", "")
    budget = _budget_text(text)
    if budget:
        profile["budget_text"] = budget

    intent = _intent_from_text(text)
    if intent:
        profile["intent"] = intent
        profile["intent_label"] = _intent_label(intent)
    for phrase in _preference_phrases(text):
        if phrase not in profile["preferences"]:
            profile["preferences"].append(phrase)
    for phrase in _avoid_phrases(text):
        if phrase not in profile["avoid"]:
            profile["avoid"].append(phrase)


def _missing_fields(profile: Dict[str, Any]) -> List[str]:
    missing: List[str] = []
    intent = profile.get("intent") or "guide"
    discovery = profile.get("destination_mode") == "discovery"
    if intent in {"flight", "train", "destination", "guide"} and not profile.get("origin") and not profile.get("destination"):
        missing.append("出发地")
    if intent in {"flight", "train", "hotel", "guide"} and not profile.get("destination") and not discovery:
        missing.append("目的地")
    if not profile.get("date_text"):
        missing.append("出行日期或假期范围")
    if intent in {"flight", "train"} and profile.get("trip_type") == "round_trip" and not profile.get("duration_text"):
        missing.append("停留晚数或返程日期")
    if intent in {"destination", "guide", "hotel"} and not profile.get("travelers_text"):
        missing.append("出行人数")
    return missing[:3]


def _clarifying_message(profile: Dict[str, Any], missing: List[str]) -> str:
    if not missing:
        return "我已经理解了大致方向，可以继续补充预算、时间偏好或必须避开的条件。"
    prefix = "我先记下来了"
    summary = _profile_summary(profile)
    if summary:
        prefix = f"我先记下：{summary}"
    questions = "、".join(missing)
    return f"{prefix}。还差 {questions}，补上后我再给你确认查询条件。"


def _confirmation_payload(profile: Dict[str, Any]) -> Dict[str, Any]:
    facts = [
        {"label": "出发地", "value": profile.get("origin") or "待平台判断"},
        {"label": "目的地", "value": profile.get("destination") or "让平台推荐"},
        {"label": "时间", "value": profile.get("date_text") or "未指定"},
        {"label": "停留", "value": profile.get("duration_text") or "未指定"},
        {"label": "人数", "value": profile.get("travelers_text") or "未指定"},
        {"label": "预算", "value": profile.get("budget_text") or "未指定"},
    ]
    return {
        "title": conversation_title(profile),
        "summary": _profile_summary(profile),
        "facts": facts,
        "preferences": profile.get("preferences", [])[:8],
        "avoid": profile.get("avoid", [])[:6],
        "search_query": profile.get("search_query") or _search_query(profile),
    }


def _profile_summary(profile: Dict[str, Any]) -> str:
    pieces = []
    if profile.get("origin") and profile.get("destination"):
        route = f"{profile['origin']}到{profile['destination']}"
        if profile.get("trip_type") == "round_trip":
            route += "往返"
        pieces.append(route)
    elif profile.get("origin") and profile.get("destination_mode") == "discovery":
        pieces.append(f"{profile['origin']}出发目的地推荐")
    elif profile.get("destination"):
        pieces.append(str(profile["destination"]))
    if profile.get("date_text"):
        pieces.append(str(profile["date_text"]))
    if profile.get("duration_text"):
        pieces.append(str(profile["duration_text"]))
    if profile.get("travelers_text"):
        pieces.append(str(profile["travelers_text"]))
    if profile.get("intent_label"):
        pieces.append(str(profile["intent_label"]))
    return "，".join(pieces)


def _search_query(profile: Dict[str, Any]) -> str:
    lines = [
        "请基于以下多轮旅行需求查询实时旅行信息，并输出结构化卡片。",
        f"旅行概要：{_profile_summary(profile) or '用户旅行需求'}",
    ]
    mapping = [
        ("出发地", "origin"),
        ("目的地", "destination"),
        ("目的地模式", "destination_mode"),
        ("时间", "date_text"),
        ("停留/行程时长", "duration_text"),
        ("人数", "travelers_text"),
        ("预算", "budget_text"),
        ("交通/内容类型", "intent_label"),
    ]
    for label, key in mapping:
        value = profile.get(key)
        if value:
            lines.append(f"{label}：{value}")
    if profile.get("preferences"):
        lines.append("偏好：" + "、".join(profile["preferences"]))
    if profile.get("avoid"):
        lines.append("避开：" + "、".join(profile["avoid"]))
    lines.append("要求：结果必须尽量保留价格、航班号/车次号、酒店名称、日期、起降/出发到达时间、机场/车站、链接。")
    lines.append("如果是往返机票或往返交通，必须在同一个方案里同时给出去程和返程；平台未返回票价时写“未返回票价”，不要省略航段。")
    return "\n".join(lines)


def _cities_in(text: str) -> List[str]:
    found: List[str] = []
    for city in CITY_WORDS:
        if city in text and city not in found:
            found.append(city)
    return sorted(found, key=text.find)


def _route_from_text(text: str, cities: List[str]) -> Dict[str, str]:
    if len(cities) >= 2:
        if re.search(r"到|去|往返|来回|↔|⇄|-|—", text):
            return {"origin": cities[0], "destination": cities[1]}
    return {}


def _extract_date_text(text: str) -> str:
    holiday = _match_first(text, [r"(端午节?(?:前后)?)", r"(暑假(?:期间)?)", r"(寒假(?:期间)?)", r"(春节(?:前后)?)", r"(五一(?:假期)?)", r"(国庆(?:假期)?)"])
    date_range = _match_first(
        text,
        [
            r"((?:\d{4}\s*年)?\d{1,2}\s*月\s*\d{1,2}\s*(?:日|号)?\s*(?:-|—|到|至|~)\s*(?:\d{4}\s*年)?\d{1,2}\s*月\s*\d{1,2}\s*(?:日|号)?)",
            r"((?:\d{4}\s*年)?\d{1,2}\s*月\s*\d{1,2}\s*(?:日|号)?)",
        ],
    )
    if holiday and date_range:
        return f"{holiday}，{date_range}"
    return holiday or date_range or ""


def _intent_from_text(text: str) -> str:
    if re.search(r"机票|航班|飞机|直飞|转机|机场", text):
        return "flight"
    if re.search(r"火车|高铁|动车|列车|车次", text):
        return "train"
    if re.search(r"酒店|住宿|民宿|住哪", text):
        return "hotel"
    if re.search(r"目的地|有哪些选择|去哪|国家|城市", text):
        return "destination"
    if re.search(r"攻略|行程|怎么玩|路线", text):
        return "guide"
    return ""


def _intent_label(intent: str) -> str:
    return {
        "flight": "机票",
        "train": "火车票",
        "hotel": "酒店",
        "destination": "目的地推荐",
        "guide": "攻略",
    }.get(intent, "旅行")


def _budget_text(text: str) -> str:
    match = _match_first(text, [r"(预算[^，。；\n]{0,24})", r"((?:每晚|人均|总价)?\s*\d+\s*元(?:以内|以下|左右)?)", r"(最低价|便宜|价格友好|性价比)"])
    return _compact(match)


def _preference_phrases(text: str) -> List[str]:
    patterns = [
        "直飞",
        "不转机",
        "上午出发",
        "下午返回",
        "晚上返回",
        "安全",
        "发达",
        "交通方便",
        "评分高",
        "亲子友好",
        "节奏不要太赶",
        "最低价",
        "高互动",
    ]
    return [item for item in patterns if item in text]


def _avoid_phrases(text: str) -> List[str]:
    output = []
    for pattern in (r"不要[^，。；\n]{1,20}", r"不想[^，。；\n]{1,20}", r"避开[^，。；\n]{1,20}"):
        output.extend(_compact(match) for match in re.findall(pattern, text))
    return [item for item in output if item]


def _match_first(text: str, patterns: List[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return _compact(match.group(1))
    return ""


def _normalize_city(value: str, cities: List[str]) -> str:
    for city in cities:
        if city in value:
            return city
    return _compact(value)


def _is_confirmation(text: str) -> bool:
    return any(word in text for word in CONFIRM_WORDS) and len(text) <= 24


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip(" ，。；;\n\t")
