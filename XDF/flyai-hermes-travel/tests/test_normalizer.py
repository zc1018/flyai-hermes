from app.normalizer import normalize_output


def test_normalizes_explicit_blocks():
    raw = """
    {
      "summary": "推荐直飞",
      "blocks": [
        {
          "type": "flight_card",
          "title": "北京到上海",
          "jumpUrl": "https://example.com/book",
          "segments": [
            {"depCity": "北京", "arrCity": "上海"}
          ]
        }
      ]
    }
    """

    blocks = normalize_output(raw)

    assert blocks[0]["type"] == "notice"
    assert blocks[1]["type"] == "flight_card"
    assert blocks[1]["bookingUrl"] == "https://example.com/book"


def test_normalizes_common_json_card_aliases_from_hermes():
    raw = """
{
  "summary": "26年端午节北京东京最低往返组合",
  "blocks": [
    {
      "type": "flightcard",
      "title": "北京↔东京 端午节往返最低直飞组合",
      "price": "3031",
      "number": "IJ018 / HU440",
      "segments": [
        {
          "label": "去程",
          "depCity": "北京",
          "depStation": "首都国际机场",
          "depTime": "2026-05-30 10:45",
          "arrCity": "东京",
          "arrStation": "成田机场",
          "arrTime": "15:30",
          "carrier": "春秋日本航空",
          "number": "IJ018",
          "price": "1113元"
        },
        {
          "label": "返程",
          "depCity": "东京",
          "depStation": "成田机场",
          "depTime": "2026-06-04 13:55",
          "arrCity": "北京",
          "arrStation": "首都国际机场",
          "arrTime": "17:05",
          "carrier": "海南航空",
          "number": "HU440",
          "price": "1918元"
        }
      ]
    }
  ]
}
"""

    blocks = normalize_output(raw, "北京东京往返机票")

    assert blocks[0]["type"] == "notice"
    assert blocks[1]["type"] == "flight_card"
    assert blocks[1]["price"] == "¥3,031"
    assert blocks[1]["number"] == "IJ018 / HU440"
    assert len(blocks[1]["segments"]) == 2
    assert blocks[1]["segments"][0]["label"] == "去程"
    assert blocks[1]["segments"][0]["price"] == "¥1113"
    assert blocks[1]["segments"][1]["label"] == "返程"
    assert blocks[1]["segments"][1]["price"] == "¥1918"


def test_converts_hotel_item_list():
    raw = """
    {
      "status": 0,
      "data": {
        "itemList": [
          {
            "name": "杭州望湖宾馆",
            "mainPic": "https://img.example/hotel.jpg",
            "detailUrl": "https://example.com/hotel",
            "price": "¥618",
            "score": "5.0",
            "star": "豪华型"
          }
        ]
      }
    }
    """

    blocks = normalize_output(raw)

    assert blocks == [
        {
            "type": "hotel_card",
            "title": "杭州望湖宾馆",
            "subtitle": None,
            "price": "¥618",
            "address": None,
            "score": "5.0",
            "scoreDesc": None,
            "star": "豪华型",
            "imageUrl": "https://img.example/hotel.jpg",
            "bookingUrl": "https://example.com/hotel",
        }
    ]


def test_converts_flight_item_list():
    raw = """
    {
      "data": {
        "itemList": [
          {
            "adultPrice": "¥400",
            "totalDuration": "140分钟",
            "jumpUrl": "https://example.com/flight",
            "journeys": [
              {
                "segments": [
                  {
                    "transportType": "飞机",
                    "depCityName": "北京",
                    "depStationName": "首都国际机场",
                    "depDateTime": "2026-05-20 08:00:00",
                    "arrCityName": "上海",
                    "arrStationName": "浦东国际机场",
                    "arrDateTime": "2026-05-20 10:20:00",
                    "marketingTransportName": "国航",
                    "marketingTransportNo": "CA1001",
                    "seatClassName": "经济舱",
                    "duration": "140分钟"
                  }
                ]
              }
            ]
          }
        ]
      }
    }
    """

    blocks = normalize_output(raw)

    assert blocks[0]["type"] == "flight_card"
    assert blocks[0]["price"] == "¥400"
    assert blocks[0]["segments"][0]["carrier"] == "国航"
    assert blocks[0]["bookingUrl"] == "https://example.com/flight"


def test_formats_numeric_flight_durations():
    raw = """
    {
      "data": {
        "itemList": [
          {
            "totalDuration": "460",
            "journeys": [
              {
                "segments": [
                  {
                    "transportType": "飞机",
                    "depCityName": "北京",
                    "arrCityName": "东京",
                    "duration": "210"
                  }
                ]
              }
            ]
          }
        ]
      }
    }
    """

    blocks = normalize_output(raw)

    assert blocks[0]["duration"] == "7小时40分钟"
    assert blocks[0]["segments"][0]["duration"] == "3小时30分钟"


def test_markdown_fallback():
    blocks = normalize_output("# 推荐\\n- 西湖附近优先")

    assert blocks[0]["type"] == "guide_section"
    assert "推荐" in blocks[0]["items"][0]


def test_spot_entities_render_as_destination_cards_without_tags():
    raw = """
╭─ ⚕ Hermes ───────────────────────────────────────────────────────────────────╮
    暑假出国游推荐（以上海出发为例）

    基于飞猪搜索结果，以下目的地均有直飞航班、安全发达、且暑假与非暑假价差相对较小：

    <spot_entity>name:日本·东京;type:city</spot_entity>
    - 亮点：上海直飞约2.5-3小时，东方航空/春秋航空等每日多班直飞（浦东→成田/羽田）
    - 推荐理由：日本是发达国家中治安最好的之一，暑假虽为旺季但航班密集、竞争充分，机票涨幅相对温和
    - 权衡：东京市区酒店暑假价格会有一定上浮；日元汇率目前较友好

    <spot_entity>name:韩国·首尔;type:city</spot_entity>
    - 亮点：上海直飞仅约2小时，东方航空/春秋航空等大量航班可选
    - 推荐理由：距离近、航班极多，暑假价格波动最小；治安好、发达便利
    - 权衡：首尔暑假较热，但室内选择丰富

    总结

    日本和韩国是安全、发达、直飞的平衡选择。
    当前为体验模式，部分搜索结果可能受限，请前往 飞猪AI开放平台 获取正式API Key解锁完整服务。
╰──────────────────────────────────────────────────────────────────────────────╯
"""

    blocks = normalize_output(raw)

    assert blocks[0]["type"] == "destination_card"
    assert blocks[0]["title"] == "日本·东京"
    assert blocks[0]["subtitle"].startswith("上海直飞约2.5-3小时")
    assert blocks[0]["items"][0].startswith("推荐理由：")
    assert blocks[1]["title"] == "韩国·首尔"
    assert blocks[2]["type"] == "guide_section"
    assert blocks[2]["title"] == "总结"
    serialized = str(blocks)
    assert "spot_entity" not in serialized
    assert "正式API Key" not in serialized


def test_plain_destination_sections_do_not_become_flight_cards():
    raw = """
╭─ ⚕ Hermes ───────────────────────────────────────────────────────────────────╮
    基于飞猪搜索结果，以下是北京出发、直飞、安全发达、暑假与非暑假差价相对较小的出国游推荐：

    精选推荐

    首尔（韩国）
    - 亮点：北京直飞仅约2小时（CA137 首都→金浦），飞行时间最短；韩国发达且治安良好
    - 推荐理由：韩国暑假并非绝对旺季，机票价格波动相对较小；首尔购物、美食、文化体验丰富
    - 权衡：首尔以城市游为主，自然景观相对有限

    东京（日本）
    - 亮点：北京直飞约3.5小时（JL860 首都→成田），日本安全指数极高、基础设施发达
    - 推荐理由：北京-东京航线密集，竞争充分，价格涨幅相对温和
    - 权衡：暑假日本整体偏热，住宿可能略涨

    总结：最看重差价小+发达安全，首推首尔和新加坡。

    当前为体验模式，部分搜索结果可能受限，请前往 飞猪AI开放平台 获取正式API Key解锁完整服务。
╰──────────────────────────────────────────────────────────────────────────────╯
"""

    blocks = normalize_output(raw)

    assert blocks[0]["type"] == "destination_card"
    assert blocks[0]["title"] == "首尔（韩国）"
    assert blocks[0]["subtitle"].startswith("北京直飞仅约2小时")
    assert blocks[0]["items"][0].startswith("推荐理由：")
    assert blocks[1]["title"] == "东京（日本）"
    assert blocks[2]["type"] == "guide_section"
    serialized = str(blocks)
    assert "flight_card" not in serialized
    assert "正式API Key" not in serialized


def test_normalizes_table_rows_and_meta():
    raw = """
    {
      "blocks": [
        {
          "type": "hotel_card",
          "title": "酒店",
          "meta": {"address": "西湖边", "star": "高档型"}
        },
        {
          "type": "comparison_table",
          "columns": ["酒店", "价格"],
          "rows": [["A", "¥300"], ["B", "¥400"]]
        }
      ]
    }
    """

    blocks = normalize_output(raw)

    assert blocks[0]["address"] == "西湖边"
    assert blocks[0]["star"] == "高档型"
    assert blocks[1]["rows"] == [{"酒店": "A", "价格": "¥300"}, {"酒店": "B", "价格": "¥400"}]


def test_normalizes_direct_flyai_ai_search_data():
    raw = """
    {
      "data": "**[G7333](https://example.com/train)**\\n\\n- 出发站：上海虹桥 → 到达站：杭州东\\n- 发车时间：06:20 → 07:21\\n- 票价：¥73\\n- 历时：约 61分钟",
      "message": "success",
      "status": 0,
      "systemMessage": "体验模式"
    }
    """

    blocks = normalize_output(raw)

    assert blocks[0]["type"] == "train_card"
    assert blocks[0]["number"] == "G7333"
    assert blocks[0]["price"] == "¥73"
    assert blocks[0]["bookingUrl"] == "https://example.com/train"
    assert blocks[0]["segments"][0]["depStation"] == "上海虹桥"
    assert blocks[0]["segments"][0]["arrStation"] == "杭州东"
    assert blocks[0]["segments"][0]["depTime"] == "06:20"
    assert blocks[0]["segments"][0]["arrTime"] == "07:21"
    assert blocks[1]["type"] == "guide_section"
    assert blocks[2]["type"] == "notice"


def test_normalizes_hotel_markdown_data():
    raw = """
    {
      "data": "**[杭州望湖宾馆](https://example.com/hotel)**\\n\\n- 价格：¥618/晚\\n- 地址：西湖边\\n- 评分：4.8\\n- 星级：豪华型",
      "status": 0
    }
    """

    blocks = normalize_output(raw)

    assert blocks[0]["type"] == "hotel_card"
    assert blocks[0]["title"] == "杭州望湖宾馆"
    assert blocks[0]["price"] == "¥618/晚"
    assert blocks[0]["address"] == "西湖边"
    assert blocks[0]["bookingUrl"] == "https://example.com/hotel"


def test_normalizes_poi_markdown_data():
    raw = """
    {
      "data": "**[西湖游船门票](https://example.com/poi)**\\n\\n- 门票：西湖游船成人票\\n- 价格：¥55\\n- 地址：杭州西湖景区",
      "status": 0
    }
    """

    blocks = normalize_output(raw)

    assert blocks[0]["type"] == "poi_card"
    assert blocks[0]["title"] == "西湖游船门票"
    assert blocks[0]["price"] == "¥55"
    assert blocks[0]["ticketName"] == "西湖游船成人票"
    assert blocks[0]["bookingUrl"] == "https://example.com/poi"


def test_extracts_final_hermes_box_as_poi_card():
    raw = """
╭────────────────────── Hermes Agent v0.13.0 ───────────────────────╮
│ Available Tools                                                     │
╰────────────────────────────────────────────────────────────────────╯
Query: 杭州西湖门票
Initializing agent...
╭─ ⚕ Hermes ─────────────────────────────────────────────────────────╮
    西湖风景名胜区
    - 亮点：杭州标志性景点，全天开放，免费进入（无需门票），可游览苏堤、白堤、断桥等经典景观
    - 推荐理由：西湖为开放式景区，免费对公众开放，适合步行、骑行或乘船游览
    - 说明：西湖本身免门票，但景区内的游船、部分收费景点（如雷峰塔、岳王庙等）需单独购票
╰────────────────────────────────────────────────────────────────────╯
Resume this session with:
  hermes --resume abc
"""

    blocks = normalize_output(raw)

    assert blocks[0]["type"] == "poi_card"
    assert blocks[0]["title"] == "西湖风景名胜区"
    assert blocks[0]["price"] == "免费"


def test_parses_json_inside_final_hermes_box():
    raw = """
╭─ ⚕ Hermes ─────────────────────────────────────────────────────────╮
    {"data":"西湖风景名胜区是免费开放的开放式景区，不需要门票即可进入游览。不过景区内部分子景点（如雷峰塔、岳王庙等）或游船项目需要单独购票。","message":"success","status":0}
╰────────────────────────────────────────────────────────────────────╯
"""

    blocks = normalize_output(raw)

    assert blocks[0]["type"] == "poi_card"
    assert blocks[0]["title"] == "西湖风景名胜区"
    assert blocks[0]["price"] == "免费"


def test_extracts_loose_multiline_json_data_inside_final_hermes_box():
    raw = """
╭─ ⚕ Hermes ─────────────────────────────────────────────────────────╮
    {"data":"西湖风景名胜区是免费开放的开放式景区，不需要门票即可进入游览。

    基于飞猪搜索结果，推荐以下可预订选项：

    西湖风景名胜区
    - 亮点：世界文化遗产，免费开放，全天可游览
    - 注意：如需游船、雷峰塔、岳王庙等子景点，需另行购票","message":"success","status":0}
╰────────────────────────────────────────────────────────────────────╯
"""

    blocks = normalize_output(raw)

    assert blocks[0]["type"] == "poi_card"
    assert blocks[0]["title"] == "西湖风景名胜区"
    assert blocks[0]["price"] == "免费"


def test_compacts_about_style_poi_title():
    raw = '{"data":"关于杭州西湖的门票信息如下：西湖主景区免费开放，无需门票。","status":0}'

    blocks = normalize_output(raw)

    assert blocks[0]["type"] == "poi_card"
    assert blocks[0]["title"] == "杭州西湖"
    assert blocks[0]["price"] == "免费"


def test_extracts_line_style_hermes_answer_as_round_trip_flight_cards():
    raw = """
╭────────────────────── Hermes Agent v0.13.0 (2026.5.7) ───────────────────────╮
│ Available Tools                                                              │
╰──────────────────────────────────────────────────────────────────────────────╯
Query: 北京东京往返机票
  ┊ 💻 preparing terminal…
  ┊ 💻 $ flyai search-flight ...
 ─  ⚕ Hermes  ─────────────────────────────────────────────────────────────────

     基于 fly.ai 实时搜索结果，以下是 2026
     年端午节前后北京↔东京往返、不转机、停留 5 晚的最低票价组合。
     端午节日期说明
     2026 年端午节为 5 月 30 日。
     最低票价往返组合（停留 5 晚）
     推荐组合 A：总价 ¥2,813（最经济）
     - 去程：5月30日 IJ018 春秋日本航空 10:45-15:30 ¥1,114
     - 回程：6月4日 CZ648 南航 15:45-18:45 ¥1,699
     - 合计：¥2,813
     推荐组合 B：总价 ¥2,347（如接受 6 月 1 日出发）
     - 去程：6月1日 IJ018 春秋日本航空 10:45-15:30 ¥1,012
     - 回程：6月5日 IJ017 春秋日本航空 17:55-21:15 ¥1,335
     - 合计：¥2,347

 ──────────────────────────────────────────────────────────────────────────────

⚠ Iteration budget reached (8/8) — response may be incomplete
"""

    blocks = normalize_output(raw)

    assert blocks[0]["type"] == "flight_card"
    assert blocks[0]["title"] == "推荐组合 A：北京 ↔ 东京"
    assert blocks[0]["subtitle"] == "最经济"
    assert blocks[0]["price"] == "¥2,813"
    assert blocks[0]["number"] == "IJ018 / CZ648"
    assert blocks[0]["segments"][0]["depStation"] == "北京"
    assert blocks[0]["segments"][0]["arrStation"] == "东京"
    assert blocks[0]["segments"][0]["depTime"] == "5月30日 10:45"
    assert blocks[0]["segments"][0]["price"] == "¥1,114"
    assert blocks[0]["segments"][1]["depStation"] == "东京"
    assert blocks[0]["segments"][1]["arrStation"] == "北京"
    assert blocks[0]["segments"][1]["number"] == "CZ648"
    assert blocks[1]["title"] == "推荐组合 B：北京 ↔ 东京"
    assert blocks[1]["subtitle"] == "如接受 6 月 1 日出发"
    assert len(blocks) == 2


def test_extracts_wrapped_markdown_table_flight_combos():
    raw = """
 ─  ⚕ Hermes  ─────────────────────────────────────────────────────────────────

     基于 fly.ai 实时查询结果，为您整理 2026 年端午节（5 月 31
     日）前后北京-东京往返直飞、上午出发、下午/晚上返回、停留 5
     晚的最低票价组合。
     推荐最低票价往返组合
     组合一：总价 ¥2,448（最低）
     | 航段 | 日期 | 航班号 | 出发 | 到达 | 价格 |
     |---|---|---|---|---|---|
     | 北京→东京 | 5 月 30 日（周六）| IJ018 春秋日本航空 | 首都 T3 10:45 |
     成田 T3 15:30 | ¥1,113 |
     | 东京→北京 | 6 月 4 日（周四）| CZ648 南航 | 羽田 T3 15:45 | 大兴
     18:45 | ¥1,699 |

     - 停留 5 晚，覆盖端午节假期（5 月 31 日）
     - 去程上午出发符合要求；回程下午出发、傍晚到达

     组合二：总价 ¥2,607（次低，端午节当天出发）
     | 航段 | 日期 | 航班号 | 出发 | 到达 | 价格 |
     |---|---|---|---|---|---|
     | 北京→东京 | 5 月 31 日（周日/端午）| CZ647 南航 | 大兴 09:40 | 羽田
     T3 14:00 | ¥2,458 |
     | 东京→北京 | 6 月 5 日（周五）| IJ017 春秋日本航空 | 成田 T3 17:55 |
     首都 T3 21:15 | ¥1,335 |
"""

    blocks = normalize_output(raw)

    assert blocks[0]["type"] == "flight_card"
    assert blocks[0]["title"] == "组合一：北京 ↔ 东京"
    assert blocks[0]["subtitle"] == "最低"
    assert blocks[0]["price"] == "¥2,448"
    assert blocks[0]["number"] == "IJ018 / CZ648"
    assert blocks[0]["segments"][0]["depCity"] == "北京"
    assert blocks[0]["segments"][0]["depStation"] == "首都 T3"
    assert blocks[0]["segments"][0]["depTime"] == "5月30日（周六） 10:45"
    assert blocks[0]["segments"][0]["arrCity"] == "东京"
    assert blocks[0]["segments"][0]["arrStation"] == "成田 T3"
    assert blocks[0]["segments"][0]["arrTime"] == "15:30"
    assert blocks[0]["segments"][0]["carrier"] == "春秋日本航空"
    assert blocks[0]["segments"][0]["price"] == "¥1,113"
    assert blocks[0]["segments"][1]["depCity"] == "东京"
    assert blocks[0]["segments"][1]["arrCity"] == "北京"
    assert blocks[0]["segments"][1]["number"] == "CZ648"
    assert blocks[1]["title"] == "组合二：北京 ↔ 东京"
    assert blocks[1]["subtitle"] == "次低，端午节当天出发"
    assert len(blocks) == 2


def test_extracts_unlabeled_round_trip_flight_table():
    raw = """
 ─  ⚕ Hermes  ─────────────────────────────────────────────────────────────────

     基于 fly.ai 实时搜索结果，以下是 2026 年端午节前后北京-东京往返、停留
     5 晚、不转机的最低票价组合方案。
     推荐最低票价往返组合
     | 航段 | 日期 | 航班号 | 出发 | 到达 | 时长 | 价格 |
     |------|------|--------|------|------|------|------|
     | 去程 | 5 月 28 日（周四） | 春秋日本 IJ018 | 北京首都 10:45 | 东京成田 15:30 | 3h45m | ¥1,014 |
     | 返程 | 6 月 2 日（周二） | 海南航空 HU440 | 东京成田 13:55 | 北京首都 17:20 | 4h25m | ¥1,220 |
"""

    blocks = normalize_output(raw)

    assert blocks[0]["type"] == "flight_card"
    assert blocks[0]["title"] == "推荐最低票价往返组合：北京 ↔ 东京"
    assert blocks[0]["price"] == "¥2,234"
    assert blocks[0]["number"] == "IJ018 / HU440"
    assert len(blocks[0]["segments"]) == 2
    assert blocks[0]["segments"][0]["label"] == "去程"
    assert blocks[0]["segments"][0]["depCity"] == "北京"
    assert blocks[0]["segments"][0]["depStation"] == "首都"
    assert blocks[0]["segments"][0]["arrCity"] == "东京"
    assert blocks[0]["segments"][0]["arrStation"] == "成田"
    assert blocks[0]["segments"][0]["duration"] == "3小时45分钟"
    assert blocks[0]["segments"][1]["label"] == "返程"
    assert blocks[0]["segments"][1]["depCity"] == "东京"
    assert blocks[0]["segments"][1]["arrCity"] == "北京"
    assert blocks[0]["segments"][1]["number"] == "HU440"


def test_extracts_round_trip_table_with_flight_time_airport_price_columns():
    raw = """
基于 fly.ai 实时结果，2026 年端午节（6 月 20 日）前后北京↔东京往返直飞。
推荐最低总价方案
| 航段 | 日期 | 航班 | 起降时间 | 机场 | 票价 |
|---|---|---|---|---|---|
| 去程 | 6 月 19 日（周五） | IJ018 春秋日本航空 | 10:45 → 15:30 |
首都 T3 → 成田 T3 | ¥1,474 |
| 回程 | 6 月 25 日（周四） | IJ017 春秋日本航空 | 17:55 → 21:15 |
成田 T3 → 首都 T3 | ¥1,354 |
| 往返合计 | 停留 6 晚 | | | | ¥2,828 |

其他可选组合
方案 A：6 月 20 日去 + 6 月 25 日回
- 去程：CZ647 南航 09:40 大兴 → 14:00 羽田  ¥1,748
- 回程：IJ017 春秋 17:55 成田 → 21:15 首都  ¥1,354
- 合计：¥3,102
"""

    blocks = normalize_output(raw)

    assert blocks[0]["type"] == "flight_card"
    assert blocks[0]["title"] == "推荐最低总价方案：北京 ↔ 东京"
    assert blocks[0]["price"] == "¥2,828"
    assert blocks[0]["number"] == "IJ018 / IJ017"
    assert len(blocks[0]["segments"]) == 2
    assert blocks[0]["segments"][0]["label"] == "去程"
    assert blocks[0]["segments"][0]["number"] == "IJ018"
    assert blocks[0]["segments"][0]["depCity"] == "北京"
    assert blocks[0]["segments"][0]["depStation"] == "首都 T3"
    assert blocks[0]["segments"][0]["depTime"] == "6月19日（周五） 10:45"
    assert blocks[0]["segments"][0]["arrCity"] == "东京"
    assert blocks[0]["segments"][0]["arrStation"] == "成田 T3"
    assert blocks[0]["segments"][0]["price"] == "¥1,474"
    assert blocks[0]["segments"][1]["label"] == "返程"
    assert blocks[0]["segments"][1]["number"] == "IJ017"
    assert blocks[0]["segments"][1]["depCity"] == "东京"
    assert blocks[0]["segments"][1]["arrCity"] == "北京"
    assert blocks[0]["segments"][1]["price"] == "¥1,354"


def test_query_aware_repair_combines_loose_round_trip_lines():
    raw = """
基于 fly.ai 实时结果，推荐组合如下：
推荐组合
- 去程：6月19日 IJ018 春秋日本航空 10:45-15:30 ¥1,474
- 回程：6月25日 IJ017 春秋日本航空 17:55-21:15 ¥1,354
"""

    blocks = normalize_output(raw, "北京东京往返机票，停留 5 晚")

    assert blocks[0]["type"] == "flight_card"
    assert blocks[0]["title"] == "往返航班：北京 ↔ 东京"
    assert blocks[0]["price"] == "¥2,828"
    assert blocks[0]["number"] == "IJ018 / IJ017"
    assert len(blocks[0]["segments"]) == 2
    assert blocks[0]["segments"][0]["label"] == "去程"
    assert blocks[0]["segments"][0]["depStation"] == "北京"
    assert blocks[0]["segments"][0]["arrStation"] == "东京"
    assert blocks[0]["segments"][1]["label"] == "返程"
    assert blocks[0]["segments"][1]["depStation"] == "东京"
    assert blocks[0]["segments"][1]["arrStation"] == "北京"


def test_query_aware_repair_uses_embedded_raw_text_for_json_blocks():
    raw = """
{
  "summary": "最低价方案",
  "rawText": "推荐组合\\n- 去程：6月19日 IJ018 春秋日本航空 10:45-15:30 ¥1,474\\n- 回程：6月25日 IJ017 春秋日本航空 17:55-21:15 ¥1,354",
  "blocks": [
    {
      "type": "flight_card",
      "title": "只含去程的错误卡片",
      "price": "¥1,474",
      "number": "IJ018",
      "segments": [
        {"label": "去程", "depStation": "北京", "depTime": "10:45", "arrStation": "东京", "arrTime": "15:30", "number": "IJ018", "price": "¥1,474"}
      ]
    }
  ]
}
"""

    blocks = normalize_output(raw, "北京东京往返机票")

    assert blocks[0]["title"] == "查询结论"
    assert blocks[1]["type"] == "flight_card"
    assert blocks[1]["title"] == "往返航班：北京 ↔ 东京"
    assert blocks[1]["price"] == "¥2,828"
    assert blocks[1]["number"] == "IJ018 / IJ017"
    assert len(blocks[1]["segments"]) == 2


def test_query_aware_warning_when_round_trip_card_cannot_be_completed():
    raw = "北京到东京 CA181 08:05 → 12:25，价格 ¥1,474。"

    blocks = normalize_output(raw, "北京东京往返机票")

    assert blocks[0]["type"] == "notice"
    assert blocks[0]["title"] == "结果可能不完整"
    assert "往返机票" in blocks[0]["items"][0]


def test_extracts_round_trip_from_prose_flight_sections():
    raw = """
2026年端午节 北京↔东京往返机票推荐

根据飞猪数据，2026年端午节为6月19日（周五），以下方案可满足您的需求：

推荐行程方案
去程：6月19日（周五）上午北京出发 → 回程：6月24日（周三）下午/晚上东京返回
（停留5晚，包含端午节假期）

去程航班（北京→东京，上午出发，直飞）

CA181
- 航空公司：中国国航
- 时间：08:05（北京首都）→ 12:25（东京羽田）
- 飞行时长：约3小时20分
- 特点：到达羽田机场，距东京市区更近

CA925
- 航空公司：中国国航
- 时间：09:15（北京首都）→ 13:40（东京成田）
- 飞行时长：约3小时25分

回程航班（东京→北京，下午/晚上出发，直飞）

CA182
- 航空公司：中国国航
- 时间：14:00（东京羽田）→ 16:45（北京首都）
- 飞行时长：约3小时45分

CA926
- 航空公司：中国国航
- 时间：15:15（东京成田）→ 18:05（北京首都）
- 飞行时长：约3小时50分

💡 最低票价建议
目前系统中暂未提供具体日期的实时票价信息，建议您在飞猪平台搜索具体日期查看最低票价。

推荐组合（性价比优先）：
- 去程：CA181（08:05→12:25，羽田到达，距市区近）
- 回程：CA926（15:15→18:05，下午出发，时间充裕）
- 国航往返通常价格更具竞争力
> ⚠️ 以上航班时刻为参考时刻，实际请以飞猪平台实时查询为准。
"""

    blocks = normalize_output(raw)

    assert blocks[0]["type"] == "flight_card"
    assert blocks[0]["title"] == "2026年端午节 北京↔东京往返机票推荐"
    assert blocks[0]["price"] == "未返回票价"
    assert blocks[0]["number"] == "CA181 / CA926"
    assert len(blocks[0]["segments"]) == 2
    assert blocks[0]["segments"][0]["label"] == "去程"
    assert blocks[0]["segments"][0]["number"] == "CA181"
    assert blocks[0]["segments"][0]["depCity"] == "北京"
    assert blocks[0]["segments"][0]["depStation"] == "首都"
    assert blocks[0]["segments"][0]["depTime"] == "6月19日（周五） 08:05"
    assert blocks[0]["segments"][0]["arrCity"] == "东京"
    assert blocks[0]["segments"][0]["arrStation"] == "羽田"
    assert blocks[0]["segments"][0]["carrier"] == "中国国航"
    assert blocks[0]["segments"][1]["label"] == "返程"
    assert blocks[0]["segments"][1]["number"] == "CA926"
    assert blocks[0]["segments"][1]["depCity"] == "东京"
    assert blocks[0]["segments"][1]["depStation"] == "成田"
    assert blocks[0]["segments"][1]["arrCity"] == "北京"
    assert blocks[0]["segments"][1]["arrStation"] == "首都"
    assert blocks[0]["segments"][1]["depTime"] == "6月24日（周三） 15:15"
    assert blocks[0]["items"][0] == "本次 fly.ai 未返回实时票价，无法确认最低价。"


def test_extracts_round_trip_from_loose_outbound_inbound_sections():
    raw = """
以下是基于飞猪搜索结果整理的北京↔成都往返机票信息：

✈️ 去程：北京 → 成都

以下为部分代表性航班：

CZ9144
- 航空公司：南方航空 | 机型：32Z
- 时间：06:55→09:55（约3小时）
- 出发/到达：首都国际机场 → 双流国际机场

HO5911
- 航空公司：吉祥航空 | 机型：32Z
- 时间：07:45→10:25（约2小时40分，最快之一）
- 出发/到达：首都国际机场 → 双流国际机场

✈️ 返程：成都 → 北京

EU7483
- 航空公司：成都航空 | 机型：332
- 时间：09:30→12:15（约2小时45分）
- 出发/到达：双流国际机场 → 首都国际机场

HU7348
- 航空公司：海南航空 | 机型：738
- 时间：18:00→21:00（约3小时）
- 出发/到达：天府机场 → 首都国际机场

具体价格请以飞猪平台实时查询为准
"""

    blocks = normalize_output(raw, "北京到成都的往返机票")

    assert blocks[0]["type"] == "flight_card"
    assert blocks[0]["price"] == "未返回票价"
    assert blocks[0]["number"] == "CZ9144 / EU7483"
    assert len(blocks[0]["segments"]) == 2
    assert blocks[0]["segments"][0]["label"] == "去程"
    assert blocks[0]["segments"][0]["depCity"] == "北京"
    assert blocks[0]["segments"][0]["arrCity"] == "成都"
    assert blocks[0]["segments"][0]["depStation"] == "首都国际机场"
    assert blocks[0]["segments"][1]["label"] == "返程"
    assert blocks[0]["segments"][1]["depCity"] == "成都"
    assert blocks[0]["segments"][1]["arrCity"] == "北京"
    assert blocks[0]["segments"][1]["depStation"] == "双流国际机场"


def test_extracts_round_trip_from_directional_schedule_tables():
    raw = """
2026 年端午节北京↔东京往返航班信息

2026 年端午节假期为 6 月 20 日（周六）— 6 月 22 日（周一）。
一、北京 → 东京（上午出发，直飞）

| 航班号 | 航司 | 路线 | 时间 | 时长 |
|--------|------|------|------|------|
| CA181 | 中国国航 | 首都→羽田 | 08:05→12:25 | 3h20m |
| NH964 | 全日空 | 首都→羽田 | 08:20→12:55 | 3h35m |
| IJ018 | 春秋日本 | 首都→成田 | 10:45→15:30 | 3h45m |

推荐早班：CA181、NH964、JL020（08:00 左右出发）
二、东京 → 北京（下午或晚上出发，直飞）

| 航班号 | 航司 | 路线 | 时间 | 时长 |
|--------|------|------|------|------|
| HU440 | 海南航空 | 成田→首都 | 13:55→17:05 | 4h10m |
| CA182 | 中国国航 | 羽田→首都 | 14:00→16:45 | 3h45m |
| CA926 | 中国国航 | 成田→首都 | 15:15→18:05 | 3h50m |

四、最低票价说明
目前 fly.ai 体验模式暂未返回 6 月具体日期的实时票价数据。
2. 重点关注 春秋日本航空 IJ018/IJ017（廉价航空，通常价格最低）
3. 国航 CA181/CA182、南航 CZ647/CZ648 也是性价比常选
"""

    blocks = normalize_output(raw)

    assert blocks[0]["type"] == "flight_card"
    assert blocks[0]["title"] == "2026 年端午节北京↔东京往返航班信息"
    assert blocks[0]["price"] == "未返回票价"
    assert blocks[0]["number"] == "CA181 / CA182"
    assert len(blocks[0]["segments"]) == 2
    assert blocks[0]["segments"][0]["label"] == "去程"
    assert blocks[0]["segments"][0]["number"] == "CA181"
    assert blocks[0]["segments"][0]["depCity"] == "北京"
    assert blocks[0]["segments"][0]["depStation"] == "首都"
    assert blocks[0]["segments"][0]["arrCity"] == "东京"
    assert blocks[0]["segments"][0]["arrStation"] == "羽田"
    assert blocks[0]["segments"][0]["duration"] == "3小时20分钟"
    assert blocks[0]["segments"][1]["label"] == "返程"
    assert blocks[0]["segments"][1]["number"] == "CA182"
    assert blocks[0]["segments"][1]["depCity"] == "东京"
    assert blocks[0]["segments"][1]["arrCity"] == "北京"


def test_parses_last_json_from_stream_output():
    raw = """
    Hermes is thinking...
    {"debug": true}
    final:
    {"data":"**[G7333](https://example.com/train)**\\n- 上海虹桥 → 杭州东","status":0}
    """

    blocks = normalize_output(raw)

    assert blocks[0]["type"] == "train_card"
    assert blocks[0]["number"] == "G7333"
