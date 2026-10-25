# 老人导诊卡套 —— 联调接口协定（INTERFACE）

> 版本：2026-10-22 | 双方各持一份，Day1 上午开工前定死，改动需双方确认
>
> 方向约定：**板子 = ESP32-S3 开发板（硬件工程师）**；**云端 = 中转服务 + 第三方 API（软件工程师）**

---

## I1. 导诊接口：音频上传 → 科室判断

**方向**：板子 → 云端 → 板子
**触发**：用户按住按键说话，松开即上传

**请求**（板子 → 云端）
```
POST https://<cloud-host>/api/triage
Content-Type: multipart/form-data

audio: 原始音频（建议 PCM 16kHz 16bit 单声道，或云端指定格式）
device_id: "esp32s3_001"
```

**响应**（云端 → 板子，JSON）
```json
{
  "code": 0,
  "asr_text": "我这几天胸口闷，有时候心慌",
  "department": "心内科",
  "confidence": 0.92,
  "reply_text": "建议您挂心内科，门诊三楼东侧。",
  "tts_url": "https://<cloud-host>/tts/abc123.wav",
  "register": {
    "available": true,
    "room": "门诊三楼 305 诊室",
    "queue_no": "A023"
  }
}
```

| 字段 | 说明 |
|------|------|
| code | 0=成功；非 0 时板子显示"没听清，请再说一次" |
| asr_text | 识别原文（普通话文本，方言输入也转为普通话文本） |
| department | 科室名，必须命中科室映射表（见 I4），否则返回 code=2 |
| tts_url | 普通话播报音频，板子下载后经 ES8311 播放 |
| register | 伪挂号结果（环节 2 合并在此返回，省一次交互） |

**时延红线**：端到端 ≤5 秒，云端需计时日志。

---

## I2. 网络定位接口：WiFi 扫描 → 经纬度

**方向**：板子 → 腾讯位置服务（软件工程师提供 Key 和封装示例）

```
POST https://apis.map.qq.com/ws/location/v1/network
Content-Type: application/json

{
  "key": "<TENCENT_KEY>",
  "device_id": "esp32s3_001",
  "accesstype": 1,
  "macs": "aa:bb:cc:dd:ee:ff,-60|11:22:33:44:55:66,-72"
}
```

- 板子先 `esp_wifi_scan` 收集周边 AP（**无需连接**），取信号最强的 2~30 个；
- 返回 `result.location.latitude / longitude / accuracy`；
- 定位失败或 accuracy > 500m → 板子进入**手动选起点**降级流程（预置起点列表）。

---

## I3. 公交规划接口：起终点 → 老人可读指令

**方向**：板子 → 云端 → 腾讯地图公交 API → 板子（云端做精简，板子不直接解析原始 API）

**请求**
```
GET https://<cloud-host>/api/transit?from_lat=..&from_lng=..&to=<医院POI_ID>
```

**响应**（最多 3 行，每行 ≤12 字，板子原样大字渲染）
```json
{
  "code": 0,
  "lines": [
    "乘 58 路",
    "坐 6 站 到 中山医院站",
    "下车步行 200 米"
  ],
  "reply_audio": "https://<cloud-host>/tts/def456.wav"
}
```

---

## I4. 院内科室信息表（静态数据，烧入或开机拉取一次）

**来源**：软件工程师爬取目标医院官网/公众号整理，全院一张表

```json
{
  "hospital": "XX 医院",
  "departments": [
    {
      "name": "心内科",
      "building": "门诊楼",
      "floor": "3F",
      "room": "305",
      "hint": "三楼东侧，电梯左转",
      "hours": "周一至周五 8:00-17:00"
    }
  ]
}
```

- `name` 为唯一键，I1 返回的 `department` 必须在此表中；
- 板子开机时拉取一次存 Flash，离线可查；拉取失败用内置备份表。

---

## I5. 统一错误与降级信号

| code | 含义 | 板子行为 |
|------|------|----------|
| 0 | 成功 | 正常渲染 |
| 1 | ASR 未识别 | 显示+播报"没听清，请再说一次" |
| 2 | 科室未命中 | 显示"请到大厅服务台咨询" |
| 3 | 网络失败 | 显示"网络不好，请稍后再试"，播报同文案 |
| 4 | 定位失败 | 进入手动选起点菜单 |

---

## 赛前 Checklist（软件工程师）

- [ ] 腾讯位置服务 WebService Key（含网络定位、公交规划权限）
- [ ] ASR 引擎 Key（确认是否含方言模型）
- [ ] LLM API Key + 科室判断提示词
- [ ] TTS 接口（普通话）
- [ ] 云中转服务部署地址（板子固件烧录前定死 host）
