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
