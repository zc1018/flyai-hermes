# Design

## Source of truth
- Status: Active
- Last refreshed: 2026-05-20
- Primary product surfaces: 登录页、旅行查询工作台、查询历史、结果卡片、owner 后台
- Evidence reviewed: `README.md`, `static/index.html`, `static/app.js`, `static/styles.css`, `static/admin.html`, `static/admin.js`, `app/main.py`, `app/models.py`

## Brand
- Personality: 可靠、清爽、轻松、有出发感，像一个熟人小范围可用的旅行查询产品，而不是内部调试台。
- Trust signals: 环境状态、当前账户/额度、流式进度、明确错误提示、隐私提醒。
- Avoid: 大面积营销 hero、装饰性渐变球、过度拟物、只有单一绿色调的界面、把 Hermes 原始执行日志作为主要视觉焦点。

## Product goals
- Goals: 降低提问门槛，让用户快速描述旅行需求；把实时查询结果转成可扫读卡片；让配额/等待/失败状态可理解。
- Non-goals: 不做公开注册、不做订单管理、不替代飞猪正式下单流程。
- Success signals: 用户知道还能查几次；能一眼看到去程/返程、价格、航班号、酒店名等核心信息；普通用户不会误入后台。

## Personas and jobs
- Primary personas: owner、被邀请的朋友用户。
- User jobs: 查询最低价交通、找酒店、比较目的地、生成行程建议、回看历史。
- Key contexts of use: 手机或桌面浏览器，查询可能等待数十秒到数分钟。

## Information architecture
- Primary navigation: 查询页为主，owner 额外进入后台。
- Core routes/screens: `/` 查询工作台，`/admin` 管理后台。
- Content hierarchy: 登录/账户状态 > 查询输入 > 查询进度 > 结构化结果 > 历史。

## Design principles
- Principle 1: 先让用户知道“现在能做什么、系统在做什么、结果哪里重要”。
- Principle 2: 结果卡片优先展示核心字段，原始说明退到辅助位置。
- Tradeoffs: 保持原生 HTML/CSS/JS，不引入前端框架，换取部署简单和低维护成本。

## Visual language
- Color: 以天空蓝、海绿色、暖沙色和中性色为底，绿色表达可用/行动，蓝色表达信息，琥珀色表达价格和注意。
- Typography: 系统无衬线字体，标题紧凑，卡片内避免过大字号。
- Spacing/layout rhythm: 8px radius，12/16/24px 间距节奏，桌面双栏，移动端单栏。
- Shape/radius/elevation: 卡片 8px radius，轻边框和浅阴影；按钮和状态 pill 保持紧凑。
- Motion: 只使用轻量 hover/focus，不使用大幅动画。
- Imagery/iconography: 使用登机牌、路线线条和轻地图纹理表达旅行路径，不依赖外部图片。

## Components
- Existing components to reuse: status pill、history item、query form、card、timeline、admin user row。
- New/changed components: scene prompt cards、input guide chips、empty state、query stats row、friendly progress shell、history search。
- Variants and states: loading、queued、heartbeat、success、error、quota warning、empty history。
- Token/component ownership: `static/styles.css` 继续作为唯一样式源。

## Accessibility
- Target standard: 基础 WCAG 2.1 AA。
- Keyboard/focus behavior: 所有输入、按钮、历史项、快捷查询可键盘访问，有明显 focus。
- Contrast/readability: 正文和按钮对比度优先；状态色不作为唯一信息来源。
- Screen-reader semantics: 保留 `aria-live` 给流式反馈；按钮有明确文本或 title。
- Reduced motion and sensory considerations: 不依赖动画传递状态。

## Responsive behavior
- Supported breakpoints/devices: 桌面、平板、手机。
- Layout adaptations: 桌面左历史右查询，移动端查询优先、历史折到下方，结果单列。
- Touch/hover differences: 移动端按钮高度不低于 44px。

## Interaction states
- Loading: 查询按钮禁用，结果区显示进度卡。
- Empty: 登录后显示可操作的空状态和示例查询。
- Error: notice 卡片展示中文原因和下一步。
- Success: 顶部 meta 显示完成时间、耗时，结果卡片靠前。
- Disabled: 按钮降透明度并保留文字。
- Offline/slow network: 使用现有 fetch 错误和 Hermes heartbeat 文案。

## Content voice
- Tone: 直接、具体、不过度承诺。
- Terminology: 使用“访问口令”“今日剩余”“查询中”“往返”“返程”等用户可理解词。
- Microcopy rules: 不暴露内部路径；错误提示给下一步；隐私提示简短。

## Implementation constraints
- Framework/styling system: 原生 FastAPI + 静态 HTML/CSS/JS。
- Design-token constraints: 继续使用 CSS variables。
- Performance constraints: 不引入大依赖；页面首屏静态资源小。
- Compatibility constraints: 保持现有 API 和 normalizer 输出协议。
- Test/screenshot expectations: JS 语法检查、后端测试、桌面和移动截图检查。

## Open questions
- [ ] 是否需要独立产品名称替代 Hermes/FlyAI 技术名 / owner / 影响品牌呈现
- [ ] 是否需要朋友用户看到“使用说明”或“反馈入口” / owner / 影响后续产品闭环
