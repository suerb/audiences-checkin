#!/bin/bash
# audiences.me 签到启动脚本
# 由 launchd 调用，负责设置环境变量并执行 Python 脚本

set -e

# 脚本所在目录（即项目目录）
DIR="$(cd "$(dirname "$0")" && pwd)"

# 加载用户环境（launchd 不继承 shell 环境）
export PATH="/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin:$PATH"

# 日志文件
LOG_FILE="$DIR/checkin.log"

# 从配置文件加载飞书 Webhook（避免硬编码）
CONFIG_FILE="$DIR/.env"
if [ -f "$CONFIG_FILE" ]; then
    # 读取 KEY=VALUE 格式（忽略注释行）
    while IFS='=' read -r key value; do
        [[ "$key" =~ ^#.*$ || -z "$key" ]] && continue
        export "$key"="$value"
    done < "$CONFIG_FILE"
fi

# 找到 Python（优先用 venv）
VENV="$DIR/venv"
if [ -d "$VENV" ]; then
    PYTHON="$VENV/bin/python"
else
    PYTHON="$(which python3)"
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 启动签到..." >> "$LOG_FILE"

# 执行签到脚本，输出追加到日志
"$PYTHON" "$DIR/checkin_local.py" >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 签到脚本退出码：$EXIT_CODE" >> "$LOG_FILE"
echo "---" >> "$LOG_FILE"

exit $EXIT_CODE
