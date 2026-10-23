# 巡访 · 本地地点行程规划

独立的 FastAPI + 原生 HTML/CSS/JavaScript 应用，SQLite 持久化。没有修改或依赖旧的 `plaza-console` 服务、数据或配置。不需要 Node、GPU、云服务器或付费路线服务。

## 商店与医院 / 办公楼

规划页有“商店与商业区”和“医院与办公楼”两个入口。后者包含医院、诊所、牙科、律师事务所及其他 `office=*` 公司 / 机构，使用 `office_rules.json`。按**每栋已映射楼宇或办公区边界内的不同公司 / 医疗机构数**筛选和显示数量，不把相邻 POI 按距离拼成簇；只有楼的名称、层数或 `building=office` 不会生成虚构租户。重复节点 / 轮廓去重，明确不同楼层或单元保留。公司数据缺失时不能推断完整租户数。独立医院 / 诊所计 1 家，最少公司数设为 1 才能纳入；没有楼宇轮廓的独立机构使用自己的已映射位置。

两种类型共用起点、画区 / 直径、扫掠队列、接续分趟、路线、地点分享、Google 预览、历史、JSON 导出和定时搜索。搜索保存 `discovery_kind`、`min_companies` 和规则快照；旧记录没有新字段时仍按零售模式打开。医院 / 办公楼结果使用 `company_count`，不会误用零售 `shop_count`。原有零售规则不变。依据：[OSM office](https://wiki.openstreetmap.org/wiki/Key:office)、[OSM healthcare](https://wiki.openstreetmap.org/wiki/Key:healthcare)。

## 哪些工作在本机执行

取得地点数据后，几何筛选、公司 / 小店计数、去重、方位排序、OSRM 道路时间矩阵、Python Held–Karp / OR-Tools 路线优化、历史和计划均在运行此应用的电脑上执行。默认 OSRM 地址为本机 `127.0.0.1:5001`；新区域图的下载、预处理和新 OSRM 子进程也在本机。无需把矩阵再交给 OSMnx，OSRM 直接提供真实道路的驾驶时间矩阵。没有付费矩阵或云端路线优化服务。

这不等于完全离线：默认 Overpass 向 OpenStreetMap 数据服务查询搜索范围，Census / Nominatim 按主动操作查地址，Geofabrik 下载路网，地图底图与 Google iframe 在线加载。Google 预览会按相同站点顺序独立重算自己的路线，不参与应用保存的 OSRM 优化结果。要完全离线搜索地点，还需本机 OSM 查询服务，并将 `OVERPASS_URL` 指向它；已经下载路网不等于拥有本机 POI 查询库。默认本机部署没有把此类服务安装到公网。

## 运行

需要 Python 3.11+。macOS / Linux：

```sh
cd '/Users/jasonxu/Documents/CODING PROJECTS/plaza-planner'
./run.sh
```

访问 <http://127.0.0.1:8011>。本次交付已建立独立 `.venv` 并安装依赖，前端地图库已随应用提供。其他机器首次运行脚本会创建环境并安装 `requirements.txt`。

**Windows：**解压 Windows ZIP，准备本机 WSL2 + Ubuntu Docker Engine（无需 Docker Desktop 订阅；已有 Docker Desktop Linux 后端也可用），双击 `run-windows.cmd`。完整步骤和原生 Python 选项见 [deploy/WINDOWS.md](deploy/WINDOWS.md)。自动下载路网、OSRM 矩阵和路线计算都在这台 Windows 电脑上执行。

```powershell
.\run-windows.cmd
# 停止并保留历史：
.\run-windows.cmd -Action stop
```

只运行一个实例 / 一个 worker。文件锁阻止两个进程同时使用同一数据目录；同一进程最多执行两个发现任务、一项路线优化。进程运行时，内置 APScheduler 每 15 秒检查计划。关闭浏览器不会停止计划，关闭应用进程会停止计划。需要全天运行时，在本机系统服务中启动同一个命令。

## 免费地图与地址查询

当前应用的地图、绘制和地址查询均不需要 Google API key、结算账户或付费订阅。原先的 Google Maps JavaScript / Geocoding 集成已移除；已有搜索、边界、历史和行程仍可打开。地点与整趟路线现在默认使用 Google Maps iframe 预览；不调用计费 Maps JavaScript / Places / Routes API。绘制编辑器与精确 OSRM 线路仍使用免费 MapLibre。

- **绘制编辑器与 OSRM 原线路：MapLibre GL JS + OpenFreeMap。** 公共底图免费、无需注册或 key；使用 OSM 数据并保留版权署名。公共服务没有 SLA，仍需要网络。浏览器需要 WebGL 支持，服务器无需 GPU。可在 `.env` 设置 `MAP_STYLE_URL` 为自己的兼容样式服务。
- **边界编辑：Terra Draw。** 本地提供版本固定的开源 JS/CSS 文件，不依赖第三方 CDN。点按加入顶点，完成后拖动顶点修改形状；拖动边中点增加顶点。另有键盘编辑面板，支持删除顶点和在地图中心增加顶点。起点支持拖动和方向键校正。应用 / 取消和 Escape 保持草稿边界。
- **美国街道地址：US Census。** 默认“查找” / “美国街道地址”使用 Census。适合输入完整门牌街道、城市和邮编；不按商业地点名称搜索，返回位置可能沿街道范围插值，并非屋顶或入口保证。
- **公开地点 / 国际地址：Nominatim。** 单独点击“查询公开地点或国际地址”，再确认“查询此公开地点”。适合商业中心名称和国际查询。OSM 覆盖不均；城市、街道等大范围匹配会提示校正具体起点。不能据此声称其对所有美国街道地址都比 Census 更准确。

### Nominatim 使用政策与隐私

本项目明确选择 Nominatim 用于这个私人零售行程应用中**由用户主动触发的公开地点查询**，并按[公共服务使用政策](https://operations.osmfoundation.org/policies/nominatim/)实现以下限制：全应用单线程串行请求，未缓存请求间隔至少 1.1 秒；有效应用 User-Agent；持久化缓存（成功结果七天、空结果十五分钟）；尊重 403 / 429 / Retry-After，限流期间拒绝新请求，不自动重试；显示 OSM 署名。没有 autocomplete，没有抓取所有商户，没有定时地理编码；cron 重用保存的坐标。端点可通过 `NOMINATIM_URL` 切换，不需要改代码。

不要向公共 Nominatim 提交私人地址或其他敏感数据。美国街道地址默认只发给 Census，不会在 Census 失败时自动转发到 Nominatim。用户必须明确选择公开地点服务。浏览器设备坐标不会为反向地理编码上传到 Nominatim；地图瓦片服务仍会接收显示区域所需的瓦片请求。Census 和 OSM 查询结果都需要用户选择确认，并可在地图中校正。

Nominatim 不是商户发现数据库的替代品：发现完整区域内的已映射商业区仍由 Overpass 完成。公共 Nominatim 和 OpenFreeMap 都没有可用性保证；应用保留输入，错误时可重试或在已加载地图上放置起点。

Node/npm 只用于开发时更新已固定的前端依赖，正常启动不需要 Node。依赖版本见 `frontend/package-lock.json`，本地分发文件和许可证见 `static/vendor/`。地图服务不收费；本地 OSRM 仍需自己运行（见下节）。

官方依据：[OpenFreeMap](https://openfreemap.org/)、[MapLibre](https://maplibre.org/maplibre-gl-js/docs/)、[Terra Draw](https://github.com/JamesLMilner/terra-draw)、[Census](https://geocoding.geo.census.gov/geocoder/Geocoding_Services_API.html)、[Nominatim 搜索 API](https://nominatim.org/release-docs/latest/api/Search/)。

## 地址与边界

输入完整街道地址，点击“查找”，选择匹配地址作为明确确认。美国 Census 结果可能是插值位置；可打开地图校正起点。浏览器定位使用高精度请求，不使用 IP 位置。所有设备位置均须在地图中确认；报告精度大于 100 米时明确提醒移动起点或核对后确认。设备报告的精度不是定位保证。

地图对话框中的修改只作用于草稿；应用区域才提交，取消 / Escape 恢复原范围。圆形以起点为中心，单位为英里；绘制模式只使用多边形，不与圆形相交，直径值仍保留。点按顶点后点击“完成绘制”；控制点可拖动修改，边中点可增加顶点；键盘面板可删除顶点。手机支持点按和触摸拖动；“用键盘编辑边界顶点”提供键盘替代操作。地图支持方向键平移，新增顶点取地图中心。

限制：直径 0.1–30 英里，最少小店数 1–100，每趟地点数 1–15，起始角 [0,360)。多边形 3–100 个顶点，面积 100 平方米–2,000 平方公里，跨度不超过 80 公里，距起点不超过 200 公里。不支持跨日期变更线或纬度超过 ±85° 的区域。前后端均校验；自相交边界拒绝。搜索以商业区边界的内部代表点是否位于区域内判定；小店资格按该商业区的已映射内部商店计数。

## 数据、扫掠与分趟

`retail_rules.json` 是唯一可编辑的店铺资格配置，包含类别、独立店铺判据和连锁排除项。每个搜索保存该规则快照与哈希，恢复搜索时沿用原快照。OSM 未提供品牌不证明其为独立经营；此判据保守但并非商业数据库认证。

查询包含商场、零售建筑、零售用地与广泛店铺类别；按内部商店和几何重叠去重。连锁主力店不计数，也不否决相邻小店。命名相同且近距的店铺点 / 轮廓去重，未命名点与包含轮廓也会去重。没有可靠商业区轮廓的散店和仅有商场节点不随意聚成商业区；这是覆盖限制。名称和地址仅使用已映射证据，没有的标注“未命名商业区” / “地址未映射”。没有租户列表界面或租户选择控件；内部保留 OSM 来源与计数证据。

采用以起点为中心的 WGS84 方位等距投影，北=0°、顺时针增加；减去可选 baseline 后归一到 [0,360)。角度按 0.0000001° 量化，消除投影数值误差；同角度先按距离，再按 OSM ID 排序。显示从 1 开始的完整结果。`b` 仅指每趟商业区数量。

分趟在 SQLite `BEGIN IMMEDIATE` 事务内执行，`(scan_id, plaza_id)` 唯一键保证每个商业区只分配一次。未计算完成的一趟会重复打开；计算并保存路线后才能取得下一趟。最后一趟可少于 b。路线结果单独保存，不修改原扫掠队列。列表复选框仅用于在地图中查看所选商业区；当前趟从队列按序取得，路线计算覆盖当前趟全部地点。

每次发现分成最多 64 个真实区域任务。未知工作量阶段使用不定进度；取得区域总数后显示实际已完成任务。默认使用已实测可用的 Private.coffee Overpass；服务发生连接错误或 500/502/503/504 时，最多尝试一次备用 overpass-api.de，不会在 403/429 时切换服务。小范围使用单个查询，避免以前围绕起点拆成四个高度重叠的请求。自定义主服务时默认不向公共备用服务转发，除非显式设置 `OVERPASS_FALLBACK_URL`。每区原始数据写入 `data/checkpoints/`；中断、取消、限流或失败后可在历史中继续重试。单次执行最多 10 分钟，重试从已保存区域继续。每区请求受超时和大小限制。完整扫描意味着请求的映射数据已处理，**不表示现实世界覆盖完整**。未完成扫描不能开始分趟，以免恢复后改变已有队列。

## 接续分趟与地图起点

地图中的“使用当前位置”同时适用于圆形和绘制模式。主动放置、拖动或用方向键移动起点会自动选择新的坐标，并显示在地址与起点摘要中；圆心立即跟随起点。设备定位仍需核对确认。应用区域后，修改过的起点或边界会切换到新搜索，旧搜索和路线仍在历史中，避免旧区域结果与新圆心混用。第一趟的起点使用这次搜索保存的 pin 坐标。

每趟 b 是**新地点数**。b=8 时：第 1 趟固定起点 + 队列前 8 个地点；第 2 趟从第 1 趟保存的实际终点开始，再取后 8 个新地点，即 9 个矩阵点。实际驾驶顺序显示为 1..8，不修改原扫掠顺序。每趟保存起点/终点快照；已有后续行程接续时锁定前一趟路线，数据库提交时也检查。若主动选择最后返回本趟起点，下一趟从实际返回点接续。旧版第二趟及以后没有起点快照的路线须按顺序重新计算。

每个驾驶站点都有带图标的分享按钮；全趟有“在 Google Maps 中打开”。单地点继续使用已验证短链或 `/maps/place/`；整趟使用 Google 官方 `/maps/dir/?api=1`，保留顺序。电脑每链接最多 10 个目的地；手机浏览器每段最多 4 个目的地，段与段共用终点/起点，另有逐站链接。Google 会自行重新计算道路与时间，不保证与 OSRM 相同。依据：[Google Maps URL 官方限制](https://developers.google.com/maps/documentation/urls/get-started)。

## 免费部署与 ChatGPT 交接

完整运行步骤见 [deploy/SETUP.md](deploy/SETUP.md)，可直接复制的交接提示词见 [deploy/CHATGPT_SETUP_PROMPT.md](deploy/CHATGPT_SETUP_PROMPT.md)。仓库包含 Dockerfile、固定官方 OSRM 镜像的 Compose、下载/预处理/本机启动脚本与实际道路 smoke。软件和现有硬件上的自托管不需要付费 API；新租云服务器的算力可能收费。普通 ChatGPT 对话本身不能持续托管服务器。

## 本地 OSRM

只有点击“计算路线”才请求 OSRM；搜索、计划执行和分享都不会请求矩阵。默认单程，不返回起点；返回起点需明确勾选。驾驶时间是离线路网估计，不含实时交通。每个地点是商业区边界内点，并非已验证入口。OSRM 最远道路吸附 100 米；过远、不可达、超出本地数据范围、无效矩阵或几何都显示失败，保留搜索和分趟。

推荐按 [部署步骤](deploy/SETUP.md) 使用固定版本的 Compose 完成数据下载与 MLD 预处理。若已有兼容的本机 OSRM，只需配置 `OSRM_URL`。实际道路覆盖取决于所用 extract；跨州行程须包含相应路网。

先由 OR-Tools 求候选，1–12 个地点再用 Held-Karp 求时间矩阵的精确解（毫秒精度）；13–15 个地点最多优化 3 秒，明确标为“未证明最优”。矩阵中的 null、负值、非有限值和维度错误拒绝。

## 分享：目的地，不是导航线路

“分享”在应用内直接获取链接，弹出本应用的复制 / 系统分享面板，**不会要求用户进入 Google 分享对话框**。

优先调用 Google Maps 目前使用的未公开 RPC `https://www.google.com/maps/rpc/shorturl`，取得 Google 返回的真实 `https://maps.app.goo.gl/…`。服务器验证该链接 30x 指向完全一致的 `https://www.google.com/maps/place/{latitude},{longitude}` 路径后缓存。新链接可能有短暂传播延迟，验证做有限重试。无 key、无登录、无付费短网址服务，也没有编造 token。

**这不是官方公开支持的短链接创建 API。** Google 的公开 Maps URLs / Places 文档没有承诺此 RPC 的稳定性、配额或收费政策。若失败，按照用户允许的范围退回真实 `/maps/place/{lat},{lng}` 链接，面板明确显示使用了地点链接。分享不会产生 `/maps/dir` 链接、自动向他人发送消息或触发车辆命令。短链域名不代表已验证 Tesla 接受；本项目没有 Tesla 车辆测试或发送集成。

2026-10-20 实测成功：`https://maps.app.goo.gl/cyLBR1URnpf1d7pW9` 302 到 `https://www.google.com/maps/place/40.7580000,-73.9855000?...`。这是纽约时代广场坐标测试，不是用户私人位置。常规单元测试模拟第三方响应，不反复请求外部短链服务。

Google 公开文档：[Maps URLs](https://developers.google.com/maps/documentation/urls/get-started)、[Places 返回的地图链接](https://developers.google.com/maps/documentation/places/web-service/maps-links)。直接 RPC 行为依据本次实际请求与重定向验证，不能冒称是上述官方文档支持的创建接口。

## 历史、导出和定时

SQLite 位于 `data/planner.sqlite3`。每次完成扫描自动原子写 JSON：`data/exports/起点-直径或绘制区域-UTC时间-唯一ID.json`。JSON 包含全边界、配置、结果、资格规则和已保存行程。完成扫描已提交 SQLite 后才导出；磁盘导出失败会提示并允许重试，不丢失数据库记录。后续行程也刷新同一导出。删除 / 清空历史会删除对应行程、检查点与导出；**不删除定时计划**。清空操作需确认。

计划支持新增、修改、启用、停用、删除；保存完整搜索快照、IANA 时区、五段 cron、下次执行时间和上次结果。编辑已有计划默认保留原搜索设置；也可点击“使用规划页当前搜索设置”替换起点、区域和筛选条件。定时结果进入同一个历史。没有自动分享或车辆命令。

Cron 顺序为“分 时 日 月 周”，周日为 0/7，也支持 mon–sun、范围、列表与步长。由 APScheduler CronTrigger 校验；日与星期同时有限制时使用 **AND**。按保存时区执行，夏令时按该时区规则，可能遇到不存在 / 重复的墙上时间；建议选择远离夏令时切换时段的时间。同一计划正在运行时跳过新触发；应用停机期间的所有错过运行跳过，不做补跑。启用计划从下一次未来时刻开始。系统时区不改变保存的时区。

## Tailscale 私有访问

在自己已有的 Tailscale 网络中，只把新应用本机端口转发到一个**未使用**的 HTTPS 端口，例如 8444：

```sh
tailscale serve --bg --https=8444 http://127.0.0.1:8011
tailscale serve status
```

使用 `serve status` 返回的真实 tailnet 域名 / 端口，不要假设主机名。地图和地址查询无需 API key 或 referrer 设置。不要覆盖现有 443 / 8443 的服务，也不要启用公共 Funnel。本应用没有单独的用户账户；私有访问依赖本机 loopback 和 Tailscale ACL。浏览器精确定位需要安全上下文：localhost 或 Tailscale HTTPS。Tailscale 的免费资格以自己的账户方案为准；本交付未修改 tailnet 或购买套餐。

## 验证

```sh
.venv/bin/python -m unittest discover -s tests -v
```

测试覆盖边界与直径互不相交、角度和旋转基线、店铺 / 重叠轮廓去重、连锁主力店、完整队列、并发重复分趟与尾趟、搜索不调用路线、验证失败、历史和导出、重启恢复、取消与重试、cron 时区 / 周日语义 / 重叠、路线故障、两种目的地分享格式。第三方服务在测试中模拟。

实际接口验证与限制另见 `VERIFICATION.md`。地图和多边形现可直接使用；驾驶路线仍需启动覆盖目标区域的 OSRM。真实免费地图交互已验证，本机 OSRM v26.9.0 已用现有纽约路网验证两趟各 8 个新地点的真实驾驶计算与接续起点。

绘制模式要求起点位于多边形内部或边界（包括顶点）上。浏览器禁用无效区域的应用/搜索，服务器同时拒绝该配置；移动 pin 或编辑顶点后立即重新检查。

## 搜索速度与等待反馈

最近 15 分钟内相同查询区域复用本地原始地图缓存，筛选阈值变化仍重新核对资格；超过期限重新请求。缓存最多 64 个区域，与历史/检查点分开保存。公共服务等待不是完成进度：界面显示实际耗时、已完成区域数、当前提供服务的主机、备用请求、接收 KB 与筛选阶段。20 秒后标明服务响应较慢；本地轮询断连另行提示。服务端查询上限 20 秒，HTTP 连接/读取超时分别 5/25 秒；备用服务仍只尝试一次，403/429 不切换。取消会在当前网络读取结束或下个数据块后生效，不再开始备用请求；不伪装立即终止远端查询。最后一个区域处理后移除无意义的一秒等待。

在 iPhone / 支持 Web Share 的浏览器上，点击任一站点“分享”直接调用系统分享面板，使用即时可用的 `/maps/place/` 地点链接，不等待网络获取短链接，以保留 Safari 的点击激活权限。取消分享不会弹出第二个面板；系统分享不可用时回退复制链接。没有原生分享能力的浏览器仍尝试取得并验证真实短链。

## 自动路网与 Google Maps 嵌入（2026-10-22）

确认新起点后后台预备道路数据；搜索完成后核对全部结果，计算时再次核对整批起点和目的地。已有可用路网直接复用；缺少覆盖时根据 Geofabrik 公共区域目录选择覆盖整批地点的最小 extract，自动下载并执行 `extract → partition → customize`，启动仅绑定 loopback 的区域 OSRM。相邻批次仍使用同一图上的完整驾驶时间矩阵，不拼接独立图的跨区域时间。已准备数据按区域、OSRM 版本与驾驶配置缓存到 `data/routing/`；应用重启后可直接重启缓存图，不重复下载/预处理。下载失败、取消和准备失败均不修改已保存行程。

原有 `OSRM_URL` 仍可使用。已知其区域时设置 `OSRM_BASE_REGION`（例如 `us/new-york`）；否则逐点检查道路吸附。新图与旧服务使用不同本机端口，不会停掉旧服务或重写旧数据。后台只执行一个准备任务，最多缓存两个运行中的新区域进程；有路线正在使用的进程不会被淘汰，磁盘图仍保留。大区域首次准备可能需要较多 RAM、磁盘与时间；默认单个 PBF 下载上限 2048 MB，并检查可用磁盘，资源不足会说明原因，不会自动购买服务器。可在合适的自托管机器上设置 `OSRM_MAX_EXTRACT_MB`（0 为不设大小上限），不能承诺任意全球跨度都能在普通笔记本上运行。

界面显示下载 MB、提取、分区、权重与启动状态，并可“停止准备”。路线请求在准备时返回 202，浏览器显示进度后自动继续计算；不会把未准备好的矩阵当成成功。自动模式需要本机 OSRM 二进制；`Dockerfile` 已包含同一固定版本的 OSRM 与 Python。没有 OSRM 时仍可搜索，计算会明确提示安装。`OSRM_AUTO_DOWNLOAD=0` 可保留固定服务模式。

列表名称和优化路线站名可点开 Google 地点嵌入；计算后默认显示完整本趟的 Google 路线嵌入，长批次按 10 个驾驶分段切换、不漏站。默认无 key 的 Google legacy iframe 已实测地点及起点+8个新站，仍有“在 Google Maps 中打开”作为未加载时的备用。该 legacy URL 不是 Google 公开 Embed API 稳定性承诺的一部分。可选 `GOOGLE_MAPS_EMBED_KEY` 切换官方 Embed API：目前请求不收费，但 Google 要求 API key 和启用结算的项目；没有为本交付创建账号或启用结算。

Google iframe 根据相同站点顺序重新计算道路，无法直接画出任意 OSRM polyline；“查看 OSRM 原路线”保留精确的 OSRM 预览。iframe 不能用作自由绘制编辑器，区域绘制仍使用 MapLibre/Terra Draw。显示嵌入时会把对应地点坐标发给 Google。

依据：[Geofabrik 免费数据](https://download.geofabrik.de/)、[OSRM](https://github.com/Project-OSRM/osrm-backend)、[Google Embed 免费用量](https://developers.google.com/maps/documentation/embed/usage-and-billing)、[官方 key/结算前提](https://developers.google.com/maps/documentation/embed/quickstart)。
