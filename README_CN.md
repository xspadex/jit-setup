<p align="center">
  <pre align="center">
     _ _ _
    (_|_) |_
    | | | __|
    | | | |_
   _/ |_|\__|
  |__/
  </pre>
</p>

<h3 align="center"><code>jit clone</code> — just in time 的 clone，克隆即可用。</h3>

<p align="center">
  跟 <code>git clone</code> 一样，但 clone 完环境就已经配好了。<br>
  一个 AI agent，读懂你的项目，搞清楚需要什么，把开发环境跑起来。
</p>

<p align="center">
  <a href="https://pypi.org/project/jit-setup/"><img src="https://img.shields.io/pypi/v/jit-setup" alt="PyPI"></a>
  <a href="https://github.com/xspadex/jit-setup/blob/main/LICENSE"><img src="https://img.shields.io/github/license/xspadex/jit-setup" alt="License"></a>
  <a href="https://pypi.org/project/jit-setup/"><img src="https://img.shields.io/pypi/pyversions/jit-setup" alt="Python"></a>
</p>

<p align="center">
  <a href="./README.md">English</a>
</p>

---

## 问题

你看到一个项目。然后花 20 分钟看 README，搞清楚用哪个 Python 版本，建 venv，装依赖，到处找 `.env` 要填什么，最后还要调半天为什么 `torch` import 不了。每个新项目、每台新电脑，重复一遍。

## 解法

```bash
pip install jit-setup
```

```bash
jit clone https://github.com/user/repo
```

一行命令。clone 下来，扫描项目，跟你聊它发现了什么，然后把一切搞定 — 虚拟环境、依赖安装、环境变量，全套。跟 `git clone` 一样的手感，但环境已经配好了。**jit** = **just in time** — 克隆即可用。

## 用法

```
jit clone <url>                  # 克隆 + 配环境一步到位
jit                              # 配置当前目录的项目
jit /path/to/project             # 配置指定路径的项目
jit --yes                        # 自动确认系统级操作
```

它做的事：

1. **扫描** — 读取项目结构：语言、包管理器、配置文件、Dockerfile、GPU 线索
2. **规划** — 弄清楚要装什么，问你偏好（venv 还是 conda 还是 uv，npm 还是 pnpm，等等）
3. **执行** — 创建环境、安装依赖、写 `.env`、跑 setup 脚本
4. **验证** — 最后跑一遍检查，确认一切真的能用

支持 **Python**、**Node.js**、**Rust**、**Go**、**Ruby**、**Docker** 等。

## 凭什么用它

- **零配置** — 不需要 YAML、插件、也不需要给每个项目维护专门的配置文件
- **对话式** — 做危险操作之前会先问你，会解释它看到了什么
- **安全优先** — 命令沙箱化在项目目录内；系统级操作需要确认；危险命令直接拦截
- **免费可用** — 自带社区 API（每天 30 次），开箱即用不需要 API key
- **零依赖** — 纯 Python 标准库，秒装

## 自带 LLM 或用自己的

免费社区 API 开箱即用。想无限制使用或换模型，配置 `~/.jitx/config.json`：

```json
{
  "llm": {
    "base_url": "https://api.openai.com/v1",
    "api_key": "sk-xxx",
    "model": "gpt-4o"
  }
}
```

兼容任何 OpenAI 格式的 API（OpenAI、SiliconFlow、Ollama、vLLM 等）。

## 安全机制

`jit` 采用纵深防御：

- **项目隔离** — 文件读取和命令执行限制在项目目录内
- **三级命令策略** — 安全命令自动执行、系统命令请求确认、危险命令直接拦截
- **数据不外泄** — 除了 LLM API 调用（关于项目结构的对话），没有数据离开你的电脑
- **HMAC 签名** — 社区 API 请求签名防盗用

## 环境要求

- Python 3.9+
- 没了

## 开源协议

Apache-2.0
