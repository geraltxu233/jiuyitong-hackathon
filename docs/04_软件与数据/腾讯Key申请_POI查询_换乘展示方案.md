# 腾讯位置服务 Key 申请 + POI ID 查询 + 换乘展示方案

> 研究日期：2026-10-23 ｜ 配合接口协定 I2/I3 使用

---

## 一、Key 申请流程（软件同学，约 10 分钟）

### 步骤

| # | 操作 | 说明 |
|---|---|---|
| 1 | 访问 https://lbs.qq.com/ 注册/登录 | 支持微信扫码 |
| 2 | 控制台 → 应用管理 → 我的应用 → 创建应用 | 名称随意，类型选"个人" |
| 3 | 应用下「添加 Key」 | 填 Key 名称、描述 |
| 4 | ⚠️ **勾选「WebService API」** | 漏了这步后面全白搭 |
| 5 | ⚠️ 配额管理 → 账户额度 → **一键分配** | 给 Key 分配地点搜索、路线规划额度，漏了会调用报错 |
| 6 | 复制 Key | 形如 `OB4BZ-D4W3U-xxxxx-xxxxx-xxxxx-xxxxx` |

### 关键提醒

- **WebService API 勾选** + **额度一键分配**，是两个最容易漏的坑。
- 个人开发者有**免费额度**，黑客松演示量级绰绰有余。
- Key 是**通用**的：同一个 Key 可用于 WebService API、地点搜索、路线规划等，不用重复申请。

---

## 二、开发板如何调用（架构决策）

### ⚠️ 结论：ESP32-S3 不直连腾讯 API

理由：

1. **HTTPS + TLS 证书**：腾讯 API 是 HTTPS，板子做 TLS 握手、校验证书会占用大量 RAM/Flash，墨水屏板资源紧张。
2. **返回数据大**：公交规划返回的 JSON 很大（含 polyline 压缩坐标），板子解析筛选复杂、占内存。
3. **Key 安全**：Key 烧进固件会泄露。

### 正确链路（接口协定 I3 已定）

```
开发板 → 云端中转(/api/transit) → 腾讯公交API → 云端精简 → 返回板子
```

### 板子侧只需做一件事

HTTPS GET 请求自己的云端接口，解析返回的段数组渲染到墨水屏：

```
https://<cloud-host>/api/transit?from_lat=31.178&from_lng=121.423&to=中山医院
```

板子代码量很小，复杂逻辑全在云端。

---

## 三、POI ID 是什么 + 怎么查

### 定义

- **POI** = Point of Interest（兴趣点/地点）。
- **POI ID** = 腾讯地图给每个地点分配的唯一标识（一长串数字，如 `1376521901188575736`）。
- 你在手机地图搜"复旦大学附属中山医院"看到的名称、地址、电话，就是这条 POI 记录；**POI ID 是这条记录的唯一编号**，藏在返回的 `id` 字段里，手机界面不直接展示。

### 为什么要用 POI ID（而不是名称/坐标）

| 方式 | 问题 |
|---|---|
| 医院名称 | 可能重名、多院区，容易搜错 |
| 经纬度坐标 | 医院是大园区，坐标可能落在中心，终点不对 |
| **POI ID** | ✅ 唯一精确，系统自动用"出入口/引导点"当终点 |

### 查询方法：地点搜索 API

```
GET https://apis.map.qq.com/ws/place/v1/search?keyword=复旦大学附属中山医院&boundary=region(上海,0)&key=YOUR_KEY
```

返回 `data[0].id` 即 POI ID，同时含 `title`（名称）、`location`（坐标）、`address`（地址）。

### 可运行查询脚本（Python，查 4 家医院）

```python
import urllib.request, urllib.parse, json

KEY = "YOUR_KEY"  # 替换成申请的 Key
hospitals = [
    "复旦大学附属中山医院",
    "上海市第六人民医院",
    "上海交通大学医学院附属仁济医院",
    "复旦大学附属肿瘤医院",
]

for name in hospitals:
    url = "https://apis.map.qq.com/ws/place/v1/search?" + urllib.parse.urlencode({
        "keyword": name,
        "boundary": "region(上海,0)",
        "key": KEY,
    })
    data = json.load(urllib.request.urlopen(url))
    if data["status"] == 0 and data["data"]:
        poi = data["data"][0]
        print(f"{poi['title']}")
        print(f"  POI ID : {poi['id']}")
        print(f"  坐标   : {poi['location']['lat']},{poi['location']['lng']}")
        print(f"  地址   : {poi['address']}")
    else:
        print(f"{name}: 查询失败 status={data['status']} {data['message']}")
```

> 跑一次拿到 4 家医院的 POI ID，**写进科室映射总表**，I3 的 `to` 参数直接传 POI ID，导航更准。

---

## 四、换乘信息的展示方案（硬件接入）

### 两种展示形式对比

| | A：分段文字 | B：常规路线图 |
|---|---|---|
| 内容 | 乘几路→坐几站→换乘→步行 | 地图折线 + 标注 |
| 所需数据 | 只需文字字段 | polyline 坐标解压 + 地图渲染 |
| 墨水屏可行 | ✅ 纯文字 | ❌ 画不了地图 |
| 老人友好 | ✅ 大字分段 | 一般 |

**结论：硬件只能选「A：分段文字」**，墨水屏是静态黑白灰阶设备，画不了地图路线。

### 推荐结构：升级 I3 的固定 3 行为「segments 段数组」

换乘时 3 行装不下，建议改成段数组 + 换乘次数：

```json
{
  "code": 0,
  "segments": [
    {"type": "walk", "text": "步行 320 米 到 58路 起点站"},
    {"type": "bus",  "text": "乘 58 路，坐 3 站"},
    {"type": "bus",  "text": "到 中山医院站 下车"},
    {"type": "walk", "text": "下车步行 200 米 到终点"}
  ],
  "transfer_count": 0,
  "total_duration": 45
}
```

### 字段说明

| 字段 | 说明 |
|---|---|
| `segments[].type` | `walk`（步行）/ `bus`（公交）/ `subway`（地铁）/ `transfer`（换乘提示） |
| `segments[].text` | 老人可读文字，每段 ≤12 字 |
| `transfer_count` | 换乘次数（0=无换乘） |
| `total_duration` | 总耗时（分钟），可选展示 |

### 换乘时的段组织示例

```json
{
  "code": 0,
  "segments": [
    {"type": "walk",     "text": "步行 300 米 到 58路站"},
    {"type": "bus",      "text": "乘 58 路，坐 3 站"},
    {"type": "bus",      "text": "到 徐家汇站 下车"},
    {"type": "transfer", "text": "换乘：转 地铁1号线"},
    {"type": "subway",   "text": "坐 4 站 到 东安路站"},
    {"type": "walk",     "text": "下车步行 500 米 到终点"}
  ],
  "transfer_count": 1,
  "total_duration": 52
}
```

### 墨水屏渲染策略（硬件同学）

1. **每屏最多 3~4 段**，超出按键翻页。
2. **当前段高亮加粗，已完成段变灰**，让老人知道走到哪一步。
3. **换乘段（type=transfer）单独成段 + 醒目提示**。
4. 每段 ≤12 字（延续 I3 约束），墨水屏大字渲染。

### 云端生成 segments 的伪代码

```python
def build_segments(result):
    route = result["result"]["routes"][0]
    segments = []
    transfer_count = 0
    for i, step in enumerate(route["steps"]):
        if step["mode"] == "WALKING":
            dist = round(step["distance"] / 10) * 10
            if i == 0:
                segments.append({"type": "walk", "text": f"步行 {dist} 米 到车站"})
            else:
                segments.append({"type": "walk", "text": f"下车步行 {dist} 米"})
        elif step["mode"] == "TRANSIT":
            l = step["lines"][0]
            veh = {"BUS": "bus", "SUBWAY": "subway"}.get(l["vehicle"], "bus")
            segments.append({"type": veh, "text": f"乘 {l['title']} 坐 {l['station_count']} 站"})
            segments.append({"type": veh, "text": f"到 {l['getoff']['title']} 下车"})
            transfer_count += 1
            if i < len(route["steps"]) - 2:  # 后面还有公交段，说明要换乘
                next_transit = route["steps"][i+2]
                if next_transit["mode"] == "TRANSIT":
                    n = next_transit["lines"][0]
                    segments.append({"type": "transfer", "text": f"换乘：转 {n['title']}"})
    # 最后一段如果是步行，单独补"到终点"
    return segments
```

---

## 五、落地清单（软件同学）

- [ ] 申请腾讯位置服务 Key（勾选 WebService API + 一键分配额度）
- [ ] 用上面的脚本查 4 家医院 POI ID，写进科室映射总表
- [ ] 云端实现 `/api/transit`，内部调腾讯公交 API，`policy=LEAST_TRANSFER`（少换乘）
- [ ] 云端实现 `build_segments()` 精简逻辑，返回 segments 段数组
- [ ] 与硬件同学对齐 segments 字段结构（替代原 I3 固定 3 行）
