#!/bin/bash
# 一键安装 launchd 定时任务
# 运行：bash install_launchd.sh

set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$DIR/run_checkin.sh"
PLIST_SRC="$DIR/me.audiences.checkin.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/me.audiences.checkin.plist"
LOG_FILE="$DIR/checkin.log"

echo "=== audiences.me 签到定时任务安装 ==="
echo "项目目录：$DIR"

# 检查依赖
if ! command -v python3 &>/dev/null; then
    echo "错误：未找到 python3，请先安装 Python 3"
    exit 1
fi

# 创建 venv 并安装依赖
echo ""
echo "[1/4] 创建虚拟环境并安装依赖..."
python3 -m venv "$DIR/venv"
"$DIR/venv/bin/pip" install -q -r "$DIR/requirements_local.txt"
echo "依赖安装完成"

# 设置脚本可执行权限
chmod +x "$SCRIPT"

# 生成 .env 配置文件（如果不存在）
if [ ! -f "$DIR/.env" ]; then
    echo ""
    echo "[2/4] 生成配置文件 .env..."
    cat > "$DIR/.env" << 'EOF'
# audiences.me 签到配置
# 飞书机器人 Webhook（可选，不填则不推送通知）
FEISHU_WEBHOOK=
EOF
    echo "已生成 .env，请编辑填入飞书 Webhook："
    echo "   nano $DIR/.env"
else
    echo ""
    echo "[2/4] .env 已存在，跳过"
fi

# 生成最终 plist（替换占位符）
echo ""
echo "[3/4] 生成 launchd 配置..."
mkdir -p "$HOME/Library/LaunchAgents"
sed \
    -e "s|PLACEHOLDER_SCRIPT_PATH|$SCRIPT|g" \
    -e "s|PLACEHOLDER_LOG_PATH|$LOG_FILE|g" \
    "$PLIST_SRC" > "$PLIST_DEST"
echo "已写入：$PLIST_DEST"

# 加载定时任务
echo ""
echo "[4/4] 注册定时任务..."
# 先卸载旧的（忽略错误）
launchctl unload "$PLIST_DEST" 2>/dev/null || true
launchctl load "$PLIST_DEST"
echo "定时任务已注册！"

echo ""
echo "=== 安装完成 ==="
echo ""
echo "每天北京时间 09:00 自动签到"
echo "日志文件：$LOG_FILE"
echo ""
echo "手动立即测试签到："
echo "   bash $SCRIPT"
echo ""
echo "查看定时任务状态："
echo "   launchctl list | grep audiences"
echo ""
echo "卸载定时任务："
echo "   launchctl unload $PLIST_DEST"
