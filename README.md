# WeChatExporter - 微信聊天记录导出工具

Copyright © 泪无痕

## 功能介绍

- 自动检测微信数据路径
- 支持多个微信账号
- 导出格式：HTML(微信风格)、Word、PDF、PDF(手机窄版)、CSV、JSON、TXT
- 微信绿色气泡风格界面
- 联系人搜索和日期筛选
- 手机友好HTML导出

## 使用说明

### 免安装版
1. 双击 `WeChatExporter.exe` 启动
2. 程序会自动检测微信数据路径
3. 选择要导出的联系人
4. 选择导出格式和输出目录
5. 点击"开始导出"

### 系统要求
- Windows 7/8/10/11
- 无需安装Python

## 文件说明

```
WeChatExporter_PC/
├── 免安装版/
│   └── WeChatExporter.exe      # 可执行文件
├── 源代码/
│   ├── wechat_exporter_v5.py   # 主程序
│   ├── main.py                 # 启动入口
│   ├── core/                   # 核心功能
│   ├── gui/                    # 界面代码
│   └── requirements.txt        # 依赖列表
└── README.md                   # 说明文档
```

## 技术说明

- Python 3.12 + PyQt5
- SQLite数据库读取
- ZSTD解压缩
- PyInstaller打包
